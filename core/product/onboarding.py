"""First setup intent persists independently of a model's runtime health."""
from __future__ import annotations

import threading
from pathlib import Path
from .storage import atomic_json, read_json


class SetupStore:
    def __init__(self, root: Path, existing_model: str | None = None):
        self.path = Path(root) / 'product' / 'setup.json'
        self.lock = threading.RLock()
        data = read_json(self.path)
        if data is None:
            data = {'schema': 1, 'status': 'existing' if existing_model else 'new',
                    'step': 1, 'default_model': existing_model,
                    'first_candidate': None, 'microphone_test': 'not_tested',
                    'background_notice_seen': False}
            atomic_json(self.path, data)
        if not isinstance(data, dict) or data.get('schema') != 1:
            raise ValueError('首次设置记录损坏；保留原文件，请从诊断页处理。')
        if (data.get('status') not in {'new', 'in_progress', 'skipped', 'complete', 'existing'}
                or type(data.get('step')) is not int or not 1 <= data['step'] <= 6
                or data.get('microphone_test') not in {'not_tested', 'detected_signal', 'no_signal', 'failed'}):
            raise ValueError('首次设置记录字段无效；保留原文件，请检查诊断。')
        self.data = data

    def snapshot(self):
        with self.lock:
            return dict(self.data)

    def update(self, **values):
        allowed = {'status', 'step', 'microphone_test', 'background_notice_seen'}
        if set(values) - allowed:
            raise ValueError('Unsupported setup field')
        if 'step' in values and (type(values['step']) is not int or not 1 <= values['step'] <= 6):
            raise ValueError('Invalid setup step')
        if 'status' in values and values['status'] not in {'new', 'in_progress', 'skipped', 'complete', 'existing'}:
            raise ValueError('Invalid setup status')
        if 'microphone_test' in values and values['microphone_test'] not in {'not_tested', 'detected_signal', 'no_signal', 'failed'}:
            raise ValueError('Invalid microphone observation')
        if 'background_notice_seen' in values and type(values['background_notice_seen']) is not bool:
            raise ValueError('Invalid background notice flag')
        with self.lock:
            next_data = dict(self.data, **values)
            atomic_json(self.path, next_data)
            self.data = next_data
            return dict(self.data)

    def select_first(self, model_id: str):
        with self.lock:
            if self.data.get('default_model'):
                raise ValueError('已有默认模型，请使用确认切换。')
            if not isinstance(model_id, str) or not model_id.strip():
                raise ValueError('Invalid model choice')
            next_data = dict(self.data, first_candidate=model_id, status='in_progress')
            atomic_json(self.path, next_data)
            self.data = next_data

    def may_auto_activate(self, model_id: str) -> bool:
        with self.lock:
            return not self.data.get('default_model') and self.data.get('first_candidate') == model_id

    def activated(self, model_id: str):
        with self.lock:
            next_data = dict(self.data, default_model=model_id, first_candidate=None)
            atomic_json(self.path, next_data)
            self.data = next_data
