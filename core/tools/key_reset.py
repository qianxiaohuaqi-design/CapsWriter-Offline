# coding: utf-8
"""
Windows 修饰键强行复位工具

当底层 LowLevelKeyboardProc 钩子卡死或异常中断时，向 OS 强制补发 KeyUp 消息，
消除 Ctrl/Alt/Shift 键粘连和假死现象。
"""

import sys


def release_all_modifier_keys() -> None:
    """强制向 Windows 系统补发所有修饰键的 KeyUp 消息。"""
    if sys.platform != 'win32':
        return

    try:
        import ctypes

        # 核心修饰键虚拟键码 (Virtual Key Codes)
        # VK_CONTROL (0x11), VK_LCONTROL (0xA2), VK_RCONTROL (0xA3)
        # VK_MENU/ALT (0x12), VK_LMENU (0xA4), VK_RMENU (0xA5)
        # VK_SHIFT (0x10), VK_LSHIFT (0xA0), VK_RSHIFT (0xA1)
        # VK_LWIN (0x5B), VK_RWIN (0x5C)
        # VK_CAPITAL/CapsLock (0x14)
        vks = (0x11, 0xA2, 0xA3, 0x12, 0xA4, 0xA5, 0x10, 0xA0, 0xA1, 0x5B, 0x5C, 0x14)
        KEYEVENTF_KEYUP = 0x0002
        user32 = ctypes.windll.user32

        for vk in vks:
            user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
    except Exception:
        pass
