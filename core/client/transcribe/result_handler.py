# coding: utf-8
import re
import json
import math
import uuid
from pathlib import Path
from typing import Dict, Any

from config_client import ClientConfig as Config
from core.tools import srt_from_txt
from core.protocol import RecognitionMessage
from . import logger

class ResultHandler:
    """结果处理器：负责文本格式化和文件保存"""

    @staticmethod
    def smart_split(text: str, min_chars: int = 2) -> str:
        """
        智能分行功能
        1. 保留标点符号
        2. 避免在逗号处切分过短的句子
        3. 英文标点需后跟空格才切分（避免 3.14 被切分）
        """
        # 使用捕获组保留标点，英文标点需后跟空白符或结尾
        parts = re.split(r'([，。？]|[.,?!](?:\s+|$))', text)
        lines = []
        buffer = ""
        
        # 强标点（必须换行）
        strong_punct = {'。', '？', '.', '?', '!'}
        punct_chars = set(r'，。？,.?!')
        
        for part in parts:
            clean_part = part.strip()
            # 如果是标点符号（长度为1且在列表中）
            if clean_part and clean_part in punct_chars and len(clean_part) == 1:
                buffer += part
                is_strong = clean_part in strong_punct
                # 如果是强标点，强制换行
                # 如果是弱标点，要累积了一定字数才换行
                if is_strong or len(buffer) > min_chars:
                    lines.append(buffer)
                    buffer = ""
            else:
                # 是文本
                buffer += part
        
        if buffer:
            lines.append(buffer)

            
        return "\n".join(lines)

    @classmethod
    def save_results(cls, file: Path, message: RecognitionMessage, *, output_dir: Path | None = None,
                     options: dict | None = None) -> str:
        """Write only into a dedicated attempt folder, never beside source media.

        Existing results are never removed when a format is disabled. A missing
        timestamp stream permits TXT/JSON and truthfully omits SRT.
        """
        from core.product.paths import data_root
        import srt
        file = Path(file)
        if output_dir is None:
            output_dir = data_root() / 'web_gui' / 'outputs' / uuid.uuid4().hex
            output_dir.mkdir(parents=True, exist_ok=False)
            (output_dir / '.capswriter-output.json').write_text(
                json.dumps({'kind': 'transcription'}), encoding='utf-8')
        output_dir = Path(output_dir)
        if output_dir.is_symlink():
            raise ValueError('转写输出目录不能是链接。')
        output_dir.mkdir(parents=True, exist_ok=True)
        destination = output_dir / file.name
        opts = options or {}
        enabled = lambda name: bool(opts.get(name, getattr(Config, name)))
        text_display = message.text
        text_accu = message.text_accu or message.text
        text_split = cls.smart_split(text_accu)
        timestamps = message.timestamps or []
        tokens = message.tokens or []
        valid_times = (bool(tokens) and all(isinstance(token, str) for token in tokens) and len(tokens) == len(timestamps)
                       and all(isinstance(t, (int, float)) and math.isfinite(t) and t >= 0 for t in timestamps)
                       and all(a <= b for a, b in zip(timestamps, timestamps[1:])))
        payloads = {}
        if enabled('file_save_merge'):
            payloads[destination.with_suffix('.merge.txt')] = text_accu
        if enabled('file_save_txt'):
            payloads[destination.with_suffix('.txt')] = text_split
        if enabled('file_save_json'):
            payloads[destination.with_suffix('.json')] = json.dumps(
                {'timestamps': timestamps if valid_times else [], 'tokens': [str(token) for token in tokens],
                 'timestamps_available': valid_times}, ensure_ascii=False, allow_nan=False)
        if enabled('file_save_srt') and valid_times:
            words = [{'word': str(token).replace('@', ''), 'start': timestamp, 'end': timestamp + 0.2}
                     for timestamp, token in zip(timestamps, tokens)]
            for index in range(len(words) - 1):
                words[index]['end'] = min(words[index]['end'], words[index + 1]['start'])
            lines = [line.rstrip('，。？！,.?!\r\n ') for line in text_split.splitlines()]
            payloads[destination.with_suffix('.srt')] = srt.compose(srt_from_txt.lines_match_words(lines, words))
        if not payloads:
            raise ValueError('没有可保存的结果格式；无时间戳时请启用 TXT 或 JSON。')
        for target in payloads:
            if target.exists() or target.is_symlink():
                raise FileExistsError(f'已有结果受到保护，不能覆盖：{target.name}')
        for target, content in payloads.items():
            # Exclusive creation also protects against a late competing writer.
            with target.open('x', encoding='utf-8') as stream:
                stream.write(content)
        logger.debug(f'结果已保存到独立目录: {output_dir}')
        return text_display
