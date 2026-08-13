# coding: utf-8
"""字幕转写历史记录工具。"""

from __future__ import annotations

from pathlib import Path
import shutil

BASE_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = BASE_DIR / 'web_gui' / 'outputs'


def list_transcription_history(limit: int = 30) -> list[dict]:
    if not OUTPUT_DIR.exists():
        return []

    items = []
    for folder in OUTPUT_DIR.iterdir():
        if not folder.is_dir():
            continue
        files = {}
        for path in folder.iterdir():
            if not path.is_file():
                continue
            suffix = path.suffix.lower()
            if path.name.endswith('.merge.txt'):
                files['merge'] = path
            elif suffix in {'.srt', '.txt', '.json'}:
                files[suffix.lstrip('.')] = path
        if not files:
            continue
        items.append({
            'folder': folder,
            'name': folder.name,
            'created_at': folder.stat().st_mtime,
            'files': files,
        })

    items.sort(key=lambda item: item['created_at'], reverse=True)
    return items[:limit]


def delete_transcription_output(folder: Path) -> tuple[bool, str]:
    folder = Path(folder).resolve()
    output_root = OUTPUT_DIR.resolve()
    if not str(folder).startswith(str(output_root)) or not folder.exists() or not folder.is_dir():
        return False, f'不是有效的转写输出目录：{folder}'
    shutil.rmtree(folder)
    return True, f'已删除转写输出：{folder.name}'
