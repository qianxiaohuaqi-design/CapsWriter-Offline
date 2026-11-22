"""Cross-component regressions for selection recovery and process ownership.

These tests do not start an ASR engine or access a user's model/configuration.
The IPC smoke test owns its subprocess and uses only a temporary data directory.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

import psutil

from core.product.coordinator import Coordinator
from core.product.data_safety import atomic_write
from core.product.onboarding import SetupStore
from core.product.storage import atomic_json
from web_gui import config_manager as config_module
from web_gui import process_manager


ROOT = Path(__file__).resolve().parents[1]
OLD_MODEL = 'sensevoice-small-fp16'
NEW_MODEL = 'qwen3-asr'


class SelectionRecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.selection = self.root / 'product' / 'model-selection.json'
        self.server_config = self.root / 'config_server.py'
        self.original_config = "class ServerConfig:\n    model_type = 'sensevoice'\n"
        self.server_config.write_text(self.original_config, encoding='utf-8')
        self.controller = Coordinator.__new__(Coordinator)
        self.controller.root = self.root
        self.controller.model_root = self.root / 'models'
        self.controller.setup = SetupStore(self.root, OLD_MODEL)
        self.controller.config = config_module.ConfigManager
        self.controller.catalog = Mock()
        self.controller.catalog.get.side_effect = lambda model: {
            'id': model, 'engine': 'qwen_asr' if model == NEW_MODEL else 'sensevoice'}
        self.controller.shutting_down = False
        self.controller.switch = {'state': 'loading'}
        self.controller._lock = threading.RLock()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for name, value in {
            'BASE_DIR': self.root,
            'CONFIG_SERVER_PATH': self.server_config,
            'GUI_SETTINGS_PATH': self.root / 'web_gui' / 'gui_settings.json',
            'CONFIG_BACKUP_DIR': self.root / 'web_gui' / 'config_backups',
        }.items():
            self.stack.enter_context(patch.object(config_module, name, value))
        self.stack.enter_context(patch.object(process_manager, 'read_alive_pid', return_value=None))
        self.stack.enter_context(patch.object(process_manager, 'stop_pid', return_value=True))
        self.stack.enter_context(patch.object(process_manager, 'launch_client', return_value=(True, 'started')))

    def test_failed_switch_restores_legacy_custom_model_without_catalog_path(self):
        """A working legacy model need not occupy a managed catalog directory."""
        service = {'identity': OLD_MODEL}

        def launch(model_id=None):
            if model_id is None:
                service['identity'] = OLD_MODEL  # original custom config remains valid
                return True, 'legacy paths loaded'
            service['identity'] = None
            if model_id == OLD_MODEL:
                return False, 'the legacy model is outside the managed catalog path'
            return True, 'new model starts but fails to load'

        self.controller._wait_ready = lambda model_id, **_: service['identity'] == model_id
        with patch.object(process_manager, 'launch_server', side_effect=launch):
            self.controller._switch_worker(NEW_MODEL, 'test-request')
        self.assertTrue(self.controller.switch['old_model_restored'], self.controller.switch)
        self.assertEqual(service['identity'], OLD_MODEL)
        self.assertFalse(self.selection.exists(), 'Legacy selection must remain legacy after a failed switch.')

    def test_setup_write_failure_restores_selection_config_and_setup(self):
        atomic_json(self.selection, {'selected_model': OLD_MODEL})
        original_selection = self.selection.read_bytes()
        original_setup = self.controller.setup.path.read_bytes()
        real_write = atomic_write
        failed = False

        def fail_new_setup_once(path, content):
            nonlocal failed
            real_write(path, content)
            if (Path(path) == self.controller.setup.path
                    and json.loads(content).get('default_model') == NEW_MODEL and not failed):
                failed = True
                raise OSError('simulated I/O failure after writing new setup')

        self.controller._wait_ready = lambda *args, **kwargs: True
        with patch.object(process_manager, 'launch_server', return_value=(True, 'ready')), \
             patch('core.product.data_safety.atomic_write', side_effect=fail_new_setup_once):
            self.controller._switch_worker(NEW_MODEL, 'test-request')
        self.assertTrue(failed, 'The fault must occur during the real file transaction.')
        self.assertEqual(self.controller.switch['state'], 'error')
        self.assertEqual(self.selection.read_bytes(), original_selection,
                         'A failed switch must not become the default on the next launch.')
        self.assertEqual(self.server_config.read_text(encoding='utf-8'), self.original_config)
        self.assertEqual(self.controller.setup.path.read_bytes(), original_setup)
        self.assertEqual(self.controller.setup.snapshot()['default_model'], OLD_MODEL)

    def test_invalid_configuration_cannot_report_success(self):
        atomic_json(self.selection, {'selected_model': OLD_MODEL})
        broken = "class UnrelatedConfig:\n    model_type = 'sensevoice'\n"
        self.server_config.write_text(broken, encoding='utf-8')
        self.controller._wait_ready = lambda *args, **kwargs: True
        with patch.object(process_manager, 'launch_server', return_value=(True, 'ready')):
            self.controller._switch_worker(NEW_MODEL, 'test-request')
        self.assertEqual(self.controller.switch['state'], 'error')
        self.assertEqual(json.loads(self.selection.read_text())['selected_model'], OLD_MODEL)
        self.assertEqual(self.server_config.read_text(encoding='utf-8'), broken)
        self.assertEqual(self.controller.setup.snapshot()['default_model'], OLD_MODEL)


class LauncherOwnershipTests(unittest.TestCase):
    @staticmethod
    def load_launcher():
        # Do not cache temporary-path globals in the real run_app module.
        spec = importlib.util.spec_from_file_location('capswriter_review_launcher', ROOT / 'run_app.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_reopen_owned_gui_does_not_spawn_a_second_gui(self):
        with tempfile.TemporaryDirectory() as temporary, \
             patch.dict(os.environ, {'CAPSWRITER_DATA_DIR': temporary}):
            launcher = self.load_launcher()
            with patch.object(launcher, 'GUI_PROCESS', None), \
                 patch.object(launcher, '_ensure_control_center_visible'), \
                 patch.object(process_manager, 'is_port_open', return_value=True), \
                 patch.object(process_manager, 'find_listening_pid', return_value=12345), \
                 patch.object(process_manager, 'is_process_alive', return_value=True), \
                 patch.object(launcher.subprocess, 'Popen') as spawn, \
                 patch.object(process_manager, 'write_pid'):
                launcher._launch_gui()
                self.assertEqual(spawn.call_count, 0, 'Reopening an owned GUI must reuse its existing process.')

    def test_close_reused_gui_without_local_popen_handle(self):
        with tempfile.TemporaryDirectory() as temporary, \
             patch.dict(os.environ, {'CAPSWRITER_DATA_DIR': temporary}):
            launcher = self.load_launcher()
            with patch.object(launcher, 'GUI_PROCESS', None), \
                 patch.object(process_manager, 'read_alive_pid', return_value=12345), \
                 patch.object(process_manager, 'stop_gui', return_value=True) as stop:
                launcher._stop_gui()
                self.assertEqual(stop.call_count, 1)

    def test_duplicate_launcher_completes_owned_window_restore_before_return(self):
        with tempfile.TemporaryDirectory() as temporary, \
             patch.dict(os.environ, {'CAPSWRITER_DATA_DIR': temporary}):
            launcher = self.load_launcher()
            observations = []
            with patch.object(launcher.sys, 'argv', ['run_app.py']), \
                 patch.object(process_manager, 'read_alive_control_pid', return_value=os.getpid() + 1000), \
                 patch.object(process_manager, 'read_alive_pid', return_value=12345), \
                 patch.object(process_manager, 'is_port_open', return_value=True), \
                 patch.object(process_manager, 'find_listening_pid', return_value=12345), \
                 patch.object(process_manager, 'is_process_alive', return_value=True), \
                 patch.object(launcher, '_show_control_center_window', side_effect=lambda: observations.append('restored') or True), \
                 patch.object(launcher, '_open_control_center_browser') as browser, \
                 patch.object(launcher.time, 'sleep'), \
                 patch.object(launcher.threading, 'Thread') as spawn_thread, \
                 patch.object(launcher.subprocess, 'Popen') as spawn_process:
                launcher.main()
                self.assertEqual(observations, ['restored'])
                self.assertEqual(spawn_thread.call_count, 0, 'A second launcher cannot leave the restore to a daemon thread.')
                self.assertEqual(spawn_process.call_count, 0)
                self.assertEqual(browser.call_count, 0)

    def test_duplicate_launcher_never_opens_unowned_port(self):
        with tempfile.TemporaryDirectory() as temporary, \
             patch.dict(os.environ, {'CAPSWRITER_DATA_DIR': temporary}):
            launcher = self.load_launcher()
            with patch.object(launcher.sys, 'argv', ['run_app.py']), \
                 patch.object(process_manager, 'read_alive_control_pid', return_value=os.getpid() + 1000), \
                 patch.object(process_manager, 'read_alive_pid', return_value=None), \
                 patch.object(process_manager, 'is_port_open', return_value=True), \
                 patch.object(process_manager, 'find_listening_pid', return_value=12345), \
                 patch.object(process_manager, 'is_process_alive', return_value=False), \
                 patch.object(launcher, '_show_control_center_window') as restore, \
                 patch.object(launcher, '_open_control_center_browser') as browser, \
                 patch.object(launcher.subprocess, 'Popen') as spawn:
                launcher.main()
                self.assertEqual(restore.call_count, 0)
                self.assertEqual(browser.call_count, 0)
                self.assertEqual(spawn.call_count, 0)

    def test_full_exit_waits_for_coordinator_drain_acknowledgement(self):
        with tempfile.TemporaryDirectory() as temporary, \
             patch.dict(os.environ, {'CAPSWRITER_DATA_DIR': temporary}):
            launcher = self.load_launcher()
            with patch.dict(sys.modules, {'core.tools.key_reset': Mock()}), \
                 patch.object(process_manager, 'stop_all', return_value={'waiting': True}) as stop:
                self.assertFalse(launcher._request_exit())
                self.assertFalse(launcher.SHOULD_EXIT.is_set())
                stop.return_value = {'waiting': False}
                self.assertTrue(launcher._request_exit())
                self.assertTrue(launcher.SHOULD_EXIT.is_set())


class RecoveryGUIAccessTests(unittest.TestCase):
    def test_interrupted_legacy_switch_blocks_asr_even_before_selection_exists(self):
        from core.product import configuration
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            atomic_json(directory / 'product' / 'model-switch-backups' / 'import-review' / 'manifest.json',
                        {'state': 'prepared', 'files': []})
            namespace = {'ServerConfig': type('ServerConfig', (), {'model_type': 'sensevoice'})}
            with patch.dict(os.environ, {}, clear=True), \
                 patch.object(configuration, 'data_root', return_value=directory), \
                 patch.object(configuration, 'model_root', return_value=directory / 'models'):
                configuration.apply_selected_model(namespace)
            self.assertTrue(getattr(namespace['ServerConfig'], '_product_model_error', ''),
                            'A partial config transaction cannot be used merely because selection is still absent.')

    def test_missing_selected_model_does_not_prevent_gui_import(self):
        """A removable/missing model must be repairable from the control center."""
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            atomic_json(directory / 'product' / 'model-selection.json', {'selected_model': OLD_MODEL})
            environment = dict(os.environ, CAPSWRITER_DATA_DIR=temporary,
                               CAPSWRITER_MODEL_DIR=str(directory / 'models'))
            environment.pop('CAPSWRITER_MODEL_ID', None)
            result = subprocess.run([sys.executable, '-c', 'import web_gui.app; print("GUI_IMPORT_OK")'],
                                    cwd=str(ROOT), env=environment, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr[-5000:])
            self.assertIn('GUI_IMPORT_OK', result.stdout)
            self.assertEqual(json.loads((directory / 'product' / 'model-selection.json').read_text()),
                             {'selected_model': OLD_MODEL}, 'Repair UI must not clear the user\'s selection.')


class CoordinatorIPCTests(unittest.TestCase):
    def test_real_coordinator_owns_authenticated_persistent_lifecycle(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            environment = dict(os.environ, CAPSWRITER_DATA_DIR=temporary,
                               CAPSWRITER_MODEL_DIR=str(directory / 'models'))
            environment.pop('CAPSWRITER_MODEL_ID', None)
            with (directory / 'child.log').open('wb') as log:
                child = subprocess.Popen([sys.executable, str(ROOT / 'run_app.py'), '--coordinator'],
                                         cwd=str(ROOT), env=environment, stdout=log, stderr=log)
                try:
                    descriptor = directory / 'runtime' / 'coordinator.json'
                    deadline = time.monotonic() + 15
                    while not descriptor.exists() and child.poll() is None and time.monotonic() < deadline:
                        time.sleep(0.05)
                    self.assertTrue(descriptor.exists(), (directory / 'child.log').read_text(errors='replace'))
                    info = json.loads(descriptor.read_text())
                    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

                    def request(action, authorized=True):
                        headers = {'Content-Type': 'application/json'}
                        if authorized:
                            headers['Authorization'] = 'Bearer ' + info['token']
                        req = urllib.request.Request(f"http://127.0.0.1:{info['port']}/api",
                            data=json.dumps({'action': action}).encode(), headers=headers)
                        with opener.open(req, timeout=5) as response:
                            return json.load(response)

                    with self.assertRaises(urllib.error.HTTPError) as rejected:
                        request('snapshot', authorized=False)
                    self.assertEqual(rejected.exception.code, 403)
                    first = request('snapshot')['data']
                    second = request('snapshot')['data']
                    # The Windows venv executable can be a redirector whose child
                    # runs Python. The authenticated descriptor names the actual owner.
                    spawned = psutil.Process(child.pid)
                    owned_pids = {spawned.pid, *(process.pid for process in spawned.children(recursive=True))}
                    self.assertIn(info['pid'], owned_pids)
                    self.assertEqual(first['background_owner'], info['pid'])
                    self.assertEqual(second['background_owner'], info['pid'])
                    self.assertEqual(first['setup']['status'], 'new')
                    self.assertFalse(first['health']['can_dictate'])
                    self.assertTrue(request('shutdown')['ok'])
                    child.wait(timeout=10)
                    self.assertEqual(child.returncode, 0, (directory / 'child.log').read_text(errors='replace'))
                    self.assertFalse(descriptor.exists())
                    self.assertTrue((directory / 'runtime' / 'exit-ready.json').exists())
                finally:
                    if child.poll() is None:
                        try:
                            descendants = psutil.Process(child.pid).children(recursive=True)
                            for process in reversed(descendants):
                                try:
                                    process.terminate()
                                except psutil.NoSuchProcess:
                                    pass
                            psutil.wait_procs(descendants, timeout=5)
                        finally:
                            child.terminate()
                            child.wait(timeout=10)


class ProcessDiscoveryTests(unittest.TestCase):
    def test_unrelated_command_arguments_do_not_resolve_paths(self):
        foreign = Mock(pid=os.getpid() + 1)
        foreign.cmdline.return_value = ['python.exe', 'other.py', 'network-share', 'input.txt']
        owned = Mock(pid=os.getpid() + 2)
        owned.cmdline.return_value = ['python.exe', str(ROOT / 'start_server.py')]
        foreign.info = {'cmdline': foreign.cmdline.return_value}
        owned.info = {'cmdline': owned.cmdline.return_value}
        foreign_same_name = Mock(pid=os.getpid() + 3)
        foreign_same_name.cmdline.return_value = ['python.exe', str(ROOT.parent / 'other/start_server.py')]
        foreign_same_name.info = {'cmdline': foreign_same_name.cmdline.return_value}
        original_resolve = Path.resolve

        def resolve(path, *args, **kwargs):
            self.assertNotIn('other', path.parts)
            return original_resolve(path, *args, **kwargs)

        with patch.object(process_manager, 'is_frozen_app', return_value=False), \
             patch.object(Path, 'resolve', resolve), \
             patch.object(psutil, 'process_iter', return_value=[foreign, foreign_same_name, owned]):
            self.assertEqual(process_manager.find_project_processes(('start_server.py',)), [owned.pid])
        foreign.cwd.assert_not_called()


class ConfigurationBoundaryTests(unittest.TestCase):
    @staticmethod
    def namespace():
        return {
            'ServerConfig': type('ServerConfig', (), {'model_type': 'sensevoice'}),
            'SenseVoiceArgs': type('SenseVoiceArgs', (), {
                'encoder_path': 'original-encoder.onnx', 'decoder_path': 'original-decoder.onnx',
                'tokenizer_path': 'original-tokenizer.model', 'onnx_provider': 'DML',
            }),
        }

    @staticmethod
    def selected_configuration():
        return {'model_type': 'sensevoice', 'classes': {'SenseVoiceArgs': {
            'encoder_path': 'candidate-encoder.onnx', 'decoder_path': 'candidate-decoder.onnx',
            'tokenizer_path': 'candidate-tokenizer.model', 'onnx_provider': 'CPU',
        }}, 'integrity': 'manual_unverified'}

    def test_invalid_selection_values_fail_closed_without_mutating_original_settings(self):
        from core.product import configuration
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            selection = root / 'product' / 'model-selection.json'
            for value in (None, False, 0, '', '   ', [OLD_MODEL], {'model': OLD_MODEL}):
                with self.subTest(value=value):
                    atomic_json(selection, {'selected_model': value})
                    original = selection.read_bytes()
                    namespace = self.namespace()
                    with patch.dict(os.environ, {}, clear=True), \
                         patch.object(configuration, 'data_root', return_value=root), \
                         patch.object(configuration, 'model_root', return_value=root / 'models'), \
                         patch('core.product.models.ModelCatalog') as catalog:
                        configuration.apply_selected_model(namespace)
                    self.assertTrue(namespace['ServerConfig']._product_model_error)
                    self.assertEqual(namespace['SenseVoiceArgs'].encoder_path, 'original-encoder.onnx')
                    self.assertEqual(namespace['SenseVoiceArgs'].onnx_provider, 'DML')
                    self.assertEqual(selection.read_bytes(), original)
                    catalog.assert_not_called()

    def test_old_manual_acceleration_is_preserved_without_selection_file(self):
        from core.product import configuration
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            SetupStore(root, OLD_MODEL)
            namespace = self.namespace()
            with patch.dict(os.environ, {'CAPSWRITER_MODEL_ID': OLD_MODEL}, clear=True), \
                 patch.object(configuration, 'data_root', return_value=root), \
                 patch.object(configuration, 'model_root', return_value=root / 'models'), \
                 patch('core.product.models.ModelCatalog') as catalog:
                catalog.return_value.configuration.return_value = self.selected_configuration()
                configuration.apply_selected_model(namespace)
            self.assertEqual(namespace['SenseVoiceArgs'].onnx_provider, 'DML')
            self.assertEqual(namespace['SenseVoiceArgs'].encoder_path, 'candidate-encoder.onnx')
            self.assertFalse(namespace['ServerConfig']._product_model_error)
            self.assertFalse((root / 'product' / 'model-selection.json').exists())

    def test_only_explicit_matching_first_candidate_uses_cpu(self):
        from core.product import configuration
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            setup = SetupStore(root)
            setup.select_first(OLD_MODEL)
            before = setup.path.read_bytes()
            namespace = self.namespace()
            with patch.dict(os.environ, {'CAPSWRITER_MODEL_ID': OLD_MODEL}, clear=True), \
                 patch.object(configuration, 'data_root', return_value=root), \
                 patch.object(configuration, 'model_root', return_value=root / 'models'), \
                 patch('core.product.models.ModelCatalog') as catalog:
                catalog.return_value.configuration.return_value = self.selected_configuration()
                configuration.apply_selected_model(namespace)
            self.assertEqual(namespace['SenseVoiceArgs'].onnx_provider, 'CPU')
            self.assertFalse(namespace['ServerConfig']._product_model_error)
            self.assertEqual(setup.path.read_bytes(), before, 'Candidate load is not activation commit.')

    def test_mismatched_or_missing_first_candidate_never_overrides_backend(self):
        from core.product import configuration
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            setup = SetupStore(root)
            for candidate in (None, NEW_MODEL):
                with self.subTest(candidate=candidate):
                    values = setup.snapshot()
                    values['first_candidate'] = candidate
                    atomic_json(setup.path, values)
                    namespace = self.namespace()
                    with patch.dict(os.environ, {'CAPSWRITER_MODEL_ID': OLD_MODEL}, clear=True), \
                         patch.object(configuration, 'data_root', return_value=root), \
                         patch.object(configuration, 'model_root', return_value=root / 'models'), \
                         patch('core.product.models.ModelCatalog') as catalog:
                        catalog.return_value.configuration.return_value = self.selected_configuration()
                        configuration.apply_selected_model(namespace)
                    self.assertEqual(namespace['SenseVoiceArgs'].onnx_provider, 'DML')


if __name__ == '__main__':
    unittest.main()
