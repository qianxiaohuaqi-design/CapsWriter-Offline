# coding: utf-8
"""
CapsWriter-Offline 标准二进制发布打包脚本
将 PyInstaller 编译出的 CapsWriter.exe 以及项目核心文件/模型整合，
在 dist/ 目录下直接生成两个包含可执行文件 CapsWriter.exe 的解压即用 ZIP 安装包：
1. CapsWriter-Offline-Full-YYYYMMDD.zip (完整版：CapsWriter.exe + 离线语音识别模型)
2. CapsWriter-Offline-Lite-YYYYMMDD.zip (精简版：CapsWriter.exe + 免自带模型)
"""

import os
import shutil
import zipfile
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DIST_DIR = BASE_DIR / 'dist'
TARGET_DIR = DIST_DIR / 'CapsWriter-Offline'

MODEL_EXTENSIONS = {'.onnx', '.bin', '.pth', '.safetensors', '.pt', '.tflite', '.engine', '.gguf'}


def copy_source_components():
    """将项目源码模块、资源与配置文件补充拷贝至 dist/CapsWriter-Offline 目录"""
    if not TARGET_DIR.exists():
        raise FileNotFoundError("未找到编译输出目录 dist/CapsWriter-Offline，请先运行 PyInstaller 构建。")

    print("\n正在装配二进制运行目录...")
    
    # 拷贝核心文件夹
    folders_to_copy = ['assets', 'core', 'web_gui', 'LLM', 'docs', 'models', 'tools']
    for folder in folders_to_copy:
        src_folder = BASE_DIR / folder
        dest_folder = TARGET_DIR / folder
        if src_folder.exists():
            if dest_folder.exists():
                shutil.rmtree(dest_folder, ignore_errors=True)
            shutil.copytree(
                src_folder,
                dest_folder,
                ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '.git', '*.tmp', 'config_exports', 'config_backups')
            )

    # 拷贝核心文件
    files_to_copy = [
        'config_client.py',
        'config_server.py',
        'hot.txt',
        'hot-server.txt',
        'hot-rule.txt',
        'readme.md',
        'LICENSE',
        'run_app.py',
        'start_client.py',
        'start_server.py',
        '启动 CapsWriter 离线语音输入.bat',
        '启动 CapsWriter 智能控制中心.vbs',
    ]
    for file in files_to_copy:
        src_file = BASE_DIR / file
        dest_file = TARGET_DIR / file
        if src_file.exists():
            shutil.copy2(src_file, dest_file)

    print("✅ 二进制运行目录装配完成！包含 CapsWriter.exe 及其完整支撑组件。")


def build_zip_package(output_zip: Path, is_lite: bool = False):
    print(f"\n正在压缩 {'精简版 (Lite)' if is_lite else '完整版 (Full)'} 压缩包 -> {output_zip.name}")
    if output_zip.exists():
        output_zip.unlink()

    file_count = 0
    total_bytes = 0

    root_prefix = f"CapsWriter-Offline-{'Lite' if is_lite else 'Full'}"

    with zipfile.ZipFile(output_zip, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for root, dirs, files in os.walk(TARGET_DIR):
            # 过滤临时目录
            dirs[:] = [d for d in dirs if d not in ('__pycache__', '.vscode', '.git', 'config_exports', 'config_backups')]
            
            for file in files:
                file_path = Path(root) / file
                if file_path.suffix.lower() in ('.pyc', '.pyo', '.tmp', '.log', '.bak'):
                    continue

                rel_path = file_path.relative_to(TARGET_DIR)
                rel_str = str(rel_path).replace('\\', '/')

                # 精简版排除大模型权重文件
                if is_lite and 'models' in rel_path.parts:
                    if file_path.suffix.lower() in MODEL_EXTENSIONS:
                        continue

                arcname = f"{root_prefix}/{rel_str}"
                zf.write(file_path, arcname=arcname)
                file_count += 1
                total_bytes += file_path.stat().st_size

    size_mb = output_zip.stat().st_size / (1024 * 1024)
    print(f"✅ 压缩完成！包含 CapsWriter.exe，文件数: {file_count}，包大小: {size_mb:.2f} MB")
    return output_zip


def main():
    print("=" * 60)
    print("CapsWriter-Offline 可执行程序 (CapsWriter.exe) 自动化发布打包")
    print("=" * 60)

    # 1. 装配目标二进制文件夹
    copy_source_components()

    # 2. 生成发布 zip 包
    timestamp = datetime.now().strftime("%Y%m%d")
    full_zip = DIST_DIR / f"CapsWriter-Offline-Full-{timestamp}.zip"
    lite_zip = DIST_DIR / f"CapsWriter-Offline-Lite-{timestamp}.zip"

    build_zip_package(full_zip, is_lite=False)
    build_zip_package(lite_zip, is_lite=True)

    print("\n" + "=" * 60)
    print(f"🎉 包含 CapsWriter.exe 的可执行发布包已成功生成在目录: {DIST_DIR}")
    print("=" * 60)
    for p in sorted(DIST_DIR.glob("*.zip")):
        print(f"  📦 [EXE Release ZIP] {p.name} ({p.stat().st_size / (1024*1024):.2f} MB)")


if __name__ == '__main__':
    main()
