"""
LLM Typing 输出模式

直接打字输出，根据 paste 参数或 Config.paste 选择：
- paste=True: 等流式输出完成后一次性粘贴
- paste=False: 实时流式 write，结合微缓冲 (20-50ms / 8-12 字符) 降低 UI 重绘与按键高频开销
"""
import asyncio
import time
import threading
from typing import List, Optional
import keyboard

from config_client import ClientConfig as Config
from core.tools.asyncio_to_thread import to_thread
from core.client.output.text_output import TextOutput
from core.client.clipboard import paste_text
from . import logger


class StreamMicroBuffer:
    """
    流式打字微缓冲器 (20-50ms / 8-12 字符)
    特性：
    1. 首字/首个 Chunk 零延迟直接打出，保证 TTFT 响应体验
    2. 后续字符按 8-12 字符或 20-50ms 阈值微缓冲输出，降低按键事件和 UI 重绘开销
    3. 线程安全
    """

    def __init__(self, batch_size: int = 10, flush_delay_ms: float = 30.0, trash_punc: str = "，。,."):
        self.batch_size = batch_size
        self.flush_delay_s = flush_delay_ms / 1000.0
        self.trash_punc = trash_punc
        self.buffer: List[str] = []
        self.emitted_batches: List[str] = []
        self.first_token_sent = False
        self.last_flush_time = 0.0
        self._lock = threading.Lock()

    def add_chunk(self, chunk: str) -> Optional[str]:
        if not chunk:
            return None
        with self._lock:
            now = time.time()
            if not self.first_token_sent:
                self.first_token_sent = True
                self.last_flush_time = now
                self.emitted_batches.append(chunk)
                return chunk

            self.buffer.append(chunk)
            current_len = sum(len(c) for c in self.buffer)
            time_elapsed = now - self.last_flush_time

            if current_len >= self.batch_size or (self.last_flush_time > 0 and time_elapsed >= self.flush_delay_s):
                flushed = "".join(self.buffer)
                self.buffer.clear()
                self.last_flush_time = now
                self.emitted_batches.append(flushed)
                return flushed
            return None

    def flush(self, strip_trash_punc: bool = True) -> str:
        with self._lock:
            if not self.buffer:
                return ""
            remaining = "".join(self.buffer)
            self.buffer.clear()
            if strip_trash_punc and self.trash_punc:
                remaining = remaining.rstrip(self.trash_punc)
            if remaining:
                self.emitted_batches.append(remaining)
            return remaining

    def get_full_output(self) -> str:
        with self._lock:
            return "".join(self.emitted_batches) + "".join(self.buffer)


async def handle_typing_mode(handler, text: str, paste: bool = None, matched_hotwords=None, role_config=None, content=None) -> tuple:
    """打字输出模式"""
    from .llm_error_handler import handle_llm_error
    # 如果没传，则现场检测一次（兼容性）
    if not role_config or content is None:
        role_config, content = handler.detect_role(text)
    
    if not role_config:
        # 不应发生，但作为防守
        result_text = TextOutput.strip_punc(text)
        await output_text(result_text, paste)
        return (result_text, 0, 0.0, "success", None)

    handler.monitor.reset()  # 重置停止标志

    try:
        if paste:
            return await _process_paste(handler, role_config, content, matched_hotwords)
        else:
            return await _process_streaming(handler, role_config, content, matched_hotwords)

    except Exception as e:
        result_text, _, fallback_reason = handle_llm_error(e, content, role_config.name if role_config else "LLM")
        result_text = TextOutput.strip_punc(result_text)
        await output_text(result_text, paste)
        return (result_text, 0, 0.0, "fallback", fallback_reason)


async def _process_paste(handler, role_config, content, matched_hotwords) -> tuple:
    """处理粘贴模式：获取全文后一次性粘贴"""
    polished_text, token_count, gen_time = await to_thread(
        handler.process, role_config, content, matched_hotwords, None
    )
    if handler.monitor.should_stop():
        return ("", 0, 0.0, "success", None)

    final_text = TextOutput.strip_punc(polished_text or content)
    await paste_text(final_text, restore_clipboard=Config.restore_clip)
    return (final_text, token_count, gen_time, "success", None)


async def _process_streaming(handler, role_config, content, matched_hotwords) -> tuple:
    """处理流式打字模式：边生成边微缓冲模拟按键打字"""
    chunks = []
    pending_buffer = ""
    has_written_first_char = False
    micro_buffer = StreamMicroBuffer(
        batch_size=10,
        flush_delay_ms=30.0,
        trash_punc=getattr(Config, 'trash_punc', '，。,.')
    )

    def emit_text_to_keyboard(text_block: str):
        nonlocal pending_buffer, has_written_first_char
        if not text_block:
            return

        full_current = pending_buffer + text_block
        content_to_write = full_current
        trailing = ""

        # 从右向左寻找第一个非 trash 字符
        for i in range(len(full_current) - 1, -1, -1):
            char = full_current[i]
            if char == '\n' or char in Config.trash_punc:
                continue
            else:
                content_to_write = full_current[:i+1]
                trailing = full_current[i+1:]
                break
        else:
            content_to_write = ""
            trailing = full_current

        if not has_written_first_char:
            content_to_write = content_to_write.lstrip('\r\n')

        if content_to_write:
            has_written_first_char = True
            logger.debug(f"output_text: keyboard.write '{content_to_write}'")
            keyboard.write(content_to_write)
            pending_buffer = trailing
        else:
            pending_buffer = trailing

    def stream_write_chunk(chunk: str):
        if not chunk:
            return
        chunks.append(chunk)
        batched = micro_buffer.add_chunk(chunk)
        if batched:
            emit_text_to_keyboard(batched)

    # 执行流式处理
    polished_text, token_count, gen_time = await to_thread(
        handler.process, role_config, content, matched_hotwords, stream_write_chunk
    )

    # 冲刷微缓冲区剩余内容
    remaining_in_micro = micro_buffer.flush(strip_trash_punc=False)
    if remaining_in_micro:
        emit_text_to_keyboard(remaining_in_micro)

    # 阻塞，直到正常结束，或用户按下 ESC
    if handler.monitor.should_stop():
        final_text = TextOutput.strip_punc(''.join(chunks) or content)
        return (final_text, 0, 0.0, "success", None)

    # 如果模型没有任何输出，直接打出原文字
    if not chunks:
        final_text = TextOutput.strip_punc(content).lstrip('\r\n')
        if final_text:
            logger.debug(f"output_text: keyboard.write '{final_text}' (降级)")
            keyboard.write(final_text)
        return (final_text, 0, 0.0, "fallback", "empty_response")

    # 如果 LLM 只输出标点，会被拦截，就要做补偿输出
    full_output = ''.join(chunks).strip()
    if len(full_output) == 1 and full_output in Config.trash_punc and full_output != '\n':
        keyboard.write(full_output)

    return (TextOutput.strip_punc(polished_text), token_count, gen_time, "success", None)


async def output_text(text: str, paste: bool = None):
    """输出文本（根据 paste 或 Config.paste 选择方式）"""
    if paste:
        await paste_text(text, restore_clipboard=Config.restore_clip)
    else:
        clean_text = text.lstrip('\r\n')
        if clean_text:
            logger.debug(f"output_text: keyboard.write '{clean_text}'")
            keyboard.write(clean_text)

