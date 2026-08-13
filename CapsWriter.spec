# -*- mode: python ; coding: utf-8 -*-

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules


root = Path.cwd()
dist_name = os.environ.get("CAPSWRITER_DIST_NAME", "CapsWriter")
include_models = os.environ.get("CAPSWRITER_INCLUDE_MODELS", "1") != "0"


def file_data(path: str, dest: str = "."):
    p = root / path
    return [(str(p), dest)] if p.exists() else []


def folder_data(path: str, dest: str):
    p = root / path
    return [(str(p), dest)] if p.exists() else []


datas = []
for name in [
    "config_client.py",
    "config_server.py",
    "config_pill.py",
    "hot.txt",
    "hot-rule.txt",
    "hot-server.txt",
    "LICENSE",
    "readme.md",
]:
    datas += file_data(name)

datas += folder_data("assets", "assets")
datas += folder_data("docs", "docs")
if include_models:
    datas += folder_data("models", "models")
datas += folder_data("tools/ffmpeg/bin", "tools/ffmpeg/bin")

web_gui_dir = root / "web_gui"
if web_gui_dir.exists():
    for path in web_gui_dir.glob("*.py"):
        datas.append((str(path), "web_gui"))

release_web_gui_defaults = root / "packaging" / "release_defaults" / "web_gui"
if release_web_gui_defaults.exists():
    for path in release_web_gui_defaults.glob("*.json"):
        datas.append((str(path), "web_gui"))

llm_dir = root / "LLM"
if llm_dir.exists():
    for path in llm_dir.glob("*.py"):
        datas.append((str(path), "LLM"))

hiddenimports = []
binaries = []

for package in [
    "nicegui",
    "fastapi",
    "starlette",
    "uvicorn",
    "socketio",
    "engineio",
    "webview",
]:
    package_datas, package_binaries, package_hiddenimports = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hiddenimports

for package in ["web_gui", "core", "LLM"]:
    hiddenimports += collect_submodules(package)

a = Analysis(
    ["run_app.py"],
    pathex=[str(root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="CapsWriter",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=["assets\\source\\capswriter.ico"],
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name=dist_name,
)
