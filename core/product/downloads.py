"""Persistent, single-lane model download and verified transactional install.

Run this object in the application coordinator, never in a window lifetime.
Only HTTPS catalog sources are accepted in production. Resuming requires a
strong ETag and matching Content-Range; otherwise the partial file is restarted.
No files are extracted until the complete pinned archive SHA-256 matches.
"""
from __future__ import annotations

import copy
import json
import os
import re
import shutil
import stat
import threading
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from pathlib import Path
from urllib.parse import urlparse

from .models import ModelCatalog, RECEIPT_NAME, file_sha256, safe_relative
from .storage import atomic_json


ACTIVE = {"downloading", "verifying", "installing", "pausing", "cancelling"}
FINAL = {"completed", "failed", "paused", "cancelled"}
ALLOWED_SOURCE_HOSTS = {"github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com"}


def _within(root: Path, path: Path) -> None:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError("模型路径越出专用目录") from exc
    current = path
    while current != root and current != current.parent:
        if current.is_symlink():
            raise ValueError("拒绝通过符号链接写入模型目录")
        current = current.parent


def safe_extract_zip(archive: Path, destination: Path, *, max_bytes: int,
                     stop_check=None) -> None:
    """Extract a validated ZIP without Windows/POSIX traversal or symlinks."""
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    with zipfile.ZipFile(archive) as zf:
        entries = zf.infolist()
        if len(entries) > 10000 or sum(x.file_size for x in entries) > max_bytes:
            raise ValueError("模型压缩包展开大小或文件数超出清单限制")
        seen = set()
        for entry in entries:
            name = entry.filename[:-1] if entry.is_dir() else entry.filename
            relative = safe_relative(name)
            key = relative.as_posix().casefold()
            if key in seen:
                raise ValueError("模型压缩包包含重复路径")
            seen.add(key)
            mode = (entry.external_attr >> 16) & 0xFFFF
            if entry.flag_bits & 1 or stat.S_ISLNK(mode) or stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR):
                raise ValueError("模型压缩包包含加密、链接或特殊文件")
            _within(destination, destination / relative)
        written = 0
        for entry in entries:
            if stop_check:
                stop_check()
            name = entry.filename[:-1] if entry.is_dir() else entry.filename
            target = destination / safe_relative(name)
            if entry.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(entry) as source, target.open("xb") as output:
                while True:
                    if stop_check:
                        stop_check()
                    chunk = source.read(256 * 1024)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > max_bytes:
                        raise ValueError("模型压缩包展开大小超限")
                    output.write(chunk)


class _Stopped(Exception):
    pass


class DownloadManager:
    """One application owner, persistent queue, independently queryable state."""

    def __init__(self, data_root: Path | str, model_root: Path | str,
                 catalog: ModelCatalog | None = None, *, allow_test_http=False,
                 timeout: float = 15.0):
        self.root = Path(data_root).resolve() / "model-downloads"
        self.model_root = Path(model_root).resolve()
        self.catalog = catalog or ModelCatalog()
        self.timeout = timeout
        self._allow_test_http = allow_test_http
        self._condition = threading.Condition(threading.RLock())
        self._closing = False
        self.root.mkdir(parents=True, exist_ok=True)
        self.model_root.mkdir(parents=True, exist_ok=True)
        lock_path = self.root / "owner.lock"
        _within(self.root, lock_path)
        self._owner_lock = lock_path.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt
                self._owner_lock.seek(0, os.SEEK_END)
                if self._owner_lock.tell() == 0:
                    self._owner_lock.write(b"0")
                    self._owner_lock.flush()
                self._owner_lock.seek(0)
                msvcrt.locking(self._owner_lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._owner_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self._owner_lock.close()
            raise RuntimeError("模型下载已有后台管理器；请连接现有实例") from exc
        self._state_path = self.root / "jobs.json"
        _within(self.root, self._state_path)
        self._jobs: dict[str, dict] = {}
        try:
            self._load_jobs()
        except Exception:
            self._owner_lock.close()
            raise
        self._worker = threading.Thread(target=self._run, daemon=True, name="capswriter-model-download")
        self._worker.start()

    def _load_jobs(self):
        if self._state_path.exists():
            # Fail closed, keeping a corrupt record for recovery instead of
            # silently overwriting it with an empty list.
            raw = json.loads(self._state_path.read_text(encoding="utf-8"))
            if raw.get("schema_version") != 1 or not isinstance(raw.get("jobs"), list):
                raise ValueError("模型下载记录格式不兼容；原记录已保留")
            for source in raw["jobs"]:
                job = copy.deepcopy(source)
                if not re.fullmatch(r"[0-9a-f]{32}", str(job.get("download_id", ""))):
                    raise ValueError("模型下载记录 ID 非法；原记录已保留")
                if job["status"] not in ACTIVE | FINAL | {"queued"}:
                    raise ValueError("模型下载状态非法；原记录已保留")
                try:
                    self.catalog.get(job["model_id"])
                except ValueError:
                    job.update(status="paused", stage="paused", error="模型清单已变化；旧任务和缓存保留，请重新选择模型。")
                if job["status"] in ACTIVE or job["status"] == "queued":
                    job.update(status="paused", stage="paused", error="上次应用已退出，可继续下载。")
                self._jobs[job["download_id"]] = job
            self._save()

    def _save(self):
        atomic_json(self._state_path, {"schema_version": 1, "jobs": list(self._jobs.values())})

    def snapshot(self) -> list[dict]:
        with self._condition:
            return copy.deepcopy(sorted(self._jobs.values(), key=lambda x: x["created_at"], reverse=True))

    def get(self, download_id: str) -> dict:
        with self._condition:
            return copy.deepcopy(self._job(download_id))

    def _job(self, download_id: str) -> dict:
        if download_id not in self._jobs:
            raise ValueError("没有找到该下载任务")
        return self._jobs[download_id]

    def start(self, model_id: str, *, license_accepted: bool = False) -> dict:
        item = self.catalog.get(model_id)
        if not item.get("download_enabled"):
            raise ValueError(item.get("disabled_reason", "该模型尚未完成来源核验"))
        if not license_accepted:
            raise ValueError("请先确认下载大小、目录与模型许可")
        self._check_url(item["url"])
        with self._condition:
            if self._closing:
                raise RuntimeError("应用正在退出，不能开始新的下载")
            for job in self._jobs.values():
                if job["model_id"] == model_id and job["status"] not in ("completed", "cancelled", "failed"):
                    return copy.deepcopy(job)
            if self.catalog.installed_path(model_id, self.model_root) == self.catalog.managed_path(model_id, self.model_root):
                self.catalog.verify_installation(model_id, self.model_root)
                raise ValueError("此版本已安装；请在模型中心选择启用，无需重复下载")
            self._check_space(item)
            now = time.time()
            job = {"download_id": uuid.uuid4().hex, "model_id": model_id,
                   "status": "queued", "stage": "queued", "bytes_received": 0,
                   "bytes_total": item["size_bytes"], "error": None, "etag": None,
                   "url": item["url"], "sha256": item["sha256"], "installed_path": None,
                   "created_at": now, "updated_at": now, "license_accepted": True}
            self._jobs[job["download_id"]] = job
            self._save()
            self._condition.notify_all()
            return copy.deepcopy(job)

    def pause(self, download_id: str) -> dict:
        with self._condition:
            job = self._job(download_id)
            if job["status"] == "queued":
                job.update(status="paused", stage="paused")
            elif job["status"] in ACTIVE and job["status"] != "cancelling":
                job.update(status="pausing")
            job["updated_at"] = time.time()
            self._save()
            return copy.deepcopy(job)

    def cancel(self, download_id: str) -> dict:
        with self._condition:
            job = self._job(download_id)
            if job["status"] in ACTIVE:
                job.update(status="cancelling")
            elif job["status"] != "completed":
                job.update(status="cancelled", stage="cancelled")
            job["updated_at"] = time.time()
            self._save()
            return copy.deepcopy(job)

    def resume(self, download_id: str) -> dict:
        with self._condition:
            job = self._job(download_id)
            if self._closing:
                raise RuntimeError("应用正在退出")
            if job["status"] not in {"paused", "failed", "cancelled"}:
                return copy.deepcopy(job)
            item = self.catalog.get(job["model_id"])
            if not item.get("download_enabled") or item["sha256"] != job["sha256"] or item["url"] != job["url"]:
                raise ValueError("模型来源已变化，旧缓存不会续写，请重新确认并新建下载")
            self._check_space(item)
            job.update(status="queued", stage="queued", error=None, updated_at=time.time())
            self._save()
            self._condition.notify_all()
            return copy.deepcopy(job)

    def shutdown(self, timeout: float = 30.0) -> bool:
        with self._condition:
            self._closing = True
            for job in self._jobs.values():
                if job["status"] in ACTIVE and job["status"] != "cancelling":
                    job["status"] = "pausing"
                elif job["status"] == "queued":
                    job.update(status="paused", stage="paused")
            self._save()
            self._condition.notify_all()
        self._worker.join(timeout=timeout)
        if not self._worker.is_alive():
            self._owner_lock.close()
        return not self._worker.is_alive()

    def _check_space(self, item):
        # Cache and staging may live on different volumes. Conservative budget:
        # archive + expected maximum unpacked bytes, plus 64 MiB reserve.
        reserve = 64 * 1024 * 1024
        archive = item["size_bytes"]
        unpacked = item.get("max_unpacked_bytes", archive * 4)
        if shutil.disk_usage(self.root).free < archive + reserve or shutil.disk_usage(self.model_root).free < archive + unpacked + reserve:
            raise ValueError("可用磁盘空间不足；请释放空间或选择其他模型目录")

    def _check_url(self, url):
        value = urlparse(url)
        if value.username or value.password or value.fragment:
            raise ValueError("模型下载 URL 含不允许的认证或片段")
        if self._allow_test_http and value.scheme == "http" and value.hostname in {"127.0.0.1", "localhost", "::1"}:
            return
        if value.scheme != "https" or value.hostname not in ALLOWED_SOURCE_HOSTS:
            raise ValueError("模型下载只允许已核验的 HTTPS 来源")

    def _update(self, download_id, **values):
        with self._condition:
            job = self._job(download_id)
            # A progress callback must not overwrite a concurrently requested stop.
            if job["status"] in {"pausing", "cancelling"} and values.get("status") not in FINAL:
                values.pop("status", None)
            job.update(values, updated_at=time.time())
            self._save()

    def _stop_check(self, download_id):
        with self._condition:
            if self._closing or self._job(download_id)["status"] in {"pausing", "cancelling"}:
                raise _Stopped()

    def _run(self):
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._closing or any(x["status"] == "queued" for x in self._jobs.values()))
                if self._closing:
                    return
                job = next(x for x in self._jobs.values() if x["status"] == "queued")
                download_id = job["download_id"]
                job.update(status="downloading", stage="downloading", error=None)
                self._save()
            try:
                self._execute(download_id)
            except _Stopped:
                with self._condition:
                    status = "cancelled" if self._job(download_id)["status"] == "cancelling" else "paused"
                self._update(download_id, status=status, stage=status)
            except Exception as exc:
                with self._condition:
                    current = self._job(download_id)["status"]
                if current in {"pausing", "cancelling"} or self._closing:
                    status = "cancelled" if current == "cancelling" else "paused"
                    self._update(download_id, status=status, stage=status)
                else:
                    self._update(download_id, status="failed", stage="failed", error=f"{type(exc).__name__}: {exc}"[:500])

    def _open(self, url, headers):
        self._check_url(url)
        # Validate each redirect before sending, not only its final destination.
        owner = self
        class CheckedRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, response_headers, newurl):
                owner._check_url(newurl)
                return super().redirect_request(req, fp, code, msg, response_headers, newurl)
        opener = urllib.request.build_opener(CheckedRedirect())
        return opener.open(urllib.request.Request(url, headers={"User-Agent": "CapsWriter-Offline-V1", "Accept-Encoding": "identity", **headers}), timeout=self.timeout)

    def _download(self, download_id, item, part):
        job = self.get(download_id)
        offset = part.stat().st_size if part.exists() else 0
        etag = job.get("etag")
        if offset == item["size_bytes"]:
            self._update(download_id, bytes_received=offset)
            return
        can_resume = bool(offset and offset < item["size_bytes"] and etag and not etag.startswith("W/"))
        headers = {"Range": f"bytes={offset}-", "If-Range": etag} if can_resume else {}
        try:
            response = self._open(item["url"], headers)
        except urllib.error.HTTPError as exc:
            if can_resume and exc.code in {405, 412, 416, 501}:
                response = self._open(item["url"], {})
                can_resume = False
            else:
                raise
        if can_resume:
            expected_range = f"bytes {offset}-{item['size_bytes'] - 1}/{item['size_bytes']}"
            if response.status != 206 or response.headers.get("ETag") != etag or response.headers.get("Content-Range") != expected_range:
                response.close()
                response = self._open(item["url"], {})
                can_resume = False
        if not can_resume:
            offset = 0
            if response.status != 200:
                response.close()
                raise ValueError("服务器未返回完整文件；缓存已保留")
        if response.headers.get("Content-Encoding", "identity") != "identity":
            response.close()
            raise ValueError("模型服务器返回了不支持的内容编码")
        length = response.headers.get("Content-Length")
        if length is not None and int(length) != item["size_bytes"] - offset:
            response.close()
            raise ValueError("模型文件大小与已核验清单不一致")
        self._update(download_id, etag=response.headers.get("ETag"), bytes_received=offset)
        last_save = time.monotonic()
        with response, part.open("ab" if can_resume else "wb") as target:
            while True:
                self._stop_check(download_id)
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                offset += len(chunk)
                if offset > item["size_bytes"]:
                    raise ValueError("下载内容超过已核验大小")
                target.write(chunk)
                with self._condition:
                    self._job(download_id)["bytes_received"] = offset
                if time.monotonic() - last_save >= 0.5:
                    target.flush()
                    self._update(download_id, bytes_received=offset)
                    last_save = time.monotonic()
            target.flush()
            os.fsync(target.fileno())
        self._update(download_id, bytes_received=offset)
        if offset != item["size_bytes"]:
            raise ValueError("下载中断，已保存缓存，可稍后继续")

    def _execute(self, download_id):
        job = self.get(download_id)
        item = self.catalog.get(job["model_id"])
        cache = self.root / download_id
        _within(self.root, cache)
        cache.mkdir(exist_ok=True)
        part = cache / "archive.part"
        _within(self.root, part)
        self._download(download_id, item, part)
        self._stop_check(download_id)
        self._update(download_id, status="verifying", stage="verifying")
        if file_sha256(part) != item["sha256"]:
            # Keep diagnostic bytes under an owned unique name. A retry must not
            # repeatedly reuse the bad complete archive.
            part.rename(cache / ("rejected-" + uuid.uuid4().hex + ".bin"))
            self._update(download_id, etag=None, bytes_received=0)
            raise ValueError("模型 SHA-256 校验失败，拒绝安装；旧模型保持不变")
        self._stop_check(download_id)
        self._update(download_id, status="installing", stage="installing")
        staging_parent = self.model_root / ".capswriter-staging"
        _within(self.model_root, staging_parent)
        staging_parent.mkdir(exist_ok=True)
        # The staging directory needs uniqueness, not a second copy of the job
        # ID. Shorter staging paths leave room for archive names on Windows.
        stage = staging_parent / uuid.uuid4().hex
        target = self.catalog.managed_path(item["id"], self.model_root)
        _within(self.model_root, target)
        if target.exists():
            # Never replace an existing model, including an interrupted or
            # unrecognized installation at a seemingly owned path.
            verified = self.catalog.verify_installation(item["id"], self.model_root)
            if verified["path"] != str(target) or not self.catalog._valid_receipt(target, item):
                raise ValueError("安装目标已存在且无法确认归属；保留原文件")
            self._update(download_id, status="completed", stage="completed", installed_path=str(target))
            return
        try:
            safe_extract_zip(part, stage, max_bytes=item.get("max_unpacked_bytes", item["size_bytes"] * 4), stop_check=lambda: self._stop_check(download_id))
            extracted = stage / safe_relative(item["archive_root"]) if item.get("archive_root") else stage
            hashes = {}
            for filename in item["required_files"]:
                self._stop_check(download_id)
                file = extracted / safe_relative(filename)
                if not file.is_file() or file.stat().st_size == 0:
                    raise ValueError("模型压缩包缺少必要文件：" + filename)
                hashes[filename] = file_sha256(file)
            atomic_json(extracted / RECEIPT_NAME, {"schema_version": 1, "model_id": item["id"],
                         "archive_sha256": item["sha256"], "files": hashes,
                         "source_url": item["url"], "license_url": item["license_url"],
                         "attribution": item.get("attribution", ""), "installed_at": time.time()})
            self._stop_check(download_id)
            target.parent.mkdir(parents=True, exist_ok=True)
            # Same-volume rename is the commit point; completed means installed,
            # never "Worker ready". No previous model is removed.
            with self._condition:
                self._stop_check(download_id)
                if target.exists():
                    raise ValueError("安装目标已存在；拒绝覆盖")
                extracted.rename(target)
                self._update(download_id, status="completed", stage="completed", installed_path=str(target))
        finally:
            # The only cleanup is the unique staging directory created above.
            # Never remove user folders, partial download evidence or models.
            if stage.exists() and not stage.is_symlink():
                _within(staging_parent, stage)
                shutil.rmtree(stage)
