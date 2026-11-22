# 更新日志

> **交接入口更新（2026-10-27）**：用户已启用网页端规划、本地 Codex 实施、GitHub 文档交接的新协作流程。当前进度请读 [docs/项目管理/当前交接.md](项目管理/当前交接.md)，任务与规划请读 [项目管理入口](项目管理/README.md)。下方 **2026-10-25 本地恢复与当前交接** 是当时的历史快照，保留原文，不再反映实时实施范围。历史 118 项测试不代表此后版本已重新通过。软件版本日志仍在本文件维护。


## 2026-10-25 本地恢复与当前交接

本节是当前交接和本轮开发记录。下方 v2.6 及更早条目为历史版本说明；用户文档不是待办或实施授权。日期均为 Asia/Shanghai。

### 当前范围与阶段

- 目标：维护 Windows 离线听写与桌面控制中心，包括快捷键、浮层、历史、热词、转写、可选 AI 和配置管理。依据 README 与代码恢复现有范围，不将所有可选方向当成已批准需求。
- 本轮明确授权：读取与核对、可恢复的工作区整理、相关验证和只包含本轮文件的本地提交。没有推送、上传、部署或业务服务重启授权。
- 阶段：已完成项目的维护接续，P0 恢复 → P5 有限验证 → P7 文档交付，待用户验收。用户在本轮明确更正：项目已经完成，目前处于维护状态，因此没有业务实施计划或待办；这不是阻塞，也不据此创建新功能任务。没有既有项目交接，当前事实在本节接续。
- 网页端方案：本轮未提供；用户已确认决定：维护状态及本轮恢复与整理边界；客户端事实：以下检查、修改与证据。用户所述项目完成与本轮真实环境未验收分别记录，建议不视为已确认决定。

### Git 与原有改动

恢复基点：项目根 `D:\Project\CapsWriter-Offline-Dev`，分支 `master`，HEAD `3a99d8939b25052291ee4415d4ceeae57dc60b55`；进入本轮时暂存区为空。该提交之后的原有工作区状态独立保留：

- 修改：`.gitignore`、`readme.md`。
- 删除：`tools/build_release_variants.py`、`tools/enhance_small_icon.py`，均在进入本轮前已删除，不是本轮删除成果。
- 未跟踪：`build.spec`、`docs/开发指南.md`、`tests/e2e/test_runner.py`、四个 `test_tier*.py`。没有把这些原有文件纳入本轮提交。

### 已实现与证据边界

- 已阅读启动器 `run_app.py`、配置管理、转写服务/历史、快捷键浮层接线及 WebSocket 连接实现。代码具备服务/客户端/GUI 协调、设置和历史入口、模型配置、热词、文件转写和可选 AI 能力；这是实现存在的证据，不能替代运行验收。
- 历史上已经修改的历史条数限制、默认保留历史和冷启动连接重试在当前代码中仍有实现，不重复开发。此处没有将旧提交信息或历史日志作为本轮测试通过。
- 本轮环境：Windows，Python 3.11.9。已用 `importlib.util.find_spec` 检查 httpx、openai、rich、nicegui、webview、numpy、sounddevice、sherpa_onnx 可发现；未据此宣称模型可加载或环境全量通过。
- 本轮执行 `python -B -m tests.e2e.test_runner --json-report .agentspec/backups/20261025-context-recovery/e2e-report.json`：退出码 0，118/118 通过，约 64.45 秒。使用现有未跟踪测试、回环 HTTP/SSE 模拟服务、临时配置及部分测试辅助实现；覆盖 LLM/上下文等有限范围，不是完整桌面端到端验收。
- 本轮 AST 检查：对 core、web_gui、LLM、tests、tools 和三个启动入口的 310 个 Python 文件进行内存解析，未发现语法错误；不导入业务启动器，不生成字节码。文档检查复核相对链接并处理 URL 编码，不把 `%20` 误判为失效链接。
- 本轮整理校验命令：`python -B .agentspec/backups/20261025-context-recovery/verify_recovery.py`，原有 11 项状态/字节不变、13 份备份哈希一致、27 处本地文档链接有效，无控制字符或语法错误。脚本和 `final-audit.json` 仅留本机；`git diff --check` 通过。本次改动仅涉及文档，不重复运行已通过且未受影响的业务测试。
- 未验证：真实浏览器设置保存/刷新/异常提示、真实模型/麦克风/听写上屏、转写输出、真实 AI 服务、原生窗口行为、打包与用户验收。本轮没有修改网页或业务行为，也没有启动/重启服务。

### 整理与 Skill 可读性

- 根 `AGENTS.md` 原先不存在；新增轻量导航，当前交接映射到本节，沿用现有 CHANGELOG 记录，不复制一套 spec/plan/tasks。
- AgentSpec 核心位于共享 `D:\Project\_expertise\skills\agentspec\SKILL.md`，核心、用户协作习惯、开发流程、技能选择和交接与证据均已实际读取。核心未在当前宿主技能清单单列，使用方式是显式读取，不宣称自动发现或钩子加载已验证。
- `docs` 在技能选择中登记为候选，本机入口 `C:\Users\reina\.agents\skills\docs\SKILL.md` 存在、当前 Codex 清单暴露且已实际读取，用于文档导航；其中其他项目的目录不适用于本项目。`git-workflow` 的 `C:\Users\reina\.cc-switch\skills\git-workflow\SKILL.md` 同样可用且已读取，用于隔离本地提交。可读不等于业务执行效果通过；未安装或启用扩展。缺失时按基础文件/Git 工具降级并说明限制。
- 检查了 docs、代码、运行目录和打包调用关系。当前没有 `doc/`、`.venv/`、`.agentspec/` 既有目录或独立需求素材；没有据名称或年龄删除内容。保留 docs 中的使用说明与历史资料，通过本入口分清用途，不移动它们以免破坏引用。
- 修复 `docs/显卡加速的若干问题.md` 的缺失截图引用，保留历史性能说法并明确未在本轮复测。
- 开发指南中的泛称路径需按代码解读：转写结果是 `web_gui/outputs/`，配置备份/导出是 `web_gui/config_backups/`、`web_gui/config_exports/`。未修改该原有未跟踪指南；指南对两个已删除脚本的描述不构成本轮已核验删除安全的结论。
- 没有从活跃目录移除文件。原有 11 项改动的当前字节（原有删除项保存 HEAD 字节）、两份本轮修改文档的原字节、路径、大小、SHA-256、初始 Git 状态和恢复说明保存在本机 `.agentspec/backups/20261025-context-recovery/`，逐项哈希校验；`manifest.json` 与 `RESTORE.txt` 指明恢复来源。不要整批覆盖后续改动，也不要将 `deleted-head/` 当作原先仍在磁盘的文件。
- 本机 `.git/info/exclude` 增加 `/.agentspec/`，原排除文件已备份，`git check-ignore` 确认生效。快照、日志、测试报告与私有配置不入库、不上传。

### 下一步与验收

1. 用户检查根 AGENTS 的导航及本节事实；确认原有改动仍在。文档验收无需启动服务，预期各相对链接可定位到正确文件，历史与本轮证据分开。
2. 保持维护状态，目前没有业务实施待办。后续按用户报告的具体问题恢复相关代码与证据、最小修复并回归；不为填补计划而新增功能。无需用户补交并不存在的实施计划。
3. 原有未提交整理和五个测试文件如需纳入后续交付，先单独审查其来源、覆盖生产实现的程度及删除项恢复证据，不混入本轮文档提交。
4. 本轮代码版本为上述基点加本轮文档提交；使用 `git log -1 --oneline -- AGENTS.md docs/CHANGELOG.md` 定位交付提交。提交只包含根 AGENTS、本节及缺失图片说明。状态为客户端自检完成、用户未验收，提交不代表用户接受。

## v2.6

- **GPU 预加速**：服务端新配置（默认关闭），适用于 Nvidia 独显，录音开始时主动锁定显存高频率，大幅降低模型转录延迟至 0.1s
- **标点处理优化**：仅对 8 词以内的结果去除末尾标点，长句不再误伤；可通过配置 config_client.py 指定某些程序强制去除标点
- **带延迟的附加回车**：用于同花顺等场景，输入股票名后等待 0.5s 自动回车切换，可在 config_client.py 配置
- **角色功能简化**：角色功能简化为单一开关；默认角色改用 DeepSeek API，方便新用户填入密钥后直接体验
- **ITN 增强**：连续多数字转换更鲁棒
- **活动窗口日志**：每次识别后通过 debug 日志输出当前活动窗口名，方便用户配置 paste_apps
- **标点模型容错**：取消强制检查，加载失败时静默回退，避免启动受阻
- **热词使用体验**：右键菜单点击「热词」直接打开 hot.txt 文件，不再弹窗
- **重启菜单**：托盘右键新增「重启」选项，便于修改配置后快速重启生效
- **集成显卡兼容**：加入调试性配置项，集显出问题时可通过禁用 feature 解决
- **Windows Terminal 适配**：支持隐藏 Windows Terminal 控制台窗口

## v2.5

- **引入 [Qwen3-ASR-1.7B](https://github.com/HaujetZhao/Qwen3-ASR-GGUF)**：140ms 极速推理，准确率夯爆。Decoder Vulkan 加速默认打开，需占 1.6GB 显存。显卡空闲时，会降低显存频率，冷启动转录延迟升至 300ms。若用管理员权限运行 `nvidia-smi -lmc 9000` 锁定显存不降频，实测 RTX5050 转录延迟可降至 100ms
- **集成 Force Aligner**：辅助 Qwen3-ASR 支持时间戳，按需加载、超时释放，仅文件转录时占用资源
- **热词别名**：热词支持用 `|` 分隔定义多个别名，
- **角色别名**：`name` 用 `|` 分隔定义多个别名，解决 ASR 对角色名识别不准的问题
- **移除纠错历史**：热词别名已能覆盖纠错历史的需求，移除 `hot-rectify.txt` 及相关逻辑
- **文件转录支持热词**：文件转录现在也可以使用热词功能
- **语言配置**：`config_client.py` 新增 `language` 选项，支持指定识别目标语言
- **架构重构**：进行了大量重构，方便后续维护
- **日志优化**：只保留一份日志文件

## v2.4

- **改进 [Fun-ASR-Nano-GGUF](https://github.com/HaujetZhao/Fun-ASR-GGUF) 模型，使 Encoder 支持通过 DML 用显卡（独显、集显均可）加速推理，Encoder 和 CTC 默认改为 FP16 精度，以便更好利用显卡算力**，短音频延迟最低可降至 200ms 以内。
  - 若用管理员权限运行 `nvidia-smi -lmc 9000` 锁定显存不降频，实测 RTX5050 转录延迟可降至 100ms
- 服务端 Fun-ASR-Nano 使用单独的热词文件 hot-server.txt ，只具备建议替换性，而客户端的热词具有强制替换性，二者不再混用
- 可以在句子的开头或结尾说「逗号、句号、回车」，自动转换为对应标点符号，支持说连续多个回车。
- Fun-ASR-Nano 加入采样温度，避免极端情况下的因贪婪采样导致的无限复读
- 服务端字母拼写合并处理

## v2.3

- **引入 [Fun-ASR-Nano-GGUF](https://github.com/HaujetZhao/Fun-ASR-GGUF) 模型支持，推理更轻快**
- 重构了大文件转录逻辑，采用异步流式处理
- 优化中英混排空格
- 增强了服务端对异常断连的清理逻辑

## v2.2

- **改进热词检索**：将每个热词的前两个音素作为索引进行匹配，而非只用首音素索引。
- **UDP广播和控制**：支持将结果 UDP 广播，也可以通过 UDP 控制客户端，便于做扩展。
- **Toast窗口编辑**：支持对角色输出的 Toast 窗口内容进行编辑。
- **多快捷键**：支持设置多个听写键，以及鼠标快捷键，通过 pynput 实现。
- **繁体转换**：支持输出繁体中文，通过 zhconv 实现。

## v2.1

- **更强的模型**：内置多种模型可选，速度与准确率大幅提升。
- **更准的 ITN**：重新编写了数字 ITN 逻辑，日期、分数、大写转换更智能。
- **RAG 检索增强**：热词识别不再死板，支持音素级的 fuzzy 匹配，就算发音稍有偏差也能认出。
- **LLM 角色系统**：集成大模型，支持润色、翻译、写作等多种自定义角色。
- **纠错检索**：可记录纠错历史，辅助LLM润色。
- **托盘化运行**：新增托盘图标，可以完全隐藏前台窗口。
- **完善的日志**：全链路日志记录，排查问题不再抓瞎。


## v1.0 

- 通过分段识别和去重，实现了支持无限时长语音的转写
- 客户端支持转写音视频文件为 srt 字幕，只需将音视频文件拖动到客户端 exe 上打开即可

## v0.6 


- 新增日记功能，将每日的录音结果保存在一个 Markdown 文件中
- 新增关键词日记功能，每日的以关键词开头的录音结果会保存在特别的 Markdown 文件中
- 新建录音文件夹的时候，会复制一个 Python 辅助脚本，用于清理没有被 Markdown 文件引用的附件，这样一来，通过编辑 Markdown 日记就可以清理不需要保存的录音
- 新增定义录音文件保存目录
- 默认保存48000采样率高品质录音录音，如果用户安装了 FFmpeg 则保存为 mp3 格式，否则保存为 wav 格式
- 输入方式改为模拟 Ctrl + V 粘贴，粘贴完后恢复剪贴板内容
- 使用 rich 库输出彩色文字，尽量在各种终端达到一致的显示效果

## v0.5 

- 修改热词文件后，不用重启客户端，就可以动态更新热词了。

## v0.4

- 为客户端加入了三种热词功能：中文、英文、自定义
- 改进了对中文数字的搜索，当数字的左侧或者右侧有英文时，就一定会被选中。
- 改进了中英空格排版，能够正常输出 iPhone 4s 这样的词语。


## v0.3 

- 客户端当音频设备名不可 utf-8 解码时，不再闪退
- 客户端添加配置可以编辑修改，要消除识别结果末尾哪些标点
- 客户端添加配置可以修改快捷键
- 客户端添加配置可以修改快捷键触发的时间阈值
- 客户端连接中断会自动进行重试
- 客户端提示当前所用的快捷键
- 服务端对识别结果，中英文混排进行空格校正
- 当地址无法被绑定时提示问题，而不是直接闪退


## 起源

**2020年10月**，因手机上的语音输入法很好用，但电脑上却没有足够好用的语音输入法，我手写了一个工具 **[CapsWriter](https://github.com/HaujetZhao/CapsWriter)**，通过长按大写锁定键录音，松开后，调用阿里云的一句话识别 API，识别后上屏。12月的时候，还加入图形化界面。但当时大学宿舍的校园网经常抽风，没有稳定的网络环境，转录延迟飘忽不定。有时候说完话了，等了好几秒，才发现 WiFi 没有连接，毁心态。但是当时并没有识别率能满足要求的离线中文 ASR 模型。

后来 Whisper 发布，中文识别率确实不错，但是模型延迟很高，无法满足本地语音输入。

到了**2023年5月**，在B站看到了 [极客湾](https://space.bilibili.com/25876945) 的视频 [我们做了个能对话的AI派蒙，免费给大家玩！](https://www.bilibili.com/video/BV1bm4y117ba/) ，他们实现的效果非常好，其中提到 ASR 模型用了阿里 [FunASR](https://github.com/modelscope/FunASR) 团队发布的开源 [Paraformer](https://www.modelscope.cn/models/iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8358-tensorflow1) 模型，于是就拿来测试了下，果然识别准确率很棒、延迟超低，于是弃坑 **[CapsWriter](https://github.com/HaujetZhao/CapsWriter)** ，立马新开了 **[CapsWriter-Offline](https://github.com/HaujetZhao/CapsWriter-Offline)** ，用 [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) 调用 Paraformer 进行转录，效果非常好。

**2025年12月09日**，我看到阿里 [FunASR](https://github.com/modelscope/FunASR) 团队开源了 [Fun-ASR-Nano](https://www.modelscope.cn/models/FunAudioLLM/Fun-ASR-Nano-2512) 模型，拿来一测，果然准确率又上一个台阶，在 sherpa_onnx 支持后，我赶紧更新了 CapsWriter-Offline v2.1，一次性加入了 SenseVoice-Small 和 Fun-ASR-Nano 模型的支持。但问题是当时 sherpa_onnx 是用 ONNX 实现的 Fun-ASR-Nano，速度有些慢。

我研究了 Fun-ASR-Nano 的架构，发现它的 Decoder 是 LLM 架构，而推理 LLM 最快的是 [LLama.cpp](https://github.com/ggml-org/llama.cpp) 。随后实现了 [Fun-ASR-GGUF](https://github.com/HaujetZhao/Fun-ASR-GGUF)，用 onnx 和 gguf 格式混合运行 Fun-ASR-Nano，并用 [LLama.cpp](https://github.com/ggml-org/llama.cpp) 加速它的 LLM Decoder 部分，在我的笔记本上实现了最快的推理速度：

| 设备 | RTF |
|------|-----|
| GPU RTX5050  | 0.025 |
| CPU U9-285H  | 0.1 |

**2026年01月21日**，[Qwen3-TTS](https://www.modelscope.cn/collections/Qwen/Qwen3-TTS) 开源发布了，生成效果极其优异，让我特别想要，但官方版本推理速度很慢，于是基于 [Fun-ASR-GGUF](https://github.com/HaujetZhao/Fun-ASR-GGUF) 的加速推理经验，又实现了 [Qwen3-TTS-GGUF](https://github.com/HaujetZhao/Qwen3-TTS-GGUF) ，也是用 [LLama.cpp](https://github.com/ggml-org/llama.cpp) 加速它的 LLM Decoder 部分。

**2026年01月28日**，间隔没几天，[Qwen3-ASR](https://www.modelscope.cn/collections/Qwen/Qwen3-ASR) 开源发布了，本来没抱太大预期的，但下载 1.7B 一测后，又给我震惊了，准确率竟比 Fun-ASR-Nano 还上一个台阶，能吊打闭源模型！于是在 [Qwen3-TTS-GGUF](https://github.com/HaujetZhao/Qwen3-TTS-GGUF) 经验的帮助下，我马不停蹄地实现了 [Qwen3-ASR-GGUF](https://github.com/HaujetZhao/Qwen3-ASR-GGUF) 加速推理，实现了最快的推理速度：

| 设备 | RTF |
|------|-----|
| GPU RTX5050  | 0.05 |
| CPU U9-285H  | 0.2 |

这就是 CapsWriter-Offline 大致的来路。总体就是这几个组件：

- 模型推理
- 热词替换
- 按键监听与录音

当前主要提供 Windows 打包，是因为本项目目前围绕 Windows 桌面听写、快捷键监听和托盘控制进行适配；其他系统需要根据各自的输入法、全局快捷键和桌面通知机制做额外调整。
