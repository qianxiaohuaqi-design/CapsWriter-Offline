"""Durable file-transcription queue. GUI lifetime does not own an ASR task.

This module uses only the standard library. The coordinator supplies the current
*ready* model identity and dispatch policy; the legacy ASR adapter is lazy-loaded.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from contextlib import contextmanager
from typing import Callable

ACTIVE = frozenset({'queued', 'running', 'cancelling'})
TERMINAL = frozenset({'completed', 'failed', 'cancelled', 'interrupted'})
MODEL_LOST_GRACE_SECONDS = 5.0


class TaskConflict(ValueError):
    """The requested transition would lose state or silently change inputs."""


def source_identity(path: Path) -> dict:
    path = Path(path).expanduser().resolve(strict=True)
    if not path.is_file():
        raise ValueError('请选择可读取的音频或视频文件。')
    before = path.stat()
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise TaskConflict('源文件在检查期间发生变化，请保存文件后重试。')
    return {'path': str(path), 'size': after.st_size, 'mtime_ns': after.st_mtime_ns,
            'sha256': digest.hexdigest()}


def process_identity() -> dict:
    try:
        import psutil
        started = psutil.Process(os.getpid()).create_time()
    except ImportError:
        started = None
    return {'pid': os.getpid(), 'started': started, 'instance': uuid.uuid4().hex}


def owner_alive(owner: dict | None) -> bool:
    if not owner or not isinstance(owner.get('pid'), int):
        return False
    try:
        import psutil
        proc = psutil.Process(owner['pid'])
        return proc.is_running() and owner.get('started') is not None and abs(proc.create_time() - owner['started']) < 0.01
    except ImportError:
        # Without creation-time validation, a PID is insufficient evidence.
        return owner.get('pid') == os.getpid()
    except Exception:
        return False


class TaskStore:
    def __init__(self, data_root: Path):
        self.root = Path(data_root).resolve()
        self.directory = self.root / 'web_gui' / 'tasks'
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / 'tasks.sqlite3'
        self.output_root = self.root / 'web_gui' / 'outputs'
        if self.directory.is_symlink() or self.path.is_symlink() or self.output_root.is_symlink():
            raise ValueError('任务或输出目录不能是链接。')
        self._lock = threading.RLock()
        with self._connection() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, updated REAL NOT NULL, record TEXT NOT NULL)')

    @contextmanager
    def _connection(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.execute('PRAGMA synchronous=FULL')
        try:
            with db:
                yield db
        finally:
            db.close()

    def _update(self, task_id: str, change: Callable[[dict], None]) -> dict:
        with self._lock, self._connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT record FROM tasks WHERE id=?', (task_id,)).fetchone()
            if row is None:
                raise KeyError('转写任务不存在。')
            record = json.loads(row[0])
            change(record)
            record['updated_at'] = time.time()
            db.execute('UPDATE tasks SET record=?,updated=? WHERE id=?',
                       (json.dumps(record, ensure_ascii=False), record['updated_at'], task_id))
            return record

    def create(self, source: Path, model_id: str, parameters: dict | None = None) -> dict:
        if not model_id or not isinstance(model_id, str):
            raise ValueError('请先加载一个可用模型。')
        identity = source_identity(source)
        params = json.loads(json.dumps(parameters or {}, ensure_ascii=False, allow_nan=False))
        stamp = time.time()
        record = {'id': uuid.uuid4().hex, 'source': identity, 'model_id': model_id,
                  'parameters': params, 'state': 'queued', 'phase': 'queued', 'percent': 0,
                  'detail': '已加入队列，等待模型空闲。', 'created_at': stamp, 'updated_at': stamp,
                  'attempt_id': None, 'attempts': [], 'outputs': {}, 'output_dir': None,
                  'owner': None, 'cancel_requested': False, 'text': ''}
        with self._lock, self._connection() as db:
            db.execute('BEGIN IMMEDIATE')
            for row in db.execute('SELECT record FROM tasks'):
                existing = json.loads(row[0])
                if (existing['state'] in ACTIVE and existing['source'] == identity
                        and existing['model_id'] == model_id and existing['parameters'] == params):
                    return existing
            db.execute('INSERT INTO tasks VALUES (?,?,?)',
                       (record['id'], stamp, json.dumps(record, ensure_ascii=False)))
        return record

    def get(self, task_id: str) -> dict:
        with self._connection() as db:
            row = db.execute('SELECT record FROM tasks WHERE id=?', (task_id,)).fetchone()
        if row is None:
            raise KeyError('转写任务不存在。')
        return json.loads(row[0])

    def list(self, limit: int = 200) -> list[dict]:
        with self._connection() as db:
            rows = db.execute('SELECT record FROM tasks ORDER BY updated DESC LIMIT ?', (max(1, min(limit, 5000)),)).fetchall()
        return [json.loads(row[0]) for row in rows]

    def validate_source(self, record: dict) -> None:
        try:
            current = source_identity(Path(record['source']['path']))
        except (OSError, ValueError) as error:
            raise TaskConflict('原文件不存在或不可读取；请重新选择文件创建任务。') from error
        if current != record['source']:
            raise TaskConflict('原文件已修改；为保护原任务，请重新选择文件创建任务。')

    def retry(self, task_id: str, current_model_id: str | None) -> dict:
        record = self.get(task_id)
        self.validate_source(record)
        if record['model_id'] != current_model_id:
            raise TaskConflict('请先切换回此任务原来使用的模型；不会自动改用其他模型。')
        def change(row):
            if row['state'] not in TERMINAL:
                raise TaskConflict('此任务仍在队列中或正在运行，请勿重复重试。')
            row.update(state='queued', phase='queued', percent=0, detail='等待按原模型和原参数从文件开头重试。',
                       attempt_id=None, owner=None, cancel_requested=False)
        return self._update(task_id, change)

    def begin(self, task_id: str, current_model_id: str | None, owner: dict) -> dict:
        record = self.get(task_id)
        self.validate_source(record)
        if record['model_id'] != current_model_id:
            raise TaskConflict('当前模型与任务原模型不同，请切换回原模型后重试。')
        def change(row):
            if row['state'] != 'queued':
                raise TaskConflict('任务已经开始或已结束。')
            attempt_id = uuid.uuid4().hex
            output_dir = self.output_root / row['id'] / attempt_id
            if self.output_root.is_symlink() or output_dir.parent.is_symlink():
                raise ValueError('输出路径包含链接，已停止运行。')
            output_dir.mkdir(parents=True, exist_ok=False)
            # Ownership marker is separate from user-facing recognition JSON.
            (output_dir / '.capswriter-output.json').write_text(json.dumps({'task_id': row['id'], 'attempt_id': attempt_id}), encoding='utf-8')
            row['attempts'].append({'id': attempt_id, 'started_at': time.time(), 'state': 'running',
                                    'output_dir': str(output_dir)})
            row.update(state='running', phase='checking', attempt_id=attempt_id, owner=owner,
                       percent=0, detail='正在检查源文件和模型。', cancel_requested=False)
        return self._update(task_id, change)

    def progress(self, task_id: str, attempt_id: str, event: dict) -> dict:
        def change(row):
            if row['attempt_id'] != attempt_id or row['state'] not in ('running', 'cancelling'):
                return
            row['phase'] = str(event.get('phase', row['phase']))
            row['percent'] = max(0, min(100, float(event.get('percent', row['percent']))))
            if row['cancel_requested']:
                row['detail'] = '正在安全停止：等待当前识别结束，不再保存本次结果。'
            else:
                row['detail'] = str(event.get('detail', row['detail']))
        return self._update(task_id, change)

    def cancel(self, task_id: str) -> dict:
        def change(row):
            if row['state'] == 'queued':
                row.update(state='cancelled', phase='cancelled', detail='已从队列移除。', cancel_requested=True)
            elif row['state'] in ('running', 'cancelling'):
                row.update(state='cancelling', cancel_requested=True,
                           detail='正在安全停止：等待当前识别结束，不会强杀模型。')
        return self._update(task_id, change)

    def finish(self, task_id: str, attempt_id: str, *, ok: bool, message: str,
               outputs: dict | None = None, text: str = '', interrupted: bool = False) -> dict:
        def change(row):
            if row['attempt_id'] != attempt_id or row['state'] not in ('running', 'cancelling'):
                return
            attempt = next(item for item in row['attempts'] if item['id'] == attempt_id)
            state = 'interrupted' if interrupted else ('cancelled' if row['cancel_requested'] else ('completed' if ok else 'failed'))
            if state == 'completed':
                folder = Path(attempt['output_dir']).resolve()
                clean = {}
                for kind, value in (outputs or {}).items():
                    file = Path(value)
                    if file.is_symlink() or not file.is_file() or file.resolve().parent != folder:
                        raise TaskConflict('结果路径不属于本次运行，已阻止更新。')
                    clean[str(kind)] = str(file.resolve())
                row.update(outputs=clean, output_dir=str(folder), text=text)
                attempt['outputs'] = clean
            attempt.update(state=state, finished_at=time.time(), message=message)
            row.update(state=state, phase=state, owner=None,
                       detail='已安全停止；之前成功的结果已保留。' if state == 'cancelled' else message,
                       percent=100 if state == 'completed' else row['percent'])
        return self._update(task_id, change)

    def fail_queued(self, task_id: str, message: str) -> dict:
        def change(row):
            if row['state'] == 'queued':
                row.update(state='failed', phase='error', detail=message)
        return self._update(task_id, change)

    def recover_orphans(self, is_alive: Callable[[dict | None], bool] = owner_alive) -> int:
        recovered = 0
        for record in self.list(5000):
            if record['state'] not in ('running', 'cancelling') or is_alive(record['owner']):
                continue
            self.finish(record['id'], record['attempt_id'], ok=False,
                        message='上次运行已中断，原文件和已完成结果保留；可从头重试。', interrupted=True)
            recovered += 1
        return recovered


class TranscriptionQueue:
    """One file at a time; cooperative cancellation waits for safe ASR completion."""
    def __init__(self, data_root: Path, current_model: Callable[[], str | None],
                 can_run: Callable[[], bool] | None = None, runner=None, dispatch_lock=None):
        self.store = TaskStore(data_root)
        self.current_model = current_model
        self.can_run = can_run or (lambda: True)
        self.runner = runner
        self.owner = process_identity()
        self._dispatch_lock = dispatch_lock or threading.RLock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._running_id = None
        self.store.recover_orphans()
        self._thread = threading.Thread(target=self._run_thread, name='CapsWriter-Transcription', daemon=True)
        self._thread.start()

    @staticmethod
    def default_parameters() -> dict:
        from config_client import ClientConfig
        fields = ('file_save_srt', 'file_save_txt', 'file_save_json', 'file_save_merge',
                  'file_seg_duration', 'file_seg_overlap', 'context', 'language', 'hot', 'hot_rule')
        return {key: getattr(ClientConfig, key) for key in fields}

    def submit(self, path: str | Path, model_id: str | None = None, parameters: dict | None = None) -> dict:
        if self._stop.is_set():
            raise TaskConflict('应用正在退出，请稍后重新打开。')
        active = self.current_model()
        if not active or (model_id is not None and model_id != active):
            raise TaskConflict('请先成功加载所选模型。')
        result = self.store.create(Path(path), active, self.default_parameters() if parameters is None else parameters)
        self._wake.set()
        return result

    def retry(self, task_id: str) -> dict:
        if self._stop.is_set():
            raise TaskConflict('应用正在退出。')
        result = self.store.retry(task_id, self.current_model())
        self._wake.set()
        return result

    def cancel(self, task_id: str) -> dict:
        return self.store.cancel(task_id)

    def snapshot(self) -> list[dict]:
        return self.store.list()

    def busy(self) -> bool:
        return self._running_id is not None or any(row['state'] in ('running', 'cancelling') for row in self.store.list())

    def shutdown(self, timeout: float = 10.0) -> bool:
        self._stop.set()
        self._wake.set()
        if self._running_id:
            self.store.cancel(self._running_id)
        self._thread.join(timeout=max(0, timeout))
        return not self._thread.is_alive()

    def _run_thread(self):
        asyncio.run(self._run())

    async def _run(self):
        while not self._stop.is_set():
            queued = sorted((row for row in self.store.list() if row['state'] == 'queued'), key=lambda row: row['created_at'])
            if not queued:
                await asyncio.sleep(0.2)
                continue
            with self._dispatch_lock:
                model = self.current_model()
                allowed = bool(not self._stop.is_set() and self.can_run() and model is not None)
                if allowed:
                    self._running_id = queued[0]['id']
            if not allowed:
                await asyncio.sleep(0.2)
                continue
            row = queued[0]
            try:
                row = self.store.begin(row['id'], model, self.owner)
            except (ValueError, OSError) as error:
                self.store.fail_queued(row['id'], str(error))
                self._running_id = None
                continue
            attempt_id = row['attempt_id']
            try:
                runner = self.runner
                if runner is None:
                    from web_gui.transcription_service import transcribe_file
                    runner = transcribe_file
                running = asyncio.create_task(runner(Path(row['source']['path']),
                                      progress=lambda event: self.store.progress(row['id'], attempt_id, event),
                                      output_dir=Path(row['attempts'][-1]['output_dir']),
                                      options=row['parameters'],
                                      cancel_requested=lambda: self.store.get(row['id'])['cancel_requested']))
                missing_since = None
                interrupted = False
                while not running.done():
                    await asyncio.wait({running}, timeout=0.5)
                    if running.done():
                        break
                    if self.current_model() != row['model_id']:
                        missing_since = missing_since or time.monotonic()
                    else:
                        missing_since = None
                    if missing_since is not None and time.monotonic() - missing_since >= MODEL_LOST_GRACE_SECONDS:
                        # The owning ready model has vanished/changed. Stop only
                        # our client/FFmpeg resources; never claim server cancel.
                        running.cancel()
                        try:
                            await running
                        except asyncio.CancelledError:
                            pass
                        self.store.finish(row['id'], attempt_id, ok=False, interrupted=True,
                                          message='模型运行状态已失效；任务标为中断，原文件和已完成结果保留。')
                        interrupted = True
                        break
                if interrupted:
                    continue
                result = await running
                if result.ok:
                    self.store.validate_source(row)
                self.store.finish(row['id'], attempt_id, ok=result.ok, message=result.message,
                                  outputs=result.output_files, text=result.text)
            except Exception as error:
                self.store.finish(row['id'], attempt_id, ok=False, message=f'转写失败：{error}')
            finally:
                self._running_id = None
