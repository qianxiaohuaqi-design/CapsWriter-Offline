# coding: utf-8
"""字幕转写历史记录工具。"""

from __future__ import annotations

from pathlib import Path
import json
import shutil

from core.product.paths import data_root
BASE_DIR = data_root()
OUTPUT_DIR = BASE_DIR / 'web_gui' / 'outputs'


def list_transcription_history(limit: int = 30) -> list[dict]:
    if not OUTPUT_DIR.exists():
        return []

    items = []
    tasks_by_id = {}
    database = OUTPUT_DIR.parent / 'tasks' / 'tasks.sqlite3'
    if database.exists():
        from core.product.tasks import TaskStore
        tasks_by_id = {record['id']: record for record in TaskStore(OUTPUT_DIR.parents[1]).list(5000)}
    folders = list(OUTPUT_DIR.iterdir())
    for parent in tuple(folders):
        if parent.is_dir() and not parent.is_symlink():
            folders.extend(child for child in parent.iterdir() if child.is_dir() and not child.is_symlink())
    for folder in folders:
        if not folder.is_dir() or folder.is_symlink():
            continue
        marker = folder / '.capswriter-output.json'
        if marker.is_file() and not marker.is_symlink():
            try:
                identity = json.loads(marker.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                continue
            if identity.get('task_id'):
                task = tasks_by_id.get(identity['task_id'])
                attempt = next((item for item in (task or {}).get('attempts', []) if item['id'] == identity.get('attempt_id')), None)
                if not attempt or attempt.get('state') != 'completed':
                    continue
        files = {}
        for path in folder.iterdir():
            if not path.is_file() or path.is_symlink() or path.name.startswith('.'):
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
    raw = Path(folder)
    output_root = OUTPUT_DIR.resolve()
    if raw.is_symlink():
        return False, '不删除链接目录。'
    folder = raw.resolve()
    if output_root not in folder.parents or not folder.is_dir():
        return False, f'不是有效的转写输出目录：{folder}'
    marker = folder / '.capswriter-output.json'
    if not marker.is_file() or marker.is_symlink():
        return False, '该目录没有本应用的输出归属标记，默认保留。'
    database = OUTPUT_DIR.parent / 'tasks' / 'tasks.sqlite3'
    if database.exists():
        from core.product.tasks import TaskStore
        for record in TaskStore(OUTPUT_DIR.parents[1]).list(5000):
            if record['state'] in {'running', 'cancelling'}:
                for attempt in record.get('attempts', []):
                    if attempt['id'] == record.get('attempt_id') and Path(attempt['output_dir']).resolve() == folder:
                        return False, '当前转写仍在使用此目录，请等待任务安全结束。'
    # Reject any linked subtree; never follow it into unrelated user data.
    if any(path.is_symlink() for path in folder.rglob('*')):
        return False, '目录中包含链接，已保留全部文件。'
    shutil.rmtree(folder)
    return True, f'已删除转写输出：{folder.name}'
