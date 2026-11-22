"""Native-only first-close notice, attached inside pywebview's window process."""
from __future__ import annotations
import logging
from . import bridge
from .paths import data_root


def attach_close_notice(window):
    if getattr(window, '_capswriter_close_notice_attached', False):
        return
    window._capswriter_close_notice_attached = True

    def closing():
        if (data_root() / 'runtime' / 'exit-ready.json').exists():
            return True
        try:
            state = bridge.request(timeout=2)
            if state.get('shutting_down') or state.get('setup', {}).get('background_notice_seen'):
                return True
            accepted = window.create_confirmation_dialog(
                '关闭控制中心窗口？',
                '关闭窗口后，模型下载和已提交的转写任务会继续在后台运行。\n\n'
                '可从托盘菜单或再次打开 CapsWriter 快捷方式返回。\n'
                '要停止整个程序，请在服务与诊断或托盘中选择“完全退出”。\n\n'
                '确定关闭窗口吗？此提示确认后不再显示。')
            if not accepted:
                return False
            bridge.request('setup', timeout=2, values={'background_notice_seen': True})
            return True
        except Exception:
            logging.getLogger(__name__).warning('Background connection unavailable while closing the window')
            # Keep the visible recovery entry until a user explicitly accepts
            # this different outcome; never claim jobs will survive an unknown owner.
            return bool(window.create_confirmation_dialog(
                '后台状态暂时无法确认',
                '当前无法确认后台任务状态。建议取消关闭，在“服务与诊断”重新连接。\n\n'
                '仍然关闭此窗口吗？'))

    window.events.closing += closing


def install_close_notice():
    """Picklable callback passed through app.native.start_args['func']."""
    import webview
    for window in webview.windows:
        attach_close_notice(window)
