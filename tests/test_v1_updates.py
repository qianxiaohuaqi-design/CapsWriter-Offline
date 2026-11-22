import copy
import importlib.util
import io
import json
from pathlib import Path
import unittest
from urllib.error import URLError

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("capswriter_updates", ROOT / "core/product/updates.py")
updates = importlib.util.module_from_spec(spec)
spec.loader.exec_module(updates)


def release(version="1.1.0", prerelease=False):
    filename = f"CapsWriter-Offline-{version}-Windows-x64-Setup.exe"
    return {"tag_name": "v" + version, "published_at": "2026-11-22T00:00:00Z", "draft": False,
            "prerelease": prerelease, "html_url": updates.RELEASES_URL + "/tag/v" + version,
            "body": "Fixture release notes", "assets": [{"name": filename, "state": "uploaded", "size": 123,
             "digest": "sha256:" + "1" * 64, "browser_download_url": updates.RELEASES_URL + "/download/v" + version + "/" + filename}]}


class Response(io.BytesIO):
    def geturl(self):
        return updates.API_URL


class UpdatesTests(unittest.TestCase):
    def opener(self, payload):
        def open_(request, timeout):
            self.assertTrue(request.full_url.startswith(updates.API_URL))
            self.assertFalse(request.has_header("Authorization"))
            self.assertLessEqual(timeout, 30)
            return Response(json.dumps(payload).encode())
        return open_

    def test_versions_numeric_and_prerelease(self):
        ordered = ["1.0.0-alpha", "1.0.0-alpha.2", "1.0.0-alpha.10", "1.0.0-beta", "1.0.0-rc.1", "1.0.0", "1.9.0", "1.10.0"]
        parsed = [updates.Version(value) for value in ordered]
        self.assertEqual(sorted(reversed(parsed)), parsed)
        for value in ("1.0", "1.0.0-", "1.0.0-01", "../1.0.0", "v1.0.0-a..b"):
            with self.assertRaises(ValueError):
                updates.Version(value)

    def test_available_with_verified_metadata(self):
        result = updates.check_for_update("1.0.0", "stable", opener=self.opener(release()))
        self.assertEqual(result["status"], "available")
        self.assertEqual(len(result["assets"]), 1)
        self.assertEqual(result["assets"][0]["sha256"], "1" * 64)

    def test_older_remote_does_not_offer_downgrade(self):
        result = updates.check_for_update("1.10.0", "stable", opener=self.opener(release("1.9.0")))
        self.assertEqual(result["status"], "up_to_date")

    def test_stable_excludes_preview_and_draft(self):
        values = [release("2.0.0-rc.1", True), release("1.1.0"), {**release("3.0.0"), "draft": True}]
        result = updates.check_for_update("1.0.0", "stable", opener=self.opener(values))
        self.assertEqual(result["latest_version"], "1.1.0")

    def test_bad_host_hash_or_asset_version_not_linked(self):
        base = release()
        for field, value in (("browser_download_url", "https://github.com.evil.invalid/file.exe"), ("digest", "sha256:bad"), ("name", "CapsWriter-Offline-1.0.0-Windows-x64-Setup.exe")):
            item = copy.deepcopy(base)
            item["assets"][0][field] = value
            self.assertEqual(updates.release_assets(item), [])

    def test_offline_keeps_state_and_does_not_expose_exception(self):
        def failure(*args, **kwargs):
            raise URLError("private secret path C:/users/alice")
        result = updates.check_for_update("1.0.0", "stable", opener=failure)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["assets"], [])
        self.assertNotIn("alice", str(result))

    def test_large_response_rejected(self):
        result = updates.check_for_update("1.0.0", "stable", opener=lambda *a, **kw: Response(b" " * (updates.MAX_RESPONSE + 1)))
        self.assertEqual(result["status"], "unavailable")

    def test_public_manifest_needs_real_gates(self):
        manifest = json.loads((ROOT / "website/release-manifest.json").read_text())
        self.assertEqual(updates.validate_release_manifest(manifest), [])
        item = release("1.0.0-rc.1")["assets"][0]
        artifact = {"kind": "installer", "filename": item["name"], "architecture": "x64", "sha256": "1" * 64,
                    "size": 123, "signature": "unsigned", "url": item["browser_download_url"], "public_verified": True, "verified_at": "2026-11-22T00:00:00Z"}
        manifest["artifacts"] = [artifact]
        self.assertIn("unverified_public_link", updates.validate_release_manifest(manifest))
        manifest.update(status="published", windows_validated=True, code_sha="a" * 40)
        self.assertEqual(updates.validate_release_manifest(manifest), [])
        artifact["sha256"] = "no digest"
        self.assertIn("invalid_sha256", updates.validate_release_manifest(manifest))


if __name__ == "__main__":
    unittest.main()
