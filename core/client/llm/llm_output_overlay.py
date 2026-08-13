"""
LLM overlay preview output mode.

Streams polished text into the modern CapsWriter dictation overlay instead of
opening the legacy Toast window or writing into the active text field.
"""

from config_client import ClientConfig as Config
from core.client.output.text_output import TextOutput
from core.client.llm.llm_output_typing import output_text
from core.tools.asyncio_to_thread import to_thread


def show_overlay_preview(text: str, *, final: bool = True) -> bool:
    if not text:
        return False
    try:
        from core.ui.modern_overlay.pill_overlay import get_pill_overlay, is_pill_enabled
        if not is_pill_enabled():
            return False
        get_pill_overlay().show_preview(text, final=final)
        return True
    except Exception:
        return False


async def handle_overlay_preview_mode(handler, text: str, role_config=None, matched_hotwords=None, content=None) -> tuple:
    """Show the LLM result in the modern overlay preview."""
    from .llm_error_handler import handle_llm_error

    if not role_config or content is None:
        role_config, content = handler.detect_role(text)

    if not role_config:
        result_text = TextOutput.strip_punc(text)
        if not show_overlay_preview(result_text):
            await output_text(result_text, Config.paste)
        return (result_text, 0, 0.0)

    handler.monitor.reset()
    chunks = []

    def stream_overlay_chunk(chunk: str):
        if not chunk:
            return
        chunks.append(chunk)
        show_overlay_preview(''.join(chunks), final=False)

    try:
        polished_text, token_count, gen_time = await to_thread(
            handler.process, role_config, content, matched_hotwords, stream_overlay_chunk
        )
    except Exception as e:
        result_text, _ = handle_llm_error(e, content, role_config.name if role_config else "LLM")
        result_text = TextOutput.strip_punc(result_text)
        if not show_overlay_preview(result_text):
            await output_text(result_text, Config.paste)
        return (result_text, 0, 0.0)

    final_text = TextOutput.strip_punc(polished_text or ''.join(chunks) or content)
    if not show_overlay_preview(final_text):
        await output_text(final_text, Config.paste)
    return (final_text, token_count, gen_time)
