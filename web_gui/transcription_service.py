# coding: utf-8
"""
控制中心文件转写服务。

复用 CapsWriter 现有 FileTranscriber / ResultHandler 链路，避免 GUI 端另写 ASR。
"""

from __future__ import annotations

import time
import asyncio
import uuid
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from core.protocol import RecognitionMessage
from config_client import ClientConfig as Config


ProgressCallback = Callable[[dict], None]
from core.product.paths import data_root
BASE_DIR = data_root()
OUTPUT_DIR = BASE_DIR / 'web_gui' / 'outputs'


@dataclass
class TranscriptionResult:
    ok: bool
    message: str
    input_file: Optional[Path] = None
    output_dir: Optional[Path] = None
    output_files: dict[str, Path] = field(default_factory=dict)
    text: str = ''


class ControlCenterTranscriptionApp:
    """FileTranscriber 所需的最小 app 门面。"""

    def __init__(self):
        import asyncio
        from core.client.connection import WebSocketManager
        from core.client.hotword.manager import HotwordManager
        from core.client.state import ClientState

        self.loop = asyncio.get_running_loop()
        self.state = ClientState(app=self)
        self.ws = WebSocketManager(self)
        self.hotword = HotwordManager(
            hotword_files=None,
            threshold=Config.hot_thresh,
            similar_threshold=Config.hot_similar,
        )


def _collect_output_files(file: Path, options: dict | None = None) -> dict[str, Path]:
    options = options or {}
    candidates = {}
    if options.get('file_save_srt', Config.file_save_srt):
        candidates['srt'] = file.with_suffix('.srt')
    if options.get('file_save_txt', Config.file_save_txt):
        candidates['txt'] = file.with_suffix('.txt')
    if options.get('file_save_json', Config.file_save_json):
        candidates['json'] = file.with_suffix('.json')
    if options.get('file_save_merge', Config.file_save_merge):
        candidates['merge'] = file.with_suffix('.merge.txt')
    return {kind: path for kind, path in candidates.items() if path.exists()}


def get_media_tool_status() -> dict:
    """Return FFmpeg/ffprobe availability without printing user-facing console noise."""
    from core.client.transcribe.media_tool import MediaTool
    ffmpeg_path, ffprobe_path = MediaTool.resolve_tools()
    return {
        'ffmpeg': ffmpeg_path,
        'ffprobe': ffprobe_path,
        'ok': bool(ffmpeg_path),
        'full_progress': bool(ffprobe_path),
    }


def regenerate_srt_from_txt(txt_file: Path) -> tuple[bool, str, Optional[Path]]:
    """Regenerate an SRT file from an edited TXT and the matching JSON timestamp file."""
    from core.tools import srt_from_txt
    txt_file = Path(txt_file).expanduser().resolve()
    if not txt_file.exists():
        return False, f'TXT 文件不存在：{txt_file}', None
    if txt_file.name.endswith('.merge.txt'):
        return False, 'merge.txt 是未切分全文，请选择同名 .txt 分行稿来重建 SRT。', None
    json_file = txt_file.with_suffix('.json')
    if not json_file.exists():
        return False, f'缺少同名 JSON 时间戳文件：{json_file.name}', None
    try:
        raw = json.loads(json_file.read_text(encoding='utf-8'))
        timestamps, tokens = raw.get('timestamps', []), raw.get('tokens', [])
        valid = (isinstance(timestamps, list) and isinstance(tokens, list) and bool(tokens)
                 and len(tokens) == len(timestamps) and all(isinstance(token, str) for token in tokens)
                 and all(isinstance(value, (int, float)) and math.isfinite(value) and value >= 0 for value in timestamps)
                 and all(a <= b for a, b in zip(timestamps, timestamps[1:])))
        if not valid:
            return False, '此结果缺少可用时间戳，不能生成 SRT；原 TXT/JSON 保持不变。', None
        words = srt_from_txt.get_words(json_file)
        if not words:
            return False, '此结果缺少可用时间戳，不能生成 SRT；原 TXT/JSON 保持不变。', None
        # New filename preserves an older subtitle, including files outside our data directory.
        srt_file = txt_file.with_name(f'{txt_file.stem}.rebuilt-{uuid.uuid4().hex[:10]}.srt')
        if srt_file.exists() or srt_file.is_symlink():
            return False, '目标路径已存在，请重试。', None
        import srt
        lines = [line.rstrip('，。？！,.?!\r\n ') for line in srt_from_txt.get_lines(txt_file)]
        content = srt.compose(srt_from_txt.lines_match_words(lines, words))
        with srt_file.open('x', encoding='utf-8') as stream:
            stream.write(content)
        return True, f'已生成新的 SRT：{srt_file.name}，原字幕保留。', srt_file
    except Exception as e:
        return False, f'重新生成 SRT 失败：{e}', None



async def transcribe_file(file: Path, progress: Optional[ProgressCallback] = None, *,
                          output_dir: Path | None = None, options: dict | None = None,
                          cancel_requested: Callable[[], bool] | None = None) -> TranscriptionResult:
    """
    转写单个音视频文件。

    progress 回调收到的字段：
    - phase: checking | sending | receiving | saving | done | error
    - percent: 0-100
    - detail: 人类可读状态
    """

    def emit(phase: str, percent: float, detail: str) -> None:
        if progress:
            progress({'phase': phase, 'percent': max(0, min(100, percent)), 'detail': detail})

    from core.client.transcribe.file_transcriber import FileTranscriber
    from core.client.transcribe.result_handler import ResultHandler
    from core.client.transcribe.media_tool import MediaTool

    file = Path(file).expanduser().resolve()
    options = options or {}
    cancelled = cancel_requested or (lambda: False)
    if cancelled():
        return TranscriptionResult(False, '任务已安全停止。', file)
    if output_dir is None:
        output_dir = OUTPUT_DIR / uuid.uuid4().hex
        output_dir.mkdir(parents=True, exist_ok=False)
        (output_dir / '.capswriter-output.json').write_text(json.dumps({'kind': 'transcription'}), encoding='utf-8')
    app = ControlCenterTranscriptionApp()
    transcriber = FileTranscriber(app, file, options=options)

    emit('checking', 2, '正在检查文件、FFmpeg 与 ASR 服务连接...')
    if not file.exists():
        return TranscriptionResult(False, f'文件不存在: {file}', file)
    if not MediaTool.check_environment():
        return TranscriptionResult(
            False,
            '未检测到内置 FFmpeg。请将 ffmpeg.exe 和 ffprobe.exe 放入 tools/ffmpeg/bin 后重试。',
            file,
        )

    app.hotword.start()
    try:
        if not await transcriber.check():
            return TranscriptionResult(False, '无法连接 ASR 服务端，或转写条件检查失败。', file)

        emit('sending', 8, '正在提取并发送音频...')

        def on_send_progress(sent_seconds: float, total_seconds: float) -> None:
            if total_seconds > 0:
                percent = 8 + (sent_seconds / total_seconds) * 27
                emit('sending', percent, f'正在提取并发送音频：{sent_seconds:.1f}s / {total_seconds:.1f}s')
            else:
                emit('sending', min(34, 8 + sent_seconds), f'正在提取并发送音频：{sent_seconds:.1f}s')

        await transcriber.send(on_send_progress)
        if not transcriber.task_id:
            return TranscriptionResult(False, '音频发送失败，未创建转写任务。', file)

        emit('receiving', 35, '正在等待模型识别结果...')
        message = await _receive_with_progress(transcriber, emit)
        if message is None:
            return TranscriptionResult(False, '未收到最终识别结果。', file)

        if cancelled():
            return TranscriptionResult(False, '任务已安全停止；未写入本次结果。', file)

        emit('saving', 92, '正在应用热词并保存字幕文件...')
        transcriber._apply_hotwords(message)
        if not (message.text or message.text_accu or message.tokens):
            return TranscriptionResult(
                False,
                '未从该文件中提取到可识别音频，或音轨为空。',
                file,
            )
        text_display = ResultHandler.save_results(file, message, output_dir=output_dir, options=options)
        outputs = _collect_output_files(output_dir / file.name, options)
        detail = '转写完成'
        if options.get('file_save_srt', Config.file_save_srt) and 'srt' not in outputs:
            detail += '；此模型未提供可用时间戳，已保存文本，未生成 SRT'


        emit('done', 100, '转写完成。')
        return TranscriptionResult(
            True,
            detail,
            input_file=file,
            output_dir=output_dir,
            output_files=outputs,
            text=text_display,
        )
    except asyncio.CancelledError:
        emit('error', 0, '本地连接已停止；服务端推理状态需重新核验。')
        raise
    except Exception as e:
        emit('error', 100, f'转写失败: {e}')
        return TranscriptionResult(False, f'转写失败: {e}', file)
    finally:
        try:
            await transcriber.close()
        except Exception:
            pass
        app.hotword.stop()


async def _receive_with_progress(transcriber: FileTranscriber, emit) -> Optional[RecognitionMessage]:
    last_percent = 35.0
    while True:
        msg = await transcriber._ws_manager.receive()
        if not msg:
            return None
        if getattr(msg, 'task_id', None) != transcriber.task_id:
            continue

        if transcriber._audio_duration > 0:
            percent = 35 + (msg.duration / transcriber._audio_duration) * 55
        else:
            percent = min(90, last_percent + 2)
        last_percent = percent

        emit('receiving', percent, f'模型识别进度：{msg.duration:.1f}s')
        if msg.is_final:
            msg.time_start = msg.time_start or time.time()
            return msg
