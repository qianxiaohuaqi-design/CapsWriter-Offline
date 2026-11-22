"""Versioned model catalog and non-destructive installed-model discovery.

Catalog entries describe files, never readiness. A Worker must actually load a
model before the control center can call it ready. Existing manual installations
remain discoverable and are never moved or replaced by the downloader.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import threading
import uuid
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlparse


MANIFEST_PATH = Path(__file__).with_name("model_manifest.json")
RECEIPT_NAME = ".capswriter-install.json"


def safe_relative(value: str) -> Path:
    """Reject paths whose Windows and POSIX interpretations differ dangerously."""
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise ValueError("模型清单含非法路径")
    p = PurePosixPath(value)
    if p.is_absolute() or any(x in ("", ".", "..") for x in value.split("/")):
        raise ValueError("模型路径必须是安全的相对路径")
    for part in p.parts:
        stem = part.split(".")[0].upper()
        if ":" in part or part.endswith((" ", ".")) or stem in {
            "CON", "PRN", "AUX", "NUL", *("COM" + str(x) for x in range(1, 10)),
            *("LPT" + str(x) for x in range(1, 10)),
        }:
            raise ValueError("模型路径不能包含 Windows 设备名或数据流")
    return Path(*p.parts)


def file_sha256(path: Path) -> str:
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


class ModelCatalog:
    def __init__(self, manifest_path: Path | str | None = None, *, entries=None,
                 manual_registry_path: Path | str | None = None):
        raw = {"models": entries} if entries is not None else json.loads(
            Path(manifest_path or MANIFEST_PATH).read_text(encoding="utf-8"))
        self._models: dict[str, dict[str, Any]] = {}
        for source in raw["models"]:
            item = copy.deepcopy(source)
            model_id = item["id"]
            if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,90}", model_id) or model_id in self._models:
                raise ValueError("模型 ID 非法或重复")
            for name in item.get("required_files", []):
                safe_relative(name)
            if item.get("legacy_directory"):
                safe_relative(item["legacy_directory"])
            if item.get("archive_root"):
                safe_relative(item["archive_root"])
            if item.get("download_enabled"):
                source_url = urlparse(item["url"])
                # Production entries must have reviewed HTTPS source, digest,
                # license and a complete file/layout contract.
                if source_url.scheme not in ("https", "http") or not source_url.hostname:
                    raise ValueError("模型来源 URL 非法")
                if not re.fullmatch(r"[0-9a-f]{64}", item.get("sha256", "")):
                    raise ValueError("模型下载缺少固定 SHA-256")
                if not item.get("license_url") or not item.get("required_files") or int(item.get("size_bytes", 0)) < 1:
                    raise ValueError("模型下载来源/许可/大小/文件列表不完整")
            self._models[model_id] = item
        self._registry_lock = threading.RLock()
        if manual_registry_path is None and entries is None:
            from .paths import data_root
            manual_registry_path = data_root() / "product" / "manual-models.json"
        self.manual_registry_path = Path(manual_registry_path) if manual_registry_path else None
        self._manual = self._read_manual_registry()

    def _read_manual_registry(self) -> dict:
        path = self.manual_registry_path
        if not path or not path.exists():
            return {}
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("schema_version") != 1 or not isinstance(value.get("models"), dict):
            raise ValueError("手动模型目录记录损坏；原文件已保留")
        for model_id, directory in value["models"].items():
            if not isinstance(model_id, str) or not isinstance(directory, str) or not Path(directory).is_absolute():
                raise ValueError("手动模型目录记录包含无效路径；原文件已保留")
        return value["models"]

    def register_manual(self, model_id: str, directory: Path | str, *,
                        model_root: Path | str | None = None, replace: bool = False) -> dict:
        """Remember an explicitly confirmed known-layout local directory.

        This does not load a Worker. The coordinator must reject an active model
        or ask for explicit repair/switch confirmation before changing a current
        selection. No model files are copied, changed or deleted by this method.
        Passing model_root also rejects replacement of a discovered installation.
        """
        item = self.get(model_id)
        if self.manual_registry_path is None:
            raise ValueError("手动目录记录路径未配置")
        supplied = Path(directory).expanduser()
        if not supplied.is_absolute():
            raise ValueError("请选择完整的本地模型目录路径")
        chosen = supplied.resolve()
        if not self._files_present(chosen, item):
            raise ValueError("该目录缺少此模型的必要文件，请选择包含 " + "、".join(item["required_files"]) + " 的目录")
        with self._registry_lock:
            current = self._read_manual_registry()
            previous = current.get(model_id)
            if previous and Path(previous).resolve() != chosen and not replace:
                raise ValueError("该模型已有手动目录；请通过明确的修复/切换流程处理，不能覆盖选择")
            if model_root is not None:
                existing = self.installed_path(model_id, model_root)
                if existing and existing.resolve() != chosen and not replace:
                    raise ValueError("该模型已经安装；不会覆盖已有选择")
            current[model_id] = str(chosen)
            target = self.manual_registry_path
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(target.name + "." + uuid.uuid4().hex + ".tmp")
            try:
                with temporary.open("x", encoding="utf-8") as stream:
                    json.dump({"schema_version": 1, "models": current}, stream, ensure_ascii=False, indent=2)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, target)
            finally:
                if temporary.exists():
                    temporary.unlink()
            self._manual = current
        return {**item, "installed": True, "installed_path": str(chosen),
                "installation_kind": "manual", "integrity": "manual_unverified", "can_download": bool(item.get("download_enabled"))}

    def get(self, model_id: str) -> dict:
        try:
            return copy.deepcopy(self._models[model_id])
        except KeyError as exc:
            raise ValueError("未知模型，请刷新模型中心") from exc

    def managed_path(self, model_id: str, model_root: Path | str) -> Path:
        item = self.get(model_id)
        digest = item.get("sha256", "unverified")[:12]
        return Path(model_root).resolve() / ".capswriter-installed" / model_id / digest

    def _files_present(self, directory: Path, item: dict) -> bool:
        if not directory.is_dir():
            return False
        return all((directory / safe_relative(name)).is_file()
                   and (directory / safe_relative(name)).stat().st_size > 0
                   for name in item["required_files"])

    def installed_path(self, model_id: str, model_root: Path | str) -> Path | None:
        item = self.get(model_id)
        if model_id in self._manual:
            chosen = Path(self._manual[model_id])
            # A missing explicitly chosen directory is a repair case. Do not
            # silently substitute the bundled/default location for that choice.
            return chosen if self._files_present(chosen, item) else None
        managed = self.managed_path(model_id, model_root)
        if self._files_present(managed, item) and self._valid_receipt(managed, item):
            return managed
        legacy = item.get("legacy_directory")
        if legacy:
            manual = Path(model_root).resolve() / safe_relative(legacy)
            if self._files_present(manual, item):
                return manual
        return None

    @staticmethod
    def _valid_receipt(directory: Path, item: dict) -> bool:
        try:
            receipt = json.loads((directory / RECEIPT_NAME).read_text(encoding="utf-8"))
            return receipt.get("model_id") == item["id"] and receipt.get("archive_sha256") == item.get("sha256")
        except (OSError, ValueError, TypeError):
            return False

    def verify_installation(self, model_id: str, model_root: Path | str) -> dict:
        item = self.get(model_id)
        path = self.installed_path(model_id, model_root)
        if path is None:
            raise ValueError("模型文件缺失，请安装模型或检查高级设置中的手动路径")
        managed = self.managed_path(model_id, model_root)
        if path != managed or model_id in self._manual:
            return {"path": str(path), "integrity": "manual_unverified", "files_present": True}
        if path.is_symlink() or (path / RECEIPT_NAME).is_symlink():
            raise ValueError("已安装模型目录或记录是符号链接，无法确认完整性")
        receipt = json.loads((path / RECEIPT_NAME).read_text(encoding="utf-8"))
        hashes = receipt.get("files", {})
        for filename in item["required_files"]:
            file = path / safe_relative(filename)
            if file.is_symlink() or filename not in hashes or file_sha256(file) != hashes[filename]:
                raise ValueError("已安装模型校验失败；保留原文件，请重新安装到独立位置或修复")
        return {"path": str(path), "integrity": "verified", "files_present": True}

    def list_models(self, model_root: Path | str) -> list[dict]:
        result = []
        for model_id in self._models:
            item = self.get(model_id)
            path = self.installed_path(model_id, model_root)
            managed = self.managed_path(model_id, model_root)
            is_managed = path == managed and model_id not in self._manual
            item.update(installed=path is not None, installed_path=str(path) if path else None,
                        manual_directory=self._manual.get(model_id),
                        installation_kind=("managed" if is_managed else "manual") if path else None,
                        # This is a receipt, not a re-hash or Worker readiness.
                        integrity="verified_at_install" if is_managed else ("manual_unverified" if path else "not_installed"),
                        can_download=bool(item.get("download_enabled")))
            result.append(item)
        return result

    def configuration(self, model_id: str, model_root: Path | str) -> dict:
        """Return class-level configuration values; the caller owns switching.

        Shape: {model_type, classes: {SenseVoiceArgs: {encoder_path, ...}}}.
        Caller applies these to the existing classes before EngineFactory loads.
        """
        item = self.get(model_id)
        verified = self.verify_installation(model_id, model_root)
        path = Path(verified["path"])
        classes = {}
        for class_name, values in item.get("configuration", {}).items():
            classes[class_name] = {
                key: str(path / safe_relative(value["file"])) if isinstance(value, dict) and "file" in value
                else str(path) if value == "$MODEL_DIR" else value
                for key, value in values.items()
            }
        return {"model_type": item["engine"], "classes": classes, "integrity": verified["integrity"]}


def inspect_configured_model(namespace: dict, base_root: Path | str | None = None) -> dict:
    """Inspect actual existing Args, including custom per-file legacy paths.

    This read-only observation is for repair/compatibility UI. It never promotes
    file presence to integrity or readiness, and never rewrites a path setting.
    """
    engine = str(getattr(namespace["ServerConfig"], "model_type", "")).lower()
    model_id = getattr(namespace["ServerConfig"], "_product_model_id", engine)
    fields = {
        "sensevoice": ("SenseVoiceArgs", ("encoder_path", "decoder_path", "tokenizer_path")),
        "fun_asr_nano": ("FunASRNanoGGUFArgs", ("encoder_onnx_path", "ctc_onnx_path", "decoder_gguf_path", "tokens_path")),
        "paraformer": ("ParaformerArgs", ("paraformer", "tokens")),
        "qwen_asr": ("Qwen3ASRGGUFArgs", ("encoder_frontend_fn", "encoder_backend_fn", "llm_fn")),
    }
    if engine not in fields:
        return {"engine": engine, "model_id": model_id, "files_present": False,
                "integrity": "manual_unverified", "required_files": [], "error": "未识别的现有模型配置"}
    class_name, keys = fields[engine]
    config = namespace.get(class_name)
    files = []
    try:
        for key in keys:
            path = Path(getattr(config, key)).expanduser()
            if engine == "qwen_asr":
                path = Path(getattr(config, "model_dir")).expanduser() / path
            if not path.is_absolute() and base_root is not None:
                path = Path(base_root) / path
            files.append(path)
        missing = [str(path) for path in files if not path.is_file() or path.stat().st_size == 0]
        error = getattr(namespace["ServerConfig"], "_product_model_error", None)
        return {"engine": engine, "model_id": model_id, "files_present": not missing and not error,
                "integrity": "manual_unverified", "required_files": [str(path) for path in files],
                "missing": missing, "error": str(error) if error else None}
    except (OSError, TypeError, ValueError, AttributeError) as exc:
        return {"engine": engine, "model_id": model_id, "files_present": False,
                "integrity": "manual_unverified", "required_files": [str(path) for path in files],
                "missing": [], "error": "现有模型路径无法解析：" + str(exc)}
