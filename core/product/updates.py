"""User-initiated update metadata, never an installer or background poller.

Only the project's GitHub release API is contacted. API keys and local config are
never sent. Returned release notes are plain text: callers must not render HTML.
"""
from __future__ import annotations

import json
import re
from functools import total_ordering
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlsplit
from urllib.request import Request, urlopen

REPOSITORY = "qianxiaohuaqi-design/CapsWriter-Offline"
RELEASES_URL = f"https://github.com/{REPOSITORY}/releases"
API_URL = f"https://api.github.com/repos/{REPOSITORY}/releases"
MAX_RESPONSE = 2 * 1024 * 1024
VERSION_PATTERN = re.compile(r"^v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$")
HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")


@total_ordering
class Version:
    """Small SemVer comparator; does not order 1.10 lexicographically before 1.9."""
    def __init__(self, value: str):
        match = VERSION_PATTERN.fullmatch(str(value))
        if not match:
            raise ValueError("版本不是受支持的语义版本号")
        self.value = str(value).removeprefix("v")
        self.core = tuple(map(int, match.group(1, 2, 3)))
        self.pre = tuple(match.group(4).split(".")) if match.group(4) else ()
        if any(not item or (item.isdigit() and len(item) > 1 and item[0] == "0") for item in self.pre):
            raise ValueError("无效的预览版本号")

    def __eq__(self, other):
        if not isinstance(other, Version):
            return NotImplemented
        return (self.core, self.pre) == (other.core, other.pre)

    def __lt__(self, other):
        if not isinstance(other, Version):
            return NotImplemented
        if self.core != other.core:
            return self.core < other.core
        if not self.pre:
            return False
        if not other.pre:
            return True
        for a, b in zip(self.pre, other.pre):
            if a == b:
                continue
            if a.isdigit() and b.isdigit():
                return int(a) < int(b)
            if a.isdigit() != b.isdigit():
                return a.isdigit()
            return a < b
        return len(self.pre) < len(other.pre)


def build_metadata() -> dict[str, Any]:
    try:
        from .paths import resource_root
        root = resource_root()
    except (ImportError, AttributeError):
        root = Path(__file__).resolve().parents[2]
    try:
        value = json.loads((root / "packaging" / "version.json").read_text(encoding="utf-8"))
        Version(value["version"])
        return value
    except (OSError, ValueError, KeyError, TypeError):
        return {"version": "1.0.0-rc.1", "channel": "preview", "status": "source_candidate", "architecture": "x64"}


def current_version() -> str:
    return str(build_metadata()["version"])


def _official_release_url(value: str, *, asset: bool = False) -> bool:
    if not isinstance(value, str):
        return False
    try:
        p = urlsplit(value)
    except ValueError:
        return False
    prefix = f"/{REPOSITORY}/releases/" + ("download/" if asset else "tag/")
    path = unquote(p.path)
    return (p.scheme == "https" and p.netloc == "github.com" and
            not p.query and not p.fragment and path.startswith(prefix) and
            ".." not in path.split("/") and "\\" not in path)


def release_assets(release: dict) -> list[dict[str, Any]]:
    """Return only matching x64 Windows installers/portable ZIPs with API hashes."""
    tag = str(release.get("tag_name", ""))
    version = Version(tag).value.split("+", 1)[0]
    result = []
    raw_assets = release.get("assets", [])
    if not isinstance(raw_assets, list):
        return result
    for item in raw_assets:
        if not isinstance(item, dict) or item.get("state") != "uploaded":
            continue
        name = item.get("name", "")
        kind = next((kind for kind, suffix in (("installer", "Setup.exe"), ("portable", "Portable.zip"))
                     if name == f"CapsWriter-Offline-{version}-Windows-x64-{suffix}"), None)
        url = item.get("browser_download_url", "")
        digest = str(item.get("digest", ""))
        size = item.get("size")
        if (not kind or not _official_release_url(url, asset=True) or
                unquote(urlsplit(url).path).split("/")[-2:] != [tag, name] or
                not digest.startswith("sha256:") or not HASH_PATTERN.fullmatch(digest[7:]) or
                isinstance(size, bool) or not isinstance(size, int) or size <= 0):
            continue
        result.append({"kind": kind, "name": name, "url": url, "size": size,
                       "sha256": digest[7:], "architecture": "x64", "checksum_source": "GitHub release API"})
    return result


def check_for_update(current_version: str | None = None, channel: str | None = None,
                     timeout: float = 10, opener: Callable | None = None) -> dict[str, Any]:
    """Perform exactly one explicit check. UI should run this off its event loop.

    `opener` is dependency injection for tests. No credentials or telemetry are
    added; failures never include response bodies, redirects or private paths.
    """
    meta = build_metadata()
    current_version = current_version or meta["version"]
    channel = channel or meta.get("channel", "preview")
    base = {"status": "unavailable", "current_version": current_version, "channel": channel,
            "latest_version": None, "release_url": RELEASES_URL, "notes": "", "assets": [], "message": ""}
    try:
        current = Version(current_version)
        if channel not in ("stable", "preview"):
            raise ValueError("不支持的更新渠道")
        req = Request(API_URL + ("/latest" if channel == "stable" else "?per_page=30"), headers={
            "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "CapsWriter-Offline-Manual-Update-Check"})
        with (opener or urlopen)(req, timeout=max(1, min(float(timeout), 30))) as response:
            # GitHub API must not silently redirect the metadata fetch elsewhere.
            final_url = response.geturl() if hasattr(response, "geturl") else req.full_url
            if urlsplit(final_url).netloc != "api.github.com" or urlsplit(final_url).scheme != "https":
                raise ValueError("更新元数据来源不匹配")
            raw = response.read(MAX_RESPONSE + 1)
            if len(raw) > MAX_RESPONSE:
                raise ValueError("更新元数据过大")
            payload = json.loads(raw.decode("utf-8"))
        records = payload if isinstance(payload, list) else [payload]
        candidates = []
        for item in records:
            if not isinstance(item, dict) or item.get("draft") or not item.get("published_at"):
                continue
            try:
                version = Version(item.get("tag_name", ""))
            except ValueError:
                continue
            if channel == "stable" and (item.get("prerelease") or version.pre):
                continue
            if not _official_release_url(item.get("html_url", "")):
                continue
            if unquote(urlsplit(item["html_url"]).path).split("/")[-1] != item["tag_name"]:
                continue
            candidates.append((version, item))
        if not candidates:
            base["message"] = "尚无可核验的对应渠道版本。可查看项目发行页。"
            return base
        version, latest = max(candidates, key=lambda pair: pair[0])
        base.update(latest_version=version.value, release_url=latest["html_url"],
                    notes=str(latest.get("body") or "")[:16000], assets=release_assets(latest))
        base["status"] = "available" if version > current else "up_to_date"
        base["message"] = (f"发现 {version.value}。请查看发行说明、校验值后手动升级；当前任务不会被中断。"
                           if version > current else "当前版本已不低于该渠道的最新版本。")
        if version > current and not base["assets"]:
            base["message"] += " 暂无带完整校验信息的 Windows x64 安装资产。"
    except HTTPError as error:
        base["message"] = ("尚未发布此渠道的版本。" if error.code == 404 else
                           "GitHub 暂时无法提供更新信息，请稍后重试。")
    except (OSError, URLError, ValueError, TypeError, KeyError, UnicodeError):
        base["message"] = "无法安全检查更新，请检查网络后重试。当前程序和设置没有变化。"
    return base


def validate_release_manifest(value: dict) -> list[str]:
    """Validate site metadata. Public links require Windows and per-file evidence."""
    errors = []
    if not isinstance(value, dict) or value.get("schema") != "capswriter.release.v1":
        return ["invalid_schema"]
    try:
        version = Version(value.get("version", "")).value
    except ValueError:
        errors.append("invalid_version")
        version = ""
    if value.get("channel") not in ("stable", "preview"):
        errors.append("invalid_channel")
    if value.get("status") not in ("candidate", "published"):
        errors.append("invalid_status")
    artifacts = value.get("artifacts", [])
    if not isinstance(artifacts, list):
        return errors + ["invalid_artifacts"]
    seen = set()
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            errors.append("invalid_artifact")
            continue
        kind = artifact.get("kind")
        if not isinstance(kind, str):
            errors.append("invalid_kind")
            continue
        suffix = {"installer": "Setup.exe", "portable": "Portable.zip"}.get(kind)
        if not suffix or kind in seen:
            errors.append("invalid_or_duplicate_kind")
        seen.add(kind)
        name = f"CapsWriter-Offline-{version}-Windows-x64-{suffix}"
        if artifact.get("filename") != name or artifact.get("architecture") != "x64":
            errors.append("invalid_filename_or_architecture")
        if not HASH_PATTERN.fullmatch(str(artifact.get("sha256", ""))):
            errors.append("invalid_sha256")
        size = artifact.get("size")
        if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
            errors.append("invalid_size")
        if artifact.get("signature") not in ("unsigned", "signed_verified"):
            errors.append("invalid_signature")
        if artifact.get("public_verified"):
            url = artifact.get("url", "")
            if not (value.get("status") == "published" and value.get("windows_validated") is True and
                    re.fullmatch(r"[0-9a-f]{40}", str(value.get("code_sha", ""))) and
                    _official_release_url(url, asset=True) and
                    unquote(urlsplit(url).path).split("/")[-2:] == ["v" + version, name] and
                    artifact.get("verified_at")):
                errors.append("unverified_public_link")
        elif artifact.get("url"):
            errors.append("unverified_url_must_be_empty")
    return errors
