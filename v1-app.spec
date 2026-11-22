# -*- mode: python ; coding: utf-8 -*-
"""Unified V1 application. Run through tools/build_v1.py, which stages clean defaults."""
import json
from pathlib import Path
import sys
from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_dynamic_libs

ROOT = Path(SPEC).resolve().parent
sys.path.insert(0, str(ROOT))
binaries, datas, hiddenimports = [], [], []

# Dynamic/native backends; any required package missing fails the build explicitly.
for package in ("nicegui", "webview", "onnxruntime", "sherpa_onnx", "sounddevice", "sentencepiece", "soundfile", "gguf", "pypinyin", "pystray", "nagisa", "soynlp"):
    package_data, package_binaries, package_imports = collect_all(package)
    # Dependency test models are not runtime assets and must not enter a model-free package.
    datas += [(src, dest) for src, dest in package_data if Path(src).suffix.lower() not in (".onnx", ".gguf") and "tests" not in Path(src).parts]
    binaries += package_binaries
    hiddenimports += package_imports

# Enumerate module names without importing the application's audio/hotkey modules.
for folder in ("core", "web_gui"):
    for source in (ROOT / folder).rglob("*.py"):
        if "export" in source.parts:
            continue
        relative = source.relative_to(ROOT).with_suffix("")
        parts = list(relative.parts)
        if parts[-1] == "__init__":
            parts.pop()
        hiddenimports.append(".".join(parts))
hiddenimports += ["config_client", "config_server", "config_pill", "webview.platforms.winforms", "clr", "clr_loader", "pystray._win32", "pynput.keyboard._win32", "pynput.mouse._win32", "win32api", "win32gui", "win32con", "win32process"]

for folder in ("assets", "LLM", "docs", "THIRD_PARTY", "packaging/release_defaults"):
    if (ROOT / folder).is_dir():
        datas.append((str(ROOT / folder), folder))
for name in ("config_client.py", "config_server.py", "config_pill.py", "hot.txt", "hot-server.txt", "hot-rule.txt", "LICENSE", "readme.md", "packaging/version.json", "packaging/RELEASE_NOTES.md", "packaging/PRIVACY.md", "web_gui/app.py"):
    datas.append((str(ROOT / name), str(Path(name).parent)))
for source in (ROOT / "core").rglob("*"):
    if source.is_file() and source.suffix in (".json", ".dict"):
        datas.append((str(source), str(source.relative_to(ROOT).parent)))

vendor_path = ROOT / "packaging/vendor_inventory.json"
if vendor_path.is_file():
    datas.append((str(vendor_path), "packaging"))
    for entry in json.loads(vendor_path.read_text(encoding="utf-8"))["files"]:
        relative = Path(entry["target"])
        binaries.append((str(ROOT / relative), str(relative.parent)))

analysis = Analysis([str(ROOT / "run_app.py")], pathex=[str(ROOT)], binaries=binaries, datas=datas,
                    hiddenimports=sorted(set(hiddenimports)), hookspath=[], hooksconfig={},
                    runtime_hooks=[str(ROOT / "packaging/runtime_hook.py")],
                    excludes=["IPython", "notebook", "matplotlib", "PyQt5", "PyQt6", "PySide2", "PySide6", "torch", "transformers"],
                    noarchive=False)
pyz = PYZ(analysis.pure)
exe = EXE(pyz, analysis.scripts, [], exclude_binaries=True, name="CapsWriter", debug=False,
          bootloader_ignore_signals=False, strip=False, upx=False, console=False,
          disable_windowed_traceback=False, contents_directory="_internal", icon=str(ROOT / "assets/source/capswriter.ico"))
coll = COLLECT(exe, analysis.binaries, analysis.datas, strip=False, upx=False, name="CapsWriter")
