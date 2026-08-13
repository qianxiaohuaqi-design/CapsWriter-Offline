# coding: utf-8
"""
听写输入历史记录

将每次最终输出的文本写入本地 JSONL 文件，供控制中心恢复复制。
"""

from __future__ import annotations

import json
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from . import logger

BASE_DIR = Path(__file__).resolve().parents[3]
HISTORY_PATH = BASE_DIR / 'web_gui' / 'input_history.jsonl'
CLEAR_MARKER_PATH = BASE_DIR / 'web_gui' / 'input_history_cleared_at.txt'
DEFAULT_MAX_ITEMS = 200
DIARY_RE = re.compile(r'^\[(?P<time>\d{2}:\d{2}:\d{2})\]\((?P<audio>.*)\)\s+(?P<text>.+)$')

_LOCK = threading.Lock()


def _read_jsonl(path: Path = HISTORY_PATH) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding='utf-8', errors='ignore').splitlines():
        raw = line.strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and data.get('text'):
            records.append(data)
    return records


def _write_jsonl(records: list[dict[str, Any]], path: Path = HISTORY_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = '\n'.join(json.dumps(item, ensure_ascii=False) for item in records)
    path.write_text(f'{payload}\n' if payload else '', encoding='utf-8')


def _read_clear_marker() -> datetime | None:
    try:
        raw = CLEAR_MARKER_PATH.read_text(encoding='utf-8').strip()
        return datetime.fromisoformat(raw)
    except Exception:
        return None


def _load_diary_history(limit: int = 80) -> list[dict[str, Any]]:
    """从每日听写日记中回填历史，兼容客户端未重启或旧版本未写 JSONL 的情况。"""
    records = []
    cleared_at = _read_clear_marker()
    for path in sorted(BASE_DIR.glob('20??/**/*.md'), key=lambda p: p.stat().st_mtime, reverse=True):
        if path.name.endswith('-默认.md'):
            continue
        try:
            lines = path.read_text(encoding='utf-8', errors='ignore').splitlines()
        except Exception:
            continue

        date_parts = path.relative_to(BASE_DIR).parts[:3]
        if len(date_parts) >= 3:
            date_label = f'{date_parts[0]}-{date_parts[1]}-{Path(date_parts[2]).stem.zfill(2)}'
        else:
            date_label = ''

        for line in reversed(lines):
            match = DIARY_RE.match(line.strip())
            if not match:
                continue
            text = match.group('text').strip()
            if not text:
                continue
            created_at = f'{date_label} {match.group("time")}'.strip()
            if cleared_at:
                try:
                    created_dt = datetime.strptime(created_at, '%Y-%m-%d %H:%M:%S')
                except ValueError:
                    created_dt = None
                if created_dt and created_dt <= cleared_at:
                    continue
            records.append({
                'id': f'diary-{path.as_posix()}-{match.group("time")}-{len(records)}',
                'created_at': created_at,
                'text': text,
                'original_text': '',
                'source_text': '',
                'role_name': '',
                'processed': False,
                'paste': None,
                'process_name': '听写日记',
                'time_start': None,
                'length': len(text),
                'source': 'diary',
                'audio_path': match.group('audio'),
            })
            if len(records) >= limit:
                return records
    return records


def append_input_history(
    text: str,
    *,
    original_text: str = '',
    source_text: str = '',
    role_name: str | None = None,
    processed: bool = False,
    paste: bool | None = None,
    process_name: str = '',
    time_start: float | None = None,
    max_items: int = DEFAULT_MAX_ITEMS,
) -> None:
    """追加一条最终输出历史。失败只写日志，不影响核心听写输出。"""
    text = (text or '').strip()
    if not text:
        return

    record = {
        'id': uuid4().hex,
        'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'text': text,
        'original_text': (original_text or '').strip(),
        'source_text': (source_text or '').strip(),
        'role_name': role_name or '',
        'processed': bool(processed),
        'paste': paste,
        'process_name': process_name or '',
        'time_start': time_start,
        'length': len(text),
    }

    try:
        with _LOCK:
            records = _read_jsonl()
            records.append(record)
            _write_jsonl(records[-max_items:])
        logger.debug(f"输入历史已写入: {HISTORY_PATH}")
    except Exception as e:
        logger.debug(f"写入输入历史失败，已跳过: {e}")


def load_input_history(limit: int = 80) -> list[dict[str, Any]]:
    """读取最近输入历史，按时间倒序返回。"""
    with _LOCK:
        records = _read_jsonl()

    jsonl_records = list(reversed(records[-limit:]))
    diary_records = _load_diary_history(limit=limit)

    merged = []
    seen = set()
    for item in jsonl_records + diary_records:
        key = (item.get('created_at'), item.get('text'))
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)

    return sorted(merged, key=lambda item: item.get('created_at', ''), reverse=True)[:limit]


def clear_input_history() -> None:
    """清空输入历史。"""
    with _LOCK:
        CLEAR_MARKER_PATH.parent.mkdir(parents=True, exist_ok=True)
        CLEAR_MARKER_PATH.write_text(datetime.now().isoformat(timespec='seconds'), encoding='utf-8')
        _write_jsonl([])
