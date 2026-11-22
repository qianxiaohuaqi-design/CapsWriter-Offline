# V1 构建、发行核验与本地试用

`1.0.0-rc.1` 是 V1 产品候选版本，旧配置中的 `2.6` 是原内核的历史版本，不能拿旧值替代产品发行版本。新应用版本取自 `packaging/version.json`，与 `core.product.__version__` 保持一致。

## Windows 源码试用

先在独立 worktree/目录保护原有 `e7cc1e2`、11 项修改和用户资料，再使用 Python 3.11 x64 新建专用虚拟环境：

```powershell
py -3.11 -m venv .venv-v1
.venv-v1\Scripts\python.exe -m pip install -r requirements-v1.txt
.venv-v1\Scripts\python.exe -m pip check
.venv-v1\Scripts\python.exe run_app.py
```

`requirements-v1.txt` 复用旧锁定依赖，并补齐实际引擎源码导入的 sentencepiece/soundfile/pydub/nagisa/soynlp。这些版本存在不代表 Windows 联调已经通过；环境解析或动态库 ABI 不匹配须留下实际错误与修复记录。原生窗口需要 WebView2 Runtime，可从 [微软官方入口](https://developer.microsoft.com/microsoft-edge/webview2/) 获取，不由构建脚本暗中安装。

Linux 上的逻辑/轻量 UI 测试使用 `requirements-v1-test.txt`，它按实际转写、输入与 LLM 导入链列出轻量依赖，运行可用 `PYNPUT_BACKEND=dummy python -m unittest discover -s tests -p "test_v1*.py" -v`；系统 Python 仍须提供 `tkinter`/Tcl/Tk（例如发行版的 `python3-tk`），这些逻辑测试不创建真实窗口、无需 `DISPLAY`，生命周期测试的 PortAudio 导入隔离不代表麦克风验收，实际窗口需要可用桌面/显示服务，Windows 全功能仍使用 `requirements-v1.txt`。

## 尽早做最小打包

```powershell
.venv-v1\Scripts\python.exe tools/build_v1.py --minimal --output build/v1-smoke-01
```

最小打包生成同一个应用入口与 Portable ZIP，暂不包含未核验的 FFmpeg/GGUF 外部二进制，仅用于尽早发现资源、启动、窗口/后台与写目录问题。它不能作为完整发行包；模型与音频实际测试需具备对应依赖。

构建前必须提交审核过的源码与本次新 `v1-app.spec`（根 `.gitignore` 默认忽略 spec，需要显式加入）。脚本只选择 Git 跟踪的允许文件，拒绝符号链接；不复制根用户角色、热词、私有配置、模型、录音、日志、历史或未知文件。可先运行 `--dry-run` 查看计划，或 `--stage-only --output build/v1-stage-01` 在任何平台检查干净源码暂存。

## 完整安装器与 Portable

完整构建还需要：Windows x64、Inno Setup 6.3 或更新的 6.x，以及经过核验的 FFmpeg/ffprobe 和三个现有 GGUF 引擎所需的 llama.cpp DLL。原项目的 GGUF 绑定指定了其 ABI；不可因为另一个 DLL 也叫 llama.dll 就直接替换。

复制 `vendor-manifest.example.json` 到仓库外，在本机填写真实来源。清单条目结构：

```json
{
  "schema": "capswriter.reviewed-vendors.v1",
  "files": [
    {
      "component": "ffmpeg",
      "source_file": "reviewed/ffmpeg.exe",
      "target": "tools/ffmpeg/bin/ffmpeg.exe",
      "sha256": "由实际文件计算的64位SHA256，不得使用示例字符串",
      "source_url": "经核验的上游HTTPS文件地址",
      "version": "实际版本和构建标记",
      "license_id": "实际构建适用的许可证",
      "reviewed": true,
      "license_files": [
        {"path": "reviewed/LICENSE.txt", "name": "ffmpeg-LICENSE.txt", "sha256": "实际许可证文件的SHA256"}
      ]
    }
  ]
}
```

这只是字段说明，不是可以跳过验证的下载清单。构建脚本验证每个文件与许可字节、限定目标目录、不覆盖暂存代码。GGUF target 分别为 `core/server/engines/{fun_asr_gguf,qwen_asr_gguf,force_aligner_gguf}/inference/bin/`。至少包括各自的 `llama.dll`、`ggml.dll`、`ggml-base.dll`，并按实际构建加入所有依赖 DLL。FFmpeg 来源从 [官方页面](https://ffmpeg.org/download.html) 查证，具体许可取决于实际编译配置。

```powershell
.venv-v1\Scripts\python.exe tools/build_v1.py --vendor-manifest C:\reviewed\vendor.json --iscc "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" --output build/v1-full-01
```

脚本会在全新的输出目录连续完成：干净源码暂存、第三方许可与版本清单、统一 PyInstaller onedir、发行内容审计、Portable ZIP、当前用户 EXE 安装包、SHA256SUMS 与 `release-manifest.json`。不存在清空旧 build/dist 的操作；重复目录会直接拒绝。

安装器检测 WebView2 缺失时明确提示，用户可选择继续安装，绝不静默联网安装。编译器含简中语言文件时启用简中；若未包含，应从 Inno 上游获取适配版本并在正式发行前验证中文安装界面。安装器没有自启注册项，没有自动终止进程，没有个人数据删除段。Launcher 的 `CapsWriter.Offline.ControlCenter` 命名 mutex 活跃时升级/卸载停止并提示先完全退出。EXE 与 ZIP 使用同一 onedir，只有 ZIP 加入 `portable.flag`。

## 发布之前的真实验证

构建出来的清单始终是 `candidate`、`windows_validated=false`、`public_verified=false`，签名默认 `unsigned`。它们不能自动当成发布证据。

1. 在干净 Win10/11 上按 G0–G8 跑真实模型/麦克风/IME/焦点/后台、已有模型兼容、Portable 移动、安装升级卸载和数据哈希测试，保存对应代码与产物 SHA。
2. 确认完整第三方许可与签名状态。若手动签名，必须签名后重新生成产物 SHA/大小；不要手填“已签名”。
3. 用户授权正式发布后，上传到指定 GitHub Release，再以实际公开文件核对版本、大小与 SHA-256。
4. 只有全部核验完成，才能在官网 `release-manifest.json` 写入 `published`、`windows_validated=true`、真实 40 位代码 SHA、每个文件的 `public_verified=true` / `verified_at` / 真实 URL。未通过或未执行保留 disabled。

当前仓库只提供构建与验证工具，不含本轮已经实测的 Windows EXE/ZIP。`website/` 是静态源码，不会随 Git push 自动部署。

## 官方技术参考

- [PyInstaller spec 文件](https://pyinstaller.org/en/stable/spec-files.html)、[资源与隐藏导入 hooks](https://pyinstaller.org/en/stable/hooks.html)
- [Inno Setup 当前用户权限](https://jrsoftware.org/is6help/topic_setup_privilegesrequired.htm)、[卸载删除范围](https://jrsoftware.org/ishelp/topic_uninstalldeletesection.htm)
- [GitHub Release API](https://docs.github.com/en/rest/releases/releases)、[资产摘要字段](https://docs.github.com/en/rest/releases/assets)
- [WebView2 发行与 Runtime 检测](https://learn.microsoft.com/microsoft-edge/webview2/concepts/distribution)
- 新增 Python 依赖来源：[sentencepiece](https://pypi.org/project/sentencepiece/0.2.3/)、[soundfile](https://pypi.org/project/soundfile/0.14.0/)、[pydub](https://pypi.org/project/pydub/0.25.1/)、[nagisa](https://pypi.org/project/nagisa/0.3.2/)、[soynlp](https://pypi.org/project/soynlp/0.0.493/)。链接核对日期 2026-11-22，完整组合以实际构建回执为准。
