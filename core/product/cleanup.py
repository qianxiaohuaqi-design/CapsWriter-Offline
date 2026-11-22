"""Preview-bound cleanup of explicitly selected files in the user's data root."""
from __future__ import annotations
import hashlib
import os
from pathlib import Path


def contained_file(root: Path, path: Path) -> Path:
    root = Path(root).resolve()
    path = Path(os.path.abspath(path))
    path.relative_to(root)
    if path == root or not path.is_file():
        raise ValueError('清理目标不是数据目录内的普通文件。')
    for parent in (path, *path.parents):
        if parent == root:
            break
        if parent.is_symlink():
            raise ValueError('清理跳过符号链接及其内容。')
    path.resolve().relative_to(root)
    return path


def file_identity(root: Path, path: Path) -> dict:
    path = contained_file(root, path)
    before = path.stat()
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
        raise ValueError('文件在预览时发生变化，请重新预览。')
    return {'path': str(path), 'size': after.st_size, 'mtime_ns': after.st_mtime_ns,
            'inode': after.st_ino, 'sha256': digest.hexdigest()}


def preview_files(root: Path, paths) -> list[dict]:
    return [file_identity(root, path) for path in dict.fromkeys(map(Path, paths))]


def delete_preview(root: Path, items: list[dict]) -> tuple[int, int]:
    deleted = failed = 0
    for item in items:
        try:
            path = Path(item['path'])
            if file_identity(root, path) != item:
                raise ValueError('文件在确认前已变化，跳过删除。')
            contained_file(root, path).unlink()
            deleted += 1
        except (OSError, ValueError, KeyError):
            failed += 1
    return deleted, failed
