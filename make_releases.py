# coding: utf-8
"""
CapsWriter-Offline 标准打包工具
自动在 dist/ 目录生成：
1. CapsWriter-Offline-Full-YYYYMMDD.zip (完整版：全量代码 + 语音识别模型)
2. CapsWriter-Offline-Lite-YYYYMMDD.zip (精简版：全量代码 + 免自带模型)
"""

import os
import sys
import zipfile
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DIST_DIR = BASE_DIR / 'dist'

MODEL_EXTENSIONS = {'.onnx', '.bin', '.pth', '.safetensors', '.pt', '.tflite', '.engine', '.gguf'}


def should_exclude_path(path: Path) -> bool:
    parts = path.parts
    # 忽略 VCS / 编译 / 临时文件
    if any(p in ('.git', '__pycache__', '.pytest_cache', '.vscode', '.idea', 'build', 'dist', 'release', '.codex', '.playwright-mcp', '.agents', 'config_exports', 'config_backups') for p in parts):
        return True
    if path.suffix in ('.pyc', '.pyo', '.tmp', '.log', '.bak'):
        return True
    return False


def build_zip_package(output_zip: Path, is_lite: bool = False):
    print(f"\n正在构建 {'精简版 (Lite)' if is_lite else '完整版 (Full)'} 压缩包 -> {output_zip.name}")
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    if output_zip.exists():
        output_zip.unlink()

    file_count = 0
    total_bytes = 0

    with zipfile.ZipFile(output_zip, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for root, dirs, files in os.walk(BASE_DIR):
            # 过滤忽略目录
            dirs[:] = [d for d in dirs if not should_exclude_path(Path(root) / d)]
            
            for file in files:
                file_path = Path(root) / file
                if should_exclude_path(file_path):
                    continue

                rel_path = file_path.relative_to(BASE_DIR)
                rel_str = str(rel_path).replace('\\', '/')

                # 精简版排除大模型权重文件
                if is_lite and 'models' in rel_path.parts:
                    if file_path.suffix.lower() in MODEL_EXTENSIONS:
                        continue

                # 排除根目录下临时生成的 zip 文件
                if file_path.suffix.lower() == '.zip' and rel_path.parent == Path('.'):
                    continue

                # 将文件打包在子目录下，解压后目录名更美观
                root_prefix = 'CapsWriter-Offline-Lite' if is_lite else 'CapsWriter-Offline-Full'
                arcname = f"{root_prefix}/{rel_str}"

                zf.write(file_path, arcname=arcname)
                file_count += 1
                total_bytes += file_path.stat().st_size

    size_mb = output_zip.stat().st_size / (1024 * 1024)
    print(f"✅ 完成！包含 {file_count} 个文件，压缩包大小: {size_mb:.2f} MB")
    return output_zip


def main():
    print("=" * 60)
    print("CapsWriter-Offline 离线语音输入与控制中心 - 自动打包生成器")
    print("=" * 60)

    timestamp = datetime.now().strftime("%Y%m%d")
    
    full_zip = DIST_DIR / f"CapsWriter-Offline-Full-{timestamp}.zip"
    lite_zip = DIST_DIR / f"CapsWriter-Offline-Lite-{timestamp}.zip"

    build_zip_package(full_zip, is_lite=False)
    build_zip_package(lite_zip, is_lite=True)

    print("\n" + "=" * 60)
    print(f"🎉 所有压缩包均已成功生成在目标目录: {DIST_DIR}")
    print("=" * 60)
    for p in DIST_DIR.glob("*.zip"):
        print(f"  📦 [ZIP] {p.name} ({p.stat().st_size / (1024*1024):.2f} MB)")


if __name__ == '__main__':
    main()
