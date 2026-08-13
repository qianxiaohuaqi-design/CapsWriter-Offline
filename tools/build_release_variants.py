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
        model_dir = out_dir / "_internal" / "models"
        model_dir.mkdir(parents=True, exist_ok=True)
        (model_dir / "README_模型下载.txt").write_text(
            "这是 CapsWriter 精简版，未内置 ASR 模型。\n"
            "\n"
            "推荐模型：SenseVoice-Small，体积较小，日常听写延迟低。\n"
            "说明：这里使用的是原项目整理的适配模型下载包，SenseVoice 本身是独立开源语音识别模型。\n"
            "下载地址：https://github.com/HaujetZhao/CapsWriter-Offline/releases/tag/models\n"
            "下载后解压到此 models 目录，再回到 CapsWriter 的“语音识别与硬件”页面查看模型状态。\n"
            "\n"
            "也可以在软件页面里点击“模型配置指引”查看同样的信息。\n",
            encoding="utf-8",
        )

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
