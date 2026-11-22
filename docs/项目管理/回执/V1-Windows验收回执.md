# CapsWriter-Offline V1 Windows 验收回执

日期 2026-11-22（Asia/Shanghai）。产品版本 **1.0.0-rc.1**。本次在 Windows 本机验证开发分支的源码及本地发行候选。公开 Release 和官网发布需另行确认。

## 版本与环境

| 项目 | 实测记录 |
| --- | --- |
| 开发分支 | `feat/capswriter-v1-20261122` |
| 拉取时远端完整 SHA | `6c6259a961813bc584acbd2bc95688b84def2a6e` |
| Windows 修复后源码与最终构建输入 SHA | `7f68491d4fdb2cda27c780b24b383dab116b7549` |
| 环境 | Windows 11 家庭中文版 x64，10.0.26200，Python 3.11.9 AMD64 独立虚拟环境 |
| 硬件 | AMD Ryzen 9 8945HX，NVIDIA GeForce RTX 5060 Laptop GPU，约 15.8 GiB RAM |
| 核心依赖 | NiceGUI 3.15.0，PyWebView 6.2.1，WebView2 Runtime 157.0.4341.60，websockets 15.0.1，ONNX Runtime DirectML 1.24.4，sherpa-onnx 1.13.4，sounddevice 0.5.5，PyInstaller 6.21.0，Inno Setup 6.7.4 |
| 依赖安装 | 首次 pip 下载超时。改用 uv 安装同一份 `requirements-v1.txt`，锁定版本保持一致，随后 `pip check` 通过 |

## 原工作区保护和 M1-A 衔接

- 原 `master` 停在 `e7cc1e26bf456439c2ff3492447801cf512b47f2`，暂存区为空。独立审阅分支 `review/capswriter-v1-windows-20261122` 使用另一 worktree。原工作区 11 项修改、删除和未跟踪文件（含 `build.spec`）的本机 SHA-256/状态清单前后相同。原有删除项比较 HEAD 中对应文件的 SHA-256。
- 启动前检查继承的覆盖变量，核对实际数据根和模型根都位于试用目录。原有 SenseVoice 和 Qwen3-ASR 通过完整模型目录只读登记，两个目录下八个文件的 SHA-256 和大小前后相同。哈希清单、绝对目录和运行原始日志仅留本机。
- M1-A 的资源、数据和模型三根隔离由 `core/product/paths.py` 与现有调用方保留。源码默认路径兼容，安装和 Portable 通过运行形态选择默认位置，可单独覆盖数据、模型根。配置备份、历史、转写、日志、浮层设置和私有配置继续使用统一路径。搜索旧模型目录、手动模型和旧配置的逐文件路径都复用 `core/product/models.py` 和实际配置类。
- M1-A 的文件可用性、完整性和运行就绪三类证据在 V1 分别由目录检查、安装校验和 Worker 实例心跳提供。V1 记录手动模型为未校验；对于只有固定文件名的旧 Qwen GGUF 按通用 Qwen 模型识别，未猜测 Q4 或 Q5。沿用现有 Qwen/Paraformer 补充对齐或标点资源的配置入口。

## 已复现和修复的问题

| 现象 | 原因和修复 | 验证 |
| --- | --- | --- |
| Windows 原生窗口首次白屏 | 初次渲染在诊断页同步遍历系统进程，并解析无关命令行里的路径。先按组件筛选，比较规范化的实际启动路径，避免访问外部路径或不可达共享 | 截取过基线白屏。修复后源码启动的本机页面实际返回 HTTP 200，约 1.14 秒。外部同名路径和正常进程归属加入回归 |
| 安装布局路径测试失败 | 环境已有 `LOCALAPPDATA` 时仍提前调用 `Path.home()`，改为仅在缺省时解析用户目录 | 路径、干净默认配置与旧数据保护测试通过 |
| Windows IPC 和 GUI 验证脚本误判或清理失败 | venv 的 Python 重定向进程与实际协调器 PID 不同。用真实描述符与子进程树确认归属，模拟 GUI 在删除临时目录前等待后台和重定向进程退出、关闭日志句柄 | IPC 测试通过，NiceGUI 六项模拟操作与真实 IPC 完成并正常退出 |
| PyInstaller 生成 EXE 后，模型/数据审计阻止 ZIP | 剔除 scikit-learn 示例和测试数据、ONNX Runtime 导出工具，只允许 Nagisa 日语对齐实际需要的三个依赖文件 | 审计单测与完整 onedir 清单通过 |
| 模型完整下载后安装回执写入失败 | 本机未启用 Win32 长路径，旧安装临时路径达到 281 字符。缩短暂存名称并复用原子 JSON 写入 | 中文嵌套路径回归通过。同一下载任务复用已下载归档，完成实际安装和回读校验 |
| AI 角色编辑保存后，运行时可能仍读取资源目录中的同名文件 | 角色加载从活动数据档案读取具体文件，热重载读取最新字节。创建器按字面量保存角色名，支持引号和换行 | Windows 隔离档案保存、重新加载、名称字面量及四套本机流式 SDK 请求通过 |
| 长时间构建时，清单可能被写入后来提交的 SHA | 制品元数据绑定暂存前的提交，正式打包检查暂存前后的跟踪文件，版本读取已暂存文件 | 前序构建发生过偏差，按已知暂存源码归档其单项实证，最终只交付重新构建并核对过源码 SHA 的制品 |

## Windows 执行证据

- `python -m unittest discover -s tests -p "test_v1_*.py" -v` 在独立 Python 3.11 x64 环境执行，共 134 项，130 PASS、0 FAIL、4 SKIP。跳过项受本机创建符号链接的权限限制。
- `python tools/verify_v1_ui.py` 的六项流程通过，覆盖无模型引导、跳过和继续、麦克风未测试状态、重开及同后台所有者。事件来自 NiceGUI 模拟操作，原生窗口、桌面焦点、热键另行验收。
- 实际 Windows Worker 由可核验的独立进程执行。在真实 WebSocket 上传公开 MP3，用 FFmpeg 解码后完成识别、保存 TXT/JSON/SRT/MERGE、同任务从头重试和独立 SRT 重建。另运行公开样本派生的 WAV 和 MP4 队列，确认取消从 `cancelling` 到 `cancelled`，MP4 随后完成，重新打开队列保留结果。故意提供不可解码音频时任务明确失败。成功、失败、取消和单项重试后，源媒体、源旁同名旧字幕和首个 attempt 产物的哈希均一致。 [公开音频及其固定来源](https://raw.githubusercontent.com/HaujetZhao/SenseVoice-ONNX/4d7bce67e86b4049db1a58a6c2e2ecd34dfefd9c/test-fun.mp3)
- 公开 MP3 为 84,140 字节，SHA-256 为 `ba5f8a9db8665afb48f1be359d4faef615dc7679165303f1e291da488469aafa`。最终 onedir 包内的独立 Worker 分别只读加载两个已有模型，在相同公开 MP3 上完成非空识别并生成 TXT/JSON。验证范围包括实际包内 Worker 和 WebSocket。
- 隔离协调器先加载 SenseVoice，再确认切换 Qwen3-ASR。未确认的第二次切换被拒绝。随后加载隔离伪损坏模型，候选失败并恢复 Qwen 的真实 Worker 及默认设置。测试使用单独端口，禁用了原生听写客户端的启动。
- 用户后续要求跳过下载，用两份已有模型继续。该要求前已完成独立 SenseVoice 下载验证：固定源完整 433,798,984 字节，1,310,720 字节时实测暂停并用同一任务继续，修复后复用同一缓存完成安装，重新校验三份文件。未继续安排下载相关桌面场景。 [固定上游资产](https://github.com/HaujetZhao/CapsWriter-Offline/releases/tag/models) [模型来源与许可](V1-模型来源与下载验证-20261122.md)
- 生产配置代码在隔离档案上实测了预览不写盘、预览过期拦截、确认保存并重新读取、导出脱敏、非法热词规则拒绝和外部文件修改保护。四个内置 AI 角色通过真实 OpenAI SDK 对本机模拟 SSE 服务发出请求，合成历史经限额保留三条，导出无测试密钥。手动更新实际访问项目 GitHub API，当前渠道返回 `up_to_date`，无安装资产。网站 Node 检查通过。

## 30 条主用例

`NOT_RUN` 表示完整用例有未执行的关键场景。已经执行的子场景列在同一行。执行范围遵循[主用例](../设计/V1-关键操作验收用例-R1.json)和[冻结边界](../设计/V1-验收边界补充-20261122.md)。

| ID | 结果 | 证据或未执行范围 |
| --- | --- | --- |
| WF-01 | NOT_RUN | 已实测无模型启动和 HTTP 页面。完整原生向导和导航由用户操作 |
| WF-02 | NOT_RUN | Windows 模拟 UI 跑完跳过、继续、重开。原生窗口关开待核对 |
| MD-01 | NOT_RUN | 真实下载器完成固定资产传输。用户后续跳过下载确认 UI 和并行显示 |
| MD-02 | PASS | 真实暂停、同一任务继续、缓存复用。Windows 循环 HTTP 回归覆盖不可信续传重启 |
| MD-03 | PASS | 真实 SHA-256 校验、安装、重新核验。旧模型只读登记，失败及恶意归档由自动化覆盖 |
| MD-04 | PASS | 显式指定 SenseVoice 后，真实 Worker 就绪才提交为默认。向导点击留待用户验收 |
| MD-05 | PASS | 实际确认切换 Qwen，未确认请求被拒，坏候选失败后恢复原 Qwen Worker 和配置。活动听写保护由自动化覆盖 |
| DI-01 | NOT_RUN | 真实麦克风、全局热键、长按/单击及听写和转写竞争待用户操作 |
| DI-02 | NOT_RUN | 输入安全自动化覆盖焦点和归属。托盘客户端、IME、剪贴板和实际焦点待用户操作 |
| DI-03 | NOT_RUN | 编辑状态与迟到回调有自动化覆盖。原生编辑浮层和脱敏截图待用户操作 |
| AI-01 | NOT_RUN | 新档案默认关闭，角色读写回归已过。真实语音触发和授权提供商待用户操作 |
| FT-01 | NOT_RUN | 实测音视频队列和 per-file 状态。听写抢占场景待用户操作 |
| FT-02 | PASS | 实测运行中请求安全停止，观察到 `cancelling` → `cancelled`，后一视频仍完成 |
| FT-03 | NOT_RUN | 实测队列重开读回和同任务重试。意外退出后原生界面重连在途任务待验 |
| FT-04 | PASS | 实测从时间戳重建到独立 SRT，旧结果保留。无有效时间戳拒绝有自动化覆盖 |
| CF-01 | PASS | 隔离档案实测非法规则拒绝、外部修改冲突保持原数据 |
| CF-02 | PASS | 生产接口导出并读回，合成密钥缺席 |
| CF-03 | PASS | 生产接口预览、版本校验、过期拒绝、确认提交再读回 |
| DT-01 | NOT_RUN | 隔离夹具覆盖预览后的变化、越界、归属。真实用户数据不用于清理测试 |
| SV-01 | NOT_RUN | 实测真实 Worker 就绪。原生诊断页发起重启和用户确认待验 |
| SV-02 | NOT_RUN | IPC 模拟证实重开复用后台。原生关闭和下载字节对比按用户要求跳过 |
| SV-03 | NOT_RUN | IPC 安全结束及退出标记已验证。真实托盘完全退出和活跃任务排空待用户操作 |
| UP-01 | PASS | 实际访问官方 GitHub API，当前渠道无较新可验证资产。离线/错误用例自动化通过 |
| UP-02 | NOT_RUN | 最小包和最终制品已构建。当前试用仍持有应用互斥锁，实际覆盖升级未执行 |
| UP-03 | NOT_RUN | 当前试用仍持有应用互斥锁，未启动安装流程。干净环境实装和卸载后数据哈希待核对 |
| UP-04 | NOT_RUN | 网站技术检查通过，未获公开分发授权，下载按钮禁用 |
| WF-03 | NOT_RUN | 真实原生窗口过程和本机页面已验证一部分。完整打包界面故障修复流程待验 |
| SV-04 | NOT_RUN | 未占用用户现役端口来故障注入。自动化覆盖未知端口路径和恢复失败 |
| FT-05 | PASS | 真实 Worker 成功、失败、取消和单项重试后，源媒体、旧同名 TXT/JSON/SRT/MERGE 及首次结果的哈希均不变 |
| CF-04 | PASS | Windows 临时目录中故障注入通过，核对半写入回退和损坏记录保留 |

## G0–G8 门禁和候选制品

| 门禁 | 结果 | 证据或剩余条件 |
| --- | --- | --- |
| G0 本地保护 | PASS | 原快照、虚拟环境与目录隔离已核对，两个既有模型以只读方式使用 |
| G1 模型状态与启动 | NOT_RUN | 真实 Worker 及白屏修复有证据。原生坏模型、旧 PID 和未知端口需集成检查 |
| G2 模型下载可靠 | NOT_RUN | 已覆盖真实下载、校验与超长路径修复。用户随后跳过原生下载/关窗场景 |
| G3 日常听写 | NOT_RUN | 等待用户完成麦克风、热键、IME、所有输出方式和焦点验收 |
| G4 文件转写 | NOT_RUN | 实际 Windows 场景覆盖成功、批量、取消、重试和 SRT。等在途原生重连及听写竞争 |
| G5 数据与配置 | NOT_RUN | 数据保护自动化与生产接口验证通过。等待原生保存刷新及完整历史/日记回归 |
| G6 Windows 发行 | NOT_RUN | 最终 Portable 移动和安装布局默认路径探测通过。实际安装器因试用进程持锁留待运行，未取得干净 Win10/11 验证环境 |
| G7-T 官网技术 | NOT_RUN | Node 静态校验已过，最终本地构建元数据已核对。此次六张 Windows 脱敏实拍待补 |
| G7-P 公开下载 | NOT_RUN | 未发布，公开字节和签名没有验收 |
| G8 证据完整 | NOT_RUN | 已记录执行证据和最终制品。需补用户手动验收、截图以及安装环境证据 |

修复后原生 WebView2 进程与试用控制中心建立实际连接。查询到了 Windows 默认音频输入设备，本轮未采集用户声音。用户接管桌面操作。白屏复现图属于故障证据，通用、模型中心、向导、字幕任务、服务诊断和编辑浮层的脱敏截图待补，旧官网图不作为本次证据。

前置最小包完成 PyInstaller onedir。无模型时，包内协调器启动、返回路径与状态并安全退出。首轮 ZIP 被依赖数据审计拦下，修复后完成完整构建。

最终 Portable 校验了整个 ZIP 的 CRC。解压到中文及空格路径后启动包内协调器，退出并移动整个程序目录重开。子进程清除了 CapsWriter 路径覆盖，观察到两次默认路径与新位置一致，测试数据 SHA-256 保持一致。无 Portable 标记的最终 onedir 使用隔离环境的 `LOCALAPPDATA/CapsWriter` 默认根，程序目录旁未生成数据。安装、升级、卸载验证因用户试用仍持有 `CapsWriter.Offline.ControlCenter` 互斥锁留待执行。

### 本地发行候选

完整构建返回 0。清洁暂存 295 个文件，最终包审计清单含 3,298 个文件。再次读取 EXE 和 ZIP 字节的大小与 SHA-256 均匹配 `release-manifest.json` 和 `SHA256SUMS.txt`。清单仍标为 `candidate`，保留 `windows_validated=false`。

| 文件 | 字节 | SHA-256 | 签名状态 |
| --- | ---: | --- | --- |
| `CapsWriter-Offline-1.0.0-rc.1-Windows-x64-Setup.exe` | 227,436,193 | `304005b19a223fbd00991fbb0441c2a93c2a464e209ccbd50b7ed40d14ddbbbf` | Windows Authenticode `NotSigned` |
| `CapsWriter-Offline-1.0.0-rc.1-Windows-x64-Portable.zip` | 375,281,426 | `fbee7c30ad8ea624bc8cfa5ca591362a32d5277dce2626c81aae45a1c840b2ff` | ZIP 校验和及 CRC 已核对，未单独签名 |

- 构建输出中包含 `_internal/THIRD_PARTY/python-inventory.json`，记录构建虚拟环境中 119 项 Python 分发包的版本和可获取的许可文件。第三方二进制清单位于 `_internal/packaging/vendor_inventory.json`，共 62 个经核验的文件。制品包含程序及依赖资源，SenseVoice 和 Qwen3-ASR 权重仅从本机原目录读取。
- FFmpeg/ffprobe 使用 `n8.1.3-39-g660b5aa7db` Windows x64 LGPL 构建。上游归档 SHA-256 `47d2e37e5ab9dc775cf5e5e595a817a11534c2fd6533a16db476f45dd8a7ce83`。包内 `FFmpeg-LICENSE.txt`，运行时使用 LGPL v3 或更高版本许可。 [固定上游发行](https://github.com/FFmpeg-Builds/FFmpeg-Builds/releases/tag/autobuild-2026-11-21-12-37)
- 三个 GGUF 引擎使用其源码指定的 llama.cpp `b7798` Win x64 Vulkan 发行。上游归档 SHA-256 `d478b7070dd12a5c64478a398352e1f880d488c4c346a8f00e7051935ef6f8e8`。包内附 llama.cpp MIT 和 LLVM OpenMP 许可文件。相关依赖的公开再分发核验仍属发行门禁。 [固定上游发行](https://github.com/ggml-org/llama.cpp/releases/tag/b7798)

### 待用户验收

先在试用窗口登记本地 SenseVoice 并确认加载，用空白记事本试实际快捷键和中文输入。随后测试确认输出、编辑浮层、错焦点保护和托盘重开，保留六张脱敏截图。完全退出试用窗口并确认事务停止后，才能执行本机当前用户的安装、升级和卸载测试。干净 Win10/11、真实 AI 提供商和公开下载各保留未验收状态。
