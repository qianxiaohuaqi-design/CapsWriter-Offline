"""Safety tests for clean staging; never writes to the real application data root."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("capswriter_build_v1", ROOT / "tools/build_v1.py")
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


class StagingTests(unittest.TestCase):
    def fixture(self, root):
        paths = {"run_app.py": "print('safe')\n", "v1-app.spec": "# example\n",
                 "config_client.py": "from pathlib import Path\nclass ClientConfig:\n    llm_enabled = True\n    context = 'PRIVATE USER TEXT'\n# new wiring stays\nresult = Path('.')\n",
                 "config_server.py": "class ServerConfig:\n    addr = '0.0.0.0'\n",
                 "config_pill.py": "a = 1\n",
                 "packaging/release_defaults/config_client.py": "class ClientConfig:\n    llm_enabled = False\n    context = ''\n",
                 "packaging/release_defaults/config_server.py": "class ServerConfig:\n    addr = '127.0.0.1'\n",
                 "packaging/release_defaults/config_pill.py": "a = 1\n",
                 "packaging/release_defaults/LLM/default.py": "enabled = False\napi_key = ''\n",
                 "packaging/release_defaults/hot.txt": "# clean\n",
                 "core/product/models.py": "value = 1\n",
                 "web_gui/private_config.json": '{"api_key":"private"}',
                 "web_gui/input_history.jsonl": "private history",
                 "LLM/custom.py": "api_key = 'user-key'\n",
                 "hot.txt": "PRIVATE HOTWORDS\n",
                 "models/model.onnx": "weights",
                 "recordings/a.wav": "recording"}
        for name, content in paths.items():
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        return list(paths)

    def test_stage_excludes_private_and_resets_literal_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            output = Path(tmp) / "stage"
            names = self.fixture(root)
            inventory = build.stage_sources(root, output, names)
            self.assertGreater(len(inventory["files"]), 4)
            self.assertFalse((output / "web_gui/private_config.json").exists())
            self.assertFalse((output / "web_gui/input_history.jsonl").exists())
            self.assertFalse((output / "LLM/custom.py").exists())
            self.assertFalse((output / "models/model.onnx").exists())
            self.assertEqual((output / "hot.txt").read_text(), "# clean\n")
            client = (output / "config_client.py").read_text()
            self.assertIn("llm_enabled = False", client)
            self.assertNotIn("PRIVATE USER TEXT", client)
            self.assertIn("Path('.')", client)
            self.assertIn("PRIVATE USER TEXT", (root / "config_client.py").read_text())

    def test_existing_output_never_deleted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            names = self.fixture(root)
            out = Path(tmp) / "existing"
            out.mkdir()
            (out / "important.txt").write_text("keep")
            with self.assertRaises(FileExistsError):
                build.stage_sources(root, out, names)
            self.assertEqual((out / "important.txt").read_text(), "keep")

    def test_symlink_source_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            names = self.fixture(root)
            (root / "core/product/models.py").unlink()
            try:
                (root / "core/product/models.py").symlink_to(root / "hot.txt")
            except (OSError, NotImplementedError):
                self.skipTest("symlink creation unavailable")
            with self.assertRaises(ValueError):
                build.stage_sources(root, Path(tmp) / "stage", names)

    def test_traversal_and_runtime_data_excluded(self):
        for bad in ("../secret.py", "core/../../secret.py", "C:/key.py", "core\\secret.py"):
            with self.assertRaises(ValueError):
                build.allowed_source(bad)
        for bad in ("models/weights.onnx", "web_gui/private_config.json", "web_gui/uploads/user.wav", "core/server/engines/qwen_asr_gguf/export/convert.py"):
            self.assertFalse(build.allowed_source(bad))

    def test_bundle_audit_rejects_private_or_model_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "CapsWriter.exe").write_bytes(b"fake test executable")
            (root / "user.onnx").write_bytes(b"weights")
            with self.assertRaises(ValueError):
                build.audit_bundle(root)
            (root / "user.onnx").unlink()
            (root / "input_history.jsonl").write_text("personal")
            with self.assertRaises(ValueError):
                build.audit_bundle(root)

    def test_only_known_nagisa_runtime_data_can_ship_under_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nagisa = root / '_internal/nagisa/data'
            nagisa.mkdir(parents=True)
            for extension in ('dict', 'hp', 'model'):
                (nagisa / f'nagisa_v001.{extension}').write_bytes(b'nagisa test fixture')
            self.assertEqual(len(build.audit_bundle(root)['files']), 3)
            unknown = nagisa / 'unrelated.model'
            unknown.write_bytes(b'unknown')
            with self.assertRaises(ValueError):
                build.audit_bundle(root)

    def test_literal_key_causes_hard_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "role.py").write_text("api_key = 'not-a-real-key-test'\n")
            with self.assertRaises(ValueError):
                build.assert_no_literal_secrets(Path(tmp))

    def test_portable_marker_does_not_modify_installer_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp) / "bundle"
            bundle.mkdir()
            (bundle / "CapsWriter.exe").write_bytes(b"test fixture")
            archive = Path(tmp) / "portable.zip"
            build.zip_portable(bundle, archive)
            self.assertFalse((bundle / "portable.flag").exists())
            with zipfile.ZipFile(archive) as handle:
                self.assertIn("CapsWriter/portable.flag", handle.namelist())
                self.assertEqual(handle.read("CapsWriter/CapsWriter.exe"), b"test fixture")

    def test_vendor_missing_review_and_checksum_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "stage/packaging").mkdir(parents=True)
            binary = root / "ffmpeg.exe"
            binary.write_bytes(b"test fixture")
            entry = {"component": "ffmpeg", "source_file": "ffmpeg.exe", "target": "tools/ffmpeg/bin/ffmpeg.exe", "sha256": build.sha256(binary), "reviewed": False}
            manifest = root / "vendors.json"
            manifest.write_text(json.dumps({"schema": "capswriter.reviewed-vendors.v1", "files": [entry]}))
            with self.assertRaises(ValueError):
                build.stage_vendors(manifest, root / "stage", full=False)
            entry.update(reviewed=True, sha256="0" * 64)
            manifest.write_text(json.dumps({"schema": "capswriter.reviewed-vendors.v1", "files": [entry]}))
            with self.assertRaises(ValueError):
                build.stage_vendors(manifest, root / "stage", full=False)
            self.assertFalse((root / "stage/tools/ffmpeg/bin/ffmpeg.exe").exists())

    def test_inno_preserves_data_and_does_not_start_or_kill_automatically(self):
        text = (ROOT / "packaging/capswriter.iss").read_text()
        self.assertIn("PrivilegesRequired=lowest", text)
        self.assertNotIn("[UninstallDelete]", text)
        self.assertNotIn("[Registry]", text)
        self.assertIn("CloseApplications=no", text)
        self.assertIn("postinstall skipifsilent unchecked", text)
        self.assertIn("CapsWriter.Offline.ControlCenter", text)


if __name__ == "__main__":
    unittest.main()
