"""Instance-bound runtime observations; port presence is never model readiness."""
from __future__ import annotations

import os
import threading
import time
import uuid
from pathlib import Path

from .paths import data_root
from .storage import atomic_json, read_json


def process_birth(pid: int) -> float | None:
    try:
        import psutil
        proc = psutil.Process(pid)
        if not proc.is_running() or proc.status() == psutil.STATUS_ZOMBIE:
            return None
        return proc.create_time()
    except Exception:
        return None


def same_process(pid, birth) -> bool:
    if not isinstance(pid, int) or not isinstance(birth, (int, float)):
        return False
    observed = process_birth(pid)
    return observed is not None and abs(observed - birth) < 0.01


def read_status(component: str, root: Path | None = None, max_age: float = 4.0) -> dict:
    path = (root or data_root()) / 'runtime' / f'{component}.json'
    try:
        record = read_json(path, {})
        now = time.time()
        if (not isinstance(record, dict) or record.get('component') != component
                or not record.get('instance_id')
                or not same_process(record.get('pid'), record.get('birth'))
                or not 0 <= now - float(record.get('updated_at', 0)) <= max_age):
            return {'component': component, 'alive': False, 'ready': False, 'state': 'offline'}
        record = dict(record, alive=True)
        if component == 'server' and record.get('ready'):
            record['ready'] = same_process(record.get('worker_pid'), record.get('worker_birth'))
            if not record['ready']:
                record['state'] = 'error'
        return record
    except (OSError, ValueError, TypeError):
        return {'component': component, 'alive': False, 'ready': False, 'state': 'unreadable'}


class StatusPublisher:
    def __init__(self, component: str, collect, root: Path | None = None):
        self.component = component
        self.collect = collect
        self.path = (root or data_root()) / 'runtime' / f'{component}.json'
        self.identity = {'component': component, 'instance_id': uuid.uuid4().hex,
                         'pid': os.getpid(), 'birth': process_birth(os.getpid())}
        self.stopped = threading.Event()
        self.thread = None

    def publish(self):
        try:
            payload = self.collect()
        except Exception:
            payload = {'state': 'error', 'ready': False, 'message': '运行状态采集失败'}
        atomic_json(self.path, {**self.identity, **payload, 'updated_at': time.time()})

    def start(self):
        self.publish()
        def loop():
            while not self.stopped.wait(0.5):
                try:
                    self.publish()
                except OSError:
                    pass
        self.thread = threading.Thread(target=loop, name=f'cw-{self.component}-status', daemon=True)
        self.thread.start()
        return self

    def stop(self):
        self.stopped.set()
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout=1)
        atomic_json(self.path, {**self.identity, 'updated_at': time.time(),
                               'state': 'stopped', 'ready': False})


def health_snapshot(root: Path | None = None) -> dict:
    server, client = read_status('server', root), read_status('client', root)
    inference = read_status('inference', root)
    file_busy = inference.get('state') == 'busy' and inference.get('task_type') == 'file'
    ready = bool(server.get('alive') and server.get('ready'))
    client_ok = bool(client.get('alive') and client.get('connected')
                     and client.get('microphone_ready') and client.get('hotkeys_ready'))
    return {'server_alive': server.get('alive', False), 'server_pid': server.get('pid'),
            'client_alive': client.get('alive', False), 'client_pid': client.get('pid'),
            'model_loaded': ready, 'can_dictate': ready and client_ok and not file_busy,
            'file_inference_busy': file_busy, 'inference': inference,
            'model_id': server.get('model_id'), 'server': server, 'client': client,
            'latest_error': server.get('error') or client.get('error') or '暂无最近错误'}
