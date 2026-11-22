"""Reproducible V1 source staging and Windows packaging, without user data.

Examples:
  python tools/build_v1.py --stage-only --output build/v1-review
  python tools/build_v1.py --minimal --output build/v1-smoke
  python tools/build_v1.py --vendor-manifest C:/reviewed/vendor.json --iscc "C:/Program Files (x86)/Inno Setup 6/ISCC.exe" --output build/v1-release

Never removes an existing output directory. A build is not a release: metadata
always starts with windows_validated=False/public_verified=False.
"""
from __future__ import annotations

import argparse
import ast
import copy
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
import zipfile

ROOT = Path(__file__).resolve().parents[1]
DEFAULTS = Path("packaging/release_defaults")
ENTRY_FILES = {"run_app.py", "start_client.py", "start_server.py", "config_client.py", "config_server.py", "config_pill.py", "LICENSE", "readme.md", "v1-app.spec"}
STATIC_FILES = {"assets/source/capswriter.ico", "assets/ui/capswriter_logo.png", "assets/ui/control-center-preview.png", "core/tools/zhconv/zhcdict.json", "packaging/version.json", "packaging/runtime_hook.py", "packaging/capswriter.iss", "packaging/RELEASE_NOTES.md", "packaging/PRIVACY.md"}
GGUF_ROOTS = tuple(f"core/server/engines/{name}/inference/bin/" for name in ("fun_asr_gguf", "qwen_asr_gguf", "force_aligner_gguf"))
HEX64 = re.compile(r"[0-9a-f]{64}")
PRIVATE_NAMES = {"input_history.jsonl", "input_history_cleared_at.txt", "gui_settings.json", "private_config.json", "llm_profiles.json"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative(name: str) -> PurePosixPath:
    rel = PurePosixPath(name)
    if not name or "\\" in name or ":" in name or rel.is_absolute() or any(p in ("", ".", "..") for p in name.split("/")):
        raise ValueError("Unsafe relative build path")
    return rel


def allowed_source(name: str) -> bool:
    rel = _safe_relative(name)
    if name in ENTRY_FILES or name in STATIC_FILES:
        return True
    if any(part in {"__pycache__", ".git", "logs", "outputs", "uploads", "config_backups", "config_exports", "export"} for part in rel.parts):
        return False
    if name.startswith(("core/", "web_gui/")) and rel.suffix == ".py":
        return True
    if name.startswith("core/product/") and rel.suffix == ".json":
        return True
    if name.startswith("core/tools/chinese_itn/resources/") and rel.suffix == ".json":
        return True
    if name.startswith("core/server/engines/") and "/inference/assets/" in name and rel.suffix == ".dict":
        return True
    if name.startswith("packaging/release_defaults/") and rel.suffix in {".py", ".txt", ".json"}:
        return True
    if name.startswith("docs/") and len(rel.parts) == 2 and rel.suffix == ".md":
        return True
    return False


def _copy_checked(source: Path, destination: Path, root: Path) -> None:
    if source.is_symlink() or not source.is_file() or not source.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"Refusing linked/outside source: {source.name}")
    cursor = source.parent
    while cursor != root:
        if cursor.is_symlink():
            raise ValueError("Refusing linked source directory")
        if cursor == cursor.parent:
            raise ValueError("Source is outside root")
        cursor = cursor.parent
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)


def _clean_config(source: str, defaults: str) -> str:
    """Preserve new config wiring while replacing editable literals with clean defaults."""
    template = ast.parse(defaults)
    values = {}
    for cls in (node for node in template.body if isinstance(node, ast.ClassDef)):
        for node in cls.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                try:
                    ast.literal_eval(node.value)
                except (ValueError, TypeError):
                    continue
                values[(cls.name, node.targets[0].id)] = node.value
    tree = ast.parse(source)
    for cls in (node for node in tree.body if isinstance(node, ast.ClassDef)):
        for node in cls.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                key = cls.name, node.targets[0].id
                if key in values:
                    node.value = copy.deepcopy(values[key])
    ast.fix_missing_locations(tree)
    return ast.unparse(tree) + "\n"


def assert_no_literal_secrets(root: Path) -> None:
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = [target.id.lower() for target in targets if isinstance(target, ast.Name)]
            if not any(name in {"api_key", "access_token", "secret_key", "authorization"} for name in names):
                continue
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str) and node.value.value:
                raise ValueError(f"Literal credential found in staged source: {path.relative_to(root)}")


def stage_sources(root: Path, output: Path, tracked_files: list[str] | None = None) -> dict:
    root, output = root.resolve(), output.absolute()
    if output.exists():
        raise FileExistsError("Output directory exists; choose a new build directory. Nothing was deleted.")
    if tracked_files is None:
        result = subprocess.run(["git", "ls-files", "-z"], cwd=root, check=True, capture_output=True)
        tracked_files = result.stdout.decode("utf-8").split("\0")
    selected = sorted(name for name in tracked_files if name and allowed_source(name))
    if "run_app.py" not in selected or "v1-app.spec" not in selected:
        raise ValueError("Required entry/spec not tracked; commit reviewed source before building.")
    output.mkdir(parents=True)
    for name in selected:
        _copy_checked(root / name, output / name, root)
    for name in ("config_client.py", "config_server.py", "config_pill.py"):
        source, defaults = root / name, root / DEFAULTS / name
        if not defaults.exists():
            raise ValueError(f"Missing clean template: {name}")
        (output / name).write_text(_clean_config(source.read_text(encoding="utf-8-sig"), defaults.read_text(encoding="utf-8-sig")), encoding="utf-8")
    # Runtime bootstrap can copy these clean files once; no user profiles/roles.
    for source in sorted((root / DEFAULTS).rglob("*")):
        if not source.is_file():
            continue
        rel = source.relative_to(root / DEFAULTS)
        if rel.as_posix().startswith("LLM/") or rel.name in {"hot.txt", "hot-server.txt", "hot-rule.txt"}:
            _copy_checked(source, output / rel, root)
    assert_no_literal_secrets(output)
    inventory = [{"path": str(p.relative_to(output)).replace(os.sep, "/"), "size": p.stat().st_size, "sha256": sha256(p)}
                 for p in sorted(output.rglob("*")) if p.is_file()]
    return {"schema": "capswriter.source-stage.v1", "files": inventory}


def stage_vendors(manifest_path: Path, destination: Path, *, full: bool = True) -> dict:
    manifest_path = manifest_path.resolve()
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    if data.get("schema") != "capswriter.reviewed-vendors.v1" or not isinstance(data.get("files"), list):
        raise ValueError("Invalid vendor manifest")
    staged = []
    seen = set()
    for entry in data["files"]:
        target = str(_safe_relative(entry["target"]))
        source = manifest_path.parent / entry["source_file"]
        if source.is_symlink():
            raise ValueError("Vendor source symlinks are not accepted")
        source = source.resolve()
        expected = str(entry.get("sha256", "")).lower()
        component = entry.get("component")
        is_media = component == "ffmpeg" and target.startswith("tools/ffmpeg/bin/") and Path(target).suffix.lower() in {".exe", ".dll"}
        is_gguf = component == "gguf" and target.startswith(GGUF_ROOTS) and Path(target).suffix.lower() == ".dll"
        if not (is_media or is_gguf) or target in seen or entry.get("reviewed") is not True:
            raise ValueError("Unreviewed, duplicate or outside vendor target")
        if not HEX64.fullmatch(expected) or not source.is_file() or source.is_symlink() or sha256(source) != expected:
            raise ValueError("Vendor bytes do not match reviewed SHA-256")
        if not str(entry.get("source_url", "")).startswith("https://") or not entry.get("license_id") or not entry.get("license_files") or not entry.get("version"):
            raise ValueError("Vendor source/version/license evidence missing")
        dest = destination / target
        if dest.exists():
            raise ValueError("Vendor cannot replace an existing staged file")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dest)
        for license_file in entry["license_files"]:
            name = str(_safe_relative(license_file["name"]))
            if "/" in name:
                raise ValueError("License name must be a simple filename")
            local = manifest_path.parent / license_file["path"]
            if local.is_symlink():
                raise ValueError("Vendor license symlinks are not accepted")
            local = local.resolve()
            if not local.is_file() or local.is_symlink() or sha256(local) != license_file["sha256"]:
                raise ValueError("Vendor license does not match reviewed bytes")
            folder = destination / "THIRD_PARTY" / component
            folder.mkdir(parents=True, exist_ok=True)
            license_dest = folder / name
            if license_dest.exists() and sha256(license_dest) != license_file["sha256"]:
                raise ValueError("Conflicting license filename")
            shutil.copyfile(local, license_dest)
        seen.add(target)
        staged.append({key: entry[key] for key in ("target", "component", "sha256", "source_url", "license_id", "version")})
    if full:
        required = {"tools/ffmpeg/bin/ffmpeg.exe", "tools/ffmpeg/bin/ffprobe.exe"}
        required.update(prefix + name for prefix in GGUF_ROOTS for name in ("llama.dll", "ggml.dll", "ggml-base.dll"))
        if required - seen:
            raise ValueError("Full build missing reviewed FFmpeg/GGUF runtime files: " + ", ".join(sorted(required - seen)))
    inventory = {"schema": "capswriter.vendor-inventory.v1", "files": staged}
    (destination / "packaging" / "vendor_inventory.json").write_text(json.dumps(inventory, ensure_ascii=False, indent=2), encoding="utf-8")
    return inventory


def collect_licenses(destination: Path) -> list[dict]:
    """Inventory the dedicated build venv; include installed LICENSE/NOTICE files."""
    result = []
    (destination / "THIRD_PARTY").mkdir(parents=True, exist_ok=True)
    for distribution in sorted(importlib.metadata.distributions(), key=lambda d: d.metadata.get("Name", "")):
        name = distribution.metadata.get("Name", "unknown")
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", name)
        record = {"name": name, "version": distribution.version,
                  "license_expression": distribution.metadata.get("License-Expression", ""), "license_files": []}
        for file in distribution.files or []:
            if not re.match(r"(?i)^(license|copying|notice)(\.|$)", Path(str(file)).name):
                continue
            local = Path(distribution.locate_file(file))
            if not local.is_file() or local.stat().st_size > 2 * 1024 * 1024:
                continue
            # Hash disambiguates licenses with identical basenames from subpackages.
            basename = local.name + "-" + sha256(local)[:10] + ".txt"
            target = destination / "THIRD_PARTY" / "python" / safe / basename
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(local, target)
            record["license_files"].append(target.relative_to(destination).as_posix())
        result.append(record)
    (destination / "THIRD_PARTY" / "python-inventory.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def audit_bundle(bundle: Path) -> dict:
    bad = []
    inventory = []
    for path in sorted(bundle.rglob("*")):
        if path.is_symlink():
            bad.append(path.relative_to(bundle).as_posix())
            continue
        if not path.is_file():
            continue
        rel = path.relative_to(bundle)
        parts = set(rel.parts)
        if (parts & {".git", "logs", "uploads", "outputs", "config_backups", "config_exports", "models", "data"} or
                path.suffix.lower() in {".onnx", ".gguf", ".mp3", ".wav", ".m4a", ".log"} or
                (path.name in PRIVATE_NAMES and "release_defaults" not in parts)):
            bad.append(rel.as_posix())
        inventory.append({"path": rel.as_posix(), "size": path.stat().st_size, "sha256": sha256(path)})
    if bad:
        raise ValueError("Release contains private/runtime/model data: " + ", ".join(bad[:10]))
    assert_no_literal_secrets(bundle)
    return {"schema": "capswriter.bundle-inventory.v1", "files": inventory}


def zip_portable(bundle: Path, target: Path) -> None:
    """Marker belongs only to ZIP, so installer and portable share an immutable build."""
    if target.exists():
        raise FileExistsError("Portable archive already exists")
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(bundle.rglob("*")):
            if path.is_symlink():
                raise ValueError("Portable cannot include symlinks")
            if path.is_file():
                archive.write(path, "CapsWriter/" + path.relative_to(bundle).as_posix())
        archive.writestr("CapsWriter/portable.flag", "CapsWriter Portable V1\n")


def artifact_record(path: Path, kind: str) -> dict:
    return {"kind": kind, "filename": path.name, "architecture": "x64", "size": path.stat().st_size,
            "sha256": sha256(path), "signature": "unsigned", "url": None, "public_verified": False,
            "verified_at": None}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--stage-only", action="store_true")
    parser.add_argument("--minimal", action="store_true", help="Early smoke build without external media/GGUF DLLs; not release eligible")
    parser.add_argument("--vendor-manifest", type=Path)
    parser.add_argument("--iscc", type=Path, help="Inno Setup 6 compiler path; required for full build")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    metadata = json.loads((ROOT / "packaging/version.json").read_text(encoding="utf-8"))
    if args.dry_run:
        print(json.dumps({"version": metadata, "platform": platform.system(), "build_requires": "Windows x64, Python 3.11, requirements-v1.txt",
                          "full_build_requires": ["reviewed FFmpeg/GGUF manifest", "Inno Setup 6"], "publishes": False}, ensure_ascii=False, indent=2))
        return 0
    if not args.stage_only and sys.platform != "win32":
        parser.error("Windows build must run on Windows; use --stage-only here.")
    if not args.stage_only and not args.minimal and (not args.vendor_manifest or not args.iscc):
        parser.error("Full build needs --vendor-manifest and --iscc. --minimal is an honest smoke-only build.")
    job = (args.output or ROOT / "build" / ("v1-" + time.strftime("%Y%m%d-%H%M%S"))).absolute()
    if job.exists():
        parser.error("Output already exists; choose a new path. No files were deleted.")
    job.mkdir(parents=True)
    stage = job / "source"
    source_inventory = stage_sources(ROOT, stage)
    (job / "source-inventory.json").write_text(json.dumps(source_inventory, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.vendor_manifest:
        stage_vendors(args.vendor_manifest, stage, full=not args.minimal)
    if args.stage_only:
        print(f"Clean sources staged: {stage}")
        return 0
    collect_licenses(stage)
    subprocess.run([sys.executable, "-m", "PyInstaller", str(stage / "v1-app.spec"), "--distpath", str(job / "dist"), "--workpath", str(job / "pyinstaller")], cwd=stage, check=True)
    bundle = job / "dist" / "CapsWriter"
    inventory = audit_bundle(bundle)
    (job / "bundle-inventory.json").write_text(json.dumps(inventory, ensure_ascii=False, indent=2), encoding="utf-8")
    artifacts = job / "artifacts"
    artifacts.mkdir()
    version = metadata["version"]
    prefix = f"CapsWriter-Offline-{version}-Windows-x64-"
    portable = artifacts / (prefix + "Portable.zip")
    zip_portable(bundle, portable)
    records = [artifact_record(portable, "portable")]
    if args.iscc:
        numeric = ".".join(re.match(r"^(\d+)\.(\d+)\.(\d+)", version).groups()) + ".0"
        subprocess.run([str(args.iscc.resolve()), "/DAppVersion=" + version, "/DNumericVersion=" + numeric,
                        "/DBuildSource=" + str(bundle), "/DOutputDirectory=" + str(artifacts), str(stage / "packaging/capswriter.iss")], check=True)
        records.append(artifact_record(artifacts / (prefix + "Setup.exe"), "installer"))
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()
    manifest = {"schema": "capswriter.release.v1", "version": version, "channel": metadata["channel"], "code_sha": commit,
                "generated_at": datetime.now(timezone.utc).isoformat(), "status": "candidate", "windows_validated": False,
                "minimal_build": args.minimal, "artifacts": records}
    (artifacts / "release-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (artifacts / "SHA256SUMS.txt").write_text("".join(f"{a['sha256']}  {a['filename']}\n" for a in records), encoding="utf-8")
    print(f"Built candidate artifacts: {artifacts}. Windows acceptance and public-link verification remain required.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
