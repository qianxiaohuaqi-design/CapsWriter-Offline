# coding: utf-8
"""
CapsWriter 智能控制中心 - 全功能自动化自测试与故障诊断系统 (Auto Self-Test & Diagnostic Suite)

测试范围：
1. ASR 服务端 (6016) 状态与 WebSocket 联通
2. 听写客户端配置与按键映射
3. 控制中心 WebUI (6017) 与进程管理健康状态
4. 听写历史 JSONL / 日记存储与读取合规性
5. 媒体文件字幕转写 (SRT/TXT/JSON) 引擎测试
6. 配置导出/导入/全量恢复原子性
7. 热词库 (hot.txt) 与正则表达式 (hot-rule.txt) 规则校验
8. AI 润色配置与私密 API Key 掩码安全
9. Windows 修饰键 (Ctrl/Alt/Shift) 系统级复位测试
10. 后台进程树查找与零残留清理测试
11. 系统全量日志异常与 Traceback 深度诊断
"""

import sys
import time
import json
import tempfile
import ast
from types import SimpleNamespace
from pathlib import Path
from typing import List
from unittest.mock import patch

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

# 强制 Windows 控制台使用 UTF-8 输出
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

# 彩色终端输出
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"


class TestRunner:
    def __init__(self):
        self.passed_tests = 0
        self.failed_tests = 0
        self.warnings = 0
        self.issues_found: List[str] = []

    def log_header(self, title: str):
        print(f"\n{BOLD}{CYAN}=== [{title}] ==={RESET}")

    def log_pass(self, name: str, detail: str = ""):
        self.passed_tests += 1
        msg = f"{GREEN}[PASS]{RESET} {name}"
        if detail:
            msg += f" - {detail}"
        print(msg)

    def log_fail(self, name: str, reason: str):
        self.failed_tests += 1
        self.issues_found.append(f"{name}: {reason}")
        print(f"{RED}[FAIL]{RESET} {name} - {reason}")

    def log_warn(self, name: str, msg: str):
        self.warnings += 1
        print(f"{YELLOW}[WARN]{RESET} {name} - {msg}")


def test_unit_1_asr_server(runner: TestRunner):
    runner.log_header("Unit 1: ASR 服务端 6016 端口与状态")
    from web_gui import process_manager
    port_open = process_manager.is_port_open('127.0.0.1', 6016)
    if port_open:
        pid = process_manager.find_listening_pid(6016)
        runner.log_pass("6016 端口监听测试", f"服务在线，PID: {pid or '未知'}")
    else:
        runner.log_warn("6016 端口监听测试", "服务端未启动 (可通过 UI 或 start_server.py 拉起)")


def test_unit_2_client_config(runner: TestRunner):
    runner.log_header("Unit 2: 听写客户端配置与按键映射校验")
    try:
        from config_client import ClientConfig
        shortcuts = getattr(ClientConfig, 'shortcuts', [])
        if not shortcuts:
            runner.log_fail("客户端快捷键配置", "shortcuts 配置列表为空")
            return
        enabled_count = sum(1 for s in shortcuts if s.get('enabled'))
        runner.log_pass("快捷键结构校验", f"已配置 {len(shortcuts)} 个按键，启用 {enabled_count} 个")

        enabled_shortcuts = [s for s in shortcuts if s.get('enabled')]
        unsafe_keys = {
            'alt', 'alt_l', 'alt_r', 'alt_gr',
            'ctrl', 'ctrl_l', 'ctrl_r',
            'shift', 'shift_l', 'shift_r',
        }
        active_unsafe = [
            s.get('key') for s in enabled_shortcuts
            if s.get('type') == 'keyboard' and str(s.get('key', '')).lower() in unsafe_keys
        ]
        if active_unsafe:
            runner.log_fail("菜单栏误触回归", f"启用了裸修饰键: {active_unsafe}")
        else:
            runner.log_pass("菜单栏误触回归", "未启用会激活应用菜单栏的裸 Alt/Ctrl/Shift")

        unsuppressed_caps = [
            s.get('key') for s in enabled_shortcuts
            if str(s.get('key', '')).lower() == 'caps_lock' and not s.get('suppress')
        ]
        if unsuppressed_caps:
            runner.log_fail("CapsLock 阻塞配置", "CapsLock 听写键未开启 suppress")
        else:
            runner.log_pass("CapsLock 阻塞配置", "长按听写不会切换 CapsLock 状态")

        required_paste_apps = {
            'chrome.exe', 'msedge.exe', 'firefox.exe', 'brave.exe',
            'chatgpt.exe', 'gemini.exe',
        }
        paste_apps = {str(app).lower() for app in getattr(ClientConfig, 'paste_apps', [])}
        missing_paste_apps = sorted(required_paste_apps - paste_apps)
        if missing_paste_apps:
            runner.log_fail("网页输入兼容回归", f"以下应用未强制使用粘贴模式: {missing_paste_apps}")
        else:
            runner.log_pass("网页输入兼容回归", "主流浏览器、ChatGPT 和 Gemini 均使用粘贴模式")

        threshold = getattr(ClientConfig, 'threshold', 0.3)
        if isinstance(threshold, (int, float)) and threshold > 0:
            runner.log_pass("触发阈值校验", f"当前长按阈值: {threshold}s")
        else:
            runner.log_fail("触发阈值校验", f"非法阈值设置: {threshold}")

        from core.client.shortcut.key_mapper import KeyMapper
        key_samples = {0x41: 'a', 0x4C: 'l', 0x5A: 'z', 0x30: '0', 0x39: '9'}
        incorrect_keys = {
            hex(vk): KeyMapper.vk_to_name(vk)
            for vk, expected in key_samples.items()
            if KeyMapper.vk_to_name(vk) != expected
        }
        if incorrect_keys:
            runner.log_fail("组合键主键映射", f"物理按键映射异常: {incorrect_keys}")
        else:
            runner.log_pass("组合键主键映射", "Ctrl 等修饰键按下时，A-Z/0-9 仍按物理键稳定识别")

        from core.client.shortcut.key_mapper import WM_KEYDOWN
        from core.client.shortcut.shortcut_manager import ShortcutManager

        class FakeListener:
            def __init__(self):
                self.suppressed = 0

            def suppress_event(self):
                self.suppressed += 1

        class FakeHandler:
            def __init__(self):
                self.keydowns = []

            def handle_keydown(self, key, task):
                self.keydowns.append(key)
                task.is_recording = True

        class FakeEmulator:
            @staticmethod
            def is_emulating(_key):
                return False

        def make_combo_manager():
            manager = ShortcutManager.__new__(ShortcutManager)
            task = SimpleNamespace(is_recording=False, shortcut=SimpleNamespace(suppress=True))
            manager.tasks = {}
            manager.combo_tasks = {'ctrl+l': task}
            manager._pressed_keys = set()
            manager._restoring_keys = set()
            manager._emulator = FakeEmulator()
            manager._event_handler = FakeHandler()
            manager.keyboard_listener = FakeListener()
            return manager

        combo_manager = make_combo_manager()
        combo_filter = combo_manager.create_keyboard_filter()
        combo_filter(WM_KEYDOWN, SimpleNamespace(vkCode=0xA2))
        combo_filter(WM_KEYDOWN, SimpleNamespace(vkCode=0x4C))
        ctrl_first_ok = (
            combo_manager._event_handler.keydowns == ['ctrl+l']
            and combo_manager.keyboard_listener.suppressed == 1
        )

        reverse_manager = make_combo_manager()
        reverse_filter = reverse_manager.create_keyboard_filter()
        reverse_filter(WM_KEYDOWN, SimpleNamespace(vkCode=0x4C))
        reverse_filter(WM_KEYDOWN, SimpleNamespace(vkCode=0xA2))
        reverse_order_safe = not reverse_manager._event_handler.keydowns

        if ctrl_first_ok and reverse_order_safe:
            runner.log_pass("组合键顺序回归", "Ctrl→L 正常触发并抑制 L；L→Ctrl 不会在字符泄漏后误启动")
        else:
            runner.log_fail(
                "组合键顺序回归",
                f"ctrl_first={combo_manager._event_handler.keydowns}, reverse={reverse_manager._event_handler.keydowns}",
            )
    except Exception as e:
        runner.log_fail("客户端配置导入", str(e))


def test_unit_3_webui_and_health(runner: TestRunner):
    runner.log_header("Unit 3: 控制中心 WebUI (6017) 与健康状态探测")
    try:
        from web_gui import process_manager
        health = process_manager.get_health_status()
        runner.log_pass("健康状态探测接口", f"Server={health['server_alive']}, Client={health['client_alive']}")
        
        gui_open = process_manager.is_port_open('127.0.0.1', 6017)
        if gui_open:
            pid = process_manager.find_listening_pid(6017)
            runner.log_pass("6017 GUI 端口测试", f"GUI 窗口在线，PID: {pid or '未知'}")
        else:
            runner.log_warn("6017 GUI 端口测试", "GUI 未在运行，属于正常离线状态")

        app_source = (BASE_DIR / 'web_gui' / 'app.py').read_text(encoding='utf-8')
        app_tree = ast.parse(app_source)

        def root_call(node):
            while (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in {'props', 'classes', 'tooltip', 'style'}
            ):
                node = node.func.value
            return node if isinstance(node, ast.Call) else None

        def is_button(node):
            call = root_call(node)
            return bool(call and isinstance(call.func, ast.Attribute) and call.func.attr == 'button')

        assigned_buttons = {}
        late_bound_buttons = set()
        button_calls = []
        for node in ast.walk(app_tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and is_button(node.value):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name):
                        assigned_buttons[node.lineno] = target.id
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and (
                    node.func.attr == 'on_click'
                    or (
                        node.func.attr == 'on'
                        and node.args
                        and isinstance(node.args[0], ast.Constant)
                        and node.args[0].value == 'click'
                    )
                )
                and isinstance(node.func.value, ast.Name)
            ):
                late_bound_buttons.add(node.func.value.id)
            if isinstance(node, ast.Call) and root_call(node) is node and is_button(node):
                inline_callback = any(keyword.arg == 'on_click' for keyword in node.keywords)
                button_calls.append((node.lineno, inline_callback, assigned_buttons.get(node.lineno)))

        missing_callbacks = [
            line
            for line, inline_callback, variable in button_calls
            if not inline_callback and (not variable or variable not in late_bound_buttons)
        ]
        if missing_callbacks:
            runner.log_fail("前端按钮回调覆盖", f"这些 ui.button 未绑定点击回调: {missing_callbacks}")
        else:
            runner.log_pass("前端按钮回调覆盖", f"{len(button_calls)} 个按钮均已绑定点击回调")

        engine_tokens = [
            'MODEL_LABELS',
            'MODEL_ARG_CLASSES',
            'model_install_status',
            'language_options()',
            'normalize_language_code',
            "'fun_asr_nano'",
            "'qwen_asr'",
            '打开模型目录',
            '高级运行参数',
        ]
        if all(token in app_source for token in engine_tokens) and "'fun_asr_nano_gguf': 'FunASR-Nano" not in app_source and "'qwen3_asr_gguf': 'Qwen3-ASR" not in app_source:
            runner.log_pass("语音引擎 UI 模型映射", "模型选择使用服务端真实 model_type，并按本地模型文件检测生成选项")
        else:
            runner.log_fail("语音引擎 UI 模型映射", "模型值、安装检测或高级设置入口缺失，可能再次保存服务端不支持的 model_type")

        if 'from core.server.engines.language import LANGUAGE_MAP' in app_source and 'for code in LANGUAGE_MAP' in app_source:
            runner.log_pass("语音语言列表覆盖", "UI 语言选项来自原项目统一 LANGUAGE_MAP，而不是手写 5 个短代码")
        else:
            runner.log_fail("语音语言列表覆盖", "UI 未直接复用统一 LANGUAGE_MAP，可能遗漏 Qwen3-ASR 多语言")
    except Exception as e:
        runner.log_fail("健康状态检查", str(e))


def test_unit_4_input_history(runner: TestRunner):
    runner.log_header("Unit 4: 输入历史 JSONL 存储与读取合规性")
    try:
        from core.client.output.input_history import append_input_history, load_input_history, HISTORY_PATH
        
        test_text = f"自测试写入文本_{int(time.time())}"
        append_input_history(
            test_text,
            original_text=test_text,
            process_name="auto_self_test.py",
            paste=False
        )

        records = load_input_history(limit=80)
        found = any(r.get('text') == test_text for r in records)

        if found:
            runner.log_pass("输入历史写入与载入", f"实时数据已成功持久化并重绘 ({HISTORY_PATH.name})")
        else:
            runner.log_fail("输入历史写入与载入", "写入后在 load_input_history() 中未找到最新条目")
    except Exception as e:
        runner.log_fail("输入历史模块", str(e))


def test_unit_5_transcription_engine(runner: TestRunner):
    runner.log_header("Unit 5: 媒体文件字幕转写 (SRT/TXT/JSON) 引擎测试")
    test_wav = BASE_DIR / "测试" / "speech_test.wav"
    if not test_wav.exists():
        runner.log_warn("字幕转写测试素材", f"缺少测试素材文件: {test_wav}")
        return

    try:
        import asyncio
        from web_gui.transcription_service import regenerate_srt_from_txt, transcribe_file
        result = asyncio.run(transcribe_file(test_wav))
        if result.ok and result.output_files:
            formats = list(result.output_files.keys())
            runner.log_pass("字幕转写引擎", f"成功生成转写格式: {formats}, 输出目录: {result.output_dir.name if result.output_dir else '未知'}")
        else:
            runner.log_fail("字幕转写引擎", f"转写失败或输出文件为空: {result.message}")

        app_source = (BASE_DIR / 'web_gui' / 'app.py').read_text(encoding='utf-8')
        service_source = (BASE_DIR / 'web_gui' / 'transcription_service.py').read_text(encoding='utf-8')
        history_source = (BASE_DIR / 'web_gui' / 'transcription_history.py').read_text(encoding='utf-8')
        ui_tokens = ['转写设置', '批量转写', 'merge.txt 全文', '字幕修复', 'delete_transcription_output', 'show_history_actions']
        result_handler_source = (BASE_DIR / 'core' / 'client' / 'transcribe' / 'result_handler.py').read_text(encoding='utf-8')
        service_tokens = ['def regenerate_srt_from_txt', 'srt_from_txt.one_task', 'def get_media_tool_status']
        output_flag_tokens = [
            'Config.file_save_srt',
            'Config.file_save_txt',
            'Config.file_save_json',
            'Config.file_save_merge',
        ]
        state_tokens = ['clear_current_transcription_state', 'setattr(LiveClientConfig, key, value)']
        if (
            all(token in app_source for token in ui_tokens + state_tokens)
            and all(token in service_source for token in service_tokens + output_flag_tokens)
            and all(token in result_handler_source for token in ['cleanup_targets'] + output_flag_tokens)
            and "files['merge']" in history_source
        ):
            runner.log_pass("字幕转写 GUI 能力覆盖", "输出格式、merge 历史、环境状态、TXT 重建 SRT、单条删除、实时配置与清除当前状态已接入")
        else:
            runner.log_fail("字幕转写 GUI 能力覆盖", "字幕转写页面缺少底层功能入口或历史 merge 识别")
    except Exception as e:
        runner.log_fail("字幕转写引擎", str(e))


def test_unit_6_config_backup(runner: TestRunner):
    runner.log_header("Unit 6: 配置全量导出/导入/恢复原子性测试")
    try:
        from web_gui.config_manager import ConfigManager
        
        # 1. 导出测试
        export_path = ConfigManager.export_full_config_to_file()
        if export_path and export_path.exists():
            runner.log_pass("全量配置导出", f"已导出备份: {export_path.name}")
            
            # 2. Schema 校验
            content = export_path.read_text(encoding='utf-8')
            data = json.loads(content)
            if isinstance(data, dict) and 'configs' in data:
                runner.log_pass("备份 JSON 校验", "备份数据结构规范完整 (含 configs, hot_words_content 等)")
            else:
                runner.log_fail("备份 JSON 校验", "导出的 JSON 缺少核心节点")
        else:
            runner.log_fail("全量配置导出", "导出路径无效或文件未生成")

        # 3. 自动备份历史列表
        backups = ConfigManager.list_config_backups()
        runner.log_pass("配置自动备份历史", f"找到 {len(backups)} 条历史备份文件")

        # UI 的数字输入会写入浮点值；曾因替换串被解析成 \10 而保存失败。
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_config = Path(temp_dir) / 'config_client.py'
            temp_config.write_text(
                "class ClientConfig:\n"
                "    paste_apps = [\n"
                "        'WeiXin.exe',\n"
                "        'chrome.exe',\n"
                "    ]\n"
                "    save_diary = False\n"
                "    output_destination = 'typing'\n"
                "    hot_thresh = 0.85\n",
                encoding='utf-8',
            )
            with patch('web_gui.config_manager.CONFIG_CLIENT_PATH', temp_config), \
                 patch.object(ConfigManager, 'write_text_with_backup', lambda path, content: path.write_text(content, encoding='utf-8') is not None):
                loaded_apps = ConfigManager.get_client_var('paste_apps', [])
                ConfigManager.set_client_var('paste_apps', ['WeiXin.exe', 'chrome.exe', 'probe.exe'])
                ConfigManager.set_client_var('hot_thresh', 0.84)
                ConfigManager.set_client_var('save_diary', True)
                ConfigManager.set_client_var('output_destination', 'overlay_preview')
                saved_value = ConfigManager.get_client_var('hot_thresh', None)
                saved_diary = ConfigManager.get_client_var('save_diary', None)
                saved_output_destination = ConfigManager.get_client_var('output_destination', None)
                compile(temp_config.read_text(encoding='utf-8'), str(temp_config), 'exec')
                saved_apps = ConfigManager.get_client_var('paste_apps', [])
            if loaded_apps == ['WeiXin.exe', 'chrome.exe'] and saved_value == 0.84 and saved_diary is True and saved_apps[-1:] == ['probe.exe']:
                runner.log_pass("配置控件回调", "多行应用列表、听写日记开关与热词阈值均可正确写入并读回")
            else:
                runner.log_fail("配置控件回调", f"写入后读回异常: apps={saved_apps}, threshold={saved_value}, diary={saved_diary}")

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_server = Path(temp_dir) / 'config_server.py'
            temp_server.write_text(
                "class SenseVoiceArgs:\n"
                "    onnx_provider = 'CPU'\n"
                "    dml_pad_to = 30\n\n"
                "class FunASRNanoGGUFArgs:\n"
                "    onnx_provider = 'CPU'\n"
                "    llm_use_gpu = False\n",
                encoding='utf-8',
            )
            with patch('web_gui.config_manager.CONFIG_SERVER_PATH', temp_server), \
                 patch.object(ConfigManager, 'write_text_with_backup', lambda path, content: path.write_text(content, encoding='utf-8') is not None):
                ConfigManager.set_server_class_var('FunASRNanoGGUFArgs', 'onnx_provider', 'DML')
                fun_provider = ConfigManager.get_server_class_var('FunASRNanoGGUFArgs', 'onnx_provider', None)
                sense_provider = ConfigManager.get_server_class_var('SenseVoiceArgs', 'onnx_provider', None)
            if fun_provider == 'DML' and sense_provider == 'CPU':
                runner.log_pass("服务端类级参数写入", "ONNX/GGUF 高级参数可精确写入目标模型类，不会误改同名字段")
            else:
                runner.log_fail("服务端类级参数写入", f"类级字段写入串位: SenseVoice={sense_provider}, FunASR={fun_provider}")

        result_processor = (BASE_DIR / 'core' / 'client' / 'output' / 'result_processor.py').read_text(encoding='utf-8')
        if "Config.save_audio" in result_processor and "save_diary" in result_processor:
            runner.log_pass("录音与日记开关分离", "录音保存与 .md 听写日记由独立配置控制")
        else:
            runner.log_fail("录音与日记开关分离", "未找到独立的 save_audio/save_diary 控制逻辑")
    except Exception as e:
        runner.log_fail("配置备份模块", str(e))


def test_unit_7_hotwords_and_rules(runner: TestRunner):
    runner.log_header("Unit 7: 热词库与正则表达式修正规则校验")
    hot_txt = BASE_DIR / "hot.txt"
    hot_rule_txt = BASE_DIR / "hot-rule.txt"

    if hot_txt.exists():
        hotword_text = hot_txt.read_text(encoding='utf-8', errors='ignore')
        lines = [
            line.strip() for line in hotword_text.splitlines()
            if line.strip() and not line.strip().startswith('#')
        ]
        runner.log_pass("hot.txt 热词表", f"包含 {len(lines)} 条有效热词规则")

        forbidden_translation_aliases = {
            '模型', '界面', '服务端', '客户端', '容器', '缓存',
            '提示词', '智能体', '代理', '令牌',
        }
        translation_aliases = []
        for line in lines:
            hotword_part = line.split('~~~', 1)[0]
            parts = [part.strip() for part in hotword_part.split('|') if part.strip()]
            translation_aliases.extend(
                alias for alias in parts[1:] if alias in forbidden_translation_aliases
            )
        if translation_aliases:
            runner.log_fail("中文误翻译回归", f"热词别名中仍有中文释义: {sorted(set(translation_aliases))}")
        else:
            runner.log_pass("中文误翻译回归", "中文释义未被用作英文热词的强制替换别名")

        try:
            from config_client import ClientConfig
            from core.client.hotword.hot_phoneme import PhonemeCorrector

            corrector = PhonemeCorrector(ClientConfig.hot_thresh, ClientConfig.hot_similar)
            loaded_count = corrector.update_hotwords(hotword_text)

            chinese_samples = ['模型', '界面', '文件', '服务端', '客户端', '容器', '缓存']
            changed_chinese = {
                sample: corrector.correct(sample).text
                for sample in chinese_samples
                if corrector.correct(sample).text != sample
            }
            if changed_chinese:
                runner.log_fail("中文原意保留", f"发生了不应有的替换: {changed_chinese}")
            else:
                runner.log_pass("中文原意保留", "模型/界面/文件等中文词保持中文")

            expected_aliases = {
                'C plus plus': 'C++',
                'config client 点 py': 'config_client.py',
                'config server 点 py': 'config_server.py',
            }
            incorrect_aliases = {
                sample: corrector.correct(sample).text
                for sample, expected in expected_aliases.items()
                if corrector.correct(sample).text != expected
            }
            if incorrect_aliases:
                runner.log_fail("热词纠正回归", f"未正确纠正: {incorrect_aliases}")
            else:
                runner.log_pass("热词纠正回归", f"{len(expected_aliases)} 个真实识别样本全部通过")

            if loaded_count != len(corrector.hotwords):
                runner.log_fail("热词加载一致性", f"报告加载 {loaded_count} 条，实际为 {len(corrector.hotwords)} 条")
            else:
                runner.log_pass("热词加载一致性", f"成功加载 {loaded_count} 个热词目标")
        except Exception as e:
            runner.log_fail("热词行为回归", str(e))
    else:
        runner.log_fail("hot.txt 热词表", "未找到 hot.txt 文件")

    if hot_rule_txt.exists():
        rule_lines = [l for l in hot_rule_txt.read_text(encoding='utf-8', errors='ignore').splitlines() if l.strip()]
        runner.log_pass("hot-rule.txt 正则规则", f"包含 {len(rule_lines)} 条有效正则表达式")
    else:
        runner.log_fail("hot-rule.txt 正则规则", "未找到 hot-rule.txt 文件")

    try:
        app_source = (BASE_DIR / 'web_gui' / 'app.py').read_text(encoding='utf-8')
        hotword_ui_tokens = [
            'render_rule_file_card',
            'rule-editor',
            '打开文件',
            '替换策略',
            'hot_similar',
            '已保存，下一次听写自动生效',
            'hot.txt 热词替换表',
            'hot-rule.txt 正则规则',
        ]
        if all(token in app_source for token in hotword_ui_tokens):
            runner.log_pass("热词页面编辑器体验", "热词与正则编辑区已提供保存、刷新、打开文件和紧凑编辑器样式")
        else:
            runner.log_fail("热词页面编辑器体验", "热词页面缺少编辑器样式或基础文件操作入口")
    except Exception as e:
        runner.log_fail("热词页面编辑器体验", str(e))


def test_unit_8_ai_and_private_config(runner: TestRunner):
    runner.log_header("Unit 8: AI 润色配置与私密 API Key 掩码安全")
    try:
        from web_gui.config_manager import ConfigManager
        from web_gui.private_config import mask_key, load_private_config
        masked = mask_key("sk-1234567890abcdefghijklmnopqrstuvwxyz")
        if masked.startswith("sk-1") and masked.endswith("wxyz") and "*" in masked:
            runner.log_pass("API Key 掩码加密", f"掩码样例: {masked}")
        else:
            runner.log_fail("API Key 掩码加密", f"掩码格式异常: {masked}")

        cfg = load_private_config()
        if 'llm_api_keys' in cfg and 'llm_role_api_keys' in cfg and 'llm_profile_api_keys' in cfg:
            runner.log_pass("私密配置加载", "Provider Key、角色 Key 与 API 档案 Key 存储节点可用")
        else:
            runner.log_fail("私密配置加载", "缺少 llm_api_keys、llm_role_api_keys 或 llm_profile_api_keys")

        profiles = ConfigManager.load_llm_profiles(include_keys=False)
        if isinstance(profiles.get('profiles'), list) and 'active_profile' in profiles:
            runner.log_pass("AI API 档案加载", f"当前档案: {profiles.get('active_profile') or '未配置'}")
        else:
            runner.log_fail("AI API 档案加载", f"档案结构异常: {profiles}")

        roles = ConfigManager.list_llm_roles()
        role_names = {role.get('display_name') for role in roles}
        expected_roles = {'翻译', '小助理', '大助理'}
        if expected_roles.issubset(role_names):
            runner.log_pass("AI 角色枚举", f"已发现角色: {sorted(role_names)}")
        else:
            runner.log_fail("AI 角色枚举", f"缺少角色: {sorted(expected_roles - role_names)}")

        match = ConfigManager.preview_llm_role_match('\u7ffb\u8bd1 \u6d4b\u8bd5')
        has_active_profile = bool(ConfigManager.load_llm_profiles().get('active_profile'))
        if has_active_profile:
            if match.get('matched') and match.get('role') == '翻译' and match.get('content') == '测试':
                runner.log_pass("AI 角色触发匹配", "翻译触发词可正确剥离并命中角色")
            else:
                runner.log_fail("AI 角色触发匹配", f"匹配异常: {match}")
        elif not match.get('matched'):
            runner.log_pass("AI 角色触发匹配", "未配置 API 档案时不会误命中未绑定角色")
        else:
            runner.log_fail("AI 角色触发匹配", f"未配置 API 档案却命中角色: {match}")

        app_source = (BASE_DIR / 'web_gui' / 'app.py').read_text(encoding='utf-8')
        panel_source = (BASE_DIR / 'web_gui' / 'ai_panel.py').read_text(encoding='utf-8')
        if (
            'render_ai_panel()' in app_source
            and '角色 API Key' not in panel_source
            and 'API 配置档案' in panel_source
            and panel_source.count("ui.button('管理 API 配置'") == 1
        ):
            runner.log_pass("AI 页面信息架构", "API 档案与角色绑定已分离，且页面只有一个 API 管理主入口")
        else:
            runner.log_fail("AI 页面信息架构", "AI 页面仍可能混用 API 密钥配置和角色配置")

        if "'custom': '自定义 OpenAI 兼容 / 中转站'" in (BASE_DIR / 'web_gui' / 'config_manager.py').read_text(encoding='utf-8') and '中转站配置必须填写 Base URL' in panel_source:
            runner.log_pass("中转站 API 配置", "已提供自定义 OpenAI 兼容入口，并要求中转站填写 Base URL")
        else:
            runner.log_fail("中转站 API 配置", "缺少自定义中转站入口或 Base URL 校验")

        if 'ui.textarea(' not in panel_source.split('def open_profile_editor', 1)[1].split('def open_delete_profile_dialog', 1)[0] and '已拉取 {len(model_state["models"])} 个模型' in panel_source:
            runner.log_pass("模型拉取下拉一致性", "API 模型区域不再使用大文本框，拉取数量来自同一份模型列表")
        else:
            runner.log_fail("模型拉取下拉一致性", "模型区域仍可能出现文本框和下拉数量不一致")

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_profiles = Path(temp_dir) / 'llm_profiles.json'
            temp_profiles.write_text(json.dumps({
                'active_profile': 'only-api',
                'profiles': [{
                    'id': 'only-api',
                    'name': 'only-api',
                    'provider': 'custom',
                    'api_url': 'https://example.com/v1',
                    'models': ['m1'],
                    'default_model': 'm1',
                }],
            }, ensure_ascii=False), encoding='utf-8')
            with patch('web_gui.config_manager.LLM_PROFILES_PATH', temp_profiles), \
                 patch.object(ConfigManager, 'write_text_with_backup', lambda path, content: Path(path).write_text(content, encoding='utf-8') is not None):
                profile_id = 'only-api'
                deleted = ConfigManager.delete_llm_profile(profile_id)
                empty_profiles = ConfigManager.load_llm_profiles()
        if deleted and empty_profiles.get('profiles') == [] and empty_profiles.get('active_profile') == '':
            runner.log_pass("最后一套 API 删除", "删除最后一套 API 后保留空状态，不强制重建默认配置")
        else:
            runner.log_fail("最后一套 API 删除", f"空状态异常: {empty_profiles}")

        translation_role = next((role for role in roles if role.get('display_name') == '翻译'), None)
        if translation_role and translation_role.get('profile_id'):
            runner.log_pass("翻译角色 API 绑定", f"翻译角色已绑定 API 档案: {translation_role.get('profile_id')}")
        else:
            runner.log_fail("翻译角色 API 绑定", "翻译角色未绑定 API 档案，可能继续走旧 provider")

        pool_source = (BASE_DIR / 'core' / 'client' / 'llm' / 'llm_client_pool.py').read_text(encoding='utf-8')
        if 'key_digest' in pool_source and 'sha256' in pool_source and 'cache_key = f"{provider}_{final_url}_{key_digest}"' in pool_source:
            runner.log_pass("LLM 客户端池缓存", "缓存 key 已包含 API Key 哈希，避免同地址不同 Key 复用客户端")
        else:
            runner.log_fail("LLM 客户端池缓存", "缓存 key 未区分 API Key")
    except Exception as e:
        runner.log_fail("私密配置与 AI 模块", str(e))


def test_unit_9_win32_key_restorer(runner: TestRunner):
    runner.log_header("Unit 9: Windows 修饰键 (Ctrl/Alt/Shift) 系统级复位测试")
    try:
        from core.tools.key_reset import release_all_modifier_keys
        release_all_modifier_keys()
        runner.log_pass("系统修饰键复位", "已成功补发 VK_CONTROL/ALT/SHIFT KeyUp 消息，消除假死隐患")
    except Exception as e:
        runner.log_fail("系统修饰键复位", str(e))


def test_unit_10_process_tree_manager(runner: TestRunner):
    runner.log_header("Unit 10: 后台进程树查找与零残留清理测试")
    try:
        from web_gui import process_manager
        pids = process_manager.find_project_processes(('app.py', 'start_client.py', 'start_server.py'))
        runner.log_pass("项目关联进程查找", f"找到 {len(pids)} 个运行中的 CapsWriter 组件进程")
    except Exception as e:
        runner.log_fail("进程树管理", str(e))


def test_unit_11_graceful_gui_exit(runner: TestRunner):
    runner.log_header("Unit 11: 原生 GUI 与任务栏图标优雅退出回归")
    try:
        from unittest.mock import patch
        import run_app

        class FakeGuiProcess:
            pid = 4242

            def __init__(self):
                self.close_requested = False
                self.forced = False
                self.exited = False

            def poll(self):
                return 0 if self.exited else None

            def wait(self, timeout=None):
                if self.close_requested:
                    self.exited = True
                    return 0
                raise TimeoutError("GUI 未收到正常关闭消息")

            def terminate(self):
                self.forced = True
                self.exited = True

            def kill(self):
                self.forced = True
                self.exited = True

        fake_process = FakeGuiProcess()
        included_children = {'value': False}

        def request_window_close(pid, include_children=False):
            if pid == fake_process.pid:
                fake_process.close_requested = True
                included_children['value'] = include_children

        with patch.object(run_app, 'GUI_PROCESS', fake_process), \
             patch.object(run_app.process_manager, 'close_windows_by_pid', request_window_close), \
             patch.object(run_app.process_manager, 'stop_gui', return_value=True):
            run_app._stop_gui()

        if not included_children['value']:
            runner.log_fail("任务栏图标正常销毁", "关闭消息未覆盖持有原生窗口的 GUI 子进程")
        elif fake_process.forced:
            runner.log_fail("任务栏图标正常销毁", "GUI 收到 WM_CLOSE 后仍被立即强制终止")
        elif not fake_process.exited:
            runner.log_fail("任务栏图标正常销毁", "GUI 未在正常关闭等待期内退出")
        else:
            runner.log_pass("任务栏图标正常销毁", "先等待原生窗口退出，超时后才会使用强制终止")

        control_tray_source = (BASE_DIR / 'web_gui' / 'control_tray.py').read_text(encoding='utf-8')
        app_source = (BASE_DIR / 'web_gui' / 'app.py').read_text(encoding='utf-8')
        tray_singleton_tokens = [
            'TRAY_OWNER_FILE',
            '_claim_tray_owner',
            '_release_tray_owner',
            '_launcher_owns_tray',
            'if not _claim_tray_owner():',
        ]
        if all(token in control_tray_source for token in tray_singleton_tokens) and "CAPSWRITER_CONTROL_CENTER') != '1'" in app_source:
            runner.log_pass("控制中心托盘单例", "跨进程 PID 锁与 GUI 托管态检查已覆盖，避免重复托盘图标")
        else:
            runner.log_fail("控制中心托盘单例", "缺少跨进程托盘单例或 GUI 托管态保护")

        foreground_tokens = [
            'def open_path_foreground',
            'def _open_path_foreground_windows',
            'WScript.Shell',
            'AppActivate',
            'AttachThreadInput',
            'BringWindowToTop',
            'SetForegroundWindow',
        ]
        direct_startfile_count = app_source.count('os.startfile')
        if all(token in app_source for token in foreground_tokens) and direct_startfile_count <= 1 and "SendKeys('%')" not in app_source:
            runner.log_pass("文件目录前台打开", "GUI 文件/目录入口统一走前台激活 helper，且未使用 Alt SendKeys")
        else:
            runner.log_fail("文件目录前台打开", "文件/目录入口可能仍会在后台打开，或使用了易误触菜单栏的 SendKeys")
    except Exception as e:
        runner.log_fail("任务栏图标退出回归", str(e))


def test_unit_12_log_analyzer(runner: TestRunner):
    runner.log_header("Unit 12: logs/ 全量日志异常与 Traceback 深度诊断")
    log_dir = BASE_DIR / "logs"
    if not log_dir.exists():
        runner.log_warn("日志诊断", "logs 目录不存在")
        return

    log_files = list(log_dir.glob("*.log"))
    if not log_files:
        runner.log_pass("日志诊断", "暂无日志文件")
        return

    tracebacks_found = []
    for log_file in log_files:
        try:
            content = log_file.read_text(encoding='utf-8', errors='ignore')
            lines = content.splitlines()[-500:]  # 仅检查近期 500 行日志
            for idx, line in enumerate(lines):
                if 'Traceback (most recent call last)' in line or ' CRITICAL ' in line:
                    context_snippet = '\n'.join(lines[max(0, idx - 1):min(len(lines), idx + 6)])
                    tracebacks_found.append(f"{log_file.name}: {context_snippet}")
        except Exception:
            continue

    if not tracebacks_found:
        runner.log_pass("系统日志诊断", f"分析了近期日志，未发现活动严重的 Traceback 崩盘异常")
    else:
        runner.log_fail("系统日志诊断", f"在日志中发现了 {len(tracebacks_found)} 处近期未捕获异常:\n" + "\n---\n".join(tracebacks_found[:2]))


def test_unit_13_overlay_and_effect_notice_audit(runner: TestRunner):
    runner.log_header("Unit 13: Overlay output and confirm-preview audit")
    try:
        def u(value: str) -> str:
            return value.encode('ascii').decode('unicode_escape')

        app_source = (BASE_DIR / 'web_gui' / 'app.py').read_text(encoding='utf-8')
        pill_source = (BASE_DIR / 'core' / 'ui' / 'modern_overlay' / 'pill_overlay.py').read_text(encoding='utf-8')
        llm_handler_source = (BASE_DIR / 'core' / 'client' / 'llm' / 'llm_handler.py').read_text(encoding='utf-8')
        result_processor_source = (BASE_DIR / 'core' / 'client' / 'output' / 'result_processor.py').read_text(encoding='utf-8')
        recorder_source = (BASE_DIR / 'core' / 'client' / 'audio' / 'recorder.py').read_text(encoding='utf-8')
        config_source = (BASE_DIR / 'config_client.py').read_text(encoding='utf-8')
        pill_config_source = (BASE_DIR / 'config_pill.py').read_text(encoding='utf-8')

        if u(r'\u767d\u5e95\u6a59\u8272\u58f0\u6ce2') in app_source and u(r'\u786e\u8ba4\u540e\u5199\u5165') in app_source:
            runner.log_pass("Overlay options", "Only wave feedback and confirm-before-insert output are exposed")
        else:
            runner.log_fail("Overlay options", "Wave feedback or confirm-before-insert labels are missing")

        forbidden_overlay_labels = [
            u(r'\u5b57\u5e55\u53cd\u9988'),
            u(r'\u5b9e\u65f6\u5b57\u5e55'),
            u(r'\u6d6e\u5c42\u7c7b\u578b'),
        ]
        if not any(label in app_source for label in forbidden_overlay_labels):
            runner.log_pass("Removed subtitle feedback option", "UI no longer exposes misleading subtitle feedback type")
        else:
            runner.log_fail("Removed subtitle feedback option", "UI still exposes subtitle/capsule/type labels")

        if (
            "mode = 'caption'" not in recorder_source
            and '_segment_config' not in recorder_source
            and 'caption_preview_seg_duration' not in recorder_source
            and 'caption_preview_seg_duration' not in config_source
            and 'caption_preview_seg_overlap' not in config_source
            and 'mode =' not in pill_config_source
        ):
            runner.log_pass("Removed caption segment path", "Recorder/config no longer special-case subtitle feedback segments")
        else:
            runner.log_fail("Removed caption segment path", "Caption feedback segment wiring still exists")

        forbidden_ai_labels = [
            u(r'\u60ac\u6d6e\u7a97\u6a21\u5f0f (Toast)'),
            u(r'\u5149\u6807\u76f4\u6253\u4e0a\u5c4f (Typing)'),
            "label='output_mode'",
        ]
        if not any(label in app_source for label in forbidden_ai_labels):
            runner.log_pass("AI output semantics", "AI page no longer exposes the legacy Toast output selector")
        else:
            runner.log_fail("AI output semantics", "Legacy Toast/output selector text still appears in app.py")

        stale_restart_prompts = [
            u(r'AI \u6da6\u8272\u8bbe\u7f6e\u5df2\u4fdd\u5b58\uff0c\u91cd\u542f\u5ba2\u6237\u7aef\u540e\u751f\u6548'),
            u(r'\u542c\u5199\u72b6\u6001\u6d6e\u5c42\u5f00\u5173\u5df2\u4fdd\u5b58\uff0c\u91cd\u542f\u5ba2\u6237\u7aef\u540e\u751f\u6548'),
            u(r'\u6700\u7ec8\u8f93\u51fa\u65b9\u5f0f\u5df2\u4fdd\u5b58\uff0c\u91cd\u542f\u5ba2\u6237\u7aef\u540e\u751f\u6548'),
        ]
        if not any(prompt in app_source for prompt in stale_restart_prompts):
            runner.log_pass("Effect notice audit", "Live/next-dictation settings are not mislabeled as restart-only")
        else:
            runner.log_fail("Effect notice audit", "A live setting still says it needs restart")

        preview_tokens = [
            'preview_text = tk.Text', u(r"'\u590d\u5236'"), u(r"'\u5199\u5165\u5149\u6807\u4f4d\u7f6e'"), '_confirm_preview', '_copy_preview',
            '_schedule_preview_autoclose', '_pause_preview_autoclose', '_auto_hide_preview', "'<Escape>'",
        ]
        preview_region = pill_source[pill_source.find('def show_preview'):pill_source.find('def _hide_if_processing')]
        if all(token in pill_source for token in preview_tokens) and 'root.after(1800, self.hide)' not in preview_region:
            runner.log_pass("Confirm preview UX", "Final overlay preview is editable, copyable, confirmable, auto/manual close capable, and not fixed-time hidden")
        else:
            runner.log_fail("Confirm preview UX", "Final overlay preview is missing edit/copy/confirm/close behavior or still fixed-time hides")

        if all(token in config_source for token in ['preview_close_mode', 'preview_base_seconds', 'preview_max_seconds']) and u(r'\u786e\u8ba4\u6d6e\u5c42\u505c\u7559\u65b9\u5f0f') in app_source:
            runner.log_pass("Preview close settings", "Confirm preview close mode and timing are configurable")
        else:
            runner.log_fail("Preview close settings", "Confirm preview close settings are missing from config or UI")

        visibility_tokens = [
            'sync_preview_settings_visibility',
            'preview_settings_container.set_visibility(preview_enabled)',
            'preview_timing_row.set_visibility(preview_enabled and auto_close)',
            'handle_output_destination_change',
            'handle_preview_close_change',
        ]
        if all(token in app_source for token in visibility_tokens):
            runner.log_pass("Preview settings visibility", "Confirm-preview settings hide/show immediately according to selected output and close mode")
        else:
            runner.log_fail("Preview settings visibility", "Confirm-preview settings are not wired for immediate conditional visibility")

        if 'handle_overlay_preview_mode' in llm_handler_source and 'handle_toast_mode' not in llm_handler_source:
            runner.log_pass("LLM preview route", "LLM output routes to modern overlay preview instead of legacy Toast")
        else:
            runner.log_fail("LLM preview route", "LLM handler still exposes or misses the expected preview route")

        if (
            "get_live_client_config('output_destination'" in result_processor_source
            and "if output_destination != 'overlay_preview':" in result_processor_source
            and "if output_destination == 'overlay_preview':" in result_processor_source
        ):
            runner.log_pass("Live output destination", "Normal dictation reads final output destination live and skips legacy final hide before confirm preview")
        else:
            runner.log_fail("Live output destination", "Normal dictation does not correctly route overlay_preview or still risks legacy final hide")

        lifecycle_tokens = [
            '_cancel_pending_hide_timers',
            '_processing_hide_after_id',
            '_final_hide_after_id',
            '_preview_remaining_seconds',
            '_tick_preview_countdown',
            '_advance_preview_countdown',
            '_resume_preview_autoclose',
            "bind('<FocusOut>'",
            "bind_all('<Escape>'",
            '_preview_duration_seconds',
        ]
        if all(token in pill_source for token in lifecycle_tokens):
            runner.log_pass("Preview lifecycle timers", "Confirm preview supports stale timer cancel, countdown, focus-out resume, and direct Esc close")
        else:
            runner.log_fail("Preview lifecycle timers", "Confirm preview lifecycle is missing countdown, focus-out resume, direct Esc close, or cancellable stale timers")

        manual_status = u(r'\u5df2\u590d\u5236\u5230\u526a\u8d34\u677f\uff0c\u6309 Esc \u5173\u95ed')
        editing_status = u(r'\u6b63\u5728\u7f16\u8f91')
        paused_status = u(r'\u6b63\u5728\u7f16\u8f91\uff0c\u5012\u8ba1\u65f6\u5df2\u6682\u505c')
        if manual_status in pill_source and editing_status in pill_source and paused_status in pill_source and u(r'\u5173\u95ed\u6309\u94ae') not in pill_source:
            runner.log_pass("Preview status copy", "Manual and editing status text is concise and no longer mentions a missing close button")
        else:
            runner.log_fail("Preview status copy", "Preview status text still mentions close button or misses manual/editing copy")

        focus_tokens = [
            'self.root.after(80, self._focus_target_window)',
            '_start_preview_keyboard_listener',
            '_stop_preview_keyboard_listener',
            '_hide_after_external_paste',
            '_preview_has_focus',
            '_sync_preview_clipboard',
        ]
        if all(token in pill_source for token in focus_tokens) and 'focus_force()' not in pill_source and "bind('<KeyRelease>'" not in pill_source:
            runner.log_pass("Preview focus and paste flow", "Confirm preview restores target focus, observes external Ctrl+V to close, and syncs edited text on focus-out")
        else:
            runner.log_fail("Preview focus and paste flow", "Confirm preview may still steal focus, miss external paste close, or overwrite clipboard on every key")

        from core.ui.modern_overlay.pill_overlay import FloatingPillWindow
        short_duration = FloatingPillWindow._preview_duration_seconds(12, 4, 40)
        medium_duration = FloatingPillWindow._preview_duration_seconds(80, 4, 40)
        long_duration = FloatingPillWindow._preview_duration_seconds(220, 4, 40)
        max_duration = FloatingPillWindow._preview_duration_seconds(400, 4, 40)
        if short_duration == 4 and 4 < medium_duration < long_duration < max_duration == 40:
            runner.log_pass("Length-based preview duration", f"Duration buckets increase with text length: {short_duration}/{medium_duration}/{long_duration}/{max_duration}s")
        else:
            runner.log_fail("Length-based preview duration", f"Unexpected duration buckets: {short_duration}/{medium_duration}/{long_duration}/{max_duration}s")

        from web_gui.config_manager import ConfigManager
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_config = Path(temp_dir) / 'config_client.py'
            temp_config.write_text(
                "class ClientConfig:\n"
                "    output_destination = 'typing'\n"
                "    preview_close_mode = 'auto'\n"
                "    preview_base_seconds = 8\n",
                encoding='utf-8',
            )
            with patch('web_gui.config_manager.CONFIG_CLIENT_PATH', temp_config), \
                 patch.object(ConfigManager, 'write_text_with_backup', lambda path, content: path.write_text(content, encoding='utf-8') is not None):
                ConfigManager.set_client_var('output_destination', 'overlay_preview')
                ConfigManager.set_client_var('preview_close_mode', 'manual')
                ConfigManager.set_client_var('preview_base_seconds', 12)
                output_destination = ConfigManager.get_client_var('output_destination', None)
                close_mode = ConfigManager.get_client_var('preview_close_mode', None)
                base_seconds = ConfigManager.get_client_var('preview_base_seconds', None)
                compile(temp_config.read_text(encoding='utf-8'), str(temp_config), 'exec')

        if output_destination == 'overlay_preview' and close_mode == 'manual' and base_seconds == 12:
            runner.log_pass("Preview config read/write", "output_destination and preview close settings save/read correctly")
        else:
            runner.log_fail("Preview config read/write", f"Unexpected values: output={output_destination}, close={close_mode}, base={base_seconds}")
    except Exception as e:
        runner.log_fail("Overlay confirm-preview audit", str(e))


def main():
    print(f"{BOLD}{CYAN}" + "=" * 65 + f"{RESET}")
    print(f"{BOLD}{CYAN}   CapsWriter 智能控制中心 - 全功能自动化自测试与故障诊断系统{RESET}")
    print(f"{BOLD}{CYAN}" + "=" * 65 + f"{RESET}")

    runner = TestRunner()

    test_unit_1_asr_server(runner)
    test_unit_2_client_config(runner)
    test_unit_3_webui_and_health(runner)
    test_unit_4_input_history(runner)
    test_unit_5_transcription_engine(runner)
    test_unit_6_config_backup(runner)
    test_unit_7_hotwords_and_rules(runner)
    test_unit_8_ai_and_private_config(runner)
    test_unit_9_win32_key_restorer(runner)
    test_unit_10_process_tree_manager(runner)
    test_unit_11_graceful_gui_exit(runner)
    test_unit_12_log_analyzer(runner)
    test_unit_13_overlay_and_effect_notice_audit(runner)

    print(f"\n{BOLD}{CYAN}" + "=" * 65 + f"{RESET}")
    print(f"{BOLD}自测试结果汇总:{RESET}")
    print(f" - 通过单元: {GREEN}{runner.passed_tests}{RESET}")
    print(f" - 失败单元: {RED}{runner.failed_tests}{RESET}")
    print(f" - 警告项目: {YELLOW}{runner.warnings}{RESET}")
    print(f"{BOLD}{CYAN}" + "=" * 65 + f"{RESET}")

    if runner.failed_tests > 0:
        print(f"\n{BOLD}{RED}发现的待修复 Bug 列表:{RESET}")
        for issue in runner.issues_found:
            print(f" - {issue}")
        sys.exit(1)
    else:
        print(f"\n{BOLD}{GREEN}🎉 所有功能模块测试全部通过！系统运行状态健康良好。{RESET}")
        sys.exit(0)


if __name__ == "__main__":
    main()
