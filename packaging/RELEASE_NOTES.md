# CapsWriter Offline V1 · 预览候选版

本构建用于 V1 集中验收。Windows 10 / 11 x64 为首发目标，真实设备、输入法、托盘、安装升级与卸载测试必须按回执完成后才可作为公众正式下载。

安装包不包含大型语音模型。首次启动可下载经过清单核验的模型，也可保留原有模型和配置。AI 润色默认关闭，只有用户主动配置并启用后才将相关文字发送至所选服务。

## 使用方式

- 安装版：程序默认放在当前用户的 LocalAppData/Programs/CapsWriter；个人数据在 LocalAppData/CapsWriter。
- Portable：完整解压到可写目录，保留 portable.flag；数据在应用目录的 data，模型在 models。
- 关闭控制中心窗口后应用可在托盘继续运行；完全退出请用托盘菜单，安全暂停下载并结束所属进程。
- 升级前先完全退出应用，备份个人配置；安装程序不自动终止正在运行的任务。
- 卸载仅处理安装器拥有的程序文件，默认保留模型、配置、热词、历史和输出。

原生界面依赖 Microsoft WebView2 Runtime。缺失时请从微软官方页面安装 Evergreen Runtime，再重新打开应用：
https://developer.microsoft.com/microsoft-edge/webview2/

本版本采用手动检查、手动升级；不会静默替换运行中的程序。模型及第三方依赖的许可证独立于主项目 MIT 许可证。
