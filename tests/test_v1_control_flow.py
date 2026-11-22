"""Boundary tests for first-use intent, pending dictation and safe UI notices.

Native dialog event behavior is exercised using an event fixture. This is not
a Windows pywebview/window rendering test.
"""
from __future__ import annotations
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from core.product.coordinator import Coordinator
from core.product.onboarding import SetupStore
from core.product.diagnostics import diagnostic_summary
from core.product import native_window


class ControllerBoundaryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.controller = Coordinator.__new__(Coordinator)
        self.controller.root = self.root
        self.controller.setup = SetupStore(self.root)
        self.controller.model_root = self.root / 'models'
        self.controller._lock = threading.RLock()
        self.controller.shutting_down = False
        self.controller.switch = {'state': 'idle'}
        self.controller.tasks = Mock()
        self.controller.tasks.busy.return_value = False
        self.controller.catalog = Mock()
        self.controller.catalog.get.return_value = {'engine': 'sensevoice'}
        self.controller.downloads = Mock()
        self.controller.downloads.start.return_value = {'download_id': 'review-download'}
        self.server = self.root / 'config_server.py'
        self.server.write_text("class ServerConfig:\n    model_type = 'sensevoice'\n"
                               "class SenseVoiceArgs:\n    onnx_provider = 'DML'\n", encoding='utf-8')

    def test_first_success_persists_cpu_but_later_switch_preserves_advanced_setting(self):
        self.controller.setup.select_first('sensevoice-small-fp16')
        self.controller._commit_selection('sensevoice-small-fp16')
        self.assertIn("onnx_provider = 'CPU'", self.server.read_text())
        self.server.write_text(self.server.read_text().replace("onnx_provider = 'CPU'", "onnx_provider = 'DML'"))
        self.controller._commit_selection('sensevoice-small-fp16')
        self.assertIn("onnx_provider = 'DML'", self.server.read_text())
        restored = SetupStore(self.root)
        self.assertEqual(restored.snapshot()['default_model'], 'sensevoice-small-fp16')
        self.assertIsNone(restored.snapshot()['first_candidate'])

    def test_existing_user_reopening_wizard_can_download_without_changing_default(self):
        self.controller.setup.activated('old-model')
        result = self.controller.action({'action': 'download', 'model_id': 'new-model',
                                         'first_setup': True, 'license_accepted': True})
        self.assertEqual(result['download_id'], 'review-download')
        self.assertEqual(self.controller.setup.snapshot()['default_model'], 'old-model')
        self.assertFalse(self.controller.setup.may_auto_activate('new-model'))

    def test_unaccepted_or_failed_download_does_not_set_first_choice(self):
        before = self.controller.setup.path.read_bytes()
        with self.assertRaises(ValueError):
            self.controller.action({'action': 'download', 'model_id': 'new-model', 'first_setup': True})
        self.controller.downloads.start.assert_not_called()
        self.controller.downloads.start.side_effect = ValueError('not enough space')
        with self.assertRaises(ValueError):
            self.controller.action({'action': 'download', 'model_id': 'new-model',
                                    'first_setup': True, 'license_accepted': True})
        self.assertEqual(self.controller.setup.path.read_bytes(), before)
        self.assertIsNone(self.controller.setup.snapshot()['first_candidate'])

    def test_pending_dictation_protects_switch_exit_and_file_dispatch(self):
        with patch('core.product.coordinator.read_status', return_value={'pending_results': True, 'recording': False}):
            self.assertFalse(self.controller.can_start_file())
            with self.assertRaisesRegex(ValueError, '听写结果'):
                self.controller.activate('sensevoice-small-fp16', confirmed=True)
            with self.assertRaisesRegex(ValueError, '听写'):
                self.controller.shutdown()
        self.assertFalse(self.controller.shutting_down)

    def test_interrupted_configuration_import_blocks_loading_until_recovered(self):
        from core.product import configuration
        from core.product.storage import atomic_json
        journal = self.root / 'web_gui' / 'config_backups' / 'import-test' / 'manifest.json'
        atomic_json(journal, {'state': 'prepared', 'files': []})
        namespace = {'ServerConfig': SimpleNamespace(model_type='sensevoice')}
        with patch.object(configuration, 'data_root', return_value=self.root):
            configuration.apply_selected_model(namespace)
        self.assertIn('配置导入', namespace['ServerConfig']._product_model_error)
        self.assertEqual(json.loads(journal.read_text())['state'], 'prepared')
        self.assertFalse((self.root / 'product' / 'model-selection.json').exists())


class DialogEvent:
    def __init__(self):
        self.handler = None

    def __iadd__(self, handler):
        self.handler = handler
        return self


class NativeCloseNoticeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.root_patch = patch.object(native_window, 'data_root', return_value=self.root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        self.window = SimpleNamespace(events=SimpleNamespace(closing=DialogEvent()),
                                      create_confirmation_dialog=Mock(return_value=True))
        native_window.attach_close_notice(self.window)

    def test_first_close_can_be_cancelled_and_does_not_save_notice(self):
        self.window.create_confirmation_dialog.return_value = False
        with patch.object(native_window.bridge, 'request', return_value={'setup': {}}) as request:
            self.assertFalse(self.window.events.closing.handler())
            self.assertEqual(request.call_count, 1)

    def test_acknowledged_notice_is_saved_and_later_close_needs_no_prompt(self):
        with patch.object(native_window.bridge, 'request', return_value={'setup': {}}) as request:
            self.assertTrue(self.window.events.closing.handler())
            request.assert_called_with('setup', timeout=2, values={'background_notice_seen': True})
        self.window.create_confirmation_dialog.reset_mock()
        with patch.object(native_window.bridge, 'request', return_value={'setup': {'background_notice_seen': True}}):
            self.assertTrue(self.window.events.closing.handler())
        self.window.create_confirmation_dialog.assert_not_called()

    def test_offline_owner_does_not_get_a_false_background_promise(self):
        self.window.create_confirmation_dialog.return_value = False
        with patch.object(native_window.bridge, 'request', side_effect=RuntimeError('offline')):
            self.assertFalse(self.window.events.closing.handler())
        args = self.window.create_confirmation_dialog.call_args.args
        self.assertIn('无法确认', args[0])


class DiagnosticsBoundaryTests(unittest.TestCase):
    def test_report_never_exports_arbitrary_paths_text_errors_or_credentials(self):
        sensitive = 'PRIVATE-CONTENT-FIXTURE'
        result = diagnostic_summary({
            'health': {'model_loaded': True, 'latest_error': sensitive, 'server': {'api_key': sensitive}},
            'setup': {'default_model': sensitive, 'status': 'existing'},
            'tasks': [{'state': 'completed', 'source': {'path': sensitive}, 'text': sensitive},
                      {'state': sensitive}],
            'downloads': [{'status': 'paused', 'url': sensitive}], 'data_root': sensitive,
        })
        self.assertNotIn(sensitive, json.dumps(result))
        self.assertEqual(result['tasks'], {'completed': 1, 'unknown': 1})
        self.assertTrue(result['observations']['model_loaded'])


if __name__ == '__main__':
    unittest.main()
