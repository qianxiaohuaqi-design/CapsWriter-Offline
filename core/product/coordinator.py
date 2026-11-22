"""Application-owned model downloads, task queue, setup and switching.

The GUI is a client. Closing it does not stop this process or create duplicate
jobs. No external service, credentials or privileged system service is required.
"""
from __future__ import annotations
import hmac
import json
import os
import secrets
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .paths import bootstrap, data_root, model_root
from .storage import OwnerLock, atomic_json, read_json
from .status import health_snapshot, read_status, process_birth
from .onboarding import SetupStore
from .models import ModelCatalog
from .downloads import DownloadManager
from .hardware import inspect_hardware
from .tasks import TranscriptionQueue


class Coordinator:
    def __init__(self, root=None, models=None):
        self.root = Path(root or data_root())
        self.model_root = Path(models or model_root())
        self.catalog = ModelCatalog(manual_registry_path=self.root / 'product' / 'manual-models.json')
        from web_gui.config_manager import ConfigManager
        self.config = ConfigManager
        configured = ConfigManager.get_server_var('model_type', 'sensevoice')
        inventory = self.catalog.list_models(self.model_root)
        existing = next((m['id'] for m in inventory if m['engine'] == configured and m['installed']), None)
        if not existing:
            # Literal custom paths express an established choice even if the
            # removable drive is currently absent; that is a repair case.
            custom_paths = {'sensevoice': ('SenseVoiceArgs', 'encoder_path'),
                            'fun_asr_nano': ('FunASRNanoGGUFArgs', 'encoder_onnx_path'),
                            'qwen_asr': ('Qwen3ASRGGUFArgs', 'model_dir'),
                            'paraformer': ('ParaformerArgs', 'paraformer')}
            selected_class, path_key = custom_paths.get(configured, ('', ''))
            explicit_path = ConfigManager.get_server_class_var(selected_class, path_key, None) if selected_class else None
            if isinstance(explicit_path, str) and explicit_path.strip():
                existing = next((m['id'] for m in inventory if m['engine'] == configured), configured)
        # Preserve explicit legacy setup even when its files are temporarily absent.
        if not existing and any((self.root / 'web_gui' / name).exists()
                                for name in ('gui_settings.json', 'input_history.jsonl')):
            existing = next((m['id'] for m in inventory if m['engine'] == configured), configured)
        self.setup = SetupStore(self.root, existing)
        self.downloads = DownloadManager(self.root, self.model_root, self.catalog)
        self._lock = threading.RLock()
        self.switch = {'state': 'idle'}
        self.shutting_down = False
        self.stopped = threading.Event()
        self.hardware = inspect_hardware(self.model_root)
        self.tasks = TranscriptionQueue(self.root, self.current_model, self.can_start_file, dispatch_lock=self._lock)
        self._auto_attempted = set()
        self._thread = threading.Thread(target=self._monitor, name='cw-product-monitor', daemon=True)
        self._thread.start()

    def _catalog_id(self, identity):
        for item in self.catalog.list_models(self.model_root):
            if identity in (item['id'], item['engine']):
                return item['id']
        return identity

    def current_model(self):
        health = health_snapshot(self.root)
        if self.switch.get('state') == 'loading' or not health['model_loaded']:
            return None
        return self._catalog_id(health.get('model_id'))

    def can_start_file(self):
        client = read_status('client', self.root)
        return (not self.shutting_down and not client.get('recording')
                and not client.get('pending_results') and self.switch.get('state') != 'loading')

    def snapshot(self):
        from .data_safety import pending_imports
        return {'model_recovery': pending_imports(self.root / 'product' / 'model-switch-backups'),
                'setup': self.setup.snapshot(), 'models': self.catalog.list_models(self.model_root),
                'hardware': self.hardware, 'downloads': self.downloads.snapshot(),
                'tasks': self.tasks.snapshot(), 'tasks_busy': self.tasks.busy(),
                'health': health_snapshot(self.root), 'switch': dict(self.switch),
                'data_root': str(self.root), 'model_root': str(self.model_root),
                'shutting_down': self.shutting_down, 'background_owner': os.getpid()}

    def _monitor(self):
        from web_gui import process_manager
        while not self.stopped.wait(0.5):
            if self.shutting_down:
                continue
            try:
                # Client starts only after the actual current Worker reports ready.
                if health_snapshot(self.root)['model_loaded'] and self.switch.get('state') != 'loading':
                    if not process_manager.read_alive_pid(process_manager.CLIENT_PID_FILE):
                        # Bounded retries avoid a microphone/driver crash loop.
                        tries = getattr(self, '_client_attempts', 0)
                        if tries < 2:
                            self._client_attempts = tries + 1
                            process_manager.launch_client()
                for job in self.downloads.snapshot():
                    job_id = job['download_id']
                    if (job['status'] == 'completed' and job_id not in self._auto_attempted
                            and self.setup.may_auto_activate(job['model_id'])):
                        self._auto_attempted.add(job_id)
                        self.activate(job['model_id'], confirmed=False)
            except Exception as error:
                self.switch = {'state': 'error', 'message': str(error)}

    def activate(self, model_id, *, confirmed):
        with self._lock:
            self.catalog.get(model_id)
            if self.shutting_down or self.switch.get('state') == 'loading':
                raise ValueError('已有模型操作正在进行，请稍候。')
            client = read_status('client', self.root)
            if self.tasks.busy() or client.get('recording') or client.get('pending_results'):
                raise ValueError('正在转写、录音或处理听写结果，请等任务结束/安全停止后再切换。')
            if not confirmed and not self.setup.may_auto_activate(model_id):
                raise ValueError('已有默认模型，切换需要明确确认。')
            self.catalog.verify_installation(model_id, self.model_root)
            request_id = uuid.uuid4().hex
            self.switch = {'state': 'loading', 'model_id': model_id, 'request_id': request_id,
                           'message': '正在加载模型；加载成功前不显示就绪。'}
            self._switch_thread = threading.Thread(target=self._switch_worker, args=(model_id, request_id), daemon=True)
            self._switch_thread.start()
            return dict(self.switch)

    def _wait_ready(self, model_id, timeout=180):
        from web_gui import process_manager
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not self.shutting_down:
            record = read_status('server', self.root)
            if record.get('ready') and self._catalog_id(record.get('model_id')) == model_id:
                return True
            pid = process_manager.read_alive_pid(process_manager.SERVER_PID_FILE)
            if not pid:
                return False
            time.sleep(0.25)
        return False

    def _commit_selection(self, model_id):
        from .data_safety import commit_transaction, fingerprint, replace_assignment, atomic_write
        server_path = self.root / 'config_server.py'
        selection_path = self.root / 'product' / 'model-selection.json'
        with self.setup.lock:
            next_setup = dict(self.setup.data, default_model=model_id, first_candidate=None)
            server_text = server_path.read_text(encoding='utf-8')
            engine = self.catalog.get(model_id)['engine']
            server_text = replace_assignment(server_text, 'model_type', engine, 'ServerConfig')
            if (self.setup.data.get('default_model') is None
                    and self.setup.data.get('first_candidate') == model_id and engine == 'sensevoice'):
                # Persist the same reliable first-use backend as the candidate
                # process. Existing users' advanced choices stay authoritative.
                server_text = replace_assignment(server_text, 'onnx_provider', 'CPU', 'SenseVoiceArgs')
            staged = {
                server_path: server_text,
                selection_path: json.dumps({'selected_model': model_id}, ensure_ascii=False, indent=2),
                self.setup.path: json.dumps(next_setup, ensure_ascii=False, indent=2),
            }
            expected = {str(path): fingerprint(path) for path in staged}
            commit_transaction(staged, self.root / 'product' / 'model-switch-backups', expected, writer=atomic_write)
            # In-memory intent changes only after all three files read back.
            self.setup.data = next_setup

    def _switch_worker(self, model_id, request_id):
        from web_gui import process_manager
        old = self.setup.snapshot().get('default_model')
        committed = False
        try:
            for pidfile in (process_manager.CLIENT_PID_FILE, process_manager.SERVER_PID_FILE):
                pid = process_manager.read_alive_pid(pidfile)
                if pid and not process_manager.stop_pid(pid):
                    raise RuntimeError('无法安全停止应用自己的识别组件。')
            ok, message = process_manager.launch_server(model_id=model_id)
            if not ok or not self._wait_ready(model_id):
                raise RuntimeError(message if not ok else '模型未能成功加载，请检查文件或运行依赖。')
            if self.shutting_down:
                return
            self._commit_selection(model_id)
            committed = True
            self.switch = {'state': 'ready', 'model_id': model_id, 'request_id': request_id,
                           'message': '模型已成功加载。'}
            self._client_attempts = 0
        except Exception as error:
            pid = process_manager.read_alive_pid(process_manager.SERVER_PID_FILE)
            if pid:
                process_manager.stop_pid(pid)
            restored = False
            if old and not self.shutting_down:
                try:
                    # No candidate override: respect the original selection OR
                    # every original custom per-file model path in legacy config.
                    ok, _ = process_manager.launch_server(model_id=None)
                    restored = ok and self._wait_ready(old)
                except Exception:
                    restored = False
            self.switch = {'state': 'error', 'request_id': request_id, 'model_id': model_id,
                           'old_model_restored': bool(restored),
                           'message': str(error) + (' 原模型已重新验证恢复。' if restored else ' 请检查模型中心后重试。')}
            if restored:
                try:
                    process_manager.launch_client()
                except Exception:
                    pass
        if committed:
            try:
                ok, message = process_manager.launch_client()
                if not ok:
                    self.switch['message'] += ' ' + message
            except Exception:
                self.switch['message'] += ' 听写客户端未能启动，请在诊断页重试。'

    def shutdown(self):
        with self._lock:
            client = read_status('client', self.root)
            if client.get('recording') or client.get('pending_results'):
                raise ValueError('正在录音或处理听写结果，请先等本次听写完成，再选择完全退出。')
            if not self.shutting_down:
                self.shutting_down = True
                threading.Thread(target=self._drain, daemon=True).start()
            return {'draining': not self.stopped.is_set(), 'message': '正在保存任务并安全退出。'}

    def _drain(self):
        # Full exit waits for actual cancellation acknowledgement/final boundary.
        self.downloads.shutdown(timeout=30)
        switch_thread = getattr(self, '_switch_thread', None)
        if switch_thread and switch_thread is not threading.current_thread():
            switch_thread.join()
        while not self.tasks.shutdown(timeout=0.5):
            time.sleep(0.1)
        while not self.downloads.shutdown(timeout=0.5):
            time.sleep(0.1)
        self.stopped.set()

    def action(self, data):
        action = data.get('action')
        if action == 'snapshot':
            return self.snapshot()
        if action == 'shutdown':
            return self.shutdown()
        if self.shutting_down:
            raise ValueError('应用正在安全退出，请等待当前任务停止。')
        if action == 'setup':
            return self.setup.update(**data.get('values', {}))
        if action == 'download':
            model_id = data['model_id']
            if data.get('license_accepted') is not True:
                raise ValueError('请先确认下载大小、目录与模型许可。')
            with self._lock:
                job = self.downloads.start(model_id, license_accepted=True)
                if data.get('first_setup') and not self.setup.snapshot().get('default_model'):
                    self.setup.select_first(model_id)
                return job
        if action in ('pause_download', 'resume_download', 'cancel_download'):
            fn = {'pause_download': self.downloads.pause, 'resume_download': self.downloads.resume,
                  'cancel_download': self.downloads.cancel}[action]
            return fn(data['download_id'])
        if action == 'activate':
            if data.get('first_setup') and not self.setup.snapshot().get('default_model'):
                self.setup.select_first(data['model_id'])
            return self.activate(data['model_id'], confirmed=data.get('confirmed') is True)
        if action == 'submit_task':
            return self.tasks.submit(data['path'], data.get('model_id'), data.get('parameters'))
        if action == 'retry_task':
            return self.tasks.retry(data['task_id'])
        if action == 'cancel_task':
            return self.tasks.cancel(data['task_id'])
        if action == 'import_manual':
            if data.get('confirmed') is not True:
                raise ValueError('请明确确认使用本地模型目录。')
            with self._lock:
                client = read_status('client', self.root)
                if (self.tasks.busy() or client.get('recording') or client.get('pending_results')
                        or self.switch.get('state') == 'loading'):
                    raise ValueError('请等当前任务完成后再导入模型。')
                model_id = data['model_id']
                if self.setup.snapshot().get('default_model') == model_id:
                    raise ValueError('此项是已有默认模型。请保留其原路径，先通过“按现有配置重试”修复；不要覆盖既有选择。')
                item = self.catalog.register_manual(model_id, data['directory'], model_root=self.model_root)
                return item
        if action == 'recover_model_switch':
            from .data_safety import recover_transaction
            with self._lock:
                client = read_status('client', self.root)
                if (self.tasks.busy() or client.get('recording') or client.get('pending_results')
                        or self.switch.get('state') == 'loading'):
                    raise ValueError('有运行任务，请先等待或安全停止。')
                allowed = {self.root / 'config_server.py', self.root / 'product' / 'model-selection.json', self.setup.path}
                recover_transaction(Path(data['journal']), self.root / 'product' / 'model-switch-backups', allowed)
                self.setup = SetupStore(self.root)
                return {'message': '已恢复模型切换前配置，可按原设置重试加载。'}
        if action == 'start_components':
            from web_gui import process_manager
            with self._lock:
                if self.switch.get('state') == 'loading':
                    raise ValueError('模型切换正在进行，请等待加载结果。')
                self._client_attempts = 0
                if (not health_snapshot(self.root)['model_loaded']
                        and process_manager.read_alive_pid(process_manager.SERVER_PID_FILE)):
                    result = process_manager.restart_server()
                else:
                    result = process_manager.launch_missing_components()
                return dict(zip(('ok', 'message'), result))
        raise ValueError('未知操作，请刷新控制中心。')


def serve():
    root = bootstrap()
    lock = OwnerLock(root / 'runtime' / 'coordinator.lock')
    if not lock.acquire():
        return
    # Only the singleton owner can retire an earlier exit acknowledgement.
    (root / 'runtime' / 'exit-ready.json').unlink(missing_ok=True)
    controller = None
    server = None
    descriptor_path = root / 'runtime' / 'coordinator.json'
    token = secrets.token_urlsafe(32)
    try:
        controller = Coordinator()
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                if self.path != '/api' or self.headers.get('Origin') or not hmac.compare_digest(
                        self.headers.get('Authorization', ''), 'Bearer ' + token):
                    self.send_error(403)
                    return
                try:
                    size = int(self.headers.get('Content-Length', '0'))
                    if not 0 < size <= 1024 * 1024:
                        self.send_error(413)
                        return
                    request = json.loads(self.rfile.read(size))
                    result = {'ok': True, 'data': controller.action(request)}
                except Exception as error:
                    result = {'ok': False, 'message': str(error)}
                encoded = json.dumps(result, ensure_ascii=False, allow_nan=False).encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Content-Length', str(len(encoded)))
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                self.wfile.write(encoded)
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.timeout = 0.25
        atomic_json(descriptor_path, {'pid': os.getpid(), 'birth': process_birth(os.getpid()),
                                     'port': server.server_port, 'token': token})
        os.chmod(descriptor_path, 0o600)
        while not controller.stopped.is_set():
            server.handle_request()
    finally:
        if server:
            server.server_close()
        if controller and not controller.stopped.is_set():
            controller.shutdown()
            controller.stopped.wait(30)
        try:
            info = read_json(descriptor_path, {})
            if info.get('token') == token:
                descriptor_path.unlink(missing_ok=True)
        except Exception:
            pass
        # Signal launcher/GUI only after durable task shutdown, never on a crash.
        if controller and controller.stopped.is_set():
            atomic_json(root / 'runtime' / 'exit-ready.json', {'pid': os.getpid(), 'at': time.time()})
        lock.release()
