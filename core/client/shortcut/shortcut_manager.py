# coding: utf-8
"""
快捷键管理器（重构版）

统一管理多个快捷键，处理键盘和鼠标事件，支持：
1. 多快捷键并发处理
2. 防止不同按键互相干扰
3. restore 功能的防自捕获逻辑
4. hold_mode 和 click_mode 支持
"""
from __future__ import annotations
import time
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Dict, List, Optional, Set

from pynput import keyboard, mouse

from . import logger
from core.client.shortcut.key_mapper import *
from core.client.shortcut.key_mapper import KeyMapper
from core.client.shortcut.emulator import ShortcutEmulator
from core.client.shortcut.event_handler import ShortcutEventHandler
from core.client.shortcut.task import ShortcutTask

if TYPE_CHECKING:
    from core.client.shortcut.shortcut_config import Shortcut
    from core.client.state import ClientState
    from core.client.app import CapsWriterClient



class ShortcutManager:
    """
    快捷键管理器

    统一管理多个快捷键，使用 pynput 监听键盘和鼠标事件。
    所有事件处理都在 win32_event_filter 中完成，确保高性能和低延迟。
    """

    def __init__(self, app: CapsWriterClient, shortcuts: List[Shortcut]):
        """
        初始化快捷键管理器

        Args:
            app: 客户端 App 实例
            shortcuts: 快捷键配置列表
        """
        self.app = app
        self.shortcuts = shortcuts

        # 监听器
        self.keyboard_listener: Optional[keyboard.Listener] = None
        self.mouse_listener: Optional[mouse.Listener] = None

        # 快捷键任务映射（key -> ShortcutTask）
        self.tasks: Dict[str, ShortcutTask] = {}
        self.combo_tasks: Dict[str, ShortcutTask] = {}
        self._pressed_keys: Set[str] = set()

        # 线程池
        self._pool = ThreadPoolExecutor(max_workers=4)

        # 按键模拟器
        self._emulator = ShortcutEmulator()

        # 按键恢复状态追踪
        self._restoring_keys = set()

        # 事件处理器
        self._event_handler = ShortcutEventHandler(self.tasks, self._pool, self._emulator)

        # 初始化快捷键任务
        self._init_tasks()

    @property
    def state(self) -> ClientState:
        """快捷访问状态单例"""
        return self.app.state

    def _init_tasks(self) -> None:
        """初始化所有快捷键任务"""
        from config_client import ClientConfig as Config

        for shortcut in self.shortcuts:
            if not shortcut.enabled:
                continue

            task = ShortcutTask(self.app, shortcut)
            task._manager_ref = lambda: self  # 弱引用，用于回调
            task.pool = self._pool
            task.threshold = shortcut.get_threshold(Config.threshold)
            self.tasks[shortcut.key] = task
            if shortcut.type == 'keyboard' and self._is_combo_key(shortcut.key):
                self.combo_tasks[shortcut.key] = task

    @staticmethod
    def _is_combo_key(key_name: str) -> bool:
        return '+' in key_name

    @staticmethod
    def _split_combo_key(key_name: str) -> Set[str]:
        return {part.strip() for part in key_name.split('+') if part.strip()}

    @staticmethod
    def _generic_aliases(key_name: str) -> Set[str]:
        aliases = {key_name}
        if key_name in {'ctrl_l', 'ctrl_r'}:
            aliases.add('ctrl')
        elif key_name in {'shift_l', 'shift_r'}:
            aliases.add('shift')
        elif key_name in {'alt_l', 'alt_r', 'alt_gr'}:
            aliases.add('alt')
        elif key_name in {'cmd_l', 'cmd_r'}:
            aliases.add('cmd')
        return aliases

    def _add_pressed_key(self, key_name: str) -> None:
        self._pressed_keys.update(self._generic_aliases(key_name))

    def _discard_pressed_key(self, key_name: str) -> None:
        for alias in self._generic_aliases(key_name):
            self._pressed_keys.discard(alias)

    @classmethod
    def _is_modifier_key(cls, key_name: str) -> bool:
        return bool(cls._generic_aliases(key_name) & {'ctrl', 'shift', 'alt', 'cmd'})

    def _find_combo_task(self, key_name: str, *, require_active: bool = False):
        key_aliases = self._generic_aliases(key_name)
        for combo_key, task in self.combo_tasks.items():
            parts = self._split_combo_key(combo_key)
            if not parts.intersection(key_aliases):
                continue
            if require_active and not task.is_recording:
                continue
            if parts.issubset(self._pressed_keys) or (require_active and task.is_recording):
                return combo_key, task
        return None, None

    # ========== 监听器创建 ==========

    def create_keyboard_filter(self):
        """创建键盘事件过滤器"""
        def win32_event_filter(msg, data):
            # 只处理 KEYDOWN 和 KEYUP 消息
            if msg not in KEYBOARD_MESSAGES:
                return True

            key_name = KeyMapper.vk_to_name(data.vkCode)

            # 防自捕获检查
            if self._check_emulating(key_name, msg):
                return True
            if self._check_restoring(key_name, msg):
                return True

            if msg in KEY_DOWN_MESSAGES:
                self._add_pressed_key(key_name)

            # 查找匹配的单键或组合键
            task = self.tasks.get(key_name)
            event_key_name = key_name
            # 组合键只由主键完成触发：先 Ctrl 后 L 是 Ctrl+L；先 L 后 Ctrl
            # 仍视作普通输入，避免已经送入目标窗口的 L 又启动听写。
            modifier_completing_combo = msg in KEY_DOWN_MESSAGES and self._is_modifier_key(key_name)
            if task is None and not modifier_completing_combo:
                combo_key, task = self._find_combo_task(key_name, require_active=msg in KEY_UP_MESSAGES)
                event_key_name = combo_key or key_name
            if task is None:
                if msg in KEY_UP_MESSAGES:
                    self._discard_pressed_key(key_name)
                return True

            # 处理按键事件
            if msg in KEY_DOWN_MESSAGES:
                self._event_handler.handle_keydown(event_key_name, task)
            elif msg in KEY_UP_MESSAGES:
                self._event_handler.handle_keyup(event_key_name, task)
                self._discard_pressed_key(key_name)

            # 阻塞事件（注意：修饰键的松开消息 KEY_UP 绝不阻塞，确保 OS 键盘状态机正常复位）
            if task.shortcut.suppress and self.keyboard_listener:
                if msg in KEY_DOWN_MESSAGES or data.vkCode not in (0xA2, 0xA3, 0xA0, 0xA1, 0x12, 0xA4, 0xA5, 0x11, 0x10, 0x14):
                    self.keyboard_listener.suppress_event()

            return True

        return win32_event_filter

    def create_mouse_filter(self):
        """创建鼠标事件过滤器"""
        def win32_event_filter(msg, data):
            # 只处理 XBUTTON 消息
            if msg not in MOUSE_MESSAGES:
                return True

            # 获取按键标识
            xbutton = (data.mouseData >> 16) & 0xFFFF
            button_name = 'x1' if xbutton == XBUTTON1 else 'x2'

            # 防自捕获检查
            if self._check_emulating(button_name, msg, is_mouse=True):
                return True

            # 查找匹配的快捷键
            if button_name not in self.tasks:
                return True

            task = self.tasks[button_name]

            # 处理鼠标事件
            if msg == WM_XBUTTONDOWN:
                self._event_handler.handle_keydown(button_name, task)
            elif msg == WM_XBUTTONUP:
                self._handle_mouse_keyup(button_name, task)

            # 阻塞事件
            if task.shortcut.suppress and self.mouse_listener:
                self.mouse_listener.suppress_event()

            return True

        return win32_event_filter

    def _handle_mouse_keyup(self, button_name: str, task) -> None:
        """处理鼠标按键释放事件"""
        # 单击模式
        if not task.shortcut.hold_mode:
            if task.pressed:
                task.pressed = False
                task.released = True
                task.event.set()
            return

        # 长按模式
        if not task.is_recording:
            return

        duration = time.time() - task.recording_start_time
        logger.debug(f"[{button_name}] 松开按键，持续时间: {duration:.3f}s")

        if duration < task.threshold:
            task.cancel()
            if task.shortcut.suppress:
                logger.debug(f"[{button_name}] 安排异步补发鼠标按键")
                self._pool.submit(self._emulator.emulate_mouse_click, button_name)
        else:
            task.finish()

    # ========== 按键恢复管理 ==========

    def schedule_restore(self, key: str) -> None:
        """
        安排按键恢复（延迟执行，避免在事件处理中阻塞）

        Args:
            key: 要恢复的按键

        注意：标志清除只在按键释放事件中处理（_check_restoring），
        避免在线程中提前清除导致主线程收到重复消息。
        """
        from pynput import keyboard

        self._restoring_keys.add(key)

        def do_restore():
            import time
            time.sleep(0.05)  # 延迟 50ms
            if key == 'caps_lock':
                controller = keyboard.Controller()
                controller.press(keyboard.Key.caps_lock)
                controller.release(keyboard.Key.caps_lock)

        self._pool.submit(do_restore)

    def is_restoring(self, key: str) -> bool:
        """检查是否正在恢复指定按键"""
        return key in self._restoring_keys

    def clear_restoring_flag(self, key: str) -> None:
        """清除恢复标志"""
        self._restoring_keys.discard(key)

    # ========== 防自捕获检查 ==========

    def _check_emulating(self, key_name: str, msg: int, is_mouse: bool = False) -> bool:
        """检查是否正在模拟按键"""
        if not self._emulator.is_emulating(key_name):
            return False

        # 松开时清除标志
        if is_mouse:
            if msg == WM_XBUTTONUP:
                self._emulator.clear_emulating_flag(key_name)
        else:
            if msg in (WM_KEYUP, WM_SYSKEYUP):
                self._emulator.clear_emulating_flag(key_name)

        return True  # 放行

    def _check_restoring(self, key_name: str, msg: int) -> bool:
        """检查是否正在恢复按键"""
        if not self.is_restoring(key_name):
            return False

        if msg in (WM_KEYUP, WM_SYSKEYUP):
            self.clear_restoring_flag(key_name)

        return True  # 放行

    # ========== 公共接口 ==========

    def start(self) -> None:
        """启动所有监听器"""
        has_keyboard = any(s.type == 'keyboard' for s in self.shortcuts if s.enabled)
        has_mouse = any(s.type == 'mouse' for s in self.shortcuts if s.enabled)

        if has_keyboard:
            if self.keyboard_listener and self.keyboard_listener.is_alive():
                logger.debug("键盘监听器已在运行，跳过启动")
            else:
                self.keyboard_listener = keyboard.Listener(
                    win32_event_filter=self.create_keyboard_filter()
                )
                self.keyboard_listener.start()
                logger.info("键盘监听器已启动")

        if has_mouse:
            if self.mouse_listener and self.mouse_listener.is_alive():
                logger.debug("鼠标监听器已在运行，跳过启动")
            else:
                self.mouse_listener = mouse.Listener(
                    win32_event_filter=self.create_mouse_filter()
                )
                self.mouse_listener.start()
                logger.info("鼠标监听器已启动")

        # 打印所有启用的快捷键
        for shortcut in self.shortcuts:
            if shortcut.enabled:
                mode = "长按" if shortcut.hold_mode else "单击"
                toggle = "可恢复" if shortcut.is_toggle_key() else "普通键"
                logger.info(f"  [{shortcut.key}] {mode}模式, 阻塞:{shortcut.suppress}, {toggle}")

    def stop(self) -> None:
        """停止所有监听器和清理资源"""
        # 强制解发修饰键释放，防止退出后留存 Alt/Ctrl 粘连
        try:
            from core.tools.key_reset import release_all_modifier_keys
            release_all_modifier_keys()
        except Exception:
            pass

        if self.keyboard_listener:
            try:
                self.keyboard_listener.stop()
                logger.debug("键盘监听器已停止")
            except Exception:
                pass
            finally:
                self.keyboard_listener = None
                
        if self.mouse_listener:
            try:
                self.mouse_listener.stop()
                logger.debug("鼠标监听器已停止")
            except Exception:
                pass
            finally:
                self.mouse_listener = None

        # 取消所有任务
        for task in self.tasks.values():
            if task.is_recording:
                task.cancel()
        self._pressed_keys.clear()

        # 关闭线程池
        try:
            from core.ui.modern_overlay.pill_overlay import close_pill_overlay
            close_pill_overlay()
        except Exception:
            pass

        self._pool.shutdown(wait=False)
        logger.debug("快捷键管理器线程池已关闭")
