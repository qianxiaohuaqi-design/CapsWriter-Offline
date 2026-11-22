"""Original-window guard for keyboard output, propagated through async tasks.

An open port, the current window at ASR completion, or an HWND alone is not
sufficient identity. Never capture a replacement target when validation fails.
"""
from __future__ import annotations

import contextvars
import os
import sys
import threading
import uuid
from contextlib import contextmanager

_current_target = contextvars.ContextVar('capswriter_input_target', default=None)
_epoch_lock = threading.Lock()
_latest_epoch = None


def _capture_window() -> dict | None:
    if sys.platform != 'win32':
        return None
    try:
        import win32gui
        import win32process
        import psutil
        hwnd = win32gui.GetForegroundWindow()
        pid = win32process.GetWindowThreadProcessId(hwnd)[1]
        if not hwnd or pid == os.getpid():
            return None
        return {'hwnd': hwnd, 'pid': pid, 'started': psutil.Process(pid).create_time()}
    except Exception:
        return None


def capture_input_target() -> dict:
    global _latest_epoch
    with _epoch_lock:
        _latest_epoch = uuid.uuid4().hex
        target = {'epoch': _latest_epoch, 'window': _capture_window(), 'blocked': False}
    return target


@contextmanager
def output_target(target: dict | None):
    token = _current_target.set(target)
    try:
        yield
    finally:
        _current_target.reset(token)


def target_allows_output() -> bool:
    target = _current_target.get()
    allowed = bool(target and not target.get('blocked') and target.get('epoch') == _latest_epoch
                   and target.get('window') is not None and target['window'] == _capture_window())
    if not allowed and target is not None:
        target['blocked'] = True  # Regaining focus does not silently resume a half-written result.
    return allowed


def output_was_blocked() -> bool:
    target = _current_target.get()
    return target is None or bool(target.get('blocked'))


def keep_for_manual_paste(text: str) -> None:
    if not text:
        return
    try:
        import pyclip
        pyclip.copy(text)
    except Exception:
        pass
    from core.logger import get_logger
    get_logger('client').warning('输入目标已改变或不可验证；停止自动写入，完整结果保留到输入历史和可用剪贴板。')


def guarded_write(text: str, write) -> bool:
    for start in range(0, len(text), 24):
        if not target_allows_output():
            return False
        write(text[start:start + 24])
    return True


def current_input_turn() -> bool:
    target = _current_target.get()
    return target is None or target.get('epoch') == _latest_epoch


def original_window() -> dict | None:
    target = _current_target.get()
    return target.get('window') if target else None


def overlay_turn() -> int | None:
    target = _current_target.get()
    return target.get('overlay_turn') if target else None
