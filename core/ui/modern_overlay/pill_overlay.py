# coding: utf-8
"""
CapsWriter 现代半透明灵动胶囊指示器 (Modern Floating Pill Overlay)

提供媲美 Apple Intelligence / Wispr Flow 风格的半透明磨砂胶囊浮窗：
- 按住快捷键说话时：在屏幕中上方优雅浮现，伴随流光波形/发光绿点提示“正在听写...”；
- 松开按键时：柔和渐隐消失，绝不抢抓窗口焦点，绝不遮挡文字输入框。
"""

import os
import sys
import time
import math
import ast
import threading
import tkinter as tk
from pathlib import Path

# 配置判断
BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
CONFIG_PILL_PATH = BASE_DIR / 'config_pill.py'
CONFIG_CLIENT_PATH = BASE_DIR / 'config_client.py'
TRANSPARENT_COLOR = '#ff00ff'
ORANGE = '#f97316'
AMBER = '#f59e0b'
LIGHT_AMBER = '#fff7ed'

_audio_level = 0.0
_audio_level_lock = threading.Lock()


def set_audio_level(level):
    """接收麦克风 RMS 音量，供浮层动画平滑追踪。"""
    global _audio_level
    try:
        level = max(0.0, min(1.0, float(level)))
    except Exception:
        level = 0.0
    with _audio_level_lock:
        _audio_level = level


def get_audio_level():
    with _audio_level_lock:
        return _audio_level


def is_pill_enabled():
    if not CONFIG_PILL_PATH.exists():
        return True
    try:
        content = CONFIG_PILL_PATH.read_text(encoding='utf-8')
        return 'enabled = True' in content or 'enabled=True' in content
    except Exception:
        return True


def get_pill_mode():
    return 'wave'


def get_client_config(var_name, default):
    try:
        tree = ast.parse(CONFIG_CLIENT_PATH.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if (
                isinstance(node, (ast.Assign, ast.AnnAssign))
                and any(
                    isinstance(target, ast.Name) and target.id == var_name
                    for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
                )
            ):
                return ast.literal_eval(node.value)
    except Exception:
        pass
    return default


class FloatingPillWindow:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self.root = None
        self.canvas = None
        self.preview_frame = None
        self.preview_text = None
        self.preview_status = None
        self.is_visible = False
        self.anim_thread = None
        self.stop_anim = False
        self.phase = 0.0
        self.text = ''
        self.state = 'recording'
        self._target_hwnd = None
        self._text_animation_id = None
        self._preview_close_after_id = None
        self._processing_hide_after_id = None
        self._final_hide_after_id = None
        self._preview_remaining_seconds = None
        self._preview_user_editing = False
        self._preview_keyboard_listener = None
        self._preview_pressed_keys = set()
        self.width = 300
        self.height = 64
        self._last_geometry = None
        self._force_caption = False
        self.recording_started_at = 0.0
        self.level = 0.0
        self._setup_thread()

    def _setup_thread(self):
        """在独立 UI 线程中启动 Tkinter 循环"""
        self.ui_ready = threading.Event()
        self.thread = threading.Thread(target=self._run_ui, daemon=True)
        self.thread.start()
        self.ui_ready.wait(timeout=2.0)

    def _run_ui(self):
        try:
            self.root = tk.Tk()
            self.root.title('CapsWriter Dictation Overlay')
            self.root.withdraw()
            self.root.overrideredirect(True)
            self.root.attributes('-topmost', True)
            self.root.attributes('-alpha', 0.0)
            if sys.platform == 'win32':
                self.root.attributes('-toolwindow', True)
                self.root.attributes('-transparentcolor', TRANSPARENT_COLOR)

            # Wispr Flow 风格：底部居中的轻量悬浮录音条
            screen_width = self.root.winfo_screenwidth()
            screen_height = self.root.winfo_screenheight()
            pill_width = self.width
            pill_height = self.height
            x = (screen_width - pill_width) // 2
            y = max(40, screen_height - pill_height - 76)

            self.root.geometry(f"{pill_width}x{pill_height}+{x}+{y}")
            self.root.configure(bg=TRANSPARENT_COLOR)

            # 画布
            self.canvas = tk.Canvas(
                self.root,
                width=pill_width,
                height=pill_height,
                bg=TRANSPARENT_COLOR,
                highlightthickness=0
            )
            self.canvas.pack(fill='both', expand=True)

            self.ui_ready.set()
            self.root.mainloop()
        except Exception as e:
            print(f"[PillOverlay] UI error: {e}")
            self.ui_ready.set()

    def show_recording(self, text=""):
        """显示底部实时字幕胶囊"""
        if not is_pill_enabled() or not self.root:
            return
        self.root.after(0, lambda: self._do_show(text))

    def _do_show(self, text):
        self._cancel_pending_hide_timers()
        self._cancel_preview_autoclose()
        self.stop_anim = False
        self.is_visible = True
        self.state = 'recording'
        self.text = text or '正在听写...'
        self._force_caption = False
        self._show_canvas()
        self.recording_started_at = time.time()
        self._apply_geometry()
        self.root.deiconify()

        # 淡入动画
        for alpha in [0.18, 0.45, 0.76, 0.92]:
            self.root.attributes('-alpha', alpha)
            self.root.update_idletasks()
            time.sleep(0.015)

        # 启动绘图与发光波形动画
        if not self.anim_thread or not self.anim_thread.is_alive():
            self.anim_thread = threading.Thread(target=self._animate_loop, daemon=True)
            self.anim_thread.start()

    def show_processing(self, text="正在整理文字..."):
        """松开快捷键后等待最终识别结果。"""
        if not is_pill_enabled() or not self.root:
            return
        self.root.after(0, lambda: self._do_processing(text))

    def _do_processing(self, text):
        if not self.is_visible:
            self._do_show(text)
        self.state = 'processing'
        self.text = text
        self._force_caption = False
        self._show_canvas()
        self._apply_geometry()
        self._draw_pill()
        self._cancel_pending_hide_timers()
        self._processing_hide_after_id = self.root.after(8000, self._hide_if_processing)

    def update_text(self, text, final=False, force_caption=False):
        """更新字幕内容；final=True 时短暂停留后自动隐藏。"""
        if not is_pill_enabled() or not self.root or not text:
            return
        self.root.after(0, lambda: self._do_update_text(text, final, force_caption))

    def _do_update_text(self, text, final, force_caption=False):
        self._cancel_pending_hide_timers()
        if not self.is_visible:
            self._do_show(text)
        self.state = 'final' if final else 'recording'
        self._force_caption = bool(force_caption)
        self._show_canvas()
        self._apply_geometry()
        if not final and self._active_mode() == 'caption' and self.text and text.startswith(self.text):
            self._animate_text(self.text, text)
        else:
            self._cancel_text_animation()
            self.text = text
            self._draw_pill()
        if final:
            self._final_hide_after_id = self.root.after(1800, self.hide)

    def show_preview(self, text, final=True):
        """Show final text in a persistent editable preview overlay."""
        if not final:
            self.update_text(text, final=False, force_caption=True)
            return
        if not is_pill_enabled() or not self.root or not text:
            return
        target_hwnd = self._get_foreground_hwnd()
        self.root.after(0, lambda: self._do_show_preview(text, target_hwnd))

    def _do_show_preview(self, text, target_hwnd=None):
        self._cancel_pending_hide_timers()
        self._cancel_text_animation()
        self._cancel_preview_autoclose()
        self.stop_anim = True
        self.is_visible = True
        self.state = 'preview'
        self.text = text
        self._force_caption = True
        self._target_hwnd = target_hwnd
        self._preview_user_editing = False
        self._set_clipboard(text)
        self._apply_preview_geometry()
        self._build_preview(text)
        self.root.deiconify()
        self.root.lift()
        self._start_preview_keyboard_listener()
        self.root.after(80, self._focus_target_window)
        self.root.attributes('-alpha', 0.97)
        self._schedule_preview_autoclose(text)

    def _hide_if_processing(self):
        self._processing_hide_after_id = None
        if self.state == 'processing':
            self.hide()

    def _animate_loop(self):
        while self.is_visible and not self.stop_anim:
            self.phase += 0.15
            self.root.after(0, self._draw_pill)
            time.sleep(0.04)

    def _draw_pill(self):
        if not self.canvas:
            return
        if self.state == 'preview':
            return
        self.canvas.delete('all')
        self._apply_geometry()

        w, h = self.width, self.height
        mode = self._active_mode()
        r = 24 if mode == 'wave' else 18

        target_level = get_audio_level() if self.state == 'recording' else 0.18
        self.level = self.level * 0.72 + target_level * 0.28

        # 1. 单层白色轻盈录音胶囊，橙色为主视觉
        self._rounded_rect(8, 8, w - 8, h - 8, r, fill='#ffffff', outline='#fed7aa', width=1)

        # 2. 状态只影响波形颜色，不额外显示左侧圆点/勾选
        active = self.state == 'recording'
        final = self.state == 'final'
        accent = ORANGE if active else AMBER
        if final:
            accent = '#22c55e'
        cy = h // 2 if mode == 'wave' else h - 22

        if mode == 'caption':
            self._draw_caption_text(w, accent)

        # 3. 中央橙色声波：音量越大，柱状波峰越高；安静时自动变平
        wave_left = 34 if mode == 'wave' else 150
        wave_right = w - 34 if mode == 'wave' else w - 150
        wave_width = wave_right - wave_left
        bar_count = 22 if mode == 'wave' else 18
        gap = wave_width / (bar_count - 1)
        center_y = cy
        for i in range(bar_count):
            x = wave_left + i * gap
            distance = abs(i - (bar_count - 1) / 2) / ((bar_count - 1) / 2)
            envelope = 1.0 - distance * 0.58
            idle = 5 + 2.4 * math.sin(self.phase + i * 0.9)
            motion = math.sin(self.phase * 1.45 + i * 0.62)
            bar_h = idle + self.level * (30 * envelope + 7 * motion)
            if not active:
                bar_h = 7 + 8 * abs(math.sin(self.phase + i * 0.5)) * (0.35 if final else 0.75)
            bar_h = max(4, min(36, bar_h))
            color = accent if i % 3 else AMBER
            self.canvas.create_line(
                x, center_y - bar_h / 2,
                x, center_y + bar_h / 2,
                fill=color,
                width=4,
                capstyle=tk.ROUND
            )

    def _cancel_text_animation(self):
        if self._text_animation_id and self.root:
            try:
                self.root.after_cancel(self._text_animation_id)
            except Exception:
                pass
        self._text_animation_id = None

    def _animate_text(self, start_text, target_text):
        self._cancel_text_animation()
        if start_text == target_text:
            self.text = target_text
            self._draw_pill()
            return
        next_len = min(len(target_text), len(start_text) + 1)
        self.text = target_text[:next_len]
        self._draw_pill()
        if next_len < len(target_text):
            self._text_animation_id = self.root.after(28, lambda: self._animate_text(self.text, target_text))

    def _active_mode(self):
        return 'caption' if self._force_caption else get_pill_mode()

    def _target_size(self):
        return (620, 112) if self._active_mode() == 'caption' else (300, 64)

    def _apply_geometry(self):
        if not self.root or not self.canvas:
            return
        width, height = self._target_size()
        if (self.width, self.height) == (width, height) and self._last_geometry:
            return
        self.width = width
        self.height = height
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        x = (screen_width - width) // 2
        y = max(40, screen_height - height - 76)
        geometry = f"{width}x{height}+{x}+{y}"
        if geometry != self._last_geometry:
            self.root.geometry(geometry)
            self.canvas.configure(width=width, height=height)
            self._last_geometry = geometry

    def _apply_preview_geometry(self):
        if not self.root:
            return
        width, height = 680, 238
        self.width = width
        self.height = height
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        x = (screen_width - width) // 2
        y = max(40, screen_height - height - 92)
        geometry = f"{width}x{height}+{x}+{y}"
        if geometry != self._last_geometry:
            self.root.geometry(geometry)
            self._last_geometry = geometry

    def _show_canvas(self):
        if self.preview_frame:
            self.preview_frame.pack_forget()
        if self.canvas:
            self.canvas.pack(fill='both', expand=True)

    def _build_preview(self, text):
        if self.canvas:
            self.canvas.pack_forget()
        if self.preview_frame:
            self.preview_frame.destroy()

        self.preview_frame = tk.Frame(self.root, bg='#ffffff', highlightbackground='#fed7aa', highlightthickness=1)
        self.preview_frame.pack(fill='both', expand=True, padx=8, pady=8)

        header = tk.Frame(self.preview_frame, bg='#ffffff')
        header.pack(fill='x', padx=18, pady=(14, 6))
        tk.Label(header, text='确认输出', bg='#ffffff', fg=ORANGE, font=('Microsoft YaHei UI', 10, 'bold')).pack(side='left')
        self.preview_status = tk.Label(
            header,
            text='已复制到剪贴板，可直接 Ctrl+V',
            bg='#ffffff',
            fg='#64748b',
            font=('Microsoft YaHei UI', 9),
        )
        self.preview_status.pack(side='right')

        self.preview_text = tk.Text(
            self.preview_frame,
            height=4,
            wrap='word',
            bd=0,
            relief='flat',
            bg='#fff7ed',
            fg='#111827',
            insertbackground=ORANGE,
            selectbackground='#fed7aa',
            font=('Microsoft YaHei UI', 12),
            padx=12,
            pady=10,
        )
        self.preview_text.insert('1.0', text)
        self.preview_text.bind('<FocusIn>', self._pause_preview_autoclose)
        self.preview_text.bind('<KeyPress>', self._pause_preview_autoclose)
        self.preview_text.bind('<Button-1>', self._pause_preview_autoclose)
        self.preview_text.bind('<FocusOut>', self._resume_preview_autoclose)
        self.preview_text.pack(fill='both', expand=True, padx=18, pady=(0, 10))

        footer = tk.Frame(self.preview_frame, bg='#ffffff')
        footer.pack(fill='x', padx=18, pady=(0, 14))
        self._make_button(footer, '复制', self._copy_preview).pack(side='left', padx=(0, 8))
        self._make_button(footer, '写入光标位置', self._confirm_preview, primary=True).pack(side='left')
        self._make_button(footer, '关闭', self.hide).pack(side='right')
        self.root.bind_all('<Escape>', self._handle_preview_escape)

    def _make_button(self, parent, text, command, primary=False):
        return tk.Button(
            parent,
            text=text,
            command=command,
            bd=0,
            relief='flat',
            cursor='hand2',
            padx=16,
            pady=8,
            bg=ORANGE if primary else '#f8fafc',
            fg='#ffffff' if primary else '#334155',
            activebackground='#ea580c' if primary else '#e2e8f0',
            activeforeground='#ffffff' if primary else '#0f172a',
            font=('Microsoft YaHei UI', 10, 'bold' if primary else 'normal'),
        )

    def _preview_value(self):
        if not self.preview_text:
            return self.text
        return self.preview_text.get('1.0', 'end-1c').strip()

    def _copy_preview(self):
        text = self._preview_value()
        if text and self._set_clipboard(text) and self.preview_status:
            self.preview_status.config(text='已复制到剪贴板')

    def _sync_preview_clipboard(self, event=None):
        text = self._preview_value()
        if text:
            self.text = text
            self._set_clipboard(text)

    def _schedule_preview_autoclose(self, text):
        self._cancel_preview_autoclose()
        if get_client_config('preview_close_mode', 'auto') != 'auto':
            self._preview_remaining_seconds = None
            if self.preview_status:
                self.preview_status.config(text='已复制到剪贴板，按 Esc 关闭')
            return
        base = max(2, int(get_client_config('preview_base_seconds', 8) or 8))
        max_seconds = max(base, int(get_client_config('preview_max_seconds', 60) or 60))
        self._preview_remaining_seconds = self._preview_duration_seconds(len(text), base, max_seconds)
        self._tick_preview_countdown()

    @staticmethod
    def _preview_duration_seconds(text_length, base, max_seconds):
        if text_length <= 30:
            return base
        if text_length <= 100:
            return int(round(base + (max_seconds - base) * 0.35))
        if text_length <= 250:
            return int(round(base + (max_seconds - base) * 0.65))
        return max_seconds

    def _cancel_preview_autoclose(self):
        if self._preview_close_after_id and self.root:
            try:
                self.root.after_cancel(self._preview_close_after_id)
            except Exception:
                pass
        self._preview_close_after_id = None

    def _tick_preview_countdown(self):
        if self.state != 'preview' or self._preview_user_editing:
            return
        if self._preview_remaining_seconds is None:
            return
        if self._preview_remaining_seconds <= 0:
            self._auto_hide_preview()
            return
        if self.preview_status:
            self.preview_status.config(text=f'已复制到剪贴板，{self._preview_remaining_seconds} 秒后自动关闭')
        self._preview_close_after_id = self.root.after(1000, self._advance_preview_countdown)

    def _advance_preview_countdown(self):
        self._preview_close_after_id = None
        if self._preview_remaining_seconds is not None:
            self._preview_remaining_seconds -= 1
        self._tick_preview_countdown()

    def _cancel_pending_hide_timers(self):
        for attr in ('_processing_hide_after_id', '_final_hide_after_id'):
            after_id = getattr(self, attr, None)
            if after_id and self.root:
                try:
                    self.root.after_cancel(after_id)
                except Exception:
                    pass
            setattr(self, attr, None)

    def _pause_preview_autoclose(self, event=None):
        if event is not None and getattr(event, 'keysym', '') == 'Escape':
            return
        self._preview_user_editing = True
        self._cancel_preview_autoclose()
        if self.preview_status:
            if get_client_config('preview_close_mode', 'auto') == 'auto':
                self.preview_status.config(text='正在编辑，倒计时已暂停')
            else:
                self.preview_status.config(text='正在编辑')

    def _resume_preview_autoclose(self, event=None):
        if self.state != 'preview':
            return
        self._sync_preview_clipboard()
        self._preview_user_editing = False
        if get_client_config('preview_close_mode', 'auto') == 'auto':
            self._tick_preview_countdown()
        elif self.preview_status:
            self.preview_status.config(text='已复制到剪贴板，按 Esc 关闭')

    def _auto_hide_preview(self):
        self._preview_close_after_id = None
        if self.state == 'preview' and not self._preview_user_editing:
            self.hide()

    def _handle_preview_escape(self, event=None):
        if self.state == 'preview':
            self.hide()
            return 'break'
        return None

    def _preview_has_focus(self):
        if not self.root:
            return False
        try:
            widget = self.root.focus_get()
        except Exception:
            return False
        return widget is not None

    def _start_preview_keyboard_listener(self):
        if self._preview_keyboard_listener:
            return
        try:
            from pynput import keyboard as pynput_keyboard
        except Exception:
            return

        def normalize(key):
            if key in (pynput_keyboard.Key.ctrl, pynput_keyboard.Key.ctrl_l, pynput_keyboard.Key.ctrl_r):
                return 'ctrl'
            if key == pynput_keyboard.Key.esc:
                return 'esc'
            char = getattr(key, 'char', None)
            return char.lower() if isinstance(char, str) else None

        def on_press(key):
            normalized = normalize(key)
            if not normalized:
                return
            self._preview_pressed_keys.add(normalized)
            if self.state != 'preview':
                return
            if normalized == 'esc':
                if self.root:
                    self.root.after(0, self.hide)
                return
            if normalized == 'v' and 'ctrl' in self._preview_pressed_keys:
                if self.root:
                    self.root.after(180, self._hide_after_external_paste)

        def on_release(key):
            normalized = normalize(key)
            if normalized:
                self._preview_pressed_keys.discard(normalized)

        try:
            self._preview_keyboard_listener = pynput_keyboard.Listener(on_press=on_press, on_release=on_release)
            self._preview_keyboard_listener.start()
        except Exception:
            self._preview_keyboard_listener = None

    def _stop_preview_keyboard_listener(self):
        listener = self._preview_keyboard_listener
        self._preview_keyboard_listener = None
        self._preview_pressed_keys.clear()
        if listener:
            try:
                listener.stop()
            except Exception:
                pass

    def _hide_after_external_paste(self):
        if self.state == 'preview' and not self._preview_has_focus():
            self.hide()

    def _confirm_preview(self):
        text = self._preview_value()
        if not text:
            self.hide()
            return
        self._set_clipboard(text)
        self._focus_target_window()
        self.root.after(140, self._paste_and_hide)

    def _paste_and_hide(self):
        try:
            from pynput import keyboard as pynput_keyboard
            controller = pynput_keyboard.Controller()
            if sys.platform == 'darwin':
                with controller.pressed(pynput_keyboard.Key.cmd):
                    controller.tap('v')
            else:
                with controller.pressed(pynput_keyboard.Key.ctrl):
                    controller.tap('v')
            try:
                from core.tools.key_reset import release_all_modifier_keys
                release_all_modifier_keys()
            except Exception:
                pass
        finally:
            self.hide()

    def _set_clipboard(self, text):
        try:
            import pyclip
            pyclip.copy(text)
            return True
        except Exception:
            return False

    def _get_foreground_hwnd(self):
        if sys.platform != 'win32':
            return None
        try:
            import win32gui
            return win32gui.GetForegroundWindow()
        except Exception:
            return None

    def _focus_target_window(self):
        if sys.platform != 'win32' or not self._target_hwnd:
            return
        try:
            import win32con
            import win32gui
            if win32gui.IsWindow(self._target_hwnd):
                win32gui.ShowWindow(self._target_hwnd, win32con.SW_RESTORE)
                win32gui.SetForegroundWindow(self._target_hwnd)
                time.sleep(0.08)
        except Exception:
            pass

    def _draw_caption_text(self, width, accent):
        text = self._format_text(self.text)
        status = '??????...' if self.state == 'processing' else ('????' if self.state == 'final' else '????')
        self.canvas.create_text(
            34, 30,
            text=status,
            anchor='w',
            fill=accent,
            font=('Microsoft YaHei UI', 10, 'bold'),
        )
        self.canvas.create_text(
            34, 62,
            text=text,
            anchor='w',
            fill='#111827',
            font=('Microsoft YaHei UI', 15),
            width=width - 68,
        )


    def _rounded_rect(self, x1, y1, x2, y2, radius, **kwargs):
        points = [
            x1 + radius, y1, x2 - radius, y1,
            x2, y1, x2, y1 + radius,
            x2, y2 - radius, x2, y2,
            x2 - radius, y2, x1 + radius, y2,
            x1, y2, x1, y2 - radius,
            x1, y1 + radius, x1, y1
        ]
        return self.canvas.create_polygon(points, smooth=True, **kwargs)

    def _format_text(self, text):
        text = (text or '').strip()
        if not text:
            return '????...'
        text = ' '.join(text.split())
        limit = 72 if self._active_mode() == 'caption' else 46
        return text if len(text) <= limit else '...' + text[-limit:]

    def hide(self):
        """淡隐关闭"""
        if not self.root or not self.is_visible:
            return
        self.root.after(0, self._do_hide)

    def _do_hide(self):
        self.stop_anim = True
        self.is_visible = False
        self._target_hwnd = None
        self._preview_remaining_seconds = None
        self._preview_user_editing = False
        self._stop_preview_keyboard_listener()
        self._cancel_text_animation()
        self._cancel_pending_hide_timers()
        self._cancel_preview_autoclose()
        try:
            if self.preview_frame:
                self.preview_frame.pack_forget()
            if self.canvas:
                self.canvas.pack(fill='both', expand=True)
            for alpha in [0.7, 0.4, 0.15, 0.0]:
                self.root.attributes('-alpha', alpha)
                self.root.update_idletasks()
                time.sleep(0.015)
            self.root.withdraw()
        except Exception:
            pass

    def close(self, timeout=1.0):
        """Immediately hide the floating window and stop its animation."""
        root = self.root
        if not root:
            return

        done = threading.Event()

        def do_close():
            try:
                self.stop_anim = True
                self.is_visible = False
                self._force_caption = False
                self._target_hwnd = None
                self._cancel_text_animation()
                self._cancel_preview_autoclose()
                if self.preview_frame:
                    self.preview_frame.pack_forget()
                try:
                    root.withdraw()
                except Exception:
                    pass
            finally:
                done.set()

        try:
            self._stop_preview_keyboard_listener()
            root.after(0, do_close)
            done.wait(timeout=timeout)
        except Exception:
            done.set()


# 全局单例接口
_pill_instance = None

def get_pill_overlay():
    global _pill_instance
    if _pill_instance is None:
        _pill_instance = FloatingPillWindow()
    return _pill_instance


def close_pill_overlay():
    """Hide the global dictation overlay without creating it."""
    if _pill_instance is None:
        return
    _pill_instance.close()
