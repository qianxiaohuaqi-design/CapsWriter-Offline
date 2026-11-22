"""Model failures are tested through real local HTTP and filesystem transactions.

No model weights or user folders are touched. These tests do not claim ASR,
Windows DirectML or native window behavior has passed.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import stat
import tempfile
import threading
import time
import unittest
import uuid
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core.product.downloads import DownloadManager, safe_extract_zip
from core.product.models import ModelCatalog, safe_relative, inspect_configured_model


def archive_bytes(names=None):
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w") as archive:
        for name, content in (names or {"fixture/model.onnx": b"model fixture", "fixture/tokens.txt": b"token fixture"}).items():
            archive.writestr(name, content)
    return target.getvalue()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        fixture = self.server.fixture
        request_range = self.headers.get("Range")
        fixture["requests"].append({"range": request_range, "if_range": self.headers.get("If-Range")})
        payload = fixture["payload"]
        mode = fixture.get("mode")
        if request_range and mode == "unsupported_range":
            self.send_error(501)
            return
        offset = int(request_range.split("=")[1].split("-")[0]) if request_range else 0
        ranged = bool(request_range and mode != "ignore_range")
        if not ranged:
            offset = 0
        self.send_response(206 if ranged else 200)
        self.send_header("ETag", '"v2"' if mode == "changed_etag" and ranged else '"v1"')
        if ranged:
            start = offset + 1 if mode == "bad_range" else offset
            self.send_header("Content-Range", f"bytes {start}-{len(payload) - 1}/{len(payload)}")
        self.send_header("Content-Length", str(len(payload) - offset))
        self.end_headers()
        try:
            if mode == "slow":
                for start in range(offset, len(payload), 16384):
                    self.wfile.write(payload[start:start + 16384])
                    self.wfile.flush()
                    time.sleep(0.01)
            elif mode == "truncated":
                self.wfile.write(payload[offset:offset + 24])
            else:
                self.wfile.write(payload[offset:])
        except (BrokenPipeError, ConnectionResetError):
            pass


class ModelTransactions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.fixture = {"payload": archive_bytes(), "requests": []}
        self.http_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.http_thread.start()
        self.managers = []

    def tearDown(self):
        for manager in self.managers:
            manager.shutdown()
        self.server.shutdown()
        self.server.server_close()
        self.temp.cleanup()

    def entry(self):
        payload = self.server.fixture["payload"]
        return {"id": "test-model", "name": "fixture", "engine": "sensevoice",
                "download_enabled": True, "url": f"http://127.0.0.1:{self.server.server_port}/fixture.zip",
                "size_bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest(),
                "license_url": "https://example.invalid/test-license", "archive_root": "fixture",
                "max_unpacked_bytes": 10 * 1024 * 1024, "legacy_directory": "legacy/fixture",
                "required_files": ["model.onnx", "tokens.txt"],
                "configuration": {"SenseVoiceArgs": {"encoder_path": {"file": "model.onnx"}}}}

    def manager(self, entry=None):
        catalog = ModelCatalog(entries=[entry or self.entry()])
        manager = DownloadManager(self.root / "data", self.root / "models", catalog, allow_test_http=True, timeout=2)
        self.managers.append(manager)
        return manager

    def wait(self, manager, download_id, statuses=("completed", "failed", "paused", "cancelled"), timeout=8):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = manager.get(download_id)
            if value["status"] in statuses:
                return value
            time.sleep(0.01)
        self.fail("Download state timeout: " + str(manager.get(download_id)))

    def seed_resume(self, entry, *, etag='"v1"', status="downloading"):
        download_id = uuid.uuid4().hex
        cache = self.root / "data" / "model-downloads"
        partial = cache / download_id
        partial.mkdir(parents=True)
        (partial / "archive.part").write_bytes(self.server.fixture["payload"][:30])
        job = {"download_id": download_id, "model_id": entry["id"], "status": status,
               "stage": status, "bytes_received": 30, "bytes_total": entry["size_bytes"],
               "error": None, "etag": etag, "url": entry["url"], "sha256": entry["sha256"],
               "created_at": time.time(), "updated_at": time.time(), "installed_path": None}
        (cache / "jobs.json").write_text(json.dumps({"schema_version": 1, "jobs": [job]}))
        return download_id

    def test_real_http_verified_install_and_configuration(self):
        manager = self.manager()
        result = self.wait(manager, manager.start("test-model", license_accepted=True)["download_id"])
        self.assertEqual(result["status"], "completed", result)
        self.assertNotIn("ready", result)
        path = Path(result["installed_path"])
        self.assertEqual((path / "model.onnx").read_bytes(), b"model fixture")
        config = manager.catalog.configuration("test-model", manager.model_root)
        self.assertEqual(config["integrity"], "verified")
        self.assertEqual(config["classes"]["SenseVoiceArgs"]["encoder_path"], str(path / "model.onnx"))
        (path / "model.onnx").write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "校验失败"):
            manager.catalog.configuration("test-model", manager.model_root)

    def test_installs_into_nested_chinese_model_root(self):
        # A Portable installation can live well below the drive root. Leave
        # room for staged filenames even on Windows without long paths enabled.
        parent = self.root / "便携版 测试"
        self.root = parent / ("nested-" + "x" * max(0, 132 - len(str(parent))))
        manager = self.manager()
        job = manager.start("test-model", license_accepted=True)
        result = self.wait(manager, job["download_id"])
        self.assertEqual(result["status"], "completed", result)
        self.assertEqual(manager.catalog.verify_installation("test-model", manager.model_root)["integrity"], "verified")

    def test_checksum_reject_preserves_existing_manual_model(self):
        entry = self.entry()
        self.server.fixture["payload"] = self.server.fixture["payload"][:-1] + b"X"
        manual = self.root / "models" / "legacy" / "fixture"
        manual.mkdir(parents=True)
        (manual / "model.onnx").write_bytes(b"old user model")
        (manual / "tokens.txt").write_bytes(b"old tokens")
        manager = self.manager(entry)
        result = self.wait(manager, manager.start("test-model", license_accepted=True)["download_id"])
        self.assertEqual(result["status"], "failed")
        self.assertIn("SHA-256", result["error"])
        self.assertEqual((manual / "model.onnx").read_bytes(), b"old user model")
        self.assertFalse(manager.catalog.managed_path("test-model", manager.model_root).exists())

    def test_restart_marks_orphan_paused_and_resumes_matching_identity(self):
        entry = self.entry()
        download_id = self.seed_resume(entry)
        manager = self.manager(entry)
        self.assertEqual(manager.get(download_id)["status"], "paused")
        manager.resume(download_id)
        result = self.wait(manager, download_id)
        self.assertEqual(result["status"], "completed", result)
        self.assertEqual(self.server.fixture["requests"], [{"range": "bytes=30-", "if_range": '"v1"'}])

    def test_unsafe_resume_restarts_for_changed_etag_range_ignored_and_unsupported(self):
        for mode in ("changed_etag", "bad_range", "ignore_range", "unsupported_range"):
            with self.subTest(mode=mode):
                subroot = self.root
                self.root = subroot / mode
                self.root.mkdir()
                self.server.fixture["mode"] = mode
                self.server.fixture["requests"].clear()
                entry = self.entry()
                download_id = self.seed_resume(entry)
                manager = self.manager(entry)
                manager.resume(download_id)
                result = self.wait(manager, download_id)
                self.assertEqual(result["status"], "completed", result)
                self.assertEqual(self.server.fixture["requests"][0]["range"], "bytes=30-")
                self.assertIsNone(self.server.fixture["requests"][1]["range"])
                manager.shutdown()
                self.root = subroot

    def test_weak_or_absent_etag_never_appends(self):
        entry = self.entry()
        download_id = self.seed_resume(entry, etag='W/"weak"')
        manager = self.manager(entry)
        manager.resume(download_id)
        result = self.wait(manager, download_id)
        self.assertEqual(result["status"], "completed", result)
        self.assertIsNone(self.server.fixture["requests"][0]["range"])

    def test_changed_catalog_identity_refuses_resume(self):
        entry = self.entry()
        download_id = self.seed_resume(entry)
        entry["sha256"] = "0" * 64
        manager = self.manager(entry)
        with self.assertRaisesRegex(ValueError, "来源已变化"):
            manager.resume(download_id)
        self.assertFalse(self.server.fixture["requests"])

    def test_duplicate_start_returns_same_job_and_pause_is_acknowledged(self):
        self.server.fixture["payload"] = archive_bytes({"fixture/model.onnx": b"x" * (2 * 1024 * 1024), "fixture/tokens.txt": b"t"})
        self.server.fixture["mode"] = "slow"
        manager = self.manager()
        first = manager.start("test-model", license_accepted=True)
        second = manager.start("test-model", license_accepted=True)
        self.assertEqual(first["download_id"], second["download_id"])
        self.wait(manager, first["download_id"], statuses=("downloading",))
        manager.pause(first["download_id"])
        result = self.wait(manager, first["download_id"])
        self.assertEqual(result["status"], "paused")
        self.assertIsNone(result["installed_path"])

    def test_cancel_does_not_install_or_delete_unrelated_files(self):
        manager = self.manager()
        unrelated = self.root / "models" / "unknown.bin"
        unrelated.write_bytes(b"keep")
        with manager._condition:
            job = manager.start("test-model", license_accepted=True)
            result = manager.cancel(job["download_id"])
        self.assertEqual(result["status"], "cancelled")
        self.assertEqual(unrelated.read_bytes(), b"keep")

    def test_foreign_install_target_is_not_overwritten(self):
        manager = self.manager()
        target = manager.catalog.managed_path("test-model", manager.model_root)
        target.mkdir(parents=True)
        (target / "user.txt").write_bytes(b"keep")
        result = self.wait(manager, manager.start("test-model", license_accepted=True)["download_id"])
        self.assertEqual(result["status"], "failed")
        self.assertEqual((target / "user.txt").read_bytes(), b"keep")

    def test_truncated_transfer_never_installs(self):
        self.server.fixture["mode"] = "truncated"
        manager = self.manager()
        result = self.wait(manager, manager.start("test-model", license_accepted=True)["download_id"])
        self.assertEqual(result["status"], "failed")
        self.assertIsNone(result["installed_path"])

    def test_unconfirmed_or_unverified_source_refuses_download(self):
        manager = self.manager()
        with self.assertRaisesRegex(ValueError, "确认"):
            manager.start("test-model")
        manager.catalog._models["test-model"]["download_enabled"] = False
        with self.assertRaises(ValueError):
            manager.start("test-model", license_accepted=True)
        self.assertEqual(manager.snapshot(), [])

    def test_insufficient_space_creates_no_job(self):
        manager = self.manager()
        with patch("core.product.downloads.shutil.disk_usage") as disk:
            disk.return_value.free = 0
            with self.assertRaisesRegex(ValueError, "空间不足"):
                manager.start("test-model", license_accepted=True)
        self.assertEqual(manager.snapshot(), [])

    def test_exclusive_owner_lock(self):
        self.manager()
        with self.assertRaisesRegex(RuntimeError, "已有后台管理器"):
            self.manager()

    def test_corrupt_journal_is_preserved_and_lock_released(self):
        path = self.root / "data" / "model-downloads" / "jobs.json"
        path.parent.mkdir(parents=True)
        raw = b'{"incomplete'
        path.write_bytes(raw)
        with self.assertRaises(ValueError):
            self.manager()
        self.assertEqual(path.read_bytes(), raw)
        path.write_text('{"schema_version":1,"jobs":[]}')
        # After an explicit test-fixture repair the owner lock must be reusable.
        self.assertEqual(self.manager().snapshot(), [])

    def test_managed_file_symlink_is_not_trusted(self):
        if os.name == "nt":
            self.skipTest("Windows symlink creation needs platform permission; archive symlink rejection is cross-platform")
        manager = self.manager()
        result = self.wait(manager, manager.start("test-model", license_accepted=True)["download_id"])
        self.assertEqual(result["status"], "completed")
        file = Path(result["installed_path"]) / "model.onnx"
        outside = self.root / "same-bytes.onnx"
        outside.write_bytes(file.read_bytes())
        file.unlink()
        file.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "校验失败"):
            manager.catalog.verify_installation("test-model", manager.model_root)

    def test_default_catalog_keeps_four_families_and_one_verified_download(self):
        catalog = ModelCatalog()
        entries = catalog.list_models(self.root / "models")
        self.assertEqual({x["engine"] for x in entries}, {"sensevoice", "qwen_asr", "fun_asr_nano", "paraformer"})
        self.assertEqual([x["id"] for x in entries if x["can_download"]], ["sensevoice-small-fp16"])


class ArchiveSafety(unittest.TestCase):
    def test_rejects_windows_and_posix_path_ambiguity(self):
        for value in ("../escape", "/absolute", "C:/escape", "a\\b", "x/../y", "CON", "NUL.txt", "a.", "x:stream", "a//b"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                safe_relative(value)

    def test_zip_traversal_and_symlinks_never_escape(self):
        for symlink in (False, True):
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                zip_path = root / "model.zip"
                with zipfile.ZipFile(zip_path, "w") as archive:
                    if symlink:
                        info = zipfile.ZipInfo("link")
                        info.create_system = 3
                        info.external_attr = (stat.S_IFLNK | 0o777) << 16
                        archive.writestr(info, "../outside")
                    else:
                        archive.writestr("../outside", b"bad")
                with self.assertRaises(ValueError):
                    safe_extract_zip(zip_path, root / "staging", max_bytes=4096)
                self.assertFalse((root / "outside").exists())

    def test_zip_duplicate_case_and_expansion_limits(self):
        for names, limit in (({"model": b"1", "MODEL": b"2"}, 100), ({"model": b"x" * 100}, 50)):
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                archive = root / "model.zip"
                archive.write_bytes(archive_bytes(names))
                with self.assertRaises(ValueError):
                    safe_extract_zip(archive, root / "staging", max_bytes=limit)


class ManualModels(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.model_root = self.root / "models"
        self.registry = self.root / "data" / "manual-models.json"
        self.entry = {"id": "manual-fixture", "engine": "sensevoice", "name": "fixture",
                      "download_enabled": False, "legacy_directory": "legacy",
                      "required_files": ["encoder.onnx", "tokens.txt"],
                      "configuration": {"SenseVoiceArgs": {"encoder_path": {"file": "encoder.onnx"}}}}

    def tearDown(self):
        self.temp.cleanup()

    def catalog(self):
        return ModelCatalog(entries=[self.entry], manual_registry_path=self.registry)

    def make_model(self, name):
        target = self.root / name
        target.mkdir(parents=True)
        (target / "encoder.onnx").write_bytes(b"user-provided-encoder")
        (target / "tokens.txt").write_bytes(b"user-provided-tokenizer")
        return target

    def test_register_external_directory_read_only_and_reload(self):
        directory = self.make_model("outside-model-root")
        before = {p.name: p.read_bytes() for p in directory.iterdir()}
        row = self.catalog().register_manual("manual-fixture", directory, model_root=self.model_root)
        self.assertEqual(row["integrity"], "manual_unverified")
        catalog = self.catalog()
        self.assertEqual(catalog.installed_path("manual-fixture", self.model_root), directory)
        config = catalog.configuration("manual-fixture", self.model_root)
        self.assertEqual(config["classes"]["SenseVoiceArgs"]["encoder_path"], str(directory / "encoder.onnx"))
        self.assertEqual({p.name: p.read_bytes() for p in directory.iterdir()}, before)
        self.assertFalse(self.model_root.exists())

    def test_missing_files_rejected_without_persisting(self):
        directory = self.root / "incomplete"
        directory.mkdir()
        (directory / "encoder.onnx").write_bytes(b"partial")
        with self.assertRaisesRegex(ValueError, "必要文件"):
            self.catalog().register_manual("manual-fixture", directory, model_root=self.model_root)
        self.assertFalse(self.registry.exists())

    def test_replacement_requires_confirmation_and_missing_choice_does_not_fallback(self):
        first = self.make_model("first")
        second = self.make_model("second")
        catalog = self.catalog()
        catalog.register_manual("manual-fixture", first, model_root=self.model_root)
        with self.assertRaisesRegex(ValueError, "已有手动目录"):
            catalog.register_manual("manual-fixture", second, model_root=self.model_root)
        first.rename(self.root / "unplugged")
        self.make_model("models/legacy")
        self.assertIsNone(catalog.installed_path("manual-fixture", self.model_root))
        with self.assertRaises(ValueError):
            catalog.configuration("manual-fixture", self.model_root)

    def test_existing_legacy_model_is_not_displaced(self):
        self.make_model("models/legacy")
        other = self.make_model("other")
        with self.assertRaisesRegex(ValueError, "已经安装"):
            self.catalog().register_manual("manual-fixture", other, model_root=self.model_root)
        self.assertFalse(self.registry.exists())

    def test_inspects_custom_per_file_paths_without_normalizing_them(self):
        directory = self.make_model("actual")
        namespace = {"ServerConfig": SimpleNamespace(model_type="sensevoice"),
                     "SenseVoiceArgs": SimpleNamespace(encoder_path="actual/encoder.onnx", decoder_path="actual/encoder.onnx", tokenizer_path="actual/tokens.txt")}
        result = inspect_configured_model(namespace, self.root)
        self.assertTrue(result["files_present"])
        self.assertEqual(result["integrity"], "manual_unverified")
        self.assertEqual(namespace["SenseVoiceArgs"].encoder_path, "actual/encoder.onnx")

    def test_first_setup_manual_import_persists_without_claiming_worker_ready(self):
        from core.product.onboarding import SetupStore
        data_root = self.root / "data"
        setup = SetupStore(data_root)
        directory = self.make_model("offline-model")
        source_before = {file.name: file.read_bytes() for file in directory.iterdir()}
        self.catalog().register_manual("manual-fixture", directory, model_root=self.model_root)
        setup.select_first("manual-fixture")
        # Fresh instances exercise persistence, not an in-memory-only UI mock.
        recovered = SetupStore(data_root)
        config = self.catalog().configuration("manual-fixture", self.model_root)
        self.assertTrue(recovered.may_auto_activate("manual-fixture"))
        self.assertIsNone(recovered.snapshot()["default_model"])
        self.assertEqual(config["integrity"], "manual_unverified")
        self.assertNotIn("ready", config)
        # Only the owner, after actual Worker readiness, may commit activation.
        recovered.activated("manual-fixture")
        self.assertFalse(SetupStore(data_root).may_auto_activate("different-model"))
        self.assertEqual({file.name: file.read_bytes() for file in directory.iterdir()}, source_before)


if __name__ == "__main__":
    unittest.main()
