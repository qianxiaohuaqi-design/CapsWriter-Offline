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
from core.product.paths import bootstrap, resource_root, data_root
BASE_DIR = resource_root()
bootstrap()

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

LOG_DIR = data_root() / "logs"
CONTROL_PID_FILE = LOG_DIR / "control_center.pid"
CLIENT_PID_FILE = process_manager.CLIENT_PID_FILE

# Windows 静默隐藏控制台标识
CREATE_NO_WINDOW = 0x08000000 if sys.platform == 'win32' else 0
GUI_PID_FILE = LOG_DIR / "control_center_gui.pid"
GUI_PROCESS: subprocess.Popen | None = None
SHOULD_EXIT = threading.Event()
_WINDOWS_MUTEX = None
_LAUNCHER_LOCK = None
CONTROL_CENTER_URL = "http://127.0.0.1:6017"


def _is_frozen_app() -> bool:
    return bool(getattr(sys, 'frozen', False))


def _is_gui_alive() -> bool:
    return bool((GUI_PROCESS and GUI_PROCESS.poll() is None) or
                process_manager.read_alive_pid(GUI_PID_FILE, process_manager.component_keyword('app.py')))


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
    listener = process_manager.find_listening_pid(6017)
    if not listener or not process_manager.is_process_alive(listener, process_manager.component_keyword('app.py')):
        print('控制中心端口归属未确认，未打开浏览器。')
        return
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
    gui_pid = GUI_PROCESS.pid if GUI_PROCESS and GUI_PROCESS.poll() is None else process_manager.read_alive_pid(GUI_PID_FILE, process_manager.component_keyword('app.py'))
    if sys.platform != 'win32' or not gui_pid:
        return False
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        target_pids = {gui_pid}
        try:
            import psutil
            target_pids.update(child.pid for child in psutil.Process(gui_pid).children(recursive=True))
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


def _ensure_control_center_visible(delay: float = 0.1, *, wait: bool = False) -> None:
    """Restore the owned GUI; short-lived launchers must finish this inline."""
    def worker() -> None:
        time.sleep(delay)
        for _ in range(60):
            if process_manager.is_port_open('127.0.0.1', 6017):
                listener = process_manager.find_listening_pid(6017)
                if not listener or not process_manager.is_process_alive(listener, process_manager.component_keyword('app.py')):
                    print('控制中心端口归属未确认，未唤起或打开浏览器。')
                    return
                for _ in range(20):
                    if _show_control_center_window():
                        return
                    time.sleep(0.25)
                _open_control_center_browser()
                return
            time.sleep(0.25)

    if wait:
        worker()
    else:
        threading.Thread(target=worker, daemon=True).start()


def _launch_gui(*, wait_for_window: bool = False) -> None:
    """打开控制中心 GUI；如果已打开则不重复启动。"""
    global GUI_PROCESS
    if _is_gui_alive():
        _ensure_control_center_visible(delay=0.2, wait=wait_for_window)
        return
    if process_manager.is_port_open('127.0.0.1', 6017):
        listener = process_manager.find_listening_pid(6017)
        if not listener or not process_manager.is_process_alive(listener, process_manager.component_keyword('app.py')):
            print('6017 端口被未确认归属的程序占用，请释放该端口后重新打开控制中心。')
            return
        process_manager.write_pid(GUI_PID_FILE, listener)
        _ensure_control_center_visible(wait=wait_for_window)
        return

    t_gui_spawn_start = time.perf_counter()
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
        cwd=str(data_root()),
        creationflags=CREATE_NO_WINDOW,
        env=env,
    )
    LOG_DIR.mkdir(exist_ok=True)
    process_manager.write_pid(GUI_PID_FILE, GUI_PROCESS.pid)
    _ensure_control_center_visible(wait=wait_for_window)


def _stop_gui() -> None:
    global GUI_PROCESS
    process = GUI_PROCESS
    try:
        # A reused GUI may have been launched by an earlier, short-lived process.
        # Only our own Popen handle supports wait(); the PID-file fallback checks
        # the recorded creation time and application ownership before stopping it.
        if process is not None and process.poll() is None:
            try:
                process_manager.close_windows_by_pid(process.pid, include_children=True, preserve_background=True)
                process.wait(timeout=3)
            except Exception:
                # Use the GUI-specific owned-PID fallback below; it also covers
                # windows adopted from another launcher.
                pass
    finally:
        GUI_PROCESS = None
        process_manager.stop_gui()


def _request_exit() -> bool:
    try:
        from core.tools.key_reset import release_all_modifier_keys
        release_all_modifier_keys()
    except Exception:
        pass
    result = process_manager.stop_all(include_self=False)
    if not result.get('waiting'):
        SHOULD_EXIT.set()
        return True
    return False


def main():
    global _WINDOWS_MUTEX, _LAUNCHER_LOCK
    t_bootstrap_start = time.perf_counter()

    if '--coordinator' in sys.argv:
        from core.product.coordinator import serve
        serve()
        return

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
        _launch_gui(wait_for_window=True)
        return
    from core.product.storage import OwnerLock
    _LAUNCHER_LOCK = OwnerLock(data_root() / 'runtime' / 'launcher.lock')
    if not _LAUNCHER_LOCK.acquire():
        _ensure_control_center_visible(wait=True)
        return
    if sys.platform == 'win32':
        import ctypes
        ctypes.windll.kernel32.CreateMutexW.restype = ctypes.c_void_p
        _WINDOWS_MUTEX = ctypes.windll.kernel32.CreateMutexW(None, False, 'CapsWriter.Offline.ControlCenter')
    process_manager.write_pid(CONTROL_PID_FILE, os.getpid())
    from core.product.bridge import ensure_coordinator
    from core.product.storage import read_json
    exit_marker = data_root() / 'runtime' / 'exit-ready.json'
    exit_marker.unlink(missing_ok=True)
    try:
        snapshot = ensure_coordinator()
    except Exception as error:
        snapshot = {}
        print(f'后台管理器启动失败：{error}')

    print("=" * 60)
    print(" [CapsWriter 智能控制中心] 正在初始化原生桌面客户端...")
    print("=" * 60)

    # 1. 后台静默启动 ASR 服务端 (无黑框)，端口已占用则复用已有服务
    t_srv_start = time.perf_counter()
    if snapshot.get('setup', {}).get('default_model'):
        ok, message = process_manager.launch_server()
        print(f"[1/2] {message} ({time.perf_counter() - t_srv_start:.3f}s)")
        # 轻量等待 ASR 服务端网络端口就绪（最多 2.5s），降低客户端启动后的连接拒绝率
        for _ in range(12):
            if process_manager.is_port_open('127.0.0.1', 6016):
                break
            time.sleep(0.2)

    # 2. 后台静默启动客户端按键监听 (无黑框)，优先复用本启动器拉起的存活进程
    t_cli_start = time.perf_counter()
    client_pid = process_manager.read_alive_pid(CLIENT_PID_FILE, 'start_client')
    if client_pid:
        print(f"[2/2] 检测到客户端热键监听已在运行 (PID: {client_pid})，直接复用 ({time.perf_counter() - t_cli_start:.3f}s)")
    else:
        print('[2/2] 模型真实加载成功后，由后台自动启动听写客户端。')

    # 3. 启动 100% 原生桌面客户端窗口
    print("\n正在唤醒 CapsWriter 原生桌面客户端窗口...")
    _launch_gui()
    print(f"[Launcher] 启动器就绪引导完成，耗时: {time.perf_counter() - t_bootstrap_start:.3f}s")

    # 4. 启动控制中心总托盘；托盘退出才会彻底结束后台服务
    try:
        from web_gui.control_tray import start_tray_in_background

        tray_thread = start_tray_in_background(
            open_gui=_launch_gui,
            stop_gui=_stop_gui,
            exit_app=_request_exit,
        )
    except Exception as e:
        print(f"[警告] 控制中心托盘启动失败：{e}")
        tray_thread = None

    while not SHOULD_EXIT.is_set():
        if exit_marker.exists():
            process_manager.stop_all(include_self=False, coordinator_checked=True)
            SHOULD_EXIT.set()
            break
        if not _is_gui_alive() and (not tray_thread or not tray_thread.is_alive()):
            # Without a working tray, keep a visible way to control the app.
            _launch_gui()
        time.sleep(0.5)

    try:
        if CONTROL_PID_FILE.read_text(encoding='utf-8').strip() == str(os.getpid()):
            CONTROL_PID_FILE.unlink()
    except Exception:
        pass
    if _LAUNCHER_LOCK:
        _LAUNCHER_LOCK.release()
    if _WINDOWS_MUTEX:
        import ctypes
        ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(_WINDOWS_MUTEX))

if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
