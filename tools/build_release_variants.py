from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
SPEC = ROOT / "CapsWriter.spec"


def dir_size_mb(path: Path) -> float:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file()) / 1024 / 1024


def run_build(name: str, include_models: bool) -> None:
    out_dir = DIST / name
    if out_dir.exists():
        shutil.rmtree(out_dir)

    env = os.environ.copy()
    env["CAPSWRITER_DIST_NAME"] = name
    env["CAPSWRITER_INCLUDE_MODELS"] = "1" if include_models else "0"

    subprocess.run(
        [sys.executable, "-m", "PyInstaller", str(SPEC), "--noconfirm", "--clean"],
        cwd=ROOT,
        env=env,
        check=True,
    )

    if not include_models:
        (out_dir / "_internal" / "models").mkdir(parents=True, exist_ok=True)

    zip_base = DIST / name
    zip_path = DIST / f"{name}.zip"
    if zip_path.exists():
        zip_path.unlink()
    shutil.make_archive(str(zip_base), "zip", root_dir=DIST, base_dir=name)

    print(f"{name}: {dir_size_mb(out_dir):.1f} MB, zip: {zip_path.stat().st_size / 1024 / 1024:.1f} MB")


def main() -> None:
    run_build("CapsWriter-Full", include_models=True)
    run_build("CapsWriter-Lite", include_models=False)


if __name__ == "__main__":
    main()
