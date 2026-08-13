# CapsWriter-Offline GUI 增强版

![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)
![Platform: Windows](https://img.shields.io/badge/Platform-Windows-blue.svg)
![Python: 3.11](https://img.shields.io/badge/Python-3.11-yellow.svg)

CapsWriter-Offline GUI 增强版是一个 Windows 离线语音输入工具。它可以在本地完成语音识别，把听写结果输入到当前光标位置，并提供桌面控制中心来管理快捷键、模型、热词、历史记录、音视频转写、AI 角色、备份和诊断。

![CapsWriter 控制中心](例图/屏幕截图%202026-08-13%20135513.png)

## 项目功能

- 离线语音输入：本地加载 ASR 模型，按住快捷键说话，松开后将识别结果写入当前应用。
- 桌面控制中心：通过原生窗口管理快捷键、识别模型、输出方式、热词、AI 配置和运行状态。
- 快捷键与对讲模式：支持自定义听写快捷键，并可切换长按说话或单击开始/结束录音。
- 听写状态浮层：按住说话时在屏幕底部显示录音状态和音量反馈，帮助确认麦克风正在工作。
- 两种输出方式：支持直接写入光标位置，也支持先显示可编辑确认框，再由用户确认后写入。
- 输入历史：保存最近听写结果，支持搜索、复制、查看全文和清空。
- 本地数据管理：集中查看听写日记、录音文件、转写输出和日志，方便排查和清理。
- 热词与替换规则：在控制中心里维护 `hot.txt` 和 `hot-rule.txt`，修正常用词、项目名、标点和固定格式。
- 语音识别与硬件：选择本地 ASR 模型、识别语言、格式化选项、后端和硬件加速策略。
- 字幕转写：选择音频或视频文件，生成 `TXT`、`JSON`、`SRT` 和合并文本。
- AI 润色与角色：可选配置 OpenAI 兼容 API 档案，用于润色、翻译、小助理、大助理和自定义角色。
- 服务与诊断：查看服务端、客户端、GUI、托盘和模型状态，支持重启组件和完全退出。
- 配置备份与迁移：导出/导入常用设置，默认不导出私有 API Key。

## 安装与运行

### 下载发布包

如果只是日常使用，推荐从 GitHub Releases 下载打包好的发布包：

- `CapsWriter-Full.zip`：完整版，内置本地 ASR 模型，下载后解压即可运行，体积较大。
- `CapsWriter-Lite.zip`：精简版，不内置 ASR 模型，体积较小；首次使用前需要按包内说明下载模型并放入 `models/` 目录。

解压后运行包内的 `CapsWriter.exe`。

### 环境要求

如果要从源码运行，需要准备：

- Windows
- Python 3.11
- 本地 ASR 模型文件

### 安装 Python 依赖

```powershell
pip install -r requirements-client.txt
pip install -r requirements-server.txt
```

这一步只安装 Python 依赖。语音识别还需要准备本地模型文件。

### 准备模型

将模型文件放入 `models/` 下对应目录。各模型目录内保留了下载链接说明。

### 启动

```powershell
python run_app.py
```

Windows 下也可以双击：

```text
启动 CapsWriter 智能控制中心.bat
```

启动后会同时管理 ASR 服务端、听写客户端、控制中心 GUI 和托盘菜单。

## 基本使用

1. 打开 CapsWriter 控制中心。
2. 在“通用与交互”里设置听写快捷键。
3. 按住快捷键说话。
4. 松开快捷键后，识别结果会写入当前光标位置。

常用快捷键包括 `Caps Lock`、鼠标侧键 `X2`、`F8`、`F9`、`Ctrl + Space` 和 `Ctrl + Alt + Space`。

如果启用“确认后写入”，识别完成后会先显示可编辑确认框，并把文本复制到剪贴板。

## 文档

- [控制中心使用说明](docs/控制中心说明.md)
- [常见问题](docs/常见问题.md)
- [热词功能如何使用](docs/热词功能如何使用.md)
- [文件转录功能如何使用](docs/文件转录功能如何使用.md)
- [显卡加速的若干问题](docs/显卡加速的若干问题.md)
- [模型下载的若干问题](docs/模型下载的若干问题.md)

控制中心右上角的问号按钮也可以打开 README 和 `docs/` 文档目录。

## 本地数据与隐私

普通语音识别在本地完成。以下文件可能包含用户内容或本机配置，不应提交到公开仓库：

- 输入历史：`web_gui/input_history.jsonl`
- 私有 API Key：`web_gui/private_config.json`
- GUI 设置：`web_gui/gui_settings.json`
- AI 档案设置：`web_gui/llm_profiles.json`
- 听写日记：按日期生成的 `YYYY/MM/DD.md`
- 录音文件：按日期生成的 `assets/` 目录
- 转写输出：`web_gui/outputs/`
- 日志：`logs/`
- 配置备份：`web_gui/config_backups/`

这些运行时文件已在 `.gitignore` 中排除。配置导出默认不包含 API Key。

## 与原项目的关系

本项目是 CapsWriter-Offline 的 GUI 增强分支。原项目提供离线 ASR、快捷键听写、热词处理和客户端/服务端基础流程；本分支重点补充桌面可视化管理和面向日常使用的辅助功能。

## License

本项目继承原项目 MIT License。发布修改版时请保留原版权声明和许可证文件。
