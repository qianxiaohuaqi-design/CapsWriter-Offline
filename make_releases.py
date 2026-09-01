# coding: utf-8
"""
CapsWriter-Offline 商业级解压即用发行包打包脚本

构建极简清爽的发版解压目录结构：
- CapsWriter.exe            (全局唯一可执行启动主程序)
- hot.txt / hot-rule.txt    (干净标准的热词模版，彻底擦除作者个性化热词与私人正则)
- config_client.py / config_server.py (运行配置文件)
- models/                   (语音识别模型文件夹，扁平单层结构，包含实测 SenseVoice 模型及单一说明文件)
- internal/                 (收纳所有底层代码、组件、图像资源与二进制依赖库，隐藏不展示，已清空私密 API Key 与隐私数据)

生成目标：
1. CapsWriter-Full.zip (完整版：CapsWriter.exe + 全量离线识别大模型)
2. CapsWriter-Lite.zip (精简版：CapsWriter.exe + 免自带模型)
"""

import json
import os
import shutil
import zipfile
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DIST_DIR = BASE_DIR / 'dist'
TARGET_DIR = DIST_DIR / 'CapsWriter-Offline'
INTERNAL_DIR = TARGET_DIR / 'internal'

MODEL_EXTENSIONS = {'.onnx', '.bin', '.pth', '.safetensors', '.pt', '.tflite', '.engine', '.gguf'}

MODEL_README_CONTENT = """CapsWriter-Offline 离线语音识别模型说明：

1. 完整版 (CapsWriter-Full.zip) 已默认内置 SenseVoice-Small 高精度离线模型，解压即用；
2. 如需下载或更新其他语音识别大模型（如 Paraformer / FunASR 等），请前往官方仓库 Release 页面下载：
   https://github.com/HaujetZhao/CapsWriter-Offline/releases/tag/models

下载后解压将模型文件夹放入本 models/ 目录中即可。
"""

CLEAN_HOT_TXT = """# 客户端热词文件
# 使用音素 RAG 匹配，支持中英文混合
# 具备强制性，相似度高于阈值的一定会被替换
# 井号开头的行为注释，会被忽略
# 每行一个热词，可以用 | 分割添加多个别名
# 详细说明见 docs/热词功能如何使用.md

# ====== 示例热词 ======
CapsWriter | Caps Rider
Paraformer | performer
SenseVoice-small
"""

CLEAN_HOT_RULE_TXT = """# 在此文件放置自定义规则，每行一条正则表达式，
# 左边是查找模式，右边是替换式，中间用带空格的等号分开
# 以 # 开头的会被忽略，可用作注释
# 文本两边的空格会被省略，在替换侧可用 \s 表示空格
# 例如：

毫安时     =      mAh
赫兹      =      Hz
伏特      =      V
"""

CLEAN_HOT_SERVER_TXT = """# 服务端热词文件
# 每行一个热词，井号开头的行为注释，会被忽略

# ====== 示例热词 ======
CapsWriter
SenseVoice
"""


def assemble_clean_release_directory():
    """装配商业级极简发布运行目录，并彻底脱敏私密隐私数据与个人热词"""
    if not TARGET_DIR.exists():
        raise FileNotFoundError("未找到编译输出目录 dist/CapsWriter-Offline，请先运行 PyInstaller 构建。")

    print("\n[1/3] 正在装配极简发版运行目录并清理隐私凭据与个人热词...")
    INTERNAL_DIR.mkdir(parents=True, exist_ok=True)

    # 1. 将所有源码与资源模块统一收纳放入 internal/ 隐藏内部目录
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

    # 隐私脱敏处理：清空 internal/web_gui 中的私密 API Key、历史记录与个性化私密配置
    internal_webgui = INTERNAL_DIR / 'web_gui'
    if internal_webgui.exists():
        clean_private_config = {
            "llm_api_keys": {},
            "llm_role_api_keys": {},
            "llm_profile_api_keys": {}
        }
        (internal_webgui / 'private_config.json').write_text(
            json.dumps(clean_private_config, indent=2, ensure_ascii=False),
            encoding='utf-8'
        )
        for history_file in ['input_history.jsonl', 'input_history_cleared_at.txt']:
            if (internal_webgui / history_file).exists():
                (internal_webgui / history_file).unlink()

    # 2. 根目录仅放置【配置文件 + 模型目录 + 唯一的 CapsWriter.exe】（已去除 readme.md, LICENSE 等无用说明杂物）
    user_root_files = [
        'config_client.py',
        'config_server.py',
    ]
    for file in user_root_files:
        src_file = BASE_DIR / file
        dest_file = TARGET_DIR / file
        if src_file.exists():
            shutil.copy2(src_file, dest_file)

    # 写入纯净的标准初始热词模版（彻底擦除作者个人热词偏好、个人别名与正则规则）
    (TARGET_DIR / 'hot.txt').write_text(CLEAN_HOT_TXT, encoding='utf-8')
    (TARGET_DIR / 'hot-rule.txt').write_text(CLEAN_HOT_RULE_TXT, encoding='utf-8')
    (TARGET_DIR / 'hot-server.txt').write_text(CLEAN_HOT_SERVER_TXT, encoding='utf-8')

    # 3. 彻底扁平化 models/ 目录（拍平嵌套子目录，只留单层 models/SenseVoice-Small/ 且无重复 TXT）
    models_src = BASE_DIR / 'models'
    models_dest = TARGET_DIR / 'models'
    if models_dest.exists():
        shutil.rmtree(models_dest, ignore_errors=True)
    models_dest.mkdir(parents=True, exist_ok=True)

    # 仅拷贝 SenseVoice-Small 核心 ONNX 权重文件
    sensevoice_src = models_src / 'SenseVoice-Small'
    sensevoice_dest = models_dest / 'SenseVoice-Small'
    sensevoice_dest.mkdir(parents=True, exist_ok=True)

    if sensevoice_src.exists():
        for root, dirs, files in os.walk(sensevoice_src):
            for file in files:
                if file.endswith(('.onnx', '.model', '.json', '.txt')) and not file.endswith('下载链接.txt'):
                    src_f = Path(root) / file
                    dest_f = sensevoice_dest / file
                    shutil.copy2(src_f, dest_f)

    # 根目录仅保留单个标准说明 TXT
    (models_dest / '模型下载与说明.txt').write_text(MODEL_README_CONTENT, encoding='utf-8')

    # 4. 彻底清理根目录下所有冗余的 .bat, .vbs, LICENSE, readme.md 等杂文件
    redundant_files = [
        '启动 CapsWriter 离线语音输入.bat',
        '启动 CapsWriter 智能控制中心.vbs',
        '运行自动化自测试与故障诊断.bat',
        'LICENSE',
        'readme.md',
        'README.md',
        'build.spec',
        'build-client.spec',
        'make_releases.py',
        'zip_release.py',
    ]
    for r_file in redundant_files:
        r_path = TARGET_DIR / r_file
        if r_path.exists():
            r_path.unlink()

    print("✅ 极简运行目录装配完成！已移除 readme.md / LICENSE，根目录仅保留【CapsWriter.exe】与必需配置文件。")


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

    assemble_clean_release_directory()

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
