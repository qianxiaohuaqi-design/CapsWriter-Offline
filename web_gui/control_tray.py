# coding: utf-8
"""CapsWriter 控制中心总托盘。

这个托盘属于桌面控制中心启动器，负责统一管理 GUI、ASR 服务端和听写客户端。
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Callable

from web_gui import process_manager
from core.tools.icon_assets import ICON_PATH as SOURCE_ICON_PATH
from core.tools.icon_assets import load_ico_frame

BASE_DIR = Path(__file__).resolve().parent.parent
ICON_PATH = SOURCE_ICON_PATH
MASTER_ICON_PATH = BASE_DIR / 'assets' / 'source' / 'capswriter_master.png'
TRAY_OWNER_FILE = BASE_DIR / 'logs' / 'control_center_tray.pid'


def _is_existing_tray_owner_alive() -> bool:
    owner_pid = process_manager.read_alive_pid(TRAY_OWNER_FILE)
    return bool(owner_pid and owner_pid != os.getpid())


def _launcher_owns_tray() -> bool:
    launcher_pid = process_manager.read_alive_pid(BASE_DIR / 'logs' / 'control_center.pid', 'run_app')
    return bool(launcher_pid and launcher_pid != os.getpid())


def _claim_tray_owner() -> bool:
    TRAY_OWNER_FILE.parent.mkdir(exist_ok=True)
    if _launcher_owns_tray() and os.environ.get('CAPSWRITER_CONTROL_CENTER') != '1':
        return False
    for _ in range(2):
        try:
            fd = os.open(str(TRAY_OWNER_FILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, 'w', encoding='utf-8') as file:
                file.write(str(os.getpid()))
            return True
        except FileExistsError:
            if _is_existing_tray_owner_alive():
                return False
            try:
                TRAY_OWNER_FILE.unlink()
            except Exception:
                return False
    return False


def _release_tray_owner() -> None:
    try:
        if TRAY_OWNER_FILE.read_text(encoding='utf-8').strip() == str(os.getpid()):
            TRAY_OWNER_FILE.unlink()
    except Exception:
        pass


class ControlTray:
    """控制中心总托盘。"""

    def __init__(
        self,
        *,
        open_gui: Callable[[], None],
        stop_gui: Callable[[], None],
        exit_app: Callable[[], None],
    ):
        self.open_gui = open_gui
        self.stop_gui = stop_gui
        self.exit_app = exit_app
        self.icon = None
        self._lock = threading.Lock()
        self._status_cache = '正在检查听写服务状态'
        self._status_checked_at = 0.0

    def _create_image(self):
        from PIL import Image

        for path in [ICON_PATH, MASTER_ICON_PATH]:
            if path.exists():
                try:
                    if path.suffix.lower() == '.ico':
                        image = load_ico_frame(path, 64)
                    else:
                        image = Image.open(path)
                        if image.mode != 'RGBA':
                            image = image.convert('RGBA')
                    return image.resize((64, 64), Image.Resampling.LANCZOS)
                except Exception:
                    pass

        image = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
        return image

    @staticmethod
    def _open_logs_dir():
        logs_dir = BASE_DIR / 'logs'
        logs_dir.mkdir(exist_ok=True)
        os.startfile(str(logs_dir))

    def _status_text(self) -> str:
        now = time.monotonic()
        if now - self._status_checked_at < 5:
            return self._status_cache

        self._status_checked_at = now
        try:
            server_pid = process_manager.read_alive_pid(process_manager.SERVER_PID_FILE, 'start_server')
            client_pid = process_manager.read_alive_pid(process_manager.CLIENT_PID_FILE, 'start_client')
            server_alive = bool(server_pid)
            client_alive = bool(client_pid)
        except Exception:
            return self._status_cache

        if server_alive and client_alive:
            self._status_cache = '听写服务就绪'
        elif server_alive:
            self._status_cache = 'ASR 已启动，听写客户端未运行'
        elif client_alive:
            self._status_cache = '听写客户端已启动，ASR 服务端未运行'
        else:
            self._status_cache = '听写组件未启动'
        return self._status_cache

    def _refresh(self):
        if self.icon:
            self.icon.title = f"CapsWriter\n{self._status_text()}"
            self.icon.update_menu()

    def _wrap_action(self, action: Callable[[], object]):
        def handler(icon=None, item=None):
            with self._lock:
                action()
                self._refresh()
        return handler

    @staticmethod
    def _start_all():
        process_manager.launch_server()
        process_manager.launch_client()

    @staticmethod
    def _restart_all():
        process_manager.restart_server()
        process_manager.restart_client()

    def _complete_exit(self, icon=None, item=None):
        with self._lock:
            # 1. 强制释放在 OS 中被卡住的修饰键
            try:
                from core.tools.key_reset import release_all_modifier_keys
                release_all_modifier_keys()
            except Exception:
                pass

            # 2. 停止 UI 窗口及后端全量进程
            try:
                self.stop_gui()
            except Exception:
                pass

            if self.icon:
                try:
                    self.icon.stop()
                except Exception:
                    pass
            _release_tray_owner()

            try:
                self.exit_app()
            except Exception:
                pass

    def start(self) -> None:
        try:
            import pystray
            from pystray import Menu, MenuItem as item

            menu = Menu(
                item(lambda _: self._status_text(), None, enabled=False),
                item('打开控制中心', self._wrap_action(self.open_gui), default=True),
                Menu.SEPARATOR,
                item('启动缺失组件', self._wrap_action(self._start_all)),
                item('重启听写服务', self._wrap_action(self._restart_all)),
                Menu.SEPARATOR,
                item('打开日志文件夹', self._wrap_action(self._open_logs_dir)),
                item('完全退出 CapsWriter', self._complete_exit),
            )

            self.icon = pystray.Icon(
                'capswriter_control_center',
                self._create_image(),
                title=f"CapsWriter\n{self._status_text()}",
                menu=menu,
            )
            self.icon.run()
        finally:
            _release_tray_owner()


_TRAY_INSTANCE: ControlTray | None = None
_TRAY_THREAD: threading.Thread | None = None
_TRAY_LOCK = threading.Lock()


def start_tray_in_background(
    open_gui: Callable[[], None] | None = None,
    stop_gui: Callable[[], None] | None = None,
    exit_app: Callable[[], None] | None = None,
):
    """全局安全拉起控制中心总托盘 (支持单例防重)。"""
    global _TRAY_INSTANCE, _TRAY_THREAD
    with _TRAY_LOCK:
        if _TRAY_THREAD and _TRAY_THREAD.is_alive():
            return
        if not _claim_tray_owner():
            return

        def dummy_fn():
            pass

        _TRAY_INSTANCE = ControlTray(
            open_gui=open_gui or dummy_fn,
            stop_gui=stop_gui or dummy_fn,
            exit_app=exit_app or dummy_fn,
        )
        _TRAY_THREAD = threading.Thread(target=_TRAY_INSTANCE.start, daemon=True)
        _TRAY_THREAD.start()
