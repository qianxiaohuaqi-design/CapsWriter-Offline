# coding: utf-8
"""
CapsWriter 现代 GUI 桌面客户端 (CapsWriter Control Hub - Native Client App)
基于 NiceGUI + PyWebView 原生桌面窗口构建

特点：
1. 100% 原生 Windows 独立桌面软件窗口 (Native App Window)，不再弹出浏览器标签页；
2. ☀️ 浅色桌宠模式 (默认，参照 Clawd Settings 界面) 与 🌙 暗黑科技模式一键切换；
3. 5 大分类极简导航，主展示区配备微细滚动条与溢出解锁；
4. 📁 配置全量 JSON 导出与一键在线解析恢复；
5. 🚀 ASR 后端服务 (6016) 与客户端进程监控与快捷启动拉起。
"""

import sys
import os
import asyncio
import json
import subprocess
import re
import shutil
import time

_T_GUI_IMPORT_START = time.perf_counter()
from functools import partial
from pathlib import Path

# 添加项目根目录到 Python 路径
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from nicegui import ui, app, run
from web_gui.ai_panel import render_ai_panel
from web_gui.config_manager import ConfigManager
from web_gui.transcription_service import get_media_tool_status, regenerate_srt_from_txt, transcribe_file
from web_gui import process_manager
from web_gui.transcription_history import OUTPUT_DIR, delete_transcription_output, list_transcription_history
from config_server import ModelPaths
from core.server.engines.language import LANGUAGE_MAP
from core.client.output.input_history import (
    CLEAR_MARKER_PATH,
    HISTORY_PATH,
    clear_input_history,
    load_input_history,
)
_T_GUI_IMPORTS_DONE = time.perf_counter()

# Windows 独立 AppUserModelID 与 HWND 窗口图标绑定
if sys.platform == 'win32':
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("CapsWriter.Offline.App.v3")
    except Exception:
        pass

app.add_static_files('/assets', BASE_DIR / 'assets')

_nicegui_notify = ui.notify

CREATE_NO_WINDOW = 0x08000000 if sys.platform == 'win32' else 0


def _apply_native_window_icon():
    if sys.platform != 'win32':
        return
    import ctypes
    from ctypes import wintypes
    import time

    ico_path = BASE_DIR / 'assets' / 'source' / 'capswriter.ico'
    if not ico_path.exists():
        return

    user32 = ctypes.windll.user32
    WM_SETICON = 0x0080
    ICON_SMALL = 0
    ICON_BIG = 1
    IMAGE_ICON = 1
    LR_LOADFROMFILE = 0x0010
    GWL_STYLE = -16
    GWL_EXSTYLE = -20
    WS_THICKFRAME = 0x00040000
    WS_MINIMIZEBOX = 0x00020000
    WS_MAXIMIZEBOX = 0x00010000
    SWP_NOMOVE = 0x0002
    SWP_NOSIZE = 0x0001
    SWP_NOZORDER = 0x0004
    SWP_FRAMECHANGED = 0x0020
    app_id = 'CapsWriter.Offline.App.v3'

    hicon_16 = user32.LoadImageW(None, str(ico_path), IMAGE_ICON, 16, 16, LR_LOADFROMFILE)
    hicon_24 = user32.LoadImageW(None, str(ico_path), IMAGE_ICON, 24, 24, LR_LOADFROMFILE)
    hicon_32 = user32.LoadImageW(None, str(ico_path), IMAGE_ICON, 32, 32, LR_LOADFROMFILE)
    hicon_48 = user32.LoadImageW(None, str(ico_path), IMAGE_ICON, 48, 48, LR_LOADFROMFILE)

    def _dpi_icons(hwnd):
        dpi = 96
        try:
            dpi = user32.GetDpiForWindow(hwnd) or 96
        except Exception:
            pass
        small = hicon_24 if dpi >= 144 else (hicon_16 or hicon_24)
        big = hicon_48 if dpi >= 144 else (hicon_32 or hicon_48)
        return small or big, big or small

    def _set_taskbar_identity(hwnd):
        try:
            import uuid

            class PROPERTYKEY(ctypes.Structure):
                _fields_ = [('fmtid', ctypes.c_ubyte * 16), ('pid', wintypes.DWORD)]

            class GUID(ctypes.Structure):
                _fields_ = [
                    ('Data1', wintypes.DWORD),
                    ('Data2', wintypes.WORD),
                    ('Data3', wintypes.WORD),
                    ('Data4', ctypes.c_ubyte * 8),
                ]

            class PROPVARIANT_UNION(ctypes.Union):
                _fields_ = [('pwszVal', wintypes.LPWSTR)]

            class PROPVARIANT(ctypes.Structure):
                _fields_ = [
                    ('vt', ctypes.c_ushort),
                    ('wReserved1', ctypes.c_ushort),
                    ('wReserved2', ctypes.c_ushort),
                    ('wReserved3', ctypes.c_ushort),
                    ('value', PROPVARIANT_UNION),
                ]

            def property_key(pid):
                raw = uuid.UUID('{9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3}').bytes_le
                key = PROPERTYKEY()
                for index, byte in enumerate(raw):
                    key.fmtid[index] = byte
                key.pid = pid
                return key

            def guid(value):
                source = uuid.UUID(value)
                result = GUID()
                data = source.bytes_le
                result.Data1 = int.from_bytes(data[0:4], 'little')
                result.Data2 = int.from_bytes(data[4:6], 'little')
                result.Data3 = int.from_bytes(data[6:8], 'little')
                for index, byte in enumerate(data[8:16]):
                    result.Data4[index] = byte
                return result

            def propvariant(value):
                variant = PROPVARIANT()
                variant.vt = 31  # VT_LPWSTR
                variant.value.pwszVal = value
                return variant

            iid = guid('{886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99}')
            store = ctypes.c_void_p()
            ctypes.windll.shell32.SHGetPropertyStoreForWindow(
                wintypes.HWND(hwnd),
                ctypes.byref(iid),
                ctypes.byref(store),
            )
            if not store:
                return
            vtable = ctypes.cast(store, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
            set_value = ctypes.WINFUNCTYPE(
                ctypes.c_long,
                ctypes.c_void_p,
                ctypes.POINTER(PROPERTYKEY),
                ctypes.POINTER(PROPVARIANT),
            )(vtable[5])
            commit = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p)(vtable[6])
            release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtable[2])

            values = (
                (property_key(5), propvariant(app_id)),
                (property_key(3), propvariant(f'{ico_path},0')),
                (property_key(4), propvariant('CapsWriter')),
            )
            for key, value in values:
                set_value(store, ctypes.byref(key), ctypes.byref(value))
            commit(store)
            release(store)
        except Exception:
            pass

    pid = os.getpid()

    for _ in range(25):
        time.sleep(0.2)
        found = False

        try:
            main_win = getattr(app.native, 'main_window', None)
            if main_win and getattr(main_win, 'native', None):
                form = main_win.native
                hwnd = int(form.Handle)
                
                # 直接通过 WinForms 原生 System.Drawing.Icon 赋权给 Form.Icon 属性
                try:
                    import clr
                    clr.AddReference('System.Drawing')
                    import System.Drawing
                    form.Icon = System.Drawing.Icon(str(ico_path))
                except Exception:
                    pass

                # 开启原生 DWM 贴靠分屏 (Snap Layouts) 与快捷键 (Win+方向键)
                style = user32.GetWindowLongW(hwnd, GWL_STYLE)
                user32.SetWindowLongW(hwnd, GWL_STYLE, style | WS_THICKFRAME | WS_MINIMIZEBOX | WS_MAXIMIZEBOX)
                
                # 标题栏小图标与任务栏大图标都按当前 DPI 选择最清晰的帧
                form.ShowIcon = True
                small_icon, big_icon = _dpi_icons(hwnd)
                user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, small_icon)
                user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, big_icon)
                _set_taskbar_identity(hwnd)
                user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED)
                found = True
        except Exception:
            pass

        if not found:
            WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

            def callback(hwnd, lparam):
                nonlocal found
                if not user32.IsWindowVisible(hwnd):
                    return True
                process_id = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
                if process_id.value == pid:
                    style = user32.GetWindowLongW(hwnd, GWL_STYLE)
                    user32.SetWindowLongW(hwnd, GWL_STYLE, style | WS_THICKFRAME | WS_MINIMIZEBOX | WS_MAXIMIZEBOX)
                    small_icon, big_icon = _dpi_icons(hwnd)
                    user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, small_icon)
                    user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, big_icon)
                    _set_taskbar_identity(hwnd)
                    user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED)
                    found = True
                return True

            user32.EnumWindows(WNDENUMPROC(callback), 0)

        if found:
            break

@app.on_startup
def _on_startup_icon_task():
    t_startup_start = time.perf_counter()
    import threading
    threading.Thread(target=_apply_native_window_icon, daemon=True).start()
    # 仅当 App 独立启动时才在后台拉起托盘，避免 run_app 托管时出现双重托盘
    if os.environ.get('CAPSWRITER_CONTROL_CENTER') != '1':
        try:
            from web_gui.control_tray import start_tray_in_background
            start_tray_in_background()
        except Exception:
            pass



def toast(message, *, type=None, position='bottom', close_button=False, color=None, multi_line=False, **kwargs):
    """统一 GUI 轻提示：短停留、小尺寸、贴合浅色橙色主视觉。"""
    palette = {
        'positive': 'amber-7',
        'warning': 'orange-7',
        'info': 'amber-6',
        'negative': 'red-6',
        'ongoing': 'amber-7',
    }
    kwargs.setdefault('timeout', 900 if type != 'negative' else 1600)
    kwargs.setdefault('classes', 'cw-toast')
    kwargs.setdefault('group', False)
    return _nicegui_notify(
        message,
        position=position,
        close_button=close_button,
        type=None,
        color=color or palette.get(type, 'amber-7'),
        multi_line=multi_line,
        **kwargs,
    )


ui.notify = toast


SPECIAL_CODE_MAP = {
    'Backspace': 'backspace',
    'Tab': 'tab',
    'Enter': 'enter',
    'NumpadEnter': 'enter',
    'Escape': 'esc',
    'Space': 'space',
    'CapsLock': 'caps_lock',
    'Delete': 'delete',
    'Insert': 'insert',
    'Home': 'home',
    'End': 'end',
    'PageUp': 'page_up',
    'PageDown': 'page_down',
    'ArrowUp': 'up',
    'ArrowDown': 'down',
    'ArrowLeft': 'left',
    'ArrowRight': 'right',
    'AltLeft': 'alt_l',
    'AltRight': 'alt_gr',
    'ControlLeft': 'ctrl_l',
    'ControlRight': 'ctrl_r',
    'ShiftLeft': 'shift',
    'ShiftRight': 'shift_r',
    'MetaLeft': 'cmd',
    'MetaRight': 'cmd_r',
    'Backquote': '`',
    'Minus': '-',
    'Equal': '=',
    'BracketLeft': '[',
    'BracketRight': ']',
    'Backslash': '\\',
    'Semicolon': ';',
    'Quote': "'",
    'Comma': ',',
    'Period': '.',
    'Slash': '/',
}

SHORTCUT_LABELS = {
    'alt_gr': '右 Alt',
    'alt_l': '左 Alt',
    'alt': 'Alt',
    'ctrl': 'Ctrl',
    'ctrl_l': '左 Ctrl',
    'ctrl_r': '右 Ctrl',
    'shift': 'Shift',
    'shift_r': '右 Shift',
    'cmd': 'Win',
    'cmd_r': '右 Win',
    'caps_lock': 'Caps Lock',
    'space': 'Space',
    'enter': 'Enter',
    'esc': 'Esc',
    'tab': 'Tab',
    'backspace': 'Backspace',
    'delete': 'Delete',
    'insert': 'Insert',
    'home': 'Home',
    'end': 'End',
    'page_up': 'Page Up',
    'page_down': 'Page Down',
    'up': '↑',
    'down': '↓',
    'left': '←',
    'right': '→',
}


def shortcut_display_name(key_name: str) -> str:
    parts = [part.strip() for part in str(key_name or '').split('+') if part.strip()]
    if not parts:
        return '未设置'
    display_parts = []
    for part in parts:
        if part in SHORTCUT_LABELS:
            display_parts.append(SHORTCUT_LABELS[part])
        elif re.fullmatch(r'f\d{1,2}', part):
            display_parts.append(part.upper())
        elif len(part) == 1:
            display_parts.append(part.upper())
        else:
            display_parts.append(part)
    return ' + '.join(display_parts)


def shortcut_from_keyboard_event(payload) -> str | None:
    if isinstance(payload, dict):
        data = payload
    elif isinstance(payload, (list, tuple)) and payload and isinstance(payload[0], dict):
        data = payload[0]
    else:
        return None

    if data.get('repeat'):
        return None

    code = data.get('code') or ''
    key = data.get('key') or ''
    if code.startswith('Key') and len(code) == 4:
        main_key = code[-1].lower()
    elif code.startswith('Digit') and len(code) == 6:
        main_key = code[-1]
    elif code.startswith('Numpad') and code[-1:].isdigit():
        main_key = f'numpad{code[-1]}'
    elif re.fullmatch(r'F\d{1,2}', code or ''):
        main_key = code.lower()
    else:
        main_key = SPECIAL_CODE_MAP.get(code)
        if not main_key and len(key) == 1:
            main_key = key.lower()

    if not main_key:
        return None

    modifiers = []
    if data.get('ctrlKey') and main_key not in {'ctrl', 'ctrl_l', 'ctrl_r'}:
        modifiers.append('ctrl')
    if data.get('altKey') and main_key not in {'alt', 'alt_l', 'alt_gr'}:
        modifiers.append('alt')
    if data.get('shiftKey') and main_key not in {'shift', 'shift_r'}:
        modifiers.append('shift')
    if data.get('metaKey') and main_key not in {'cmd', 'cmd_r'}:
        modifiers.append('cmd')

    if main_key in {'control', 'ctrl'}:
        main_key = 'ctrl'
    elif main_key in {'escape'}:
        main_key = 'esc'

    keys = modifiers + [main_key]
    seen = set()
    normalized = []
    for part in keys:
        if part and part not in seen:
            normalized.append(part)
            seen.add(part)
    return '+'.join(normalized)


def select_media_file_dialog() -> str:
    """打开系统文件选择器，返回选中的音视频路径。"""
    script = r'''
Add-Type -AssemblyName System.Windows.Forms
$dialog = New-Object System.Windows.Forms.OpenFileDialog
$dialog.Title = "选择要转写的音视频文件"
$dialog.Filter = "音视频文件|*.mp4;*.mkv;*.mov;*.avi;*.wav;*.mp3;*.m4a;*.flac;*.aac|所有文件|*.*"
$dialog.Multiselect = $false
$dialog.RestoreDirectory = $true
if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    Write-Output $dialog.FileName
}
'''
    result = subprocess.run(
        ['powershell', '-NoProfile', '-STA', '-Command', script],
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='ignore',
        creationflags=CREATE_NO_WINDOW,
    )
    return result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ''


def select_files_dialog(title: str, filter_spec: str, multiselect: bool = False) -> list[str]:
    """打开 Windows 文件选择器，返回一个或多个路径。"""
    multi = '$true' if multiselect else '$false'
    script = f'''
Add-Type -AssemblyName System.Windows.Forms
$dialog = New-Object System.Windows.Forms.OpenFileDialog
$dialog.Title = "{title}"
$dialog.Filter = "{filter_spec}"
$dialog.Multiselect = {multi}
$dialog.RestoreDirectory = $true
if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {{
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $dialog.FileNames
}}
'''
    result = subprocess.run(
        ['powershell', '-NoProfile', '-STA', '-Command', script],
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='ignore',
        creationflags=CREATE_NO_WINDOW,
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _focus_window_by_title_fragments(fragments: list[str]) -> None:
    if sys.platform != 'win32':
        return
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        user32 = ctypes.windll.user32
        SW_RESTORE = 9
        HWND_TOPMOST = -1
        HWND_NOTOPMOST = -2
        SWP_NOMOVE = 0x0002
        SWP_NOSIZE = 0x0001
        SWP_SHOWWINDOW = 0x0040
        user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
        user32.AttachThreadInput.restype = wintypes.BOOL
        user32.BringWindowToTop.argtypes = [wintypes.HWND]
        user32.BringWindowToTop.restype = wintypes.BOOL
        user32.SetActiveWindow.argtypes = [wintypes.HWND]
        user32.SetActiveWindow.restype = wintypes.HWND
        user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        user32.SetForegroundWindow.restype = wintypes.BOOL
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.c_void_p]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        kernel32.GetCurrentThreadId.restype = wintypes.DWORD
        normalized = [fragment.lower() for fragment in fragments if fragment]
        if not normalized:
            return

        def bring_to_front(hwnd) -> None:
            foreground_hwnd = user32.GetForegroundWindow()
            current_thread = kernel32.GetCurrentThreadId()
            foreground_thread = user32.GetWindowThreadProcessId(foreground_hwnd, None) if foreground_hwnd else 0
            target_thread = user32.GetWindowThreadProcessId(hwnd, None)
            attached: list[int] = []
            try:
                for thread_id in {foreground_thread, target_thread}:
                    if thread_id and thread_id != current_thread:
                        if user32.AttachThreadInput(current_thread, thread_id, True):
                            attached.append(thread_id)
                user32.ShowWindow(hwnd, SW_RESTORE)
                user32.BringWindowToTop(hwnd)
                user32.SetActiveWindow(hwnd)
                user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)
                user32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)
                user32.SetForegroundWindow(hwnd)
            finally:
                for thread_id in attached:
                    user32.AttachThreadInput(current_thread, thread_id, False)

        def callback(hwnd, _):
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            title = buffer.value.lower()
            if not any(fragment in title for fragment in normalized):
                return True

            bring_to_front(hwnd)
            return False

        user32.EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)(callback), 0)
    except Exception:
        pass


def _focus_opened_path_later(path: Path, *, select: bool = False) -> None:
    def worker():
        fragments = [path.name]
        if select:
            fragments.append(path.parent.name)
        elif path.is_dir():
            fragments.append(path.name)
        else:
            fragments.append(path.stem)
            fragments.append(path.parent.name)
        for delay in (0.35, 0.8, 1.4):
            time.sleep(delay)
            _focus_window_by_title_fragments(fragments)

    import threading
    threading.Thread(target=worker, daemon=True).start()


def _open_path_foreground_windows(path: Path, *, select: bool = False) -> None:
    target_title = path.parent.name if select else (path.name if path.is_dir() else path.name)
    env = os.environ.copy()
    env['CAPSWRITER_OPEN_PATH'] = str(path)
    env['CAPSWRITER_OPEN_TITLE'] = target_title
    env['CAPSWRITER_OPEN_SELECT'] = '1' if select else '0'
    script = r'''
$path = $env:CAPSWRITER_OPEN_PATH
$title = $env:CAPSWRITER_OPEN_TITLE
$select = $env:CAPSWRITER_OPEN_SELECT -eq '1'
if ($select) {
    Start-Process explorer.exe -ArgumentList "/select,`"$path`""
} elseif (Test-Path -LiteralPath $path -PathType Container) {
    Start-Process explorer.exe -ArgumentList "`"$path`""
} else {
    Start-Process -FilePath $path
}
$shell = New-Object -ComObject WScript.Shell
for ($i = 0; $i -lt 14; $i++) {
    Start-Sleep -Milliseconds 220
    if ($shell.AppActivate($title)) {
        break
    }
}
'''
    subprocess.Popen(
        ['powershell', '-NoProfile', '-STA', '-WindowStyle', 'Hidden', '-Command', script],
        cwd=str(BASE_DIR),
        env=env,
        creationflags=CREATE_NO_WINDOW,
    )


def open_path_foreground(path: Path, *, select: bool = False) -> None:
    path = Path(path)
    if sys.platform == 'win32':
        _open_path_foreground_windows(path, select=select)
        _focus_opened_path_later(path, select=select)
        return
    if sys.platform == 'darwin':
        subprocess.Popen(['open', '-R' if select else str(path), str(path)] if select else ['open', str(path)])
        return
    subprocess.Popen(['xdg-open', str(path.parent if select else path)])


MODEL_LABELS = {
    'sensevoice': 'SenseVoice-Small',
    'paraformer': 'Paraformer-Offline',
    'fun_asr_nano': 'FunASR-Nano GGUF',
    'qwen_asr': 'Qwen3-ASR GGUF',
}

MODEL_ALIASES = {
    'fun_asr_nano_gguf': 'fun_asr_nano',
    'qwen3_asr_gguf': 'qwen_asr',
}

MODEL_ARG_CLASSES = {
    'sensevoice': 'SenseVoiceArgs',
    'fun_asr_nano': 'FunASRNanoGGUFArgs',
    'qwen_asr': 'Qwen3ASRGGUFArgs',
}

MODEL_ONNX_CONFIG_KEYS = {
    'sensevoice': 'sensevoice_onnx_provider',
    'fun_asr_nano': 'fun_asr_onnx_provider',
    'qwen_asr': 'qwen_asr_onnx_provider',
}

MODEL_DML_CONFIG_KEYS = {
    'sensevoice': 'sensevoice_dml_pad_to',
    'fun_asr_nano': 'fun_asr_dml_pad_to',
    'qwen_asr': 'qwen_asr_dml_pad_to',
}

MODEL_LLM_GPU_CONFIG_KEYS = {
    'fun_asr_nano': 'fun_asr_llm_use_gpu',
    'qwen_asr': 'qwen_asr_llm_use_gpu',
}

LANGUAGE_ALIASES = {
    'zh': 'chinese',
    'cn': 'chinese',
    'en': 'english',
    'ja': 'japanese',
    'jp': 'japanese',
    'ko': 'korean',
    'kr': 'korean',
    'yue': 'cantonese',
}

LANGUAGE_LABELS = {
    'auto': '自动识别',
    'chinese': '中文',
    'english': '英文',
    'cantonese': '粤语',
    'japanese': '日语',
    'korean': '韩语',
    'arabic': '阿拉伯语',
    'german': '德语',
    'french': '法语',
    'spanish': '西班牙语',
    'portuguese': '葡萄牙语',
    'indonesian': '印尼语',
    'italian': '意大利语',
    'russian': '俄语',
    'thai': '泰语',
    'vietnamese': '越南语',
    'turkish': '土耳其语',
    'hindi': '印地语',
    'malay': '马来语',
    'dutch': '荷兰语',
    'swedish': '瑞典语',
    'danish': '丹麦语',
    'finnish': '芬兰语',
    'polish': '波兰语',
    'czech': '捷克语',
    'filipino': '菲律宾语',
    'persian': '波斯语',
    'greek': '希腊语',
    'romanian': '罗马尼亚语',
    'hungarian': '匈牙利语',
    'macedonian': '马其顿语',
}


def normalize_model_type(model_type: str | None) -> str:
    return MODEL_ALIASES.get((model_type or 'sensevoice').lower(), (model_type or 'sensevoice').lower())


def normalize_language_code(language: str | None) -> str:
    code = (language or 'auto').lower()
    return LANGUAGE_ALIASES.get(code, code if code in LANGUAGE_MAP else 'auto')


def model_required_files() -> dict[str, list[Path]]:
    return {
        'sensevoice': [ModelPaths.sensevoice_encoder, ModelPaths.sensevoice_decoder, ModelPaths.sensevoice_tokenizer],
        'paraformer': [ModelPaths.paraformer_model, ModelPaths.paraformer_tokens],
        'fun_asr_nano': [
            ModelPaths.fun_asr_nano_gguf_encoder_adaptor,
            ModelPaths.fun_asr_nano_gguf_ctc,
            ModelPaths.fun_asr_nano_gguf_llm_decode,
            ModelPaths.fun_asr_nano_gguf_token,
        ],
        'qwen_asr': [
            ModelPaths.qwen3_asr_gguf_encoder_frontend,
            ModelPaths.qwen3_asr_gguf_encoder_backend,
            ModelPaths.qwen3_asr_gguf_llm_decode,
        ],
    }


def model_install_status() -> dict[str, dict]:
    status = {}
    for model_type, files in model_required_files().items():
        missing = [path for path in files if not path.exists()]
        status[model_type] = {'installed': not missing, 'missing': missing}
    return status


def language_options() -> dict[str, str]:
    return {code: LANGUAGE_LABELS.get(code, code) for code in LANGUAGE_MAP}


def model_language_note(model_type: str) -> str:
    model_type = normalize_model_type(model_type)
    if model_type == 'sensevoice':
        return 'SenseVoice 支持自动识别、中文、英文、粤语、日语和韩语。'
    if model_type == 'fun_asr_nano':
        return 'FunASR-Nano 主要支持中文、英文和日语。'
    if model_type == 'qwen_asr':
        return 'Qwen3-ASR 支持更多语言，包括俄语、法语、德语、西班牙语等。'
    if model_type == 'paraformer':
        return 'Paraformer 是中文专用模型，会忽略语言选择。'
    return '不同模型支持的语言范围不同。'


def reveal_in_explorer(path: Path) -> None:
    """在资源管理器中定位文件；若文件不存在则打开父目录。"""
    path = Path(path)
    if path.exists() and path.is_file():
        open_path_foreground(path, select=True)
        return
    if path.exists() and path.is_dir():
        open_path_foreground(path)
        return
    parent = path.parent
    if parent.exists():
        open_path_foreground(parent)
        ui.notify(f'未找到文件，已打开父目录：{parent}', type='warning')
    else:
        ui.notify(f'路径不存在：{path}', type='warning')


def current_recording_assets_dir() -> Path:
    now = time.localtime()
    return BASE_DIR / time.strftime('%Y', now) / time.strftime('%m', now) / 'assets'


def recording_root_dir() -> Path:
    now = time.localtime()
    return BASE_DIR / time.strftime('%Y', now)


def recording_audio_files() -> list[Path]:
    files = []
    for year_dir in BASE_DIR.glob('[12][0-9][0-9][0-9]'):
        if not year_dir.is_dir():
            continue
        for path in year_dir.glob('[01][0-9]/assets/*'):
            if path.is_file() and path.suffix.lower() in {'.wav', '.mp3'}:
                files.append(path)
    return files


def diary_files() -> list[Path]:
    files = []
    for year_dir in BASE_DIR.glob('[12][0-9][0-9][0-9]'):
        if not year_dir.is_dir():
            continue
        for path in year_dir.glob('[01][0-9]/*.md'):
            if path.is_file():
                files.append(path)
    return files


def log_files() -> list[Path]:
    logs_dir = BASE_DIR / 'logs'
    return [path for path in logs_dir.glob('*.log') if path.is_file()] if logs_dir.exists() else []


def transcription_output_dirs() -> list[Path]:
    return [path for path in OUTPUT_DIR.iterdir() if path.is_dir()] if OUTPUT_DIR.exists() else []


def format_bytes(size: int) -> str:
    value = float(size)
    for unit in ('B', 'KB', 'MB', 'GB'):
        if value < 1024 or unit == 'GB':
            return f'{value:.1f} {unit}' if unit != 'B' else f'{int(value)} B'
        value /= 1024
    return f'{value:.1f} GB'


def storage_summary(files: list[Path], days: int = 30) -> dict:
    now = time.time()
    old_files = [path for path in files if path.exists() and now - path.stat().st_mtime > days * 86400]
    return {
        'count': len(files),
        'size': sum(path.stat().st_size for path in files if path.exists()),
        'old_count': len(old_files),
        'old_size': sum(path.stat().st_size for path in old_files),
        'old_files': old_files,
    }


def recording_storage_summary(days: int = 30) -> dict:
    return storage_summary(recording_audio_files(), days)


def diary_storage_summary(days: int = 30) -> dict:
    return storage_summary(diary_files(), days)


def log_storage_summary(days: int = 30) -> dict:
    return storage_summary(log_files(), days)


def transcription_storage_summary(days: int = 30) -> dict:
    dirs = transcription_output_dirs()
    files = [path for folder in dirs for path in folder.rglob('*') if path.is_file()]
    summary = storage_summary(files, days)
    summary['dir_count'] = len(dirs)
    summary['old_dirs'] = [path for path in dirs if time.time() - path.stat().st_mtime > days * 86400]
    return summary


def open_recording_assets_dir() -> None:
    folder = current_recording_assets_dir()
    folder.mkdir(parents=True, exist_ok=True)
    open_path_foreground(folder)
    ui.notify(f'已打开录音目录：{folder}', type='positive')


def open_recording_root_dir() -> None:
    folder = recording_root_dir()
    folder.mkdir(parents=True, exist_ok=True)
    open_path_foreground(folder)
    ui.notify(f'已打开录音年份目录：{folder}', type='positive')


def open_diary_root_dir() -> None:
    folder = recording_root_dir()
    folder.mkdir(parents=True, exist_ok=True)
    open_path_foreground(folder)
    ui.notify(f'已打开听写日记目录：{folder}', type='positive')


def open_transcription_output_dir() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    open_path_foreground(OUTPUT_DIR)
    ui.notify(f'已打开转写输出目录：{OUTPUT_DIR}', type='positive')


def open_logs_dir() -> None:
    logs_dir = BASE_DIR / 'logs'
    logs_dir.mkdir(exist_ok=True)
    open_path_foreground(logs_dir)
    ui.notify(f'已打开日志目录：{logs_dir}', type='positive')


def open_local_file(path: Path) -> None:
    """用系统默认程序打开本地文件。"""
    path = Path(path)
    if path.exists():
        open_path_foreground(path)
    else:
        ui.notify(f'文件不存在：{path}', type='warning')


def open_usage_guide_dialog() -> None:
    readme_path = BASE_DIR / 'readme.md'
    docs_dir = BASE_DIR / 'docs'

    sections = [
        (
            '通用与交互',
            '设置听写快捷键、长按/单击模式、听写浮层和最终输出方式。使用“确认后写入”时，听写完成后会先显示确认框；确认无误后可切回目标输入框粘贴，或按 Esc 关闭确认框。',
        ),
        (
            '本地数据管理',
            '查看最近听写输入，管理听写日记、录音文件、转写输出和日志。清空输入历史只影响 GUI 历史，不会删除录音、日记和转写文件。',
        ),
        (
            'AI 润色与角色',
            '配置 AI API 档案、默认润色和角色功能。普通听写不依赖 API；只有开启 AI 润色、翻译、小助理、大助理等功能时才需要 API Key。API Key 只保存在本机，配置导出默认不包含 Key。',
        ),
        (
            '热词与替换规则',
            'hot.txt 用于常见词、品牌名、开发术语的固定修正；hot-rule.txt 用于邮箱、符号、标点、换行等更复杂的正则替换。修改后保存即可生效。',
        ),
        (
            '语音识别与硬件',
            '选择本地 ASR 模型、识别语言、输出格式和硬件加速策略。完整版通常已内置推荐模型；精简版需要先下载模型并放入 models 目录。',
        ),
        (
            '字幕转写',
            '选择本地音频或视频文件，生成 TXT、SRT、JSON 等结果。如果要人工修字幕，建议同时生成 TXT 和 JSON；修改 TXT 后可用“字幕修复”重新生成 SRT。',
        ),
        (
            '服务与诊断',
            '查看服务端、客户端和 GUI 的运行状态。当听写无法启动、托盘异常或模型加载失败时，可以在这里重启组件、查看日志或完全退出。',
        ),
        (
            '配置备份与迁移',
            '导出或导入配置方案，用于换电脑或重装。自动备份用于恢复误操作；发布包不会包含你的 API Key、历史输入、录音和日志。',
        ),
    ]

    with ui.dialog() as dialog, ui.card().classes('w-full max-w-5xl bg-white rounded-2xl p-0 overflow-hidden'):
        with ui.row().classes('items-start justify-between w-full px-8 pt-6 pb-4 border-b border-slate-100'):
            with ui.column().classes('gap-1'):
                ui.label('CapsWriter 使用说明').classes('text-2xl font-bold text-slate-900')
                ui.label('离线语音输入与音视频转写工具。普通听写不需要联网；AI 润色、翻译和角色功能需要自行配置 API。').classes('text-sm text-slate-500')
            ui.button(icon='close', on_click=dialog.close).props('flat round color=grey-7')

        with ui.scroll_area().classes('w-full h-[70vh] max-h-[720px] px-8 py-5'):
            with ui.grid(columns=2).classes('gap-3 w-full'):
                for index, (title, body) in enumerate(sections, start=1):
                    with ui.row().classes('items-start gap-3 p-3 rounded-xl border border-slate-100 bg-slate-50/70 w-full min-h-[112px]'):
                        ui.label(str(index)).classes('w-7 h-7 rounded-full bg-amber-100 text-amber-700 text-sm font-bold flex items-center justify-center shrink-0')
                        with ui.column().classes('gap-0.5'):
                            ui.label(title).classes('text-sm font-bold text-slate-900')
                            ui.label(body).classes('text-xs text-slate-600 leading-relaxed')

        with ui.row().classes('items-center justify-between w-full gap-3 px-8 py-4 border-t border-slate-100 bg-white'):
            with ui.row().classes('items-center gap-3'):
                ui.button('打开 README', icon='article', on_click=lambda: open_local_file(readme_path)).props('outline color=grey-8').classes('bg-white')
                ui.button('打开 docs 目录', icon='folder_open', on_click=lambda: open_path_foreground(docs_dir)).props('outline color=orange').classes('bg-white')
            ui.button('关闭', on_click=dialog.close).props('color=blue').classes('px-6')

    dialog.open()


def copy_text_to_clipboard(text: str) -> None:
    """复制文本到系统剪贴板。"""
    try:
        import pyclip
        pyclip.copy(text or '')
    except Exception:
        pass
    else:
        ui.notify('已复制到剪贴板。', type='positive')
        return

    if sys.platform == 'win32':
        try:
            import ctypes
            from ctypes import wintypes

            CF_UNICODETEXT = 13
            GMEM_MOVEABLE = 0x0002
            data = (text or '').encode('utf-16-le') + b'\x00\x00'
            if not ctypes.windll.user32.OpenClipboard(0):
                raise RuntimeError('无法打开剪贴板')
            try:
                ctypes.windll.user32.EmptyClipboard()
                handle = ctypes.windll.kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
                if not handle:
                    raise RuntimeError('剪贴板内存分配失败')
                ptr = ctypes.windll.kernel32.GlobalLock(handle)
                ctypes.memmove(ptr, data, len(data))
                ctypes.windll.kernel32.GlobalUnlock(handle)
                ctypes.windll.user32.SetClipboardData(CF_UNICODETEXT, handle)
            finally:
                ctypes.windll.user32.CloseClipboard()
            ui.notify('已复制到剪贴板。', type='positive')
            return
        except Exception as e:
            ui.notify(f'复制失败：{e}', type='negative')
            return

    ui.notify('复制失败：当前系统暂不支持剪贴板。', type='negative')


@ui.page('/')
def main_page():
    t_page_start = time.perf_counter()
    cfg = ConfigManager.get_all_config()
    health = process_manager.get_health_status()

    refresh_all_js = '''() => {
            const button = document.getElementById('refresh-all');
            button?.classList.add('animate-spin');
            window.Quasar?.Notify?.create({
                message: '正在刷新配置...', color: 'amber-7', timeout: 550,
                classes: 'cw-toast', group: false,
            });
            window.setTimeout(() => {
                button?.classList.remove('animate-spin');
                window.Quasar?.Notify?.create({
                    message: '配置已刷新。', color: 'positive', timeout: 800,
                    classes: 'cw-toast', group: false,
                });
            }, 500);
            window.setTimeout(() => window.location.reload(), 1250);
        }'''

    # 当前只启用浅色主题。
    ui.dark_mode(value=False)

    # --- 自定义细滚动条样式与 Quasar 溢出滑动彻底解锁 ---
    ui.add_head_html('''
        <style>
            /* 1. 禁用全局外层 <body> 滚动条 */
            html, body {
                overflow: hidden !important;
                margin: 0 !important;
                padding: 0 !important;
                height: 100vh !important;
                width: 100vw !important;
            }
            /* 2. 彻底解除 Quasar Tab 容器对滚动的限制 */
            .q-tab-panels, .q-tab-panel {
                background: transparent !important;
                overflow: visible !important;
                padding: 0 !important;
            }
            /* 3. 定制通用优雅细滚动条 */
            ::-webkit-scrollbar { width: 7px; height: 7px; }
            ::-webkit-scrollbar-track { background: rgba(0, 0, 0, 0.02); border-radius: 9999px; }
            ::-webkit-scrollbar-thumb { background: rgba(156, 163, 175, 0.4); border-radius: 9999px; }
            ::-webkit-scrollbar-thumb:hover { background: rgba(156, 163, 175, 0.7); }
            .cw-toast {
                min-height: 42px !important;
                max-width: min(420px, calc(100vw - 48px)) !important;
                padding: 8px 14px !important;
                border-radius: 12px !important;
                box-shadow: 0 14px 34px rgba(146, 64, 14, 0.20), 0 2px 8px rgba(15, 23, 42, 0.08) !important;
                border: 1px solid rgba(255, 237, 213, 0.68) !important;
                font-size: 13px !important;
                font-weight: 600 !important;
                line-height: 1.35 !important;
            }
            .cw-toast .q-notification__message {
                font-size: 13px !important;
                line-height: 1.35 !important;
            }
            .cw-toast .q-notification__avatar,
            .cw-toast .q-notification__icon {
                font-size: 20px !important;
                margin-right: 8px !important;
            }
            .cw-toast .q-badge,
            .cw-toast .q-notification__badge,
            .q-notification .q-notification__badge {
                display: none !important;
            }
            /* 4. 完美支持无边框原生拖拽与控件抗阻解包 */
            .pywebview-drag-region {
                -webkit-app-region: drag;
            }
            .pywebview-drag-region button,
            .pywebview-drag-region input,
            .pywebview-drag-region a,
            .pywebview-drag-region .q-btn,
            .pywebview-drag-region .no-drag {
                -webkit-app-region: no-drag !important;
            }
            .rule-editor .q-field__control {
                min-height: var(--rule-editor-height, 240px) !important;
                border-radius: 14px !important;
                background: #ffffff !important;
                border: 1px solid rgb(226 232 240) !important;
                box-shadow: inset 0 1px 0 rgba(15, 23, 42, 0.03) !important;
            }
            .rule-editor .q-field__native {
                min-height: calc(var(--rule-editor-height, 240px) - 42px) !important;
                max-height: calc(var(--rule-editor-height, 240px) - 42px) !important;
                overflow-y: auto !important;
                resize: none !important;
                padding: 16px 18px !important;
                font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace !important;
                font-size: 14px !important;
                line-height: 1.55 !important;
                color: #1f2937 !important;
                caret-color: #f59e0b !important;
            }
            .rule-editor .q-field__control::before,
            .rule-editor .q-field__control::after {
                display: none !important;
            }

        </style>
    ''')

    # --- 顶栏 Header (原生窗口模式精简 Header) ---
    with ui.header().classes('w-full bg-white dark:bg-slate-900 border-b border-slate-200 dark:border-slate-800 px-8 py-3 flex justify-between items-center z-50 text-slate-800 dark:text-slate-100 shadow-sm'):
        with ui.row().classes('items-center gap-4'):
            ui.image('/assets/ui/capswriter_logo.png').classes('w-14 h-14 rounded-2xl shrink-0')
            ui.label('CapsWriter').classes('text-4xl font-bold text-slate-900 dark:text-white tracking-wide')

        with ui.row().classes('items-center gap-4'):
            # 状态指示器
            with ui.row().classes('items-center gap-2 bg-slate-100 dark:bg-slate-800 px-3 py-1.5 rounded-full border border-slate-200 dark:border-slate-700'):
                dot_class = 'bg-emerald-500' if health['server_alive'] and health['client_alive'] else 'bg-amber-500'
                ui.element('span').classes(f'w-2.5 h-2.5 rounded-full {dot_class} animate-pulse')
                ui.label(
                    f"服务 {health['server_pid'] or '-'} / 客户端 {health['client_pid'] or '-'}"
                ).classes('text-xs text-slate-600 dark:text-slate-300 font-medium')
            
            refresh_all_button = ui.button(icon='refresh').props('flat round color=grey-8 id=refresh-all title="刷新所有配置"')
            refresh_all_button.on('click', js_handler=refresh_all_js)
            ui.button(icon='help_outline', on_click=open_usage_guide_dialog).props('flat round color=grey-8 title="使用说明"')

    # --- 主体双栏布局 (无外层多余滚动条，精准内部延伸) ---
    with ui.row().classes('w-full h-[calc(100vh-80px)] overflow-hidden bg-slate-100/70 dark:bg-slate-950 text-slate-800 dark:text-slate-100 p-6 xl:px-10 gap-6 items-start flex-nowrap'):

        # --- 左侧导航边栏 ---
        with ui.card().classes('w-64 max-h-full overflow-y-auto bg-slate-200/50 dark:bg-slate-900/90 border border-slate-300/60 dark:border-slate-800 p-3 rounded-2xl shrink-0 gap-1 shadow-sm'):
            with ui.row().classes('items-center gap-2 px-3 py-2 text-slate-500 dark:text-slate-400'):
                ui.icon('tune', size='xs')
                ui.label('系统设置导航').classes('text-xs font-bold uppercase tracking-wider')
            
            with ui.tabs().props('vertical indicator-color=amber-7 text-color=grey-7 active-color=amber-9 active-bg-color=white dark:active-bg-color=slate-800 switch-indicator').classes('w-full rounded-xl') as tabs:
                tab_general = ui.tab('通用与交互', icon='settings')
                tab_data = ui.tab('本地数据管理', icon='storage')
                tab_ai = ui.tab('AI 润色与角色', icon='auto_awesome')
                tab_hotwords = ui.tab('热词与替换规则', icon='rule')
                tab_engine = ui.tab('语音识别与硬件', icon='graphic_eq')
                tab_transcribe = ui.tab('字幕转写', icon='video_library')
                tab_service = ui.tab('服务与诊断', icon='monitor_heart')
                tab_backup = ui.tab('配置备份与迁移', icon='folder_zip')

        # --- 右侧主内容展区：单级精细滚动条 ---
        with ui.card().classes('flex-1 h-full max-h-full min-w-0 bg-white dark:bg-slate-900 border border-slate-200/80 dark:border-slate-800 p-8 rounded-2xl shadow-sm overflow-y-auto'):
            with ui.tab_panels(tabs, value=tab_general, animated=False).classes('w-full bg-transparent border-0 p-0'):

                # === Tab 1: ⚙️ 通用与外观 ===
                with ui.tab_panel(tab_general):
                    with ui.column().classes('gap-6 w-full pb-8'):
                        with ui.column().classes('gap-1 border-b border-slate-100 dark:border-slate-800 pb-4 w-full'):
                            ui.label('通用与交互').classes('text-2xl font-bold text-slate-900 dark:text-white')
                            ui.label('配置 CapsWriter 在桌面上的触发热键、对讲模式、听写状态浮层及基础输入体验。').classes('text-sm text-slate-500 dark:text-slate-400')

                        # 卡片 1：热键与触发模式
                        ui.label('快捷键与对讲交互').classes('text-xs font-bold text-slate-400 uppercase tracking-wider mt-2')
                        with ui.card().classes('bg-slate-50 dark:bg-slate-800/50 border border-slate-200/60 dark:border-slate-700/60 p-6 rounded-xl gap-4 w-full shadow-none'):
                            shortcut_state = {'value': cfg['shortcut']}

                            async def save_key_cfg():
                                ConfigManager.set_active_shortcut(shortcut_state['value'])
                                ok, message = await run.io_bound(process_manager.restart_client)
                                ui.notify(f'快捷键配置保存成功，客户端已自动更新！{message}', type='positive')

                            async def handle_hold_mode_change(e):
                                ConfigManager.set_hold_mode(e.value)
                                ok, message = await run.io_bound(process_manager.restart_client)
                                mode_text = '长按对讲' if e.value else '按一下开始/按一下结束（单击）'
                                ui.notify(f'已切换为【{mode_text}】模式，客户端已自动更新！{message}', type='positive')

                            def open_shortcut_capture():
                                with ui.dialog() as dialog, ui.card().classes('w-full max-w-md p-6 bg-white rounded-2xl gap-5 shadow-xl border border-amber-100'):
                                    ui.label('更改听写触发快捷键').classes('text-lg font-bold text-slate-900')
                                    ui.label('按下任意单键或组合键，例如 Ctrl + Shift + Space。确认后重启客户端生效。').classes('text-sm text-slate-500')
                                    captured = {'value': None}
                                    capture_label = ui.label('等待按键...').classes('w-full text-center text-2xl font-bold text-amber-700 bg-amber-50 border border-amber-200 rounded-xl py-5')
                                    hidden_input = ui.input().props('autofocus readonly').classes('opacity-0 h-0 p-0 m-0')

                                    def handle_capture(e):
                                        next_shortcut = shortcut_from_keyboard_event(e.args)
                                        if not next_shortcut:
                                            return
                                        captured['value'] = next_shortcut
                                        capture_label.set_text(shortcut_display_name(next_shortcut))

                                    hidden_input.on(
                                        'keydown',
                                        handle_capture,
                                        js_handler="""(event) => {
                                            event.preventDefault();
                                            event.stopPropagation();
                                            if (event.repeat) return;
                                            emit({
                                                key: event.key,
                                                code: event.code,
                                                ctrlKey: event.ctrlKey,
                                                altKey: event.altKey,
                                                shiftKey: event.shiftKey,
                                                metaKey: event.metaKey,
                                                repeat: event.repeat,
                                            });
                                        }""",
                                    )

                                    async def apply_captured():
                                        if not captured['value']:
                                            ui.notify('请先按下一个新的快捷键。', type='warning')
                                            return
                                        shortcut_state['value'] = captured['value']
                                        shortcut_badge.set_text(shortcut_display_name(shortcut_state['value']))
                                        dialog.close()
                                        await save_key_cfg()

                                    with ui.row().classes('justify-end gap-3 w-full'):
                                        ui.button('取消', on_click=dialog.close).props('flat color=grey-7')
                                        ui.button('确认使用', icon='check', on_click=apply_captured).classes('bg-amber-600 text-white px-5')

                                dialog.open()
                                ui.timer(0.05, lambda: hidden_input.run_method('focus'), once=True)

                            with ui.row().classes('items-center justify-between w-full'):
                                with ui.column().classes('gap-0.5'):
                                    ui.label('听写触发快捷键').classes('font-semibold text-slate-900 dark:text-slate-100 text-base')
                                    ui.label('长按此键讲话，松开即可自动将识别文字打字上屏。').classes('text-xs text-slate-500 dark:text-slate-400')

                                with ui.row().classes('items-center gap-3'):
                                    shortcut_badge = ui.label(shortcut_display_name(shortcut_state['value'])).classes('min-w-36 text-center px-4 py-2 rounded-xl bg-white border border-amber-200 text-amber-700 text-base font-bold shadow-sm')
                                    ui.button('更改按键', icon='keyboard', on_click=open_shortcut_capture).props('outline color=amber-8').classes('h-10 px-4 rounded-lg text-sm font-semibold bg-white')

                            ui.separator().classes('bg-slate-200/60 dark:bg-slate-700/60')

                            with ui.row().classes('items-center justify-between w-full'):
                                with ui.column().classes('gap-0.5'):
                                    ui.label('开启长按对讲模式').classes('font-semibold text-slate-900 dark:text-slate-100 text-base')
                                    ui.label('开启后按住说话，关闭后变为“按一下开始录音、再按一下结束”。').classes('text-xs text-slate-500 dark:text-slate-400')
                                
                                ui.switch(
                                    value=cfg['hold_mode'],
                                    on_change=handle_hold_mode_change
                                ).props('color=amber-8')

                            ui.button('保存按键配置', icon='save', on_click=save_key_cfg).classes('bg-amber-600 dark:bg-emerald-600 text-white self-end px-6')

                        # 卡片 2：听写状态反馈
                        ui.label('听写状态反馈').classes('text-xs font-bold text-slate-400 uppercase tracking-wider mt-2')
                        with ui.card().classes('bg-slate-50 dark:bg-slate-800/50 border border-slate-200/60 dark:border-slate-700/60 p-6 rounded-xl gap-5 w-full shadow-none'):
                            with ui.row().classes('items-center justify-between w-full'):
                                with ui.column().classes('gap-0.5'):
                                    ui.label('启用听写状态浮层').classes('font-semibold text-slate-900 dark:text-slate-100 text-base')
                                    ui.label('按住说话时在屏幕底部显示白底橙色声波反馈，帮助确认麦克风正在工作。').classes('text-xs text-slate-500 dark:text-slate-400')

                                def handle_overlay_change(e):
                                    ConfigManager.set_pill_overlay_enabled(e.value)
                                    ui.notify('听写状态浮层开关已保存，下次听写生效。', type='positive')

                                ui.switch(
                                    value=cfg['pill_overlay'],
                                    on_change=handle_overlay_change
                                ).props('color=amber-8')

                            ui.separator().classes('bg-slate-200/60 dark:bg-slate-700/60')

                            def sync_preview_settings_visibility():
                                preview_enabled = output_destination_select.value == 'overlay_preview'
                                auto_close = preview_close_select.value == 'auto'
                                preview_settings_container.set_visibility(preview_enabled)
                                preview_timing_row.set_visibility(preview_enabled and auto_close)

                            def handle_output_destination_change(e):
                                ConfigManager.set_client_var('output_destination', e.value)
                                sync_preview_settings_visibility()
                                ui.notify('最终输出方式已保存，下次听写生效。', type='positive')

                            def handle_preview_close_change(e):
                                ConfigManager.set_client_var('preview_close_mode', e.value)
                                sync_preview_settings_visibility()
                                ui.notify('确认浮层停留方式已保存，下次听写生效。', type='positive')

                            with ui.row().classes('items-center justify-between w-full gap-4 flex-wrap'):
                                with ui.column().classes('gap-0.5 min-w-0'):
                                    ui.label('最终输出方式').classes('font-semibold text-slate-900 dark:text-slate-100 text-base')
                                    ui.label('选择识别完成后的文本去向。确认后写入会先显示可编辑结果框，并自动复制到剪贴板。').classes('text-xs text-slate-500 dark:text-slate-400')

                                output_destination_select = ui.select(
                                    options={'typing': '直接写入光标位置', 'overlay_preview': '确认后写入'},
                                    value=cfg.get('output_destination', 'typing'),
                                    label='最终输出方式',
                                    on_change=handle_output_destination_change,
                                ).classes('w-56')

                            with ui.column().classes('gap-5 w-full') as preview_settings_container:
                                with ui.row().classes('items-center justify-between w-full gap-4 flex-wrap'):
                                    with ui.column().classes('gap-0.5 min-w-0'):
                                        ui.label('确认浮层停留方式').classes('font-semibold text-slate-900 dark:text-slate-100 text-base')
                                        ui.label('自动模式会按文本长度延长停留；点进文本框编辑后会暂停自动关闭。').classes('text-xs text-slate-500 dark:text-slate-400')

                                    preview_close_select = ui.select(
                                        options={'auto': '按文本长度自动关闭', 'manual': '手动关闭'},
                                        value=cfg.get('preview_close_mode', 'auto'),
                                        label='停留方式',
                                        on_change=handle_preview_close_change,
                                    ).classes('w-56')

                                with ui.row().classes('items-center justify-end w-full gap-3 flex-wrap') as preview_timing_row:
                                    preview_base_in = ui.number(label='最短停留秒数', value=cfg.get('preview_base_seconds', 8), min=2, max=60, step=1).classes('w-40')
                                    preview_max_in = ui.number(label='最长停留秒数', value=cfg.get('preview_max_seconds', 60), min=8, max=300, step=5).classes('w-40')

                                    def save_preview_timing():
                                        base = int(preview_base_in.value or 8)
                                        max_seconds = int(preview_max_in.value or 60)
                                        if max_seconds < base:
                                            preview_max_in.value = base
                                            max_seconds = base
                                        ConfigManager.set_client_var('preview_base_seconds', base)
                                        ConfigManager.set_client_var('preview_max_seconds', max_seconds)
                                        ui.notify('确认浮层停留时间已保存，下次听写生效。', type='positive')

                                    ui.button('保存停留时间', icon='schedule', on_click=save_preview_timing).props('outline color=amber-8').classes('h-10 px-4 rounded-lg text-sm bg-white')

                            sync_preview_settings_visibility()
                        with ui.expansion('高级输入输出兼容性', icon='tune', value=False).classes('w-full bg-slate-50 dark:bg-slate-800/50 border border-slate-200/60 dark:border-slate-700/60 rounded-xl text-sm'):
                            with ui.column().classes('gap-5 p-5 w-full'):
                                ui.label('用于处理少数软件的输入兼容问题：有些应用需要强制粘贴上屏，有些应用在输出后需要自动回车。普通用户一般不需要改这里。').classes('text-sm text-slate-500 leading-relaxed')
                                paste_apps_text = '\n'.join(cfg.get('paste_apps', []))
                                enter_apps_text = '\n'.join(f'{name}, {delay}' for name, delay in cfg.get('enter_apps', []))

                                paste_apps_editor = ui.textarea(
                                    label='强制粘贴应用名单',
                                    value=paste_apps_text,
                                    placeholder='WeiXin.exe\nTelegram.exe',
                                ).props('outlined autogrow input-style="min-height: 92px; line-height: 1.55;"').classes('w-full font-mono text-sm bg-white')
                                ui.label('每行一个进程名。列在这里的应用会优先使用“粘贴文本”方式输出。').classes('text-xs text-slate-500 -mt-3')

                                enter_apps_editor = ui.textarea(
                                    label='输出后自动回车应用',
                                    value=enter_apps_text,
                                    placeholder='happ.exe, 0.5\nhexin.exe, 0.5',
                                ).props('outlined autogrow input-style="min-height: 92px; line-height: 1.55;"').classes('w-full font-mono text-sm bg-white')
                                ui.label('格式为“进程名, 延迟秒数”。例如 happ.exe, 0.5 表示输出后等待 0.5 秒再回车。').classes('text-xs text-slate-500 -mt-3')

                                def save_advanced_input():
                                    paste_apps = [line.strip() for line in paste_apps_editor.value.splitlines() if line.strip()]
                                    enter_apps = []
                                    for line in enter_apps_editor.value.splitlines():
                                        raw = line.strip()
                                        if not raw:
                                            continue
                                        parts = [p.strip() for p in raw.split(',', 1)]
                                        if len(parts) == 1:
                                            enter_apps.append((parts[0], 0.5))
                                            continue
                                        try:
                                            delay = float(parts[1])
                                        except ValueError:
                                            ui.notify(f'自动回车延迟不是数字：{raw}', type='negative')
                                            return
                                        enter_apps.append((parts[0], delay))
                                    ConfigManager.set_client_var('paste_apps', paste_apps)
                                    ConfigManager.set_client_var('enter_apps', enter_apps)
                                    ui.notify('高级输入控制已保存。可在托盘点“重启听写服务”生效，不需要完全退出。', type='positive')

                                ui.button('保存高级输入控制', icon='save', on_click=save_advanced_input).classes('bg-amber-600 text-white self-end px-6')

                # === Tab 2: 本地数据管理 ===
                with ui.tab_panel(tab_data):
                    with ui.column().classes('gap-6 w-full pb-8'):
                        with ui.column().classes('gap-1 border-b border-slate-100 dark:border-slate-800 pb-4 w-full'):
                            ui.label('本地数据管理').classes('text-2xl font-bold text-slate-900 dark:text-white')
                            ui.label('集中查看和清理输入历史、听写日记、录音、转写输出与日志。所有数据只保存在本机。').classes('text-sm text-slate-500 dark:text-slate-400')

                        with ui.card().classes('bg-slate-50 dark:bg-slate-800/50 border border-slate-200/60 dark:border-slate-700/60 p-6 rounded-xl gap-4 w-full shadow-none'):
                            history_view = {'mode': 'compact'}

                            # 头部：左侧标题，右侧固定右对齐【设置】与【展开全部/收起】按钮
                            with ui.row().classes('items-center justify-between w-full gap-3 flex-wrap'):
                                ui.label('历史记录').classes('font-semibold text-slate-900 dark:text-slate-100 text-lg')

                                with ui.row().classes('items-center gap-3 shrink-0'):
                                    refresh_history_button = ui.button('刷新', icon='refresh', on_click=lambda: (render_input_history.refresh(), ui.notify('历史记录已刷新。', type='info'))).props('outline color=amber-8').classes('h-9 px-3 rounded-lg text-sm bg-white')
                                    history_settings_button = ui.button('设置', icon='tune').props('outline color=grey-8').classes('h-9 px-3 rounded-lg text-sm bg-white')
                                    toggle_history_button = ui.button('展开全部', icon='unfold_more').props('flat color=amber-8').classes('h-9 px-3 rounded-lg text-sm')

                            history_query = {'value': ''}
                            with ui.row().classes('items-center gap-2 w-full flex-nowrap'):
                                search_input = ui.input(
                                    placeholder='搜索历史文本、应用名或角色...'
                                ).props('outlined dense clearable').classes('flex-1 bg-white')
                                search_button = ui.button(icon='search').props('outline round color=amber-8 title="搜索历史"')

                            def show_history_detail(item):
                                with ui.dialog() as dialog, ui.card().classes('w-full max-w-2xl p-6 bg-white rounded-2xl gap-4'):
                                    with ui.row().classes('items-start justify-between w-full gap-3'):
                                        with ui.column().classes('gap-1 min-w-0'):
                                            ui.label('历史全文').classes('text-lg font-bold text-slate-900')
                                            ui.label(item.get('created_at', '')).classes('text-xs text-slate-500')
                                        ui.button(icon='close', on_click=dialog.close).props('flat round color=grey-7')

                                    ui.textarea(
                                        value=item.get('text', ''),
                                        label='最终输出文本'
                                    ).props('readonly autogrow outlined').classes('w-full text-base leading-relaxed')

                                    original = item.get('original_text') or ''
                                    if original and original != item.get('text', ''):
                                        ui.textarea(
                                            value=original,
                                            label='原始识别文本'
                                        ).props('readonly autogrow outlined').classes('w-full text-sm leading-relaxed')

                                    with ui.row().classes('justify-end gap-3 w-full'):
                                        ui.button('复制最终文本', icon='content_copy', on_click=partial(copy_text_to_clipboard, item.get('text', ''))).classes('bg-amber-600 text-white px-5')

                                dialog.open()

                            def apply_history_search(val):
                                history_query['value'] = val or ''
                                render_input_history.refresh()

                            @ui.refreshable
                            def render_input_history():
                                query = history_query['value'].strip().lower()
                                records = load_input_history(limit=80)

                                if query:
                                    records = [
                                        r for r in records
                                        if query in r.get('text', '').lower()
                                        or query in (r.get('original_text') or '').lower()
                                        or query in (r.get('process_name') or '').lower()
                                        or query in (r.get('role_name') or '').lower()
                                    ]
                                elif history_view['mode'] == 'compact':
                                    records = records[:3]

                                with ui.column().classes('gap-3 w-full'):
                                    if not records:
                                        message = '没有找到匹配的历史记录。' if query else '暂无历史记录。完成一次听写输出后，这里会自动出现记录。'
                                        ui.label(message).classes('text-sm text-slate-500 py-6')
                                        return

                                    for item in records:
                                        text = item.get('text', '')
                                        preview = text if len(text) <= 180 else f'{text[:180]}...'
                                        role_name = item.get('role_name') or ''
                                        process_name = item.get('process_name') or '未知应用'
                                        mode_label = 'AI 润色' if item.get('processed') else '直接输出'
                                        paste_label = '粘贴' if item.get('paste') else '打字'

                                        with ui.card().classes('w-full bg-white border border-slate-200/80 rounded-xl p-4 gap-3 shadow-none'):
                                            with ui.row().classes('items-start justify-between gap-4 w-full'):
                                                with ui.column().classes('gap-2 min-w-0 flex-1'):
                                                    with ui.row().classes('items-center gap-2 flex-wrap'):
                                                        ui.label(item.get('created_at', '')).classes('text-xs font-semibold text-slate-500')
                                                        ui.badge(mode_label, color='orange-1').classes('text-amber-700 border border-amber-200')
                                                        ui.badge(paste_label, color='grey-2').classes('text-slate-600 border border-slate-200')
                                                        if role_name:
                                                            ui.badge(role_name, color='blue-1').classes('text-blue-700 border border-blue-200')
                                                        ui.label(process_name).classes('text-xs text-slate-400')
                                                    ui.label(preview).classes('text-base text-slate-900 leading-relaxed whitespace-pre-wrap break-words')

                                                with ui.row().classes('items-center gap-2 shrink-0'):
                                                    ui.button(icon='content_copy', on_click=partial(copy_text_to_clipboard, text)).props('flat round color=amber-8 title="复制最终文本"')
                                                    ui.button(icon='open_in_full', on_click=partial(show_history_detail, item)).props('flat round color=grey-7 title="查看全文"')

                            render_input_history()

                            def confirm_clear_history():
                                with ui.dialog() as dialog, ui.card().classes('w-full max-w-sm p-6 bg-white rounded-2xl gap-4'):
                                    ui.label('清空历史记录？').classes('text-lg font-bold text-slate-900')
                                    ui.label('这只会清除 GUI 历史记录和旧日记回填显示，不删除 .md 日记、录音、转写输出和配置。').classes('text-sm text-slate-500')

                                    def apply_clear_history():
                                        clear_input_history()
                                        history_query['value'] = ''
                                        search_input.value = ''
                                        dialog.close()
                                        render_input_history.refresh()
                                        ui.notify('历史记录已清空。', type='positive')

                                    with ui.row().classes('justify-end w-full gap-3'):
                                        ui.button('取消', on_click=dialog.close).props('flat color=grey-7')
                                        ui.button('清空', icon='delete', on_click=apply_clear_history).props('unelevated color=red-7')

                                dialog.open()

                            def set_history_view(mode: str):
                                history_view['mode'] = mode
                                if mode == 'expanded':
                                    toggle_history_button.set_text('收起')
                                else:
                                    toggle_history_button.set_text('展开全部')
                                render_input_history.refresh()

                            def toggle_history_view():
                                next_mode = 'compact' if history_view['mode'] == 'expanded' else 'expanded'
                                set_history_view(next_mode)

                            def open_history_settings_dialog():
                                current_auto_clear = ConfigManager.get_client_var('history_auto_clear_on_start', False)
                                current_max_items = ConfigManager.get_client_var('history_max_items', 0)
                                init_mode = 'unlimited' if current_max_items == 0 else 'custom'
                                init_num = current_max_items if current_max_items > 0 else 200

                                with ui.dialog() as dialog, ui.card().classes('w-full max-w-md p-6 bg-white rounded-2xl gap-5'):
                                    with ui.row().classes('items-center justify-between w-full'):
                                        ui.label('历史记录设置').classes('text-xl font-bold text-slate-900 dark:text-slate-100')
                                        ui.button(icon='close', on_click=dialog.close).props('flat round color=grey-7')

                                    # 1. 启动自动清空
                                    with ui.column().classes('gap-1 w-full bg-slate-50 dark:bg-slate-800/80 p-4 rounded-xl border border-slate-200/60 dark:border-slate-700'):
                                        auto_sw = ui.switch(
                                            '每次重启/启动 CapsWriter 时自动清空历史',
                                            value=current_auto_clear
                                        ).props('dense').classes('text-sm font-medium text-slate-800 dark:text-slate-200')
                                        ui.label('开启后，重新启动应用时会自动清除过往历史记录。').classes('text-xs text-slate-500 dark:text-slate-400 pl-7')

                                    # 2. 保留策略
                                    with ui.column().classes('gap-2 w-full bg-slate-50 dark:bg-slate-800/80 p-4 rounded-xl border border-slate-200/60 dark:border-slate-700'):
                                        ui.label('历史记录保留策略').classes('text-sm font-semibold text-slate-800 dark:text-slate-200')

                                        mode_radio = ui.radio(
                                            options={
                                                'unlimited': '全部保留 (不限制历史保存条数，默认)',
                                                'custom': '指定保留条数上限 (手动设定条数)'
                                            },
                                            value=init_mode
                                        ).props('dense').classes('text-sm text-slate-700 dark:text-slate-300 gap-2')

                                        num_input_container = ui.column().classes('w-full pl-6 pt-1')
                                        with num_input_container:
                                            num_input = ui.number(
                                                label='手动输入保存条数上限',
                                                value=init_num,
                                                min=10,
                                                max=50000,
                                                step=10
                                            ).props('outlined dense').classes('w-full text-sm bg-white dark:bg-slate-900')
                                            num_input.set_visibility(init_mode == 'custom')

                                        def on_mode_change(e):
                                            num_input.set_visibility(e.value == 'custom')
                                        mode_radio.on_value_change(on_mode_change)

                                    # 3. 清空当前历史（正常中性外观）
                                    with ui.column().classes('gap-1 w-full bg-slate-50 dark:bg-slate-800/80 p-4 rounded-xl border border-slate-200/60 dark:border-slate-700'):
                                        with ui.row().classes('items-center justify-between w-full'):
                                            with ui.column().classes('gap-0.5'):
                                                ui.label('清空当前历史').classes('text-sm font-semibold text-slate-800 dark:text-slate-200')
                                                ui.label('擦除本机所有已保存的历史记录').classes('text-xs text-slate-500 dark:text-slate-400')
                                            ui.button('清空历史', icon='delete', on_click=lambda: (dialog.close(), confirm_clear_history())).props('outline color=grey-8').classes('h-9 px-3 rounded-lg text-xs bg-white dark:bg-slate-900')

                                    # 底部操作按钮
                                    with ui.row().classes('justify-end w-full gap-2 pt-2'):
                                        ui.button('取消', on_click=dialog.close).props('flat color=grey-7').classes('h-9 px-4 rounded-lg text-sm')
                                        def save_and_close():
                                            try:
                                                new_auto_clear = bool(auto_sw.value)
                                                if mode_radio.value == 'unlimited':
                                                    new_max_items = 0
                                                else:
                                                    try:
                                                        new_max_items = max(10, int(num_input.value or 200))
                                                    except (ValueError, TypeError):
                                                        new_max_items = 200

                                                ConfigManager.set_client_var('history_auto_clear_on_start', new_auto_clear)
                                                ConfigManager.set_client_var('history_max_items', new_max_items)
                                                render_input_history.refresh()
                                                dialog.close()
                                                mode_desc = '全部保留 (不限条数)' if new_max_items == 0 else f'最多保留 {new_max_items} 条'
                                                ui.notify(f'历史配置已保存：{mode_desc}', type='positive')
                                            except Exception as ex:
                                                ui.notify(f'保存失败: {ex}', type='negative')

                                        ui.button('保存设置', icon='check', on_click=save_and_close).props('unelevated color=amber-8').classes('h-9 px-4 rounded-lg text-sm')

                                dialog.open()

                            search_input.on('update:model-value', lambda e: apply_history_search(e.args))
                            search_button.on_click(lambda *_: render_input_history.refresh())
                            search_input.on('keydown.enter', lambda *_: render_input_history.refresh())
                            history_settings_button.on_click(open_history_settings_dialog)
                            toggle_history_button.on_click(toggle_history_view)


                            last_history_mtime = {'value': max(
                                (path.stat().st_mtime for path in (HISTORY_PATH, CLEAR_MARKER_PATH) if path.exists()),
                                default=0,
                            )}

                            def auto_refresh_history():
                                current_mtime = 0
                                for path in (HISTORY_PATH, CLEAR_MARKER_PATH):
                                    try:
                                        if path.exists():
                                            current_mtime = max(current_mtime, path.stat().st_mtime)
                                    except OSError:
                                        pass
                                if current_mtime > 0 and current_mtime != last_history_mtime['value']:
                                    last_history_mtime['value'] = current_mtime
                                    render_input_history.refresh()

                            ui.timer(1.5, auto_refresh_history, active=True)

                        def delete_files(paths: list[Path]) -> tuple[int, int]:
                            deleted = 0
                            failed = 0
                            for path in paths:
                                try:
                                    if path.exists() and path.is_file():
                                        path.unlink()
                                        deleted += 1
                                except OSError:
                                    failed += 1
                            return deleted, failed

                        def delete_dirs(paths: list[Path]) -> tuple[int, int]:
                            deleted = 0
                            failed = 0
                            for path in paths:
                                try:
                                    if path.exists() and path.is_dir():
                                        shutil.rmtree(path)
                                        deleted += 1
                                except OSError:
                                    failed += 1
                            return deleted, failed

                        def confirm_file_cleanup(title: str, message: str, paths: list[Path], refresh, empty_message: str):
                            if not paths:
                                ui.notify(empty_message, type='info')
                                return
                            with ui.dialog() as dialog, ui.card().classes('w-full max-w-md p-6 bg-white rounded-2xl gap-4'):
                                ui.label(title).classes('text-lg font-bold text-slate-900')
                                ui.label(message).classes('text-sm text-slate-500 leading-relaxed')

                                def apply_cleanup():
                                    deleted, failed = delete_files(paths)
                                    dialog.close()
                                    refresh()
                                    if failed:
                                        ui.notify(f'已删除 {deleted} 个文件，{failed} 个删除失败。', type='warning')
                                    else:
                                        ui.notify(f'已删除 {deleted} 个文件。', type='positive')

                                with ui.row().classes('justify-end w-full gap-3'):
                                    ui.button('取消', on_click=dialog.close).props('flat color=grey-7')
                                    ui.button('确认删除', icon='delete', on_click=apply_cleanup).props('unelevated color=red-7')
                            dialog.open()

                        def confirm_dir_cleanup(title: str, message: str, paths: list[Path], refresh, empty_message: str):
                            if not paths:
                                ui.notify(empty_message, type='info')
                                return
                            with ui.dialog() as dialog, ui.card().classes('w-full max-w-md p-6 bg-white rounded-2xl gap-4'):
                                ui.label(title).classes('text-lg font-bold text-slate-900')
                                ui.label(message).classes('text-sm text-slate-500 leading-relaxed')

                                def apply_cleanup():
                                    deleted, failed = delete_dirs(paths)
                                    dialog.close()
                                    refresh()
                                    if failed:
                                        ui.notify(f'已删除 {deleted} 个目录，{failed} 个删除失败。', type='warning')
                                    else:
                                        ui.notify(f'已删除 {deleted} 个目录。', type='positive')

                                with ui.row().classes('justify-end w-full gap-3'):
                                    ui.button('取消', on_click=dialog.close).props('flat color=grey-7')
                                    ui.button('确认删除', icon='delete', on_click=apply_cleanup).props('unelevated color=red-7')
                            dialog.open()

                        ui.html('<div id="local-storage-cleanup"></div>').classes('h-0')
                        ui.label('听写日记 (.md)').classes('text-xs font-bold text-slate-400 uppercase tracking-wider mt-2')
                        with ui.card().classes('bg-slate-50 dark:bg-slate-800/50 border border-slate-200/60 dark:border-slate-700/60 p-6 rounded-xl gap-4 w-full shadow-none'):
                            @ui.refreshable
                            def render_diary_storage():
                                summary = diary_storage_summary()
                                ui.label(
                                    f"当前共 {summary['count']} 个日记文件，占用 {format_bytes(summary['size'])}；"
                                    f"30 天前日记 {summary['old_count']} 个，占用 {format_bytes(summary['old_size'])}。"
                                ).classes('text-xs text-slate-500 dark:text-slate-400')

                            def handle_save_diary_change(e):
                                ConfigManager.set_client_var('save_diary', e.value)
                                ui.notify('听写日记设置已保存，下一次听写生效。', type='positive')

                            with ui.row().classes('items-center justify-between w-full gap-4 flex-wrap'):
                                with ui.column().classes('gap-0.5 min-w-0'):
                                    ui.label('保存每日听写日记').classes('font-semibold text-slate-900 dark:text-slate-100 text-base')
                                    ui.label('开启后按 YYYY/MM/DD.md 保存每次听写文本；如果同时保存录音，会在日记里链接录音。').classes('text-xs text-slate-500 dark:text-slate-400')
                                    render_diary_storage()
                                with ui.row().classes('items-center gap-2 flex-wrap justify-end'):
                                    ui.button('打开目录', icon='folder_open', on_click=open_diary_root_dir).props('outline color=grey-7').classes('h-10 px-4 rounded-lg text-sm bg-white')
                                    ui.button('清空日记', icon='delete', on_click=lambda: confirm_file_cleanup(
                                        '清空全部听写日记？',
                                        '只会删除 20xx/月/*.md 日记文件，不删除录音、输入历史、转写输出和配置。',
                                        diary_files(),
                                        render_diary_storage.refresh,
                                        '没有听写日记需要清理。',
                                    )).props('outline color=red-7').classes('h-10 px-4 rounded-lg text-sm bg-white')
                                    ui.switch(value=cfg.get('save_diary', False), on_change=handle_save_diary_change).props('color=amber-8')

                        ui.label('录音文件 (.wav/.mp3)').classes('text-xs font-bold text-slate-400 uppercase tracking-wider mt-2')
                        with ui.card().classes('bg-slate-50 dark:bg-slate-800/50 border border-slate-200/60 dark:border-slate-700/60 p-6 rounded-xl gap-4 w-full shadow-none'):
                            @ui.refreshable
                            def render_recording_storage():
                                summary = recording_storage_summary()
                                ui.label(
                                    f"当前共 {summary['count']} 个录音文件，占用 {format_bytes(summary['size'])}；"
                                    f"30 天前录音 {summary['old_count']} 个，占用 {format_bytes(summary['old_size'])}。"
                                ).classes('text-xs text-slate-500 dark:text-slate-400')

                            def handle_save_audio_change(e):
                                ConfigManager.set_client_var('save_audio', e.value)
                                ui.notify('录音保存设置已保存，下一次听写生效。', type='positive')

                            with ui.row().classes('items-center justify-between w-full gap-4 flex-wrap'):
                                with ui.column().classes('gap-0.5 min-w-0'):
                                    ui.label('保存原始录音文件').classes('font-semibold text-slate-900 dark:text-slate-100 text-base')
                                    ui.label('开启后保存完成听写的原始音频，可能包含隐私内容并持续占用磁盘空间。').classes('text-xs text-slate-500 dark:text-slate-400')
                                    render_recording_storage()
                                with ui.row().classes('items-center gap-2 flex-wrap justify-end'):
                                    ui.button('本月目录', icon='folder_open', on_click=open_recording_assets_dir).props('outline color=grey-7').classes('h-10 px-4 rounded-lg text-sm bg-white')
                                    ui.button('年份目录', icon='folder', on_click=open_recording_root_dir).props('outline color=grey-7').classes('h-10 px-4 rounded-lg text-sm bg-white')
                                    ui.button('清空录音', icon='delete', on_click=lambda: confirm_file_cleanup(
                                        '清空全部录音？',
                                        '只会删除 20xx/月/assets/*.wav 和 *.mp3，不删除听写日记、输入历史、转写输出和配置。',
                                        recording_audio_files(),
                                        render_recording_storage.refresh,
                                        '没有录音文件需要清理。',
                                    )).props('outline color=red-7').classes('h-10 px-4 rounded-lg text-sm bg-white')
                                    ui.switch(value=cfg['save_audio'], on_change=handle_save_audio_change).props('color=amber-8')

                        ui.label('转写输出与日志').classes('text-xs font-bold text-slate-400 uppercase tracking-wider mt-2')
                        with ui.card().classes('bg-slate-50 dark:bg-slate-800/50 border border-slate-200/60 dark:border-slate-700/60 p-6 rounded-xl gap-4 w-full shadow-none'):
                            @ui.refreshable
                            def render_output_log_storage():
                                trans = transcription_storage_summary()
                                logs = log_storage_summary()
                                ui.label(
                                    f"转写输出 {trans['dir_count']} 个任务目录，占用 {format_bytes(trans['size'])}；"
                                    f"日志 {logs['count']} 个文件，占用 {format_bytes(logs['size'])}。"
                                ).classes('text-xs text-slate-500 dark:text-slate-400')

                            def confirm_clear_cache():
                                output_dirs = transcription_output_dirs()
                                old_logs = log_storage_summary()['old_files']
                                if not output_dirs and not old_logs:
                                    ui.notify('没有可清理的缓存。', type='info')
                                    return
                                with ui.dialog() as dialog, ui.card().classes('w-full max-w-md p-6 bg-white rounded-2xl gap-4'):
                                    ui.label('一键清理缓存？').classes('text-lg font-bold text-slate-900')
                                    ui.label(
                                        f"将删除 {len(output_dirs)} 个 GUI 转写输出目录，并删除 {len(old_logs)} 个 30 天前日志。"
                                        "不会删除输入历史、听写日记、录音文件、配置和原始音视频。"
                                    ).classes('text-sm text-slate-500 leading-relaxed')

                                    def apply_clear_cache():
                                        dir_deleted, dir_failed = delete_dirs(output_dirs)
                                        log_deleted, log_failed = delete_files(old_logs)
                                        dialog.close()
                                        render_output_log_storage.refresh()
                                        failed = dir_failed + log_failed
                                        message = f'已清理 {dir_deleted} 个转写目录、{log_deleted} 个旧日志。'
                                        ui.notify(message if not failed else f'{message} {failed} 项删除失败。', type='positive' if not failed else 'warning')

                                    with ui.row().classes('justify-end w-full gap-3'):
                                        ui.button('取消', on_click=dialog.close).props('flat color=grey-7')
                                        ui.button('确认清理', icon='cleaning_services', on_click=apply_clear_cache).props('unelevated color=red-7')
                                dialog.open()

                            with ui.row().classes('items-center justify-between w-full gap-4 flex-wrap'):
                                with ui.column().classes('gap-0.5 min-w-0'):
                                    ui.label('缓存清理').classes('font-semibold text-slate-900 dark:text-slate-100 text-base')
                                    ui.label('缓存仅指 GUI 转写输出和旧日志，不包含输入历史、听写日记和录音。').classes('text-xs text-slate-500 dark:text-slate-400')
                                    render_output_log_storage()
                                ui.button('一键清理缓存', icon='cleaning_services', on_click=confirm_clear_cache).props('outline color=red-7').classes('h-10 px-4 rounded-lg text-sm bg-white')

                            ui.separator().classes('bg-slate-200/60 dark:bg-slate-700/60')

                            with ui.row().classes('items-center justify-between w-full gap-4 flex-wrap'):
                                with ui.column().classes('gap-0.5 min-w-0'):
                                    ui.label('转写输出目录').classes('font-semibold text-slate-900 dark:text-slate-100 text-base')
                                    ui.label('只管理 GUI 生成的转写结果目录，不删除用户选择的原始音视频文件。').classes('text-xs text-slate-500 dark:text-slate-400')
                                with ui.row().classes('items-center gap-2 flex-wrap justify-end'):
                                    ui.button('打开输出目录', icon='folder_open', on_click=open_transcription_output_dir).props('outline color=grey-7').classes('h-10 px-4 rounded-lg text-sm bg-white')
                                    ui.button('清空转写输出', icon='delete', on_click=lambda: confirm_dir_cleanup(
                                        '清空全部转写输出？',
                                        '只会删除 web_gui/outputs 下的任务目录，不删除原始音视频。',
                                        transcription_output_dirs(),
                                        render_output_log_storage.refresh,
                                        '没有转写输出需要清理。',
                                    )).props('outline color=red-7').classes('h-10 px-4 rounded-lg text-sm bg-white')

                            ui.separator().classes('bg-slate-200/60 dark:bg-slate-700/60')
                            with ui.row().classes('items-center justify-between w-full gap-4 flex-wrap'):
                                with ui.column().classes('gap-0.5 min-w-0'):
                                    ui.label('日志文件').classes('font-semibold text-slate-900 dark:text-slate-100 text-base')
                                    ui.label('用于排查快捷键、录音、转写和退出问题；默认不会自动删除最新日志。').classes('text-xs text-slate-500 dark:text-slate-400')
                                    render_output_log_storage()
                                with ui.row().classes('items-center gap-2 flex-wrap justify-end'):
                                    ui.button('打开日志目录', icon='folder_open', on_click=open_logs_dir).props('outline color=grey-7').classes('h-10 px-4 rounded-lg text-sm bg-white')
                                    ui.button('清理旧日志', icon='delete_sweep', on_click=lambda: confirm_file_cleanup(
                                        '清理 30 天前的日志？',
                                        '只会删除 logs/*.log 中 30 天前的文件，不删除配置、录音、日记和转写输出。',
                                        log_storage_summary()['old_files'],
                                        render_output_log_storage.refresh,
                                        '没有 30 天前的日志需要清理。',
                                    )).props('outline color=red-7').classes('h-10 px-4 rounded-lg text-sm bg-white')

                # === Tab 3: 🤖 AI 润色与角色 ===
                with ui.tab_panel(tab_ai):
                    render_ai_panel()

                with ui.tab_panel(tab_hotwords):
                    with ui.column().classes('gap-4 w-full pb-8'):
                        with ui.column().classes('gap-1 border-b border-slate-100 dark:border-slate-800 pb-4 w-full'):
                            ui.label('热词与替换规则').classes('text-2xl font-bold text-slate-900 dark:text-white')
                            ui.label('管理音素热词替换表与正则表达式修正逻辑。').classes('text-sm text-slate-500 dark:text-slate-400')

                        hot_path = BASE_DIR / 'hot.txt'
                        hot_rule_path = BASE_DIR / 'hot-rule.txt'
                        hot_txt_content = hot_path.read_text(encoding='utf-8') if hot_path.exists() else ''
                        hot_rule_content = hot_rule_path.read_text(encoding='utf-8') if hot_rule_path.exists() else ''

                        def open_rule_file(path: Path):
                            open_path_foreground(path)

                        with ui.card().classes('bg-slate-50 dark:bg-slate-800/50 border border-slate-200/60 dark:border-slate-700/60 px-6 py-5 rounded-xl gap-4 w-full shadow-none'):
                            with ui.row().classes('items-start justify-between gap-4 w-full'):
                                with ui.column().classes('gap-1'):
                                    ui.label('替换策略').classes('font-semibold text-slate-800 dark:text-slate-200 text-base')
                                    ui.label('控制 hot.txt 和 hot-rule.txt 是否参与每次听写后的文本修正。').classes('text-xs text-slate-500 dark:text-slate-400')
                            ui.separator().classes('bg-slate-200/60 dark:bg-slate-700/60')
                            with ui.grid(columns=2).classes('w-full gap-4'):
                                with ui.card().classes('w-full p-4 rounded-lg bg-white dark:bg-slate-900/40 border border-slate-200/70 dark:border-slate-700 shadow-none gap-3'):
                                    with ui.row().classes('items-start justify-between gap-3 w-full'):
                                        with ui.column().classes('gap-1 min-w-0'):
                                            ui.label('热词替换设置').classes('font-semibold text-slate-800 dark:text-slate-200')
                                            ui.label('对应 hot.txt，用音近匹配修正常见词、品牌名和固定短语。').classes('text-xs text-slate-500 dark:text-slate-400')
                                        hot_enabled_sw = ui.switch('启用热词替换', value=cfg.get('hot_enabled', True)).classes('text-slate-700 dark:text-slate-200 shrink-0')
                                    hot_thresh_in = ui.number(
                                        label='热词替换阈值',
                                        value=cfg.get('hot_thresh', 0.85),
                                        min=0,
                                        max=1,
                                        step=0.01,
                                    ).classes('w-full')
                                    hot_similar_in = ui.number(
                                        label='相似热词阈值',
                                        value=cfg.get('hot_similar', 0.6),
                                        min=0,
                                        max=1,
                                        step=0.01,
                                    ).classes('w-full')
                                with ui.card().classes('w-full p-4 rounded-lg bg-white dark:bg-slate-900/40 border border-slate-200/70 dark:border-slate-700 shadow-none gap-3'):
                                    with ui.row().classes('items-start justify-between gap-3 w-full'):
                                        with ui.column().classes('gap-1 min-w-0'):
                                            ui.label('正则替换设置').classes('font-semibold text-slate-800 dark:text-slate-200')
                                            ui.label('对应 hot-rule.txt，启用后按文件中的正则规则修正文稿。').classes('text-xs text-slate-500 dark:text-slate-400')
                                        hot_rule_enabled_sw = ui.switch('启用正则替换', value=cfg.get('hot_rule_enabled', True)).classes('text-slate-700 dark:text-slate-200 shrink-0')
                                    ui.label('正则规则没有阈值；需要修改规则内容时，请在下方编辑 hot-rule.txt。').classes('text-xs text-slate-500 dark:text-slate-400')

                            def save_hotword_switches():
                                ConfigManager.set_client_var('hot', bool(hot_enabled_sw.value))
                                ConfigManager.set_client_var('hot_rule', bool(hot_rule_enabled_sw.value))
                                ui.notify('替换开关已保存，下一次听写自动生效。', type='positive')

                            def save_hotword_thresholds():
                                ConfigManager.set_client_var('hot_thresh', float(hot_thresh_in.value or 0.85))
                                ConfigManager.set_client_var('hot_similar', float(hot_similar_in.value or 0.6))
                                ui.notify('热词阈值已保存，下一次听写自动生效。', type='positive')

                            hot_enabled_sw.on_value_change(lambda _: save_hotword_switches())
                            hot_rule_enabled_sw.on_value_change(lambda _: save_hotword_switches())
                            hot_thresh_in.on_value_change(lambda _: save_hotword_thresholds())
                            hot_similar_in.on_value_change(lambda _: save_hotword_thresholds())

                        def render_rule_file_card(title: str, description: str, path: Path, content: str, height: int):
                            with ui.card().classes('bg-slate-50 dark:bg-slate-800/50 border border-slate-200/60 dark:border-slate-700/60 px-6 py-5 rounded-xl gap-3 w-full shadow-none'):
                                with ui.row().classes('items-start justify-between gap-4 w-full flex-nowrap'):
                                    with ui.column().classes('gap-1 min-w-0 flex-1 pr-4'):
                                        ui.label(title).classes('font-semibold text-slate-800 dark:text-slate-200 text-base')
                                        ui.label(description).classes('text-xs text-slate-500 dark:text-slate-400')
                                    with ui.row().classes('items-center justify-end gap-2 shrink-0 self-start ml-auto'):
                                        status_label = ui.label('').classes('text-xs text-slate-400 min-w-[64px] text-right')
                                        refresh_button = ui.button('刷新', icon='refresh').props('outline color=grey-8').classes('h-8 px-2.5 rounded-lg text-xs bg-white')
                                        open_button = ui.button('打开文件', icon='folder_open').props('outline color=grey-8').classes('h-8 px-2.5 rounded-lg text-xs bg-white')
                                        save_button = ui.button('保存', icon='save').classes('h-8 px-3 rounded-lg text-xs bg-amber-600 text-white')

                                editor = ui.textarea(value=content).props('borderless spellcheck=false').classes('rule-editor w-full').style(f'--rule-editor-height: {height}px;')

                                def refresh_file():
                                    editor.value = path.read_text(encoding='utf-8') if path.exists() else ''
                                    editor.update()
                                    status_label.text = '已刷新'
                                    status_label.update()
                                    ui.notify(f'{path.name} 已重新读取。', type='info')

                                def save_file():
                                    ConfigManager.write_text_with_backup(path, editor.value or '')
                                    status_label.text = '已保存'
                                    status_label.update()
                                    ui.notify(f'{path.name} 已保存，下一次听写自动生效。', type='positive')

                                refresh_button.on_click(refresh_file)
                                open_button.on_click(lambda p=path: open_rule_file(p))
                                save_button.on_click(save_file)
                                return editor

                        hot_editor = render_rule_file_card(
                            'hot.txt 热词替换表',
                            '维护常见词、品牌名、技术词和固定短语的音近纠错。',
                            hot_path,
                            hot_txt_content,
                            280,
                        )
                        rule_editor = render_rule_file_card(
                            'hot-rule.txt 正则规则',
                            '维护邮箱、符号、回车、标点等更精确的文本替换。',
                            hot_rule_path,
                            hot_rule_content,
                            220,
                        )

                # === Tab 5: 语音引擎与硬件 ===
                with ui.tab_panel(tab_engine):
                    with ui.column().classes('gap-6 w-full pb-8'):
                        with ui.column().classes('gap-1 border-b border-slate-100 dark:border-slate-800 pb-4 w-full'):
                            ui.label('语音识别与硬件').classes('text-2xl font-bold text-slate-900 dark:text-white')
                            ui.label('选择离线识别模型、语言策略、数字格式化与显卡预加速；常用选项会在切换后自动保存。').classes('text-sm text-slate-500 dark:text-slate-400')

                        current_model_type = normalize_model_type(cfg.get('model_type', 'sensevoice'))
                        install_status = model_install_status()
                        installed_model_options = {
                            key: f'{MODEL_LABELS[key]}（已安装）'
                            for key, status in install_status.items()
                            if status['installed']
                        }
                        missing_model_options = {
                            key: f'{MODEL_LABELS[key]}（缺少模型文件）'
                            for key, status in install_status.items()
                            if not status['installed']
                        }
                        model_options = installed_model_options or missing_model_options
                        if current_model_type not in model_options:
                            current_model_type = next(iter(model_options), 'sensevoice')
                        current_language = normalize_language_code(cfg.get('language', 'auto'))
                        current_onnx_key = MODEL_ONNX_CONFIG_KEYS.get(current_model_type)
                        current_dml_key = MODEL_DML_CONFIG_KEYS.get(current_model_type)
                        current_llm_gpu_key = MODEL_LLM_GPU_CONFIG_KEYS.get(current_model_type)
                        current_model_status = install_status.get(current_model_type, {'installed': False, 'missing': []})

                        def open_model_setup_dialog():
                            with ui.dialog() as dialog:
                                with ui.card().classes('w-[720px] max-w-[92vw] p-6 rounded-xl gap-5'):
                                    with ui.row().classes('items-start justify-between gap-4 w-full'):
                                        with ui.column().classes('gap-1'):
                                            ui.label('模型配置指引').classes('text-xl font-bold text-slate-900')
                                            ui.label('精简版需要先下载 ASR 模型；完整版通常已经内置 SenseVoice-Small。').classes('text-sm text-slate-500')
                                        ui.button(icon='close', on_click=dialog.close).props('flat round dense')

                                    with ui.card().classes('w-full p-4 rounded-lg bg-amber-50 border border-amber-200 shadow-none gap-2'):
                                        ui.label('推荐模型').classes('font-semibold text-amber-900')
                                        ui.label('SenseVoice-Small：推荐新用户优先使用，体积较小，日常听写延迟低。').classes('text-sm text-amber-900/80')
                                        ui.label('FunASR-Nano / Qwen3-ASR：适合想继续尝试更高识别能力的用户，体积和配置要求更高。').classes('text-sm text-amber-900/80')

                                    with ui.column().classes('gap-2'):
                                        ui.label('下载地址').classes('font-semibold text-slate-900')
                                        ui.link(
                                            'https://github.com/HaujetZhao/CapsWriter-Offline/releases/tag/models',
                                            'https://github.com/HaujetZhao/CapsWriter-Offline/releases/tag/models',
                                            new_tab=True,
                                        ).classes('text-sm text-blue-600 break-all')
                                        ui.label('这些是原项目整理的适配模型下载包，SenseVoice 本身是独立开源语音识别模型。').classes('text-sm text-slate-500')
                                        ui.label('下载后解压到本程序的 models 目录，再回到这里确认模型状态。').classes('text-sm text-slate-500')

                                    with ui.row().classes('items-center justify-end gap-2 w-full'):
                                        ui.button('打开模型目录', icon='folder_open', on_click=lambda: open_path_foreground(BASE_DIR / 'models')).props('outline color=amber-8').classes('bg-white h-10 px-4 rounded-lg')
                            dialog.open()

                        if not current_model_status['installed']:
                            with ui.card().classes('bg-amber-50 border border-amber-200 px-6 py-5 rounded-xl gap-3 w-full shadow-none'):
                                with ui.row().classes('items-start justify-between gap-4 w-full flex-wrap'):
                                    with ui.row().classes('items-start gap-3 min-w-0 flex-1'):
                                        ui.icon('warning_amber', size='md').classes('text-amber-600 mt-0.5')
                                        with ui.column().classes('gap-1 min-w-0'):
                                            ui.label('当前识别模型缺少本地文件').classes('font-bold text-amber-900 text-base')
                                            ui.label(
                                                f'已选择 {MODEL_LABELS.get(current_model_type, current_model_type)}，但模型文件未安装完整。'
                                                '精简版不会内置 ASR 模型，请先下载模型并解压到 models 目录后再启动听写服务。'
                                            ).classes('text-sm text-amber-900/80')
                                            ui.label('推荐新用户优先安装 SenseVoice-Small。').classes('text-xs text-amber-800/80')
                                    with ui.row().classes('items-center gap-2 shrink-0'):
                                        ui.button('模型配置指引', icon='help_outline', on_click=open_model_setup_dialog).props('outline color=amber-8').classes('bg-white h-10 px-4 rounded-lg')

                        with ui.grid(columns=2).classes('w-full gap-6'):
                            with ui.card().classes('bg-slate-50 dark:bg-slate-800/50 border border-slate-200/60 dark:border-slate-700/60 p-6 rounded-xl gap-4 shadow-none'):
                                with ui.row().classes('items-start justify-between gap-3 w-full'):
                                    with ui.column().classes('gap-2'):
                                        ui.icon('graphic_eq', size='md').classes('text-amber-600 dark:text-emerald-400')
                                        ui.label('识别模型').classes('font-bold text-slate-900 dark:text-white text-base')
                                    ui.button(icon='help_outline', on_click=open_model_setup_dialog).props('flat round color=amber-8').classes('shrink-0')
                                ui.label('只把本机已部署完整文件的模型放进可选列表，避免保存后服务端启动失败。').classes('text-xs text-slate-500 dark:text-slate-400')
                                model_select = ui.select(
                                    options=model_options,
                                    value=current_model_type,
                                    label='ASR 识别模型',
                                ).classes('w-full')
                                language_note = ui.label(model_language_note(current_model_type)).classes('text-xs text-slate-500 dark:text-slate-400')
                                language_select = ui.select(
                                    options=language_options(),
                                    value=current_language,
                                    label='默认识别语言',
                                ).classes('w-full')
                                ui.label('语言会保存为统一语言代码，服务端会按当前 ASR 引擎自动转换成对应格式。').classes('text-xs text-slate-500 dark:text-slate-400')

                                def update_language_note():
                                    language_note.text = model_language_note(model_select.value)
                                    language_note.update()

                                with ui.column().classes('gap-1 w-full'):
                                    ui.label('本地模型文件状态').classes('text-xs font-semibold text-slate-500 dark:text-slate-400')
                                    for model_type, status in install_status.items():
                                        icon = 'check_circle' if status['installed'] else 'error_outline'
                                        color = 'text-emerald-600' if status['installed'] else 'text-slate-400'
                                        text = '已安装' if status['installed'] else f'缺少 {len(status["missing"])} 个文件'
                                        with ui.row().classes('items-center gap-2'):
                                            ui.icon(icon, size='xs').classes(color)
                                            ui.label(f'{MODEL_LABELS[model_type]}：{text}').classes('text-xs text-slate-500 dark:text-slate-400')
                            with ui.card().classes('bg-slate-50 dark:bg-slate-800/50 border border-slate-200/60 dark:border-slate-700/60 p-6 rounded-xl gap-4 shadow-none'):
                                ui.icon('text_fields', size='md').classes('text-amber-600 dark:text-emerald-400')
                                ui.label('输出格式').classes('font-bold text-slate-900 dark:text-white text-base')
                                ui.label('这些开关只影响识别后的文本格式，不改变模型文件。').classes('text-xs text-slate-500 dark:text-slate-400')
                                format_num_sw = ui.switch('启用数字格式化', value=cfg.get('format_num', True)).classes('text-slate-700 dark:text-slate-200')
                                format_spell_sw = ui.switch('启用拼写格式化', value=cfg.get('format_spell', True)).classes('text-slate-700 dark:text-slate-200')
                                traditional_sw = ui.switch('繁体转简体', value=cfg.get('traditional_convert', False)).classes('text-slate-700 dark:text-slate-200')

                            with ui.card().classes('bg-slate-50 dark:bg-slate-800/50 border border-slate-200/60 dark:border-slate-700/60 p-6 rounded-xl gap-4 shadow-none'):
                                ui.icon('memory', size='md').classes('text-amber-600 dark:text-emerald-400')
                                ui.label('硬件加速').classes('font-bold text-slate-900 dark:text-white text-base')
                                ui.label('ONNX 后端和 GGUF GPU 选项会影响模型运行性能，通常 CPU 最稳。').classes('text-xs text-slate-500 dark:text-slate-400')
                                onnx_provider_select = ui.select(
                                    options={'CPU': 'CPU（兼容性最好）', 'DML': 'DirectML（Windows 通用 GPU）', 'CUDA': 'CUDA（NVIDIA）', 'TensorRT': 'TensorRT（NVIDIA 高性能）'},
                                    value=cfg.get(current_onnx_key, 'CPU') if current_onnx_key else 'CPU',
                                    label='当前模型 ONNX 后端',
                                ).classes('w-full')
                                llm_use_gpu_sw = ui.switch(
                                    'GGUF 解码使用 GPU',
                                    value=cfg.get(current_llm_gpu_key, False) if current_llm_gpu_key else False,
                                ).classes('text-slate-700 dark:text-slate-200')
                                if not current_llm_gpu_key:
                                    ui.label('当前模型没有独立的 GGUF 解码 GPU 开关。').classes('text-xs text-slate-500 dark:text-slate-400')
                                gpu_boost_sw = ui.switch('GPU 预加速', value=cfg.get('gpu_boost', False)).classes('text-slate-700 dark:text-slate-200')

                            with ui.card().classes('bg-slate-50 dark:bg-slate-800/50 border border-slate-200/60 dark:border-slate-700/60 p-6 rounded-xl gap-4 shadow-none'):
                                ui.icon('tune', size='md').classes('text-amber-600 dark:text-emerald-400')
                                ui.label('高级运行参数').classes('font-bold text-slate-900 dark:text-white text-base')
                                ui.label('一般不需要改；用于长音频分段、DirectML padding 和字幕对齐释放策略。').classes('text-xs text-slate-500 dark:text-slate-400')
                                with ui.expansion('展开高级设置', icon='expand_more').classes('w-full'):
                                    with ui.grid(columns=2).classes('w-full gap-4 pt-3'):
                                        dml_pad_to_in = ui.number('当前模型 DML Padding 秒数', value=cfg.get(current_dml_key, 30) if current_dml_key else 30, min=0, step=1).classes('w-full')
                                        aligner_timeout_in = ui.number('字幕对齐空闲释放秒数', value=cfg.get('aligner_timeout', 10), min=0, step=1).classes('w-full')
                                        mic_seg_duration_in = ui.number('麦克风分段长度（秒）', value=cfg.get('mic_seg_duration', 60), min=10, step=1).classes('w-full')
                                        mic_seg_overlap_in = ui.number('麦克风分段重叠（秒）', value=cfg.get('mic_seg_overlap', 4), min=0, step=1).classes('w-full')
                                        file_seg_duration_in = ui.number('文件转写分段长度（秒）', value=cfg.get('file_seg_duration', 60), min=10, step=1).classes('w-full')
                                        file_seg_overlap_in = ui.number('文件转写分段重叠（秒）', value=cfg.get('file_seg_overlap', 4), min=0, step=1).classes('w-full')
                                        gpu_boost_cmd_in = ui.input('GPU 预加速命令', value=cfg.get('gpu_boost_cmd', '')).classes('w-full')
                                        gpu_unboost_cmd_in = ui.input('GPU 恢复命令', value=cfg.get('gpu_unboost_cmd', '')).classes('w-full')
                                        gpu_unboost_timeout_in = ui.number('GPU 恢复空闲秒数', value=cfg.get('gpu_unboost_timeout', 1), min=0, step=1).classes('w-full')
                                        aligner_provider_select = ui.select(
                                            options={'CPU': 'CPU', 'DML': 'DirectML', 'CUDA': 'CUDA', 'TensorRT': 'TensorRT'},
                                            value=cfg.get('aligner_onnx_provider', 'CPU'),
                                            label='字幕对齐 ONNX 后端',
                                        ).classes('w-full')
                                        aligner_llm_gpu_sw = ui.switch('字幕对齐 GGUF 使用 GPU', value=cfg.get('aligner_llm_use_gpu', False)).classes('text-slate-700 dark:text-slate-200')
                                        aligner_dml_pad_to_in = ui.number('字幕对齐 DML Padding 秒数', value=cfg.get('aligner_dml_pad_to', 30), min=0, step=1).classes('w-full')

                                    def save_advanced_engine_cfg():
                                        selected_model = normalize_model_type(model_select.value)
                                        model_arg_class = MODEL_ARG_CLASSES.get(selected_model)
                                        if model_arg_class and MODEL_DML_CONFIG_KEYS.get(selected_model):
                                            ConfigManager.set_server_class_var(model_arg_class, 'dml_pad_to', int(dml_pad_to_in.value or 0))
                                        ConfigManager.set_server_var('gpu_boost_cmd', gpu_boost_cmd_in.value or '')
                                        ConfigManager.set_server_var('gpu_unboost_cmd', gpu_unboost_cmd_in.value or '')
                                        ConfigManager.set_server_var('gpu_unboost_timeout', int(gpu_unboost_timeout_in.value or 0))
                                        ConfigManager.set_server_var('aligner_idle_timeout', int(aligner_timeout_in.value or 0))
                                        ConfigManager.set_client_var('mic_seg_duration', int(mic_seg_duration_in.value or 60))
                                        ConfigManager.set_client_var('mic_seg_overlap', int(mic_seg_overlap_in.value or 0))
                                        ConfigManager.set_client_var('file_seg_duration', int(file_seg_duration_in.value or 60))
                                        ConfigManager.set_client_var('file_seg_overlap', int(file_seg_overlap_in.value or 0))
                                        ConfigManager.set_server_class_var('ForceAlignerGGUFArgs', 'onnx_provider', aligner_provider_select.value or 'CPU')
                                        ConfigManager.set_server_class_var('ForceAlignerGGUFArgs', 'llm_use_gpu', bool(aligner_llm_gpu_sw.value))
                                        ConfigManager.set_server_class_var('ForceAlignerGGUFArgs', 'dml_pad_to', int(aligner_dml_pad_to_in.value or 0))
                                        ui.notify('高级运行参数已保存，重启听写服务后完全生效。', type='positive')

                                    with ui.row().classes('justify-end w-full pt-2'):
                                        ui.button('保存高级参数', icon='save', on_click=save_advanced_engine_cfg).classes('bg-amber-600 dark:bg-emerald-600 text-white px-4')

                        def save_model_selection():
                            selected_model = normalize_model_type(model_select.value)
                            selected_status = install_status.get(selected_model, {'installed': False})
                            if not selected_status['installed']:
                                ui.notify('该模型文件未安装完整，已阻止保存。请先把模型放入 models 目录。', type='negative')
                                return
                            ConfigManager.set_model_type(selected_model)
                            update_language_note()
                            onnx_key = MODEL_ONNX_CONFIG_KEYS.get(selected_model)
                            if onnx_key:
                                onnx_provider_select.value = ConfigManager.get_server_class_var(MODEL_ARG_CLASSES[selected_model], 'onnx_provider', 'CPU')
                                onnx_provider_select.update()
                            llm_gpu_key = MODEL_LLM_GPU_CONFIG_KEYS.get(selected_model)
                            llm_use_gpu_sw.value = bool(cfg.get(llm_gpu_key, False)) if llm_gpu_key else False
                            llm_use_gpu_sw.update()
                            ui.notify('识别模型已保存，重启听写服务后完全生效。', type='positive')

                        def save_language_selection():
                            ConfigManager.set_client_var('language', normalize_language_code(language_select.value))
                            ui.notify('默认识别语言已保存，下一次听写读取配置。', type='positive')

                        def save_output_format():
                            ConfigManager.set_server_var('format_num', bool(format_num_sw.value))
                            ConfigManager.set_server_var('format_spell', bool(format_spell_sw.value))
                            ConfigManager.set_client_var('traditional_convert', bool(traditional_sw.value))
                            ui.notify('输出格式已保存，下一次听写读取配置。', type='positive')

                        def save_hardware_acceleration():
                            selected_model = normalize_model_type(model_select.value)
                            model_arg_class = MODEL_ARG_CLASSES.get(selected_model)
                            if model_arg_class and MODEL_ONNX_CONFIG_KEYS.get(selected_model):
                                ConfigManager.set_server_class_var(model_arg_class, 'onnx_provider', onnx_provider_select.value or 'CPU')
                            if model_arg_class and selected_model in MODEL_LLM_GPU_CONFIG_KEYS:
                                ConfigManager.set_server_class_var(model_arg_class, 'llm_use_gpu', bool(llm_use_gpu_sw.value))
                            ConfigManager.set_server_var('gpu_boost_enabled', bool(gpu_boost_sw.value))
                            ui.notify('硬件加速配置已保存，重启听写服务后完全生效。', type='positive')

                        model_select.on_value_change(lambda _: save_model_selection())
                        language_select.on_value_change(lambda _: save_language_selection())
                        format_num_sw.on_value_change(lambda _: save_output_format())
                        format_spell_sw.on_value_change(lambda _: save_output_format())
                        traditional_sw.on_value_change(lambda _: save_output_format())
                        onnx_provider_select.on_value_change(lambda _: save_hardware_acceleration())
                        llm_use_gpu_sw.on_value_change(lambda _: save_hardware_acceleration())
                        gpu_boost_sw.on_value_change(lambda _: save_hardware_acceleration())

                # === Tab 6: 📁 配置备份与迁移 (全量 JSON 导出与解析导入) ===
                with ui.tab_panel(tab_backup):
                    with ui.column().classes('gap-6 w-full pb-8'):
                        with ui.column().classes('gap-1 border-b border-slate-100 dark:border-slate-800 pb-4 w-full'):
                            ui.label('配置备份与迁移').classes('text-2xl font-bold text-slate-900 dark:text-white')
                            ui.label('更换电脑或重新安装时，导出/导入偏好设置、热词词库、AI 角色和 API 档案；出于安全考虑不导出真实 API Key。').classes('text-sm text-slate-500 dark:text-slate-400')

                        with ui.grid(columns=2).classes('w-full gap-6'):
                            # 导出卡片
                            with ui.card().classes('bg-slate-50 dark:bg-slate-800/50 border border-slate-200/60 dark:border-slate-700/60 p-6 rounded-xl gap-4 shadow-none'):
                                ui.icon('file_download', size='md').classes('text-amber-600 dark:text-emerald-400')
                                ui.label('全量导出当前配置方案').classes('font-bold text-slate-900 dark:text-white text-base')
                                ui.label('导出快捷键、语音引擎、热词、正则、转写设置、AI API 档案和角色人设；不包含 API Key、模型文件、录音、输入历史和转写结果。').classes('text-xs text-slate-500 dark:text-slate-400')

                                def trigger_export():
                                    target = ConfigManager.export_full_config_to_file()
                                    ui.notify(f'配置备份已保存：{target.name}', type='positive')
                                    reveal_in_explorer(target)

                                ui.button('导出到本地文件', icon='file_download', on_click=trigger_export).classes('bg-amber-600 dark:bg-emerald-600 text-white w-full mt-2')

                            # 导入卡片
                            with ui.card().classes('bg-slate-50 dark:bg-slate-800/50 border border-slate-200/60 dark:border-slate-700/60 p-6 rounded-xl gap-4 shadow-none'):
                                ui.icon('file_upload', size='md').classes('text-amber-600 dark:text-emerald-400')
                                ui.label('全量导入并还原配置方案').classes('font-bold text-slate-900 dark:text-white text-base')
                                ui.label('选择或粘贴之前导出的 JSON 备份，校验后还原设置。导入后需要重新填写 API Key，并确认本地模型文件已放好。').classes('text-xs text-slate-500 dark:text-slate-400')

                                async def trigger_import_dialog():
                                    with ui.dialog() as dialog, ui.card().classes('w-full max-w-lg p-6 bg-white dark:bg-slate-900 rounded-2xl gap-4'):
                                        ui.label('导入配置 JSON 内容').classes('text-lg font-bold text-slate-900 dark:text-white')
                                        ui.label('备份不会包含真实 API Key。导入后如果要使用 AI 润色或角色，请到 AI 页面重新填写密钥。').classes('text-xs text-slate-500')
                                        import_input = ui.textarea(placeholder='拖拽或粘贴 config_backup.json 的文本内容到此处...').classes('w-full h-48 font-mono bg-slate-50 dark:bg-slate-950 p-3 rounded-xl border border-slate-200 dark:border-slate-800')

                                        async def choose_import_file():
                                            selected = await run.io_bound(
                                                select_files_dialog,
                                                '选择 CapsWriter 配置备份 JSON',
                                                'JSON 配置文件|*.json|所有文件|*.*',
                                                False,
                                            )
                                            if selected:
                                                path = Path(selected[0])
                                                try:
                                                    import_input.value = path.read_text(encoding='utf-8')
                                                    ui.notify(f'已读取导入文件：{path.name}', type='positive')
                                                except Exception as e:
                                                    ui.notify(f'读取导入文件失败：{e}', type='negative')

                                        def apply_import():
                                            ok, msg = ConfigManager.import_full_config(import_input.value)
                                            if ok:
                                                ui.notify(msg, type='positive')
                                                dialog.close()
                                                ui.open('/')
                                            else:
                                                ui.notify(f"导入失败: {msg}", type='negative')

                                        with ui.row().classes('justify-end w-full gap-3 mt-2'):
                                            ui.button('选择 JSON 文件', icon='folder_open', on_click=choose_import_file).props('outline color=amber-8')
                                            ui.button('取消', on_click=dialog.close).props('flat color=grey')
                                            ui.button('解析并还原', icon='check', on_click=apply_import).classes('bg-amber-600 dark:bg-emerald-600 text-white')

                                    dialog.open()

                                ui.button('导入配置文件 (Upload / Paste)', icon='file_upload', on_click=trigger_import_dialog).classes('bg-slate-800 text-white w-full mt-2')

                        ui.label('本地自动备份与恢复').classes('text-xs font-bold text-slate-400 uppercase tracking-wider mt-2')
                        with ui.card().classes('bg-slate-50 dark:bg-slate-800/50 border border-slate-200/60 dark:border-slate-700/60 p-6 rounded-xl gap-4 w-full shadow-none'):
                            backup_box = ui.column().classes('gap-2 w-full')
                            backup_dir = BASE_DIR / 'web_gui' / 'config_backups'

                            def config_backup_files() -> list[Path]:
                                return [path for path in backup_dir.glob('*.bak') if path.is_file()] if backup_dir.exists() else []

                            def config_backup_summary() -> str:
                                files = config_backup_files()
                                size = sum(path.stat().st_size for path in files)
                                return f'当前共 {len(files)} 个自动备份，占用 {format_bytes(size)}。'

                            def open_config_backup_dir() -> None:
                                backup_dir.mkdir(parents=True, exist_ok=True)
                                open_path_foreground(backup_dir)
                                ui.notify(f'已打开自动备份目录：{backup_dir}', type='positive')

                            def set_auto_backup_enabled(event) -> None:
                                enabled = bool(event.value)
                                ConfigManager.set_auto_config_backup_enabled(enabled)
                                ui.notify(
                                    '已开启自动备份。保存配置前会保留旧版本，方便误操作后恢复。' if enabled else '已关闭自动备份。之后保存配置不会再生成新的 .bak 文件。',
                                    type='positive' if enabled else 'warning',
                                )

                            def confirm_clear_config_backups() -> None:
                                paths = config_backup_files()
                                if not paths:
                                    ui.notify('当前没有自动备份需要清理。', type='info')
                                    return
                                with ui.dialog() as dialog, ui.card().classes('w-full max-w-md p-6 bg-white rounded-2xl gap-4'):
                                    ui.label('清空所有自动备份？').classes('text-lg font-bold text-slate-900')
                                    ui.label(f'将删除 web_gui/config_backups/ 下 {len(paths)} 个 .bak 文件；不会删除当前配置、导出的迁移 JSON、热词、API 档案或密钥。').classes('text-sm text-slate-500 leading-relaxed')

                                    def apply_cleanup():
                                        deleted, failed = ConfigManager.clear_config_backups()
                                        dialog.close()
                                        render_backup_list()
                                        ui.notify(
                                            f'已清空 {deleted} 个自动备份。' if not failed else f'已清空 {deleted} 个自动备份，{failed} 个删除失败。',
                                            type='positive' if not failed else 'warning',
                                        )

                                    with ui.row().classes('justify-end w-full gap-3'):
                                        ui.button('取消', on_click=dialog.close).props('flat color=grey-7')
                                        ui.button('确认清空', icon='delete_forever', on_click=apply_cleanup).props('color=red-7')
                                dialog.open()

                            def render_backup_list():
                                backup_box.clear()
                                backups = ConfigManager.list_config_backups(limit=12)
                                with backup_box:
                                    ui.label(config_backup_summary()).classes('text-xs text-slate-500')
                                    if not backups:
                                        ui.label('暂无自动备份。保存任意配置后，这里会出现可恢复记录。').classes('text-sm text-slate-500')
                                        return
                                    for item in backups:
                                        with ui.row().classes('items-center justify-between w-full gap-3 py-2 border-b border-slate-200/60 dark:border-slate-700/60'):
                                            with ui.column().classes('gap-0 min-w-0'):
                                                ui.label(item['target_key']).classes('text-sm font-semibold text-slate-800 dark:text-slate-100')
                                                ui.label(f"{item['created_at']}  ·  {item['backup'].name}").classes('text-xs text-slate-500 break-all')

                                            def restore_backup(path=None):
                                                target = path or item['backup']
                                                ok, msg = ConfigManager.restore_config_backup(target)
                                                ui.notify(msg, type='positive' if ok else 'negative')
                                                render_backup_list()

                                            ui.button('恢复', icon='restore', on_click=lambda *_, p=item['backup']: restore_backup(p)).props('outline color=amber-8').classes('h-9 px-4 rounded-lg text-sm bg-white')

                            with ui.row().classes('justify-end w-full'):
                                ui.button('刷新列表', icon='refresh', on_click=render_backup_list).props('outline color=grey-7').classes('h-9 px-4 rounded-lg text-sm bg-white')

                            render_backup_list()

                            with ui.row().classes('items-center justify-between w-full gap-3 flex-wrap'):
                                with ui.column().classes('gap-1 min-w-[260px]'):
                                    ui.switch(
                                        '自动备份配置文件（建议开启）',
                                        value=ConfigManager.get_auto_config_backup_enabled(),
                                        on_change=set_auto_backup_enabled,
                                    ).classes('text-sm text-slate-700')
                                    ui.label('关闭后仍可正常保存配置，但不会再生成可恢复的 .bak 版本。').classes('text-xs text-slate-500')
                                with ui.row().classes('gap-2 flex-wrap'):
                                    ui.button('打开备份目录', icon='folder_open', on_click=open_config_backup_dir).props('outline color=grey-7').classes('h-9 px-4 rounded-lg text-sm bg-white')
                                    ui.button('清空备份', icon='delete_forever', on_click=confirm_clear_config_backups).props('outline color=red-7').classes('h-9 px-4 rounded-lg text-sm bg-white')

                # === Tab 6: 📂 字幕转写 ===
                with ui.tab_panel(tab_transcribe):
                    with ui.column().classes('gap-6 w-full pb-8'):
                        with ui.column().classes('gap-1 border-b border-slate-100 dark:border-slate-800 pb-4 w-full'):
                            with ui.row().classes('items-start justify-between gap-4 w-full flex-wrap'):
                                with ui.column().classes('gap-1 min-w-0'):
                                    ui.label('字幕转写').classes('text-2xl font-bold text-slate-900 dark:text-white')
                                    ui.label('选择音视频文件，生成字幕、纯文本和时间戳结果。').classes('text-sm text-slate-500 dark:text-slate-400')
                                with ui.row().classes('gap-2 items-center shrink-0 ml-auto'):
                                    ui.button('转写设置', icon='tune', on_click=lambda: open_transcribe_settings_dialog()).props('outline color=amber-8').classes('h-9 px-3 rounded-lg text-sm bg-white')
                                    ui.button('输出目录', icon='folder_open', on_click=open_transcription_output_dir).props('outline color=grey-8').classes('h-9 px-3 rounded-lg text-sm bg-white')

                        with ui.card().classes('bg-slate-50 dark:bg-slate-800/50 border border-slate-200/60 dark:border-slate-700/60 p-6 rounded-xl gap-5 w-full shadow-none'):
                            transcribe_state = {'running': False, 'last_result': None, 'task': None}
                            selected_file = {'path': None}
                            batch_queue = {'files': []}

                            def open_transcribe_settings_dialog():
                                tool_status = get_media_tool_status()
                                with ui.dialog() as dialog, ui.card().classes('w-full max-w-3xl p-0 bg-white rounded-2xl gap-0 overflow-hidden'):
                                    with ui.row().classes('items-start justify-between gap-4 w-full px-6 py-5 border-b border-slate-100'):
                                        with ui.column().classes('gap-1 min-w-0'):
                                            ui.label('转写设置').classes('text-xl font-bold text-slate-900')
                                            ffmpeg_text = 'FFmpeg 可用' if tool_status['ok'] else '未检测到 FFmpeg'
                                            ffprobe_text = 'ffprobe 可用，进度更准确' if tool_status['full_progress'] else '未检测到 ffprobe，进度会较粗略'
                                            ui.label(f'{ffmpeg_text}；{ffprobe_text}。语言、模型和分段参数来自“语音识别与硬件”。').classes('text-sm text-slate-500')
                                        ui.button(icon='close', on_click=dialog.close).props('flat round color=grey-7')

                                    with ui.column().classes('gap-5 w-full px-6 py-5'):
                                        with ui.grid(columns=2).classes('w-full gap-3'):
                                            with ui.card().classes('bg-slate-50 border border-slate-200/70 p-4 rounded-xl gap-1 shadow-none'):
                                                ui.label('FFmpeg').classes('text-xs text-slate-500')
                                                ui.label('可用' if tool_status['ok'] else '不可用').classes('text-base font-bold text-emerald-700' if tool_status['ok'] else 'text-base font-bold text-red-600')
                                                ui.label(tool_status['ffmpeg'] or '请将 ffmpeg.exe 放入 tools/ffmpeg/bin').classes('text-xs text-slate-500 break-all')
                                            with ui.card().classes('bg-slate-50 border border-slate-200/70 p-4 rounded-xl gap-1 shadow-none'):
                                                ui.label('ffprobe').classes('text-xs text-slate-500')
                                                ui.label('可用' if tool_status['full_progress'] else '不可用').classes('text-base font-bold text-emerald-700' if tool_status['full_progress'] else 'text-base font-bold text-amber-700')
                                                ui.label(tool_status['ffprobe'] or '缺少时仍可转写，但进度显示会粗略。').classes('text-xs text-slate-500 break-all')

                                        ui.label('输出格式').classes('text-sm font-semibold text-slate-800')
                                        with ui.row().classes('gap-6 items-center flex-wrap'):
                                            file_save_srt_sw = ui.switch('SRT 字幕', value=ConfigManager.get_client_var('file_save_srt', True)).classes('text-slate-700')
                                            file_save_txt_sw = ui.switch('TXT 分行文本', value=ConfigManager.get_client_var('file_save_txt', True)).classes('text-slate-700')
                                            file_save_json_sw = ui.switch('JSON 时间戳', value=ConfigManager.get_client_var('file_save_json', True)).classes('text-slate-700')
                                            file_save_merge_sw = ui.switch('merge.txt 全文', value=ConfigManager.get_client_var('file_save_merge', False)).classes('text-slate-700')
                                        ui.label('需要修字幕时建议保留 TXT 和 JSON：修改 TXT 后，可以基于 JSON 时间戳重新生成 SRT。').classes('text-xs text-slate-500')

                                        def save_transcribe_settings():
                                            from config_client import ClientConfig as LiveClientConfig

                                            values = {
                                                'file_save_srt': bool(file_save_srt_sw.value),
                                                'file_save_txt': bool(file_save_txt_sw.value),
                                                'file_save_json': bool(file_save_json_sw.value),
                                                'file_save_merge': bool(file_save_merge_sw.value),
                                            }
                                            for key, value in values.items():
                                                ConfigManager.set_client_var(key, value)
                                                setattr(LiveClientConfig, key, value)
                                            dialog.close()
                                            ui.notify('转写输出设置已保存，下一次转写立即生效。', type='positive')

                                        with ui.row().classes('justify-end gap-3 w-full flex-wrap'):
                                            ui.button('保存设置', icon='save', on_click=save_transcribe_settings).classes('h-10 px-5 rounded-lg text-sm bg-blue-600 text-white')
                                dialog.open()

                            with ui.row().classes('items-start justify-between gap-4 w-full flex-wrap'):
                                with ui.column().classes('gap-1 min-w-0'):
                                    ui.label('开始转写').classes('font-semibold text-slate-900 dark:text-slate-100 text-lg')
                                    ui.label('转写会使用当前 ASR 模型、语言、热词和正则替换规则。').classes('text-sm text-slate-500')

                            with ui.row().classes('items-center justify-between gap-4 w-full flex-wrap bg-white border border-slate-200/70 rounded-xl px-4 py-3'):
                                with ui.row().classes('items-center gap-3 min-w-0 flex-1'):
                                    ui.icon('audio_file', size='md').classes('text-amber-600')
                                    with ui.column().classes('gap-0 min-w-0 flex-1'):
                                        selected_label = ui.label('尚未选择文件。').classes('text-base text-slate-700 dark:text-slate-300 break-all')
                                        selected_path_label = ui.label('请选择一个音频或视频文件开始。').classes('text-xs text-slate-500 break-all')

                            progress = ui.linear_progress(value=0, show_value=False, size='14px').props('rounded color=amber-7 track-color=orange-1').classes('w-full')
                            status_label = ui.label('等待选择音视频文件。').classes('text-sm text-slate-500 dark:text-slate-400')
                            result_box = ui.column().classes('gap-2 w-full mt-1')
                            queue_dialog_box = {'box': None}
                            repair_state = {'txt': None}
                            repair_status_box = {'box': None}

                            def render_result(result):
                                result_box.clear()
                                with result_box:
                                    if not result:
                                        return
                                    if not result.ok:
                                        ui.label(result.message).classes('text-sm text-red-600 font-semibold')
                                        return
                                    ui.label(f'转写完成：{result.input_file.name}').classes('text-base text-emerald-700 dark:text-emerald-300 font-semibold')
                                    if result.output_dir:
                                        ui.label(f'本次输出目录：{result.output_dir}').classes('text-sm text-slate-500 break-all')
                                    if result.output_files:
                                        ui.label('已生成以下格式，点击可直接打开对应文件。').classes('text-sm text-slate-500')
                                        with ui.row().classes('gap-2.5 flex-wrap items-center'):
                                            for kind, path in result.output_files.items():
                                                ui.button(
                                                    f'打开 {kind.upper()}',
                                                    icon='open_in_new',
                                                    on_click=lambda *_, p=path: open_local_file(p)
                                                ).props('outline color=amber-8').classes('h-10 px-4 rounded-lg text-sm font-semibold bg-white')
                                            if 'txt' in result.output_files and 'json' in result.output_files:
                                                ui.button(
                                                    '字幕修复',
                                                    icon='subtitles',
                                                    on_click=lambda *_, p=result.output_files['txt']: prompt_repair_txt(p),
                                                ).props('outline color=blue-7').classes('h-10 px-4 rounded-lg text-sm font-semibold bg-white')
                                            ui.button(
                                                '打开输出目录',
                                                icon='folder_open',
                                                on_click=lambda *_, p=result.output_dir: reveal_in_explorer(p) if p else ui.notify('暂无输出目录。', type='warning')
                                            ).props('outline color=grey-7').classes('h-10 px-4 rounded-lg text-sm font-semibold bg-white')
                                    preview = (result.text or '').strip()
                                    if preview:
                                        ui.label('识别预览').classes('text-xs text-slate-500')
                                        ui.label(preview[:260] + ('...' if len(preview) > 260 else '')).classes('text-sm font-mono text-slate-700 leading-relaxed')

                                        def show_full_preview(text=preview):
                                            with ui.dialog() as dialog, ui.card().classes('w-full max-w-3xl p-6 bg-white rounded-2xl gap-4'):
                                                ui.label('识别全文').classes('text-lg font-bold text-slate-900')
                                                ui.textarea(value=text).props('readonly autogrow outlined').classes('w-full font-mono text-sm leading-relaxed')
                                                with ui.row().classes('justify-end w-full gap-3'):
                                                    ui.button('复制全文', icon='content_copy', on_click=partial(copy_text_to_clipboard, text)).props('outline color=amber-8')
                                                    ui.button('关闭', on_click=dialog.close).props('flat color=grey-7')
                                            dialog.open()

                                        ui.button('查看全文', icon='open_in_full', on_click=show_full_preview).props('flat color=amber-8').classes('w-fit')

                            def render_queue():
                                box = queue_dialog_box.get('box')
                                if box is None:
                                    return
                                box.clear()
                                files = batch_queue['files']
                                with box:
                                    if not files:
                                        ui.label('队列为空，请先添加文件。').classes('text-sm text-slate-500 py-4')
                                    else:
                                        ui.label(f'批量队列：{len(files)} 个文件').classes('text-sm font-semibold text-slate-700')
                                        for index, file in enumerate(files, start=1):
                                            with ui.row().classes('items-center justify-between w-full gap-3 py-2 border-b border-slate-100'):
                                                ui.label(f'{index}. {Path(file).name}').classes('text-sm text-slate-700 min-w-0')

                                                def remove_from_queue(path=file):
                                                    batch_queue['files'] = [item for item in batch_queue['files'] if item != path]
                                                    render_queue()

                                                ui.button(icon='close', on_click=remove_from_queue).props('flat round color=grey-7 title="移除"')

                            def render_repair_status():
                                box = repair_status_box.get('box')
                                if box is None:
                                    return
                                box.clear()
                                txt_path = repair_state.get('txt')
                                with box:
                                    if not txt_path:
                                        ui.label('尚未选择 TXT 分行稿。').classes('font-semibold text-slate-800')
                                        ui.label('适合先修正 TXT 里的错字、断句和换行，再用同名 JSON 时间戳生成新的 SRT 字幕。').classes('text-sm text-slate-500')
                                        return
                                    txt_path = Path(txt_path)
                                    json_path = txt_path.with_suffix('.json')
                                    srt_path = txt_path.with_suffix('.srt')
                                    is_merge = txt_path.name.endswith('.merge.txt')
                                    txt_ok = txt_path.exists() and txt_path.suffix.lower() == '.txt' and not is_merge
                                    json_ok = json_path.exists()
                                    ui.label(txt_path.name).classes('font-semibold text-slate-900 break-all')
                                    ui.label(str(txt_path)).classes('text-xs text-slate-500 break-all')
                                    with ui.row().classes('gap-2 flex-wrap'):
                                        ui.label('TXT 可用' if txt_ok else 'TXT 不可用').classes('text-xs px-2 py-1 rounded bg-emerald-50 text-emerald-700' if txt_ok else 'text-xs px-2 py-1 rounded bg-red-50 text-red-700')
                                        ui.label('JSON 已匹配' if json_ok else '缺少同名 JSON').classes('text-xs px-2 py-1 rounded bg-emerald-50 text-emerald-700' if json_ok else 'text-xs px-2 py-1 rounded bg-amber-50 text-amber-700')
                                    ui.label(f'输出位置：{srt_path}').classes('text-xs text-slate-500 break-all')
                                    if is_merge:
                                        ui.label('merge.txt 是全文稿，不能用于重建字幕；请选择普通 .txt 分行稿。').classes('text-sm text-red-600')
                                    elif not json_ok:
                                        ui.label('需要同名 JSON 时间戳，例如 speech.txt 旁边要有 speech.json。').classes('text-sm text-amber-700')

                            def set_repair_txt(path: Path):
                                repair_state['txt'] = Path(path)
                                render_repair_status()
                                ui.notify('已填入字幕修复模块，请检查 JSON 状态后生成 SRT。', type='info')

                            def prompt_repair_txt(path: Path):
                                path = Path(path)
                                with ui.dialog() as dialog, ui.card().classes('w-full max-w-xl p-6 bg-white rounded-2xl gap-4'):
                                    ui.label('先检查并修改 TXT').classes('text-lg font-bold text-slate-900')
                                    ui.label('字幕修复会使用 TXT 的文字和换行来重建 SRT。建议先打开 TXT 修正错字、断句和换行，保存后再导入到下面的字幕修复模块。').classes('text-sm text-slate-500 leading-relaxed')
                                    with ui.column().classes('gap-1 bg-slate-50 border border-slate-200/70 rounded-lg p-3 w-full'):
                                        ui.label(path.name).classes('font-semibold text-slate-900 break-all')
                                        ui.label(str(path)).classes('text-xs text-slate-500 break-all')

                                    def confirm_import():
                                        dialog.close()
                                        set_repair_txt(path)

                                    with ui.row().classes('justify-between w-full gap-3 flex-wrap'):
                                        ui.button('打开 TXT 修改', icon='open_in_new', on_click=lambda: open_local_file(path)).props('outline color=amber-8').classes('h-10 px-4 rounded-lg text-sm bg-white')
                                        with ui.row().classes('gap-2'):
                                            ui.button('取消', on_click=dialog.close).props('flat color=grey-7')
                                            ui.button('已保存，导入修复', icon='check_circle', on_click=confirm_import).props('unelevated color=blue-6').classes('h-10 px-4 rounded-lg text-sm text-white')
                                dialog.open()

                            async def choose_repair_txt():
                                selected = await run.io_bound(
                                    select_files_dialog,
                                    '选择已编辑的 TXT 分行稿',
                                    'TXT 文件|*.txt|所有文件|*.*',
                                    False,
                                )
                                if selected:
                                    repair_state['txt'] = Path(selected[0])
                                    render_repair_status()

                            async def generate_repair_srt():
                                txt_path = repair_state.get('txt')
                                if not txt_path:
                                    ui.notify('请先选择 TXT 分行稿。', type='warning')
                                    return
                                ok, msg, srt_path = await run.io_bound(regenerate_srt_from_txt, Path(txt_path))
                                ui.notify(msg, type='positive' if ok else 'negative')
                                if ok and srt_path:
                                    open_local_file(srt_path)
                                    render_history()
                                    render_repair_status()

                            async def run_transcription(file_path: Path):
                                if transcribe_state['running']:
                                    ui.notify('已有转写任务正在进行中，请稍候。', type='warning')
                                    return
                                transcribe_state['running'] = True
                                progress.set_value(0)
                                status_label.set_text('准备开始转写...')
                                status_label.set_visibility(True)
                                result_box.clear()
                                cancel_button.set_visibility(True)

                                def on_progress(info):
                                    value = info['percent'] / 100
                                    progress.set_value(value)
                                    status_label.set_text(f"{info['detail']}（{info['percent']:.0f}%）")

                                try:
                                    result = await transcribe_file(file_path, on_progress)
                                    transcribe_state['last_result'] = result
                                    if result.ok:
                                        progress.set_value(1)
                                    if result.ok:
                                        status_label.set_visibility(False)
                                    else:
                                        status_label.set_text(result.message)
                                    render_result(result)
                                    ui.notify(result.message, type='positive' if result.ok else 'negative')
                                except asyncio.CancelledError:
                                    progress.set_value(0)
                                    status_label.set_text('转写任务已取消。')
                                    ui.notify('转写任务已取消。', type='warning')
                                finally:
                                    transcribe_state['running'] = False
                                    transcribe_state['task'] = None
                                    cancel_button.set_visibility(False)
                                    render_history()

                            def start_transcription_task(file_path: Path):
                                if transcribe_state['running']:
                                    ui.notify('已有转写任务正在进行中，请稍候。', type='warning')
                                    return
                                transcribe_state['task'] = asyncio.create_task(run_transcription(file_path))

                            def cancel_transcription():
                                task = transcribe_state.get('task')
                                if task and not task.done():
                                    task.cancel()
                                    status_label.set_text('正在取消转写任务...')
                                else:
                                    ui.notify('当前没有正在运行的转写任务。', type='info')

                            def clear_current_transcription_state():
                                if transcribe_state['running']:
                                    ui.notify('转写进行中，结束或取消后再清除当前结果。', type='warning')
                                    return
                                selected_file['path'] = None
                                batch_queue['files'] = []
                                transcribe_state['last_result'] = None
                                progress.set_value(0)
                                selected_label.set_text('尚未选择文件。')
                                selected_path_label.set_text('请选择一个音频或视频文件开始。')
                                status_label.set_text('等待选择音视频文件。')
                                status_label.set_visibility(True)
                                result_box.clear()
                                render_queue()
                                cancel_button.set_visibility(False)
                                ui.notify('当前转写状态已清除。', type='info')

                            async def start_from_path():
                                file_path = selected_file.get('path')
                                if not file_path:
                                    ui.notify('请先选择本地音视频文件。', type='warning')
                                    return
                                if not Path(file_path).exists():
                                    ui.notify('这个文件路径不存在，请重新选择。', type='warning')
                                    return
                                start_transcription_task(Path(file_path))

                            async def choose_local_file():
                                selected = await run.io_bound(select_media_file_dialog)
                                if selected:
                                    selected_file['path'] = selected
                                    selected_label.set_text(Path(selected).name)
                                    selected_path_label.set_text(str(selected))
                                    selected_label.tooltip(selected)
                                    ui.notify('已选择本地文件。', type='positive')

                            async def choose_batch_files(close_after=False):
                                selected = await run.io_bound(
                                    select_files_dialog,
                                    '选择要批量转写的音视频文件',
                                    '音视频文件|*.mp4;*.mkv;*.mov;*.avi;*.wav;*.mp3;*.m4a;*.flac;*.aac|所有文件|*.*',
                                    True,
                                )
                                if selected:
                                    existing = set(batch_queue['files'])
                                    batch_queue['files'].extend([path for path in selected if path not in existing])
                                    selected_file['path'] = selected[0]
                                    selected_label.set_text(Path(selected[0]).name)
                                    selected_path_label.set_text(f'批量队列共 {len(batch_queue["files"])} 个文件，当前预览第一个。')
                                    render_queue()

                            async def run_batch_queue():
                                files = [Path(p) for p in batch_queue['files']]
                                if not files:
                                    ui.notify('请先选择批量文件。', type='warning')
                                    return
                                if transcribe_state['running']:
                                    ui.notify('已有转写任务正在进行中，请稍候。', type='warning')
                                    return
                                transcribe_state['running'] = True
                                cancel_button.set_visibility(True)
                                result_box.clear()
                                try:
                                    total = len(files)
                                    for index, file_path in enumerate(files, start=1):
                                        if not file_path.exists():
                                            ui.notify(f'文件不存在，已跳过：{file_path.name}', type='warning')
                                            continue
                                        progress.set_value((index - 1) / total)
                                        status_label.set_visibility(True)
                                        status_label.set_text(f'批量转写 {index}/{total}：{file_path.name}')

                                        def on_progress(info, i=index, count=total):
                                            item_fraction = info['percent'] / 100 / count
                                            progress.set_value((i - 1) / count + item_fraction)
                                            status_label.set_text(f'批量转写 {i}/{count}：{info["detail"]}（{info["percent"]:.0f}%）')

                                        result = await transcribe_file(file_path, on_progress)
                                        transcribe_state['last_result'] = result
                                        render_result(result)
                                        if not result.ok:
                                            ui.notify(f'{file_path.name} 转写失败：{result.message}', type='negative')
                                    progress.set_value(1)
                                    status_label.set_visibility(False)
                                    ui.notify('批量队列处理完成。', type='positive')
                                except asyncio.CancelledError:
                                    progress.set_value(0)
                                    status_label.set_text('批量转写已取消。')
                                    ui.notify('批量转写已取消。', type='warning')
                                finally:
                                    transcribe_state['running'] = False
                                    transcribe_state['task'] = None
                                    cancel_button.set_visibility(False)
                                    render_history()

                            def start_batch_task():
                                if transcribe_state['running']:
                                    ui.notify('已有转写任务正在进行中，请稍候。', type='warning')
                                    return
                                transcribe_state['task'] = asyncio.create_task(run_batch_queue())

                            def open_batch_dialog():
                                with ui.dialog() as dialog, ui.card().classes('w-full max-w-3xl p-0 bg-white rounded-2xl gap-0 overflow-hidden'):
                                    with ui.row().classes('items-start justify-between w-full gap-4 px-6 py-5 border-b border-slate-100'):
                                        with ui.column().classes('gap-1 min-w-0'):
                                            ui.label('批量转写').classes('text-xl font-bold text-slate-900')
                                            ui.label('把多个音视频排队逐个转写，结果会分别保存到输出目录。').classes('text-sm text-slate-500')
                                        ui.button(icon='close', on_click=dialog.close).props('flat round color=grey-7')
                                    with ui.column().classes('gap-4 w-full px-6 py-5'):
                                        queue_dialog_box['box'] = ui.column().classes('gap-1 w-full max-h-80 overflow-y-auto')
                                        render_queue()

                                        def clear_queue():
                                            batch_queue['files'] = []
                                            render_queue()

                                        def start_and_close():
                                            dialog.close()
                                            start_batch_task()

                                        with ui.row().classes('justify-between gap-3 w-full flex-wrap'):
                                            with ui.row().classes('gap-2'):
                                                ui.button('添加文件', icon='playlist_add', on_click=choose_batch_files).props('outline color=amber-8').classes('h-10 px-4 rounded-lg text-sm bg-white')
                                                ui.button('清空队列', icon='delete_sweep', on_click=clear_queue).props('outline color=grey-7').classes('h-10 px-4 rounded-lg text-sm bg-white')
                                            ui.button('开始批量', icon='queue_play_next', on_click=start_and_close).props('unelevated color=blue-6').classes('h-10 px-5 rounded-lg text-sm text-white')
                                dialog.open()

                            with ui.row().classes('items-center gap-3 w-full flex-wrap pt-1'):
                                ui.button('选择文件', icon='attach_file', on_click=choose_local_file).props('outline color=amber-8').classes('h-11 px-5 rounded-lg text-base font-semibold bg-white')
                                ui.button('开始转写', icon='subtitles', on_click=start_from_path).props('unelevated color=blue-6').classes('h-11 px-6 rounded-lg text-base font-semibold text-white shadow-sm')
                                ui.button('批量转写', icon='queue_play_next', on_click=open_batch_dialog).props('outline color=blue-7').classes('h-11 px-5 rounded-lg text-base font-semibold bg-white')
                                cancel_button = ui.button('取消转写', icon='stop_circle', on_click=cancel_transcription).props('outline color=red-7').classes('h-11 px-5 rounded-lg text-base font-semibold bg-white')
                                cancel_button.set_visibility(False)
                                ui.button('清除', icon='backspace', on_click=clear_current_transcription_state).props('flat color=grey-7').classes('h-11 px-3 rounded-lg text-sm font-semibold')

                            ui.separator().classes('bg-slate-200/60 dark:bg-slate-700/60')
                            with ui.card().classes('bg-white border border-slate-200/70 p-4 rounded-xl gap-3 w-full shadow-none'):
                                with ui.row().classes('items-start justify-between gap-4 w-full flex-wrap'):
                                    with ui.column().classes('gap-1 min-w-0'):
                                        ui.label('字幕修复：TXT 重建 SRT').classes('font-semibold text-slate-900 text-base')
                                        ui.label('编辑 TXT 分行稿后，用同名 JSON 时间戳重新生成字幕。').classes('text-sm text-slate-500')
                                    with ui.row().classes('gap-2 shrink-0'):
                                        ui.button('选择 TXT', icon='article', on_click=choose_repair_txt).props('outline color=amber-8').classes('h-9 px-3 rounded-lg text-sm bg-white')
                                        ui.button('生成 SRT', icon='subtitles', on_click=generate_repair_srt).props('outline color=blue-7').classes('h-9 px-3 rounded-lg text-sm bg-white')
                                repair_status_box['box'] = ui.column().classes('w-full gap-2 bg-slate-50 border border-slate-200/70 rounded-lg p-3')
                                render_repair_status()

                        ui.label('转写历史记录').classes('text-xs font-bold text-slate-400 uppercase tracking-wider mt-2')
                        with ui.card().classes('bg-slate-50 dark:bg-slate-800/50 border border-slate-200/60 dark:border-slate-700/60 p-6 rounded-xl gap-4 w-full shadow-none'):
                            history_box = ui.column().classes('gap-2 w-full')

                            def render_history():
                                history_box.clear()
                                history = list_transcription_history(limit=12)
                                with history_box:
                                    if not history:
                                        ui.label('暂无转写历史。完成一次转写后会自动出现在这里。').classes('text-sm text-slate-500')
                                        return
                                    for item in history:
                                        files = item['files']
                                        total_size = sum(path.stat().st_size for path in files.values() if path.exists())
                                        with ui.row().classes('items-center justify-between w-full gap-3 py-3 border-b border-slate-200/60'):
                                            with ui.column().classes('gap-0 min-w-0'):
                                                ui.label(item['name']).classes('text-sm font-semibold text-slate-800')
                                                ui.label(f"{', '.join(kind.upper() for kind in files)} · {format_bytes(total_size)}").classes('text-xs text-slate-500')
                                                ui.label(str(item['folder'])).classes('text-xs text-slate-500 break-all')
                                            with ui.row().classes('gap-2 shrink-0'):
                                                ui.button('目录', icon='folder_open', on_click=lambda *_, p=item['folder']: reveal_in_explorer(p)).props('outline color=grey-7').classes('h-9 px-3 rounded-lg text-xs bg-white')

                                                def show_history_actions(history_item=item):
                                                    files_local = history_item['files']
                                                    with ui.dialog() as dialog, ui.card().classes('w-full max-w-lg p-6 bg-white rounded-2xl gap-4'):
                                                        ui.label(history_item['name']).classes('text-lg font-bold text-slate-900')
                                                        ui.label(str(history_item['folder'])).classes('text-xs text-slate-500 break-all')
                                                        if 'txt' in files_local and 'json' in files_local:
                                                            rebuild_history_srt = lambda *_, txt_path=files_local['txt']: prompt_repair_txt(txt_path)

                                                        def confirm_delete_history(folder=history_item['folder']):
                                                            with ui.dialog() as confirm_dialog, ui.card().classes('w-full max-w-sm p-6 bg-white rounded-2xl gap-4'):
                                                                ui.label('删除这条转写输出？').classes('text-lg font-bold text-slate-900')
                                                                ui.label('只会删除这次 GUI 生成的输出目录，不会删除你的原始音视频文件。').classes('text-sm text-slate-500')

                                                                def apply_delete():
                                                                    ok, msg = delete_transcription_output(folder)
                                                                    confirm_dialog.close()
                                                                    dialog.close()
                                                                    ui.notify(msg, type='positive' if ok else 'negative')
                                                                    render_history()

                                                                with ui.row().classes('justify-end w-full gap-3'):
                                                                    ui.button('取消', on_click=confirm_dialog.close).props('flat color=grey-7')
                                                                    ui.button('删除', icon='delete', on_click=apply_delete).props('unelevated color=red-7')
                                                            confirm_dialog.open()

                                                        with ui.row().classes('items-center justify-between w-full gap-3 flex-wrap'):
                                                            with ui.row().classes('gap-2 flex-wrap'):
                                                                for kind, path in files_local.items():
                                                                    ui.button(f'打开 {kind.upper()}', icon='open_in_new', on_click=lambda *_, p=path: open_local_file(p)).props('outline color=amber-8').classes('h-9 px-3 rounded-lg text-xs bg-white')
                                                                if 'txt' in files_local and 'json' in files_local:
                                                                    ui.button('字幕修复', icon='subtitles', on_click=rebuild_history_srt).props('outline color=blue-7').classes('h-9 px-3 rounded-lg text-xs bg-white')
                                                                ui.button('删除输出', icon='delete', on_click=confirm_delete_history).props('outline color=red-7').classes('h-9 px-3 rounded-lg text-xs bg-white')
                                                            ui.button('关闭', on_click=dialog.close).props('flat color=grey-7')
                                                    dialog.open()

                                                ui.button('更多', icon='more_horiz', on_click=show_history_actions).props('outline color=amber-8').classes('h-9 px-3 rounded-lg text-xs bg-white')

                            render_history()
                            with ui.row().classes('justify-end w-full'):
                                ui.button('刷新历史', icon='refresh', on_click=render_history).props('outline color=grey-7').classes('h-9 px-4 rounded-lg text-sm bg-white')

                # === Tab 7: 服务与诊断 ===
                with ui.tab_panel(tab_service):
                    with ui.column().classes('gap-6 w-full pb-8'):
                        with ui.column().classes('gap-1 border-b border-slate-100 dark:border-slate-800 pb-4 w-full'):
                            ui.label('服务与诊断').classes('text-2xl font-bold text-slate-900 dark:text-white')
                            ui.label('查看 ASR 服务端、听写客户端和模型状态，处理启动、重启与日志排查。').classes('text-sm text-slate-500 dark:text-slate-400')

                        health_box = ui.column().classes('gap-3 w-full')
                        latest_error_box = ui.column().classes('gap-2 w-full')

                        def log_path(name: str) -> Path:
                            return BASE_DIR / 'logs' / name

                        def log_size_text(name: str) -> str:
                            path = log_path(name)
                            return format_bytes(path.stat().st_size) if path.exists() else '暂无文件'

                        def open_named_log(name: str, label: str) -> None:
                            path = log_path(name)
                            if path.exists():
                                open_local_file(path)
                                ui.notify(f'已打开{label}：{path}', type='positive')
                            else:
                                ui.notify(f'还没有生成{label}：{path}', type='warning')

                        def old_log_files(days: int = 30) -> list[Path]:
                            cutoff = time.time() - days * 24 * 60 * 60
                            return [
                                path for path in log_files()
                                if path.name not in {'client_latest.log', 'server_latest.log'} and path.stat().st_mtime < cutoff
                            ]

                        def confirm_clean_old_logs() -> None:
                            paths = old_log_files()
                            if not paths:
                                ui.notify('没有 30 天前的旧日志需要清理。', type='info')
                                return
                            with ui.dialog() as dialog, ui.card().classes('w-full max-w-md p-6 bg-white rounded-2xl gap-4'):
                                ui.label('清理 30 天前的旧日志？').classes('text-lg font-bold text-slate-900')
                                ui.label(
                                    f'将删除 logs/ 下 {len(paths)} 个 30 天前的旧日志文件；不会删除最新 client_latest.log、server_latest.log，也不会删除配置、录音和转写输出。'
                                ).classes('text-sm text-slate-500 leading-relaxed')

                                def apply_cleanup():
                                    deleted = 0
                                    failed = 0
                                    for path in paths:
                                        try:
                                            path.unlink()
                                            deleted += 1
                                        except OSError:
                                            failed += 1
                                    dialog.close()
                                    render_diagnostics()
                                    ui.notify(
                                        f'已删除 {deleted} 个旧日志。' if not failed else f'已删除 {deleted} 个旧日志，{failed} 个删除失败。',
                                        type='positive' if not failed else 'warning',
                                    )

                                with ui.row().classes('justify-end w-full gap-3'):
                                    ui.button('取消', on_click=dialog.close).props('flat color=grey-7')
                                    ui.button('确认清理', icon='delete_sweep', on_click=apply_cleanup).props('color=red-7')
                            dialog.open()

                        def confirm_stop_all() -> None:
                            with ui.dialog() as dialog, ui.card().classes('w-full max-w-md p-6 bg-white rounded-2xl gap-4'):
                                ui.label('完全退出 CapsWriter？').classes('text-lg font-bold text-slate-900')
                                ui.label('会关闭控制中心、听写客户端和 ASR 服务端。离线语音转文字会停止，之后需要重新启动程序。').classes('text-sm text-slate-500 leading-relaxed')

                                def apply_stop_all():
                                    ui.notify('正在完全退出 CapsWriter...', type='warning')
                                    dialog.close()
                                    ui.timer(0.2, lambda: process_manager.stop_all(include_self=True), once=True)

                                with ui.row().classes('justify-end w-full gap-3'):
                                    ui.button('取消', on_click=dialog.close).props('flat color=grey-7')
                                    ui.button('完全退出', icon='power_settings_new', on_click=apply_stop_all).props('color=red-7')
                            dialog.open()

                        def render_health():
                            health_box.clear()
                            state = process_manager.get_health_status()
                            gui_pid = process_manager.find_listening_pid(6017)
                            project_pids = process_manager.find_project_processes(
                                ('run_app.py', 'web_gui\\\\app.py', 'web_gui/app.py', 'start_server.py', 'start_client.py'),
                                include_self=True,
                            )
                            with health_box:
                                with ui.grid(columns=4).classes('w-full gap-3'):
                                    with ui.card().classes('bg-white border border-slate-200/60 p-4 rounded-xl gap-1 shadow-none'):
                                        ui.label('ASR 服务端').classes('text-xs text-slate-500')
                                        ui.label('在线' if state['server_alive'] else '离线').classes('text-lg font-bold text-emerald-700' if state['server_alive'] else 'text-lg font-bold text-red-600')
                                        ui.label(f"PID: {state['server_pid'] or '-'} · 端口 6016").classes('text-xs text-slate-500')
                                    with ui.card().classes('bg-white border border-slate-200/60 p-4 rounded-xl gap-1 shadow-none'):
                                        ui.label('听写客户端').classes('text-xs text-slate-500')
                                        ui.label('在线' if state['client_alive'] else '离线').classes('text-lg font-bold text-emerald-700' if state['client_alive'] else 'text-lg font-bold text-red-600')
                                        ui.label(f"PID: {state['client_pid'] or '-'}").classes('text-xs text-slate-500')
                                    with ui.card().classes('bg-white border border-slate-200/60 p-4 rounded-xl gap-1 shadow-none'):
                                        ui.label('控制中心 GUI').classes('text-xs text-slate-500')
                                        ui.label('在线' if gui_pid else '未监听').classes('text-lg font-bold text-emerald-700' if gui_pid else 'text-lg font-bold text-amber-700')
                                        ui.label(f"PID: {gui_pid or '-'} · 端口 6017").classes('text-xs text-slate-500')
                                    with ui.card().classes('bg-white border border-slate-200/60 p-4 rounded-xl gap-1 shadow-none'):
                                        ui.label('模型状态').classes('text-xs text-slate-500')
                                        ui.label('服务就绪' if state['model_loaded'] else '未就绪').classes('text-lg font-bold text-emerald-700' if state['model_loaded'] else 'text-lg font-bold text-amber-700')
                                        ui.label('基于 6016 健康探测').classes('text-xs text-slate-500')
                                ui.label(f"项目相关进程：{len(set(project_pids))} 个").classes('text-xs text-slate-500')

                        def render_diagnostics():
                            latest_error_box.clear()
                            state = process_manager.get_health_status()
                            with latest_error_box:
                                with ui.card().classes('bg-white border border-slate-200/60 p-4 rounded-xl gap-2 shadow-none w-full'):
                                    ui.label('最近错误').classes('text-xs font-bold text-slate-400 uppercase tracking-wider')
                                    ui.label(state['latest_error']).classes('text-sm text-slate-600 break-all')
                                    ui.label(f"客户端日志：{log_size_text('client_latest.log')} · 服务端日志：{log_size_text('server_latest.log')}").classes('text-xs text-slate-500')

                        def notify_process(action):
                            ok, msg = action()
                            ui.notify(msg, type='positive' if ok else 'negative')
                            render_health()
                            render_diagnostics()

                        with ui.card().classes('bg-slate-50 dark:bg-slate-800/50 border border-slate-200/60 dark:border-slate-700/60 p-6 rounded-xl gap-4 w-full shadow-none'):
                            with ui.column().classes('gap-1'):
                                ui.label('运行状态与日志').classes('text-lg font-bold text-slate-900 dark:text-slate-100')
                                ui.label('查看服务端、客户端、控制中心和日志状态；有异常时优先看这里。').classes('text-xs text-slate-500')

                            render_health()
                            render_diagnostics()

                            with ui.row().classes('justify-end w-full gap-2'):
                                ui.button('客户端日志', icon='description', on_click=lambda: open_named_log('client_latest.log', '客户端日志')).props('outline color=grey-7').classes('h-10 px-4 rounded-lg bg-white')
                                ui.button('服务端日志', icon='article', on_click=lambda: open_named_log('server_latest.log', '服务端日志')).props('outline color=grey-7').classes('h-10 px-4 rounded-lg bg-white')
                                ui.button('日志目录', icon='folder_open', on_click=open_logs_dir).props('outline color=grey-7').classes('h-10 px-4 rounded-lg bg-white')
                                ui.button('清理旧日志', icon='delete_sweep', on_click=confirm_clean_old_logs).props('outline color=red-7').classes('h-10 px-4 rounded-lg bg-white')

                        with ui.card().classes('bg-slate-50 dark:bg-slate-800/50 border border-slate-200/60 dark:border-slate-700/60 p-6 rounded-xl gap-4 w-full shadow-none'):
                            ui.label('服务操作').classes('text-lg font-bold text-slate-900 dark:text-slate-100')
                            with ui.row().classes('items-center justify-between w-full gap-4'):
                                with ui.column().classes('gap-1'):
                                    ui.label('启动缺失组件').classes('font-semibold text-slate-900 dark:text-slate-100')
                                    ui.label('只拉起当前离线的服务端或听写客户端，适合程序异常退出后恢复。').classes('text-xs text-slate-500')
                                ui.button('启动缺失组件', icon='play_arrow', on_click=lambda *_: notify_process(process_manager.launch_missing_components)).props('color=amber-8').classes('h-10 px-4 rounded-lg')

                            ui.separator().classes('bg-slate-200/60 dark:bg-slate-700/60')

                            with ui.row().classes('items-center justify-between w-full gap-4'):
                                with ui.column().classes('gap-1'):
                                    ui.label('ASR 语音服务端').classes('font-semibold text-slate-900 dark:text-slate-100')
                                    ui.label('负责 6016 WebSocket、语音识别模型推理和文件转写请求。').classes('text-xs text-slate-500')
                                with ui.row().classes('gap-2'):
                                    ui.button('拉起服务端', icon='play_arrow', on_click=lambda *_: notify_process(process_manager.launch_server)).props('outline color=amber-8').classes('h-10 px-4 rounded-lg bg-white')
                                    ui.button('重启服务端', icon='restart_alt', on_click=lambda *_: notify_process(process_manager.restart_server)).props('outline color=red-7').classes('h-10 px-4 rounded-lg bg-white')

                            with ui.row().classes('items-center justify-between w-full gap-4'):
                                with ui.column().classes('gap-0.5'):
                                    ui.label('听写客户端').classes('font-semibold text-slate-900 dark:text-slate-100')
                                    ui.label('负责全局快捷键、录音采集、识别结果处理与文字上屏。').classes('text-xs text-slate-500')
                                with ui.row().classes('gap-2'):
                                    ui.button('拉起客户端', icon='play_arrow', on_click=lambda *_: notify_process(process_manager.launch_client)).props('outline color=amber-8').classes('h-10 px-4 rounded-lg bg-white')
                                    ui.button('重启客户端', icon='restart_alt', on_click=lambda *_: notify_process(process_manager.restart_client)).props('outline color=red-7').classes('h-10 px-4 rounded-lg bg-white')

                            ui.separator().classes('bg-slate-200/60 dark:bg-slate-700/60')

                            with ui.row().classes('items-end justify-between w-full gap-4'):
                                with ui.column().classes('gap-0.5'):
                                    ui.label('完全退出').classes('font-semibold text-slate-900 dark:text-slate-100')
                                    ui.label('关闭 GUI、听写客户端、ASR 服务端和启动器，等同于托盘里的完全退出。').classes('text-xs text-slate-500')
                                with ui.row().classes('justify-end'):
                                    ui.button('完全退出', icon='power_settings_new', on_click=confirm_stop_all).props('outline color=red-7').classes('h-10 px-4 rounded-lg bg-white')

# 以 100% 原生桌面客户端软件窗口模式运行 (Native Desktop Client App Window)
if __name__ in {"__main__", "__mp_main__"}:
    t_gui_run_start = time.perf_counter()
    if sys.platform == 'win32':
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("CapsWriter.Offline.App.v3")
        except Exception:
            pass

    app.native.window_args['resizable'] = True
    ui.run(
        title='CapsWriter',
        favicon=BASE_DIR / 'assets' / 'source' / 'capswriter.ico',
        port=6017,
        reload=False,
        dark=False,
        native=True,
        window_size=(1120, 740)
    )
