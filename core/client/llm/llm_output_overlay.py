"""
LLM overlay preview output mode.

Streams polished text into the modern CapsWriter dictation overlay instead of
opening the legacy Toast window or writing into the active text field.
"""

from config_client import ClientConfig as Config
from core.client.output.text_output import TextOutput
from core.tools.asyncio_to_thread import to_thread


def show_overlay_preview(text: str, *, final: bool = True, preview_config: dict | None = None, request_id=None) -> bool:
    if not text:
        return False
    from core.product.focus import current_input_turn, overlay_turn, original_window
    if not current_input_turn():
        return True  # Preserve newer turn; old result remains in input history.
    if request_id is None:
        request_id = overlay_turn()
    try:
        from core.ui.modern_overlay.pill_overlay import get_pill_overlay, is_pill_enabled
        if not is_pill_enabled():
            return False
        return bool(get_pill_overlay().show_preview(text, final=final, preview_config=preview_config, request_id=request_id, target_identity=original_window()))
    except Exception:
        return False


def retain_preview_failure(text: str) -> None:
    """A preview failure must never turn confirmation into direct keyboard input."""
    from . import logger
    try:
        import pyclip
        pyclip.copy(text)
        logger.warning('确认浮层不可用，文字已保留到剪贴板和输入历史；请手动粘贴。')
    except Exception:
        logger.warning('确认浮层与剪贴板不可用，请从输入历史取回本次文字。')


async def handle_overlay_preview_mode(handler, text: str, role_config=None, matched_hotwords=None, content=None) -> tuple:
    """Show the LLM result in the modern overlay preview."""
    from .llm_error_handler import handle_llm_error
    from core.ui.modern_overlay.pill_overlay import get_pill_overlay
    from core.product.focus import overlay_turn
    request_id = overlay_turn()
    if request_id is None:
        request_id = get_pill_overlay()._turn_id

    if not role_config or content is None:
        role_config, content = handler.detect_role(text)

    if not role_config:
        result_text = TextOutput.strip_punc(text)
        if not show_overlay_preview(result_text, request_id=request_id):
            retain_preview_failure(result_text)
        return (result_text, 0, 0.0)

    handler.monitor.reset()
    chunks = []
    preview_config = _role_preview_config(role_config)

    def stream_overlay_chunk(chunk: str):
        if not chunk:
            return
        chunks.append(chunk)
        show_overlay_preview(''.join(chunks), final=False, preview_config=preview_config, request_id=request_id)

    try:
        polished_text, token_count, gen_time = await to_thread(
            handler.process, role_config, content, matched_hotwords, stream_overlay_chunk
        )
    except Exception as e:
        result_text, _, fallback_reason = handle_llm_error(e, content, role_config.name if role_config else "LLM")
        result_text = TextOutput.strip_punc(result_text)
        if not show_overlay_preview(result_text, preview_config=preview_config, request_id=request_id):
            retain_preview_failure(result_text)
        return (result_text, 0, 0.0, "fallback", fallback_reason)

    final_text = TextOutput.strip_punc(polished_text or ''.join(chunks) or content)
    if not show_overlay_preview(final_text, preview_config=preview_config, request_id=request_id):
        retain_preview_failure(final_text)
    return (final_text, token_count, gen_time, "success", None)


def _role_preview_config(role_config) -> dict:
    def role_value(name, default):
        value = getattr(role_config, name, None)
        return default if value in (None, '', 0) else value

    return {
        'close_mode': role_value(
            'preview_close_mode',
            getattr(Config, 'llm_role_preview_close_mode', 'auto'),
        ),
        'base_seconds': role_value(
            'preview_base_seconds',
            getattr(Config, 'llm_role_preview_base_seconds', 2),
        ),
        'max_seconds': role_value(
            'preview_max_seconds',
            getattr(Config, 'llm_role_preview_max_seconds', 10),
        ),
    }
