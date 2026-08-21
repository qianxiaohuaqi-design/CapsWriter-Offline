# coding: utf-8
"""
CapsWriter 智能控制中心 - 统一桌面客户端门面脚本 (Native Client App Launcher)

职责：
1. 静默拉起 ASR 语音模型服务端 (6016) 与 客户端按键监听器；
2. 直接弹出 100% 原生桌面客户端 GUI 窗口 (PyWebView Native App Window)。
"""

import os
import sys
import subprocess
import time
import threading
import runpy
import multiprocessing
import webbrowser
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

from web_gui import process_manager

# Windows 独立 AppUserModelID 与 150% 高 DPI 感知支持
if sys.platform == 'win32':
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("CapsWriter.Offline.App.v3")
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

LOG_DIR = BASE_DIR / "logs"
CONTROL_PID_FILE = LOG_DIR / "control_center.pid"
CLIENT_PID_FILE = process_manager.CLIENT_PID_FILE

# Windows 静默隐藏控制台标识
CREATE_NO_WINDOW = 0x08000000 if sys.platform == 'win32' else 0
GUI_PID_FILE = LOG_DIR / "control_center_gui.pid"
GUI_PROCESS: subprocess.Popen | None = None
SHOULD_EXIT = threading.Event()
CONTROL_CENTER_URL = "http://127.0.0.1:6017"


def _is_frozen_app() -> bool:
    return bool(getattr(sys, 'frozen', False))


def _is_gui_alive() -> bool:
    return bool(GUI_PROCESS and GUI_PROCESS.poll() is None)


def _has_visible_control_window() -> bool:
    if sys.platform != 'win32':
        return False
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        found = False

        def callback(hwnd, lparam):
            nonlocal found
            if not user32.IsWindowVisible(hwnd):
                return True

            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True

            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            title = buffer.value
            if title == 'CapsWriter':
                found = True
                return False
            return True

        user32.EnumWindows(EnumWindowsProc(callback), 0)
        return found
    except Exception:
        return False


def _open_control_center_browser() -> None:
    if sys.platform == 'win32':
        try:
            os.startfile(CONTROL_CENTER_URL)
            return
        except Exception:
            pass
    try:
        webbrowser.open(CONTROL_CENTER_URL)
    except Exception:
        pass


def _show_control_center_window() -> bool:
    if sys.platform != 'win32' or not GUI_PROCESS:
        return False
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        target_pids = {GUI_PROCESS.pid}
        try:
            import psutil
            target_pids.update(child.pid for child in psutil.Process(GUI_PROCESS.pid).children(recursive=True))
        except Exception:
            pass
        found = False
        EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def callback(hwnd, lparam):
            nonlocal found
            process_id = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
            if process_id.value not in target_pids:
                return True

            title_length = user32.GetWindowTextLengthW(hwnd)
            if title_length <= 0:
                return True
            title_buffer = ctypes.create_unicode_buffer(title_length + 1)
            user32.GetWindowTextW(hwnd, title_buffer, title_length + 1)
            if title_buffer.value != 'CapsWriter':
                return True

            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
            found = True
            return False

        user32.EnumWindows(EnumWindowsProc(callback), 0)
        return found
    except Exception:
        return False


def _ensure_control_center_visible(delay: float = 0.1) -> None:
    def worker() -> None:
        time.sleep(delay)
        for _ in range(60):
            if process_manager.is_port_open('127.0.0.1', 6017):
                for _ in range(20):
                    if _show_control_center_window() or _has_visible_control_window():
                        return
                    time.sleep(0.25)
                _open_control_center_browser()
                return
            time.sleep(0.25)

    threading.Thread(target=worker, daemon=True).start()


def _launch_gui() -> None:
    """打开控制中心 GUI；如果已打开则不重复启动。"""
    global GUI_PROCESS
    if _is_gui_alive():
        _ensure_control_center_visible(delay=0.2)
        return

    env = os.environ.copy()
    env['CAPSWRITER_CONTROL_CENTER'] = '1'
    if _is_frozen_app():
        command = [str(Path(sys.executable)), '--gui']
    else:
        app_script = BASE_DIR / "web_gui" / "app.py"
        if not app_script.exists():
            return
        command = [process_manager.python_executable(prefer_console=False), str(app_script)]

    GUI_PROCESS = subprocess.Popen(
        command,
        cwd=str(BASE_DIR),
        creationflags=CREATE_NO_WINDOW,
        env=env,
    )
    LOG_DIR.mkdir(exist_ok=True)
    GUI_PID_FILE.write_text(str(GUI_PROCESS.pid), encoding='utf-8')
    _ensure_control_center_visible()


def _stop_gui() -> None:
    global GUI_PROCESS
    if _is_gui_alive():
        try:
            process_manager.close_windows_by_pid(GUI_PROCESS.pid, include_children=True)
            GUI_PROCESS.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process_manager.stop_pid(GUI_PROCESS.pid)
        except Exception:
            process_manager.stop_pid(GUI_PROCESS.pid)
    GUI_PROCESS = None
    process_manager.stop_gui()


def _request_exit() -> None:
    try:
        from core.tools.key_reset import release_all_modifier_keys
        release_all_modifier_keys()
    except Exception:
        pass
    try:
        process_manager.stop_all(include_self=False)
    finally:
        SHOULD_EXIT.set()


def main():
    if '--server' in sys.argv:
        from core.server.app import CapsWriterServer

        CapsWriterServer().start()
        return

    if '--client' in sys.argv:
        from core.client import CapsWriterClient

        CapsWriterClient().start()
        return

    if '--gui' in sys.argv:
        os.environ['CAPSWRITER_CONTROL_CENTER'] = '1'
        if _is_frozen_app():
            app_script = Path(getattr(sys, '_MEIPASS', BASE_DIR)) / 'web_gui' / 'app.py'
            runpy.run_path(str(app_script), run_name='__main__')
        else:
            runpy.run_module('web_gui.app', run_name='__main__')
        return

    LOG_DIR.mkdir(exist_ok=True)
    existing_control_pid = process_manager.read_alive_control_pid(CONTROL_PID_FILE)
    if existing_control_pid and existing_control_pid != os.getpid():
        print(f"CapsWriter 控制中心已在运行 (PID: {existing_control_pid})，本次启动退出")
        if not process_manager.is_port_open('127.0.0.1', 6017):
            _launch_gui()
        else:
            _ensure_control_center_visible(delay=0.2)
        return
    CONTROL_PID_FILE.write_text(str(os.getpid()), encoding='utf-8')

    print("=" * 60)
    print(" [CapsWriter 智能控制中心] 正在初始化原生桌面客户端...")
    print("=" * 60)

    # 1. 后台静默启动 ASR 服务端 (无黑框)，端口已占用则复用已有服务
    if process_manager.is_port_open('127.0.0.1', 6016):
        print("[1/2] 检测到 ASR 服务端 (6016) 已在运行，直接复用")
    else:
        ok, message = process_manager.launch_server()
        print(f"[1/2] {message}")

    # 2. 后台静默启动客户端按键监听 (无黑框)，优先复用本启动器拉起的存活进程
    client_pid = process_manager.read_alive_pid(CLIENT_PID_FILE, 'start_client')
    if client_pid:
        print(f"[2/2] 检测到客户端热键监听已在运行 (PID: {client_pid})，直接复用")
    else:
        ok, message = process_manager.launch_client()
        print(f"[2/2] {message}")

    # 3. 启动 100% 原生桌面客户端窗口
    print("\n正在唤醒 CapsWriter 原生桌面客户端窗口...")
    _launch_gui()

    # 4. 启动控制中心总托盘；托盘退出才会彻底结束后台服务
    try:
        from web_gui.control_tray import start_tray_in_background

        start_tray_in_background(
            open_gui=_launch_gui,
            stop_gui=_stop_gui,
            exit_app=_request_exit,
        )
    except Exception as e:
        print(f"[警告] 控制中心托盘启动失败：{e}")

    while not SHOULD_EXIT.is_set():
        time.sleep(0.5)

    try:
        if CONTROL_PID_FILE.read_text(encoding='utf-8').strip() == str(os.getpid()):
            CONTROL_PID_FILE.unlink()
    except Exception:
        pass

if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
