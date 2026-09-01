# coding: utf-8
"""
CapsWriter-Offline 商业级解压即用发行包打包脚本

构建极简清爽的发版解压目录结构：
- CapsWriter.exe            (全局唯一可执行启动主程序)
- readme.md                 (使用说明文档)
- hot.txt / hot-rule.txt    (用户热词配置文件)
- config_client.py / config_server.py (运行配置文件)
- models/                   (语音识别模型文件夹)
- internal/                 (收纳所有底层代码、组件、图像资源与二进制依赖库，隐藏不展示)

生成目标：
1. CapsWriter-Full.zip (完整版：CapsWriter.exe + 全量离线识别大模型)
2. CapsWriter-Lite.zip (精简版：CapsWriter.exe + 免自带模型)
"""

import os
import shutil
import zipfile
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DIST_DIR = BASE_DIR / 'dist'
TARGET_DIR = DIST_DIR / 'CapsWriter-Offline'
INTERNAL_DIR = TARGET_DIR / 'internal'

MODEL_EXTENSIONS = {'.onnx', '.bin', '.pth', '.safetensors', '.pt', '.tflite', '.engine', '.gguf'}


def assemble_clean_release_directory():
    """装配商业级极简发布运行目录"""
    if not TARGET_DIR.exists():
        raise FileNotFoundError("未找到编译输出目录 dist/CapsWriter-Offline，请先运行 PyInstaller 构建。")

    print("\n[1/3] 正在装配极简发版运行目录...")
    INTERNAL_DIR.mkdir(parents=True, exist_ok=True)

    # 1. 将所有源码与资源模块统一收纳放入 internal/ 隐藏内部目录，保持根目录整洁
    internal_subfolders = ['assets', 'core', 'web_gui', 'LLM', 'docs']
    for folder in internal_subfolders:
        src_folder = BASE_DIR / folder
        dest_folder = INTERNAL_DIR / folder
        if src_folder.exists():
            if dest_folder.exists():
                shutil.rmtree(dest_folder, ignore_errors=True)
            shutil.copytree(
                src_folder,
                dest_folder,
                ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '.git', '*.tmp', 'config_exports', 'config_backups', 'assets_backup_old')
            )

    # 将关联 Python 源码入口文件收纳至 internal/
    py_code_files = ['run_app.py', 'start_client.py', 'start_server.py', 'block_mouse_forward.py', 'create_shortcut.py', 'config_pill.py']
    for py_file in py_code_files:
        src_file = BASE_DIR / py_file
        dest_file = INTERNAL_DIR / py_file
        if src_file.exists():
            shutil.copy2(src_file, dest_file)

    # 2. 根目录仅放置【配置文件 + 用户文档 + 模型目录 + 唯一的 CapsWriter.exe】
    # 根目录用户可编辑配置文件
    user_root_files = [
        'config_client.py',
        'config_server.py',
        'hot.txt',
        'hot-server.txt',
        'hot-rule.txt',
        'readme.md',
        'LICENSE',
    ]
    for file in user_root_files:
        src_file = BASE_DIR / file
        dest_file = TARGET_DIR / file
        if src_file.exists():
            shutil.copy2(src_file, dest_file)

    # 拷贝 models 目录至根目录
    models_src = BASE_DIR / 'models'
    models_dest = TARGET_DIR / 'models'
    if models_src.exists():
        if models_dest.exists():
            shutil.rmtree(models_dest, ignore_errors=True)
        shutil.copytree(
            models_src,
            models_dest,
            ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '*.ipynb', '.git', '*.tmp')
        )

    # 3. 彻底清理根目录下所有冗余的 .bat, .vbs 等多余启动方式，确保只有 CapsWriter.exe
    redundant_launchers = [
        '启动 CapsWriter 离线语音输入.bat',
        '启动 CapsWriter 智能控制中心.vbs',
        '运行自动化自测试与故障诊断.bat',
        'build.spec',
        'build-client.spec',
        'make_releases.py',
        'zip_release.py',
    ]
    for r_file in redundant_launchers:
        r_path = TARGET_DIR / r_file
        if r_path.exists():
            r_path.unlink()

    print("✅ 极简运行目录装配完成！根目录仅包含【CapsWriter.exe】唯一入口。")


def build_zip_package(output_zip: Path, is_lite: bool = False):
    print(f"\n[2/3] 正在压缩 {'精简版 (Lite)' if is_lite else '完整版 (Full)'} -> {output_zip.name}")
    if output_zip.exists():
        output_zip.unlink()

    file_count = 0
    total_bytes = 0
    root_prefix = f"CapsWriter-Offline-{'Lite' if is_lite else 'Full'}"

    with zipfile.ZipFile(output_zip, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for root, dirs, files in os.walk(TARGET_DIR):
            dirs[:] = [d for d in dirs if d not in ('__pycache__', '.vscode', '.git', 'config_exports', 'config_backups', 'tools')]
            
            for file in files:
                file_path = Path(root) / file
                if file_path.suffix.lower() in ('.pyc', '.pyo', '.tmp', '.log', '.bak', '.ipynb'):
                    continue

                rel_path = file_path.relative_to(TARGET_DIR)
                rel_str = str(rel_path).replace('\\', '/')

                # 精简版排除 models/ 目录下的大模型权重文件
                if is_lite and 'models' in rel_path.parts:
                    if file_path.suffix.lower() in MODEL_EXTENSIONS:
                        continue

                arcname = f"{root_prefix}/{rel_str}"
                zf.write(file_path, arcname=arcname)
                file_count += 1
                total_bytes += file_path.stat().st_size

    size_mb = output_zip.stat().st_size / (1024 * 1024)
    print(f"✅ 压缩完成！包文件名: {output_zip.name}，文件数: {file_count}，文件大小: {size_mb:.2f} MB")
    return output_zip


def main():
    print("=" * 60)
    print("CapsWriter-Offline 极简商业发布包自动打包生成器")
    print("=" * 60)

    # 1. 编译并打包 PyInstaller
    # 2. 装配极简发版目录
    assemble_clean_release_directory()

    # 3. 输出不带日期的标准 ZIP 压缩包
    full_zip = DIST_DIR / "CapsWriter-Full.zip"
    lite_zip = DIST_DIR / "CapsWriter-Lite.zip"

    build_zip_package(full_zip, is_lite=False)
    build_zip_package(lite_zip, is_lite=True)

    print("\n" + "=" * 60)
    print(f"🎉 打包任务完成！压缩包已生成在目标目录: {DIST_DIR}")
    print("=" * 60)
    for p in [full_zip, lite_zip]:
        print(f"  📦 [Release ZIP] {p.name} ({p.stat().st_size / (1024*1024):.2f} MB)")


if __name__ == '__main__':
    main()
