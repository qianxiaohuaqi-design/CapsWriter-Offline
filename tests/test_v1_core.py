"""Risk-boundary checks for writable paths, observations, setup, and cleanup.

Only temporary directories are used. Frozen/Portable path selection is simulated;
these tests do not claim a Windows installation or a physical microphone check.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from core.product import cleanup, onboarding, paths, status
from core.product.onboarding import SetupStore
from core.product.storage import atomic_json


class TemporaryDataTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()


class DataPathBoundaryTests(TemporaryDataTests):
    def frozen_environment(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.resources = self.root / 'bundle'
        self.application = self.root / 'application'
        self.local = self.root / 'profile'
        self.resources.mkdir()
        self.application.mkdir()
        stack.enter_context(patch.dict(os.environ, {'LOCALAPPDATA': str(self.local)}, clear=True))
        stack.enter_context(patch.object(sys, 'frozen', True, create=True))
        stack.enter_context(patch.object(sys, '_MEIPASS', str(self.resources), create=True))
        stack.enter_context(patch.object(sys, 'executable', str(self.application / 'CapsWriter.exe')))
        return stack

    def test_installation_portable_and_explicit_overrides_remain_separate(self):
        self.frozen_environment()
        self.assertEqual(paths.resource_root(), self.resources)
        self.assertEqual(paths.data_root(), self.local / 'CapsWriter')
        self.assertEqual(paths.model_root(), self.local / 'CapsWriter' / 'models')
        (self.application / 'portable.flag').touch()
        self.assertEqual(paths.data_root(), self.application / 'data')
        self.assertEqual(paths.model_root(), self.application / 'models')
        with patch.dict(os.environ, {'CAPSWRITER_DATA_DIR': str(self.root / 'isolated-data')}):
            self.assertEqual(paths.data_root(), self.root / 'isolated-data')
            self.assertEqual(paths.model_root(), self.root / 'isolated-data' / 'models')
            with patch.dict(os.environ, {'CAPSWRITER_MODEL_DIR': str(self.root / 'isolated-models')}):
                self.assertEqual(paths.model_root(), self.root / 'isolated-models')
        self.assertFalse((self.application / 'data').exists(), 'Resolving a path must not migrate or create user data.')

    def test_bootstrap_preserves_existing_bytes_and_uses_clean_defaults(self):
        self.frozen_environment()
        defaults = self.resources / 'packaging' / 'release_defaults'
        (defaults / 'web_gui').mkdir(parents=True)
        (defaults / 'config_client.py').write_bytes(b'new default configuration\n')
        (defaults / 'hot.txt').write_bytes(b'clean shipped hotwords\n')
        (defaults / 'web_gui' / 'private_config.json').write_bytes(b'{"llm_api_keys": {}}')
        (self.resources / 'hot.txt').write_bytes(b'source-specific hotwords must not leak\n')
        (self.resources / 'models').mkdir()
        (self.resources / 'models' / 'model.onnx').write_bytes(b'not a distributable default')
        root = paths.data_root()
        (root / 'web_gui').mkdir(parents=True)
        protected = {
            root / 'config_client.py': b'original user bytes\r\n',
            root / 'web_gui' / 'private_config.json': b'{"existing": "keep-this-local-value"}',
            root / 'unrelated.bin': b'unknown user data',
        }
        for file, content in protected.items():
            file.write_bytes(content)
        self.assertEqual(paths.bootstrap(), root)
        paths.bootstrap()
        for file, content in protected.items():
            self.assertEqual(file.read_bytes(), content)
        self.assertEqual((root / 'hot.txt').read_bytes(), b'clean shipped hotwords\n')
        self.assertFalse((root / 'models' / 'model.onnx').exists())

    def test_frozen_missing_clean_default_never_copies_source_private_data(self):
        self.frozen_environment()
        (self.resources / 'web_gui').mkdir()
        (self.resources / 'web_gui' / 'private_config.json').write_text('{"secret": "source-only"}')
        paths.bootstrap()
        self.assertFalse((paths.data_root() / 'web_gui' / 'private_config.json').exists())

    def test_editable_config_is_read_as_data_without_executing_code_or_model_paths(self):
        sentinel = self.root / 'executed.txt'
        config = self.root / 'config_client.py'
        config.write_text(
            f"__import__('pathlib').Path({str(sentinel)!r}).write_text('bad')\n"
            "class ClientConfig:\n"
            "    output_destination = 'overlay_preview'\n"
            f"    language = __import__('pathlib').Path({str(sentinel)!r}).write_text('bad')\n"
            "    invented_field = 100\n"
            "class ModelPaths:\n"
            "    encoder = 'untrusted replacement'\n", encoding='utf-8')
        client = type('ClientConfig', (), {'output_destination': 'typing', 'language': 'zh'})
        models = type('ModelPaths', (), {'encoder': 'computed trusted path'})
        namespace = {'__file__': str(self.root / 'bundle' / 'config_client.py'),
                     'ClientConfig': client, 'ModelPaths': models}
        with patch.dict(os.environ, {'CAPSWRITER_DATA_DIR': str(self.root)}):
            paths.apply_config_overrides('config_client', namespace)
        self.assertEqual(client.output_destination, 'overlay_preview')
        self.assertEqual(client.language, 'zh')
        self.assertFalse(hasattr(client, 'invented_field'))
        self.assertEqual(models.encoder, 'computed trusted path')
        self.assertFalse(sentinel.exists())


class RuntimeObservationBoundaryTests(TemporaryDataTests):
    def setUp(self):
        super().setUp()
        self.now = time.time()
        self.pid = os.getpid()
        self.birth = status.process_birth(self.pid)
        self.assertIsNotNone(self.birth, 'Tests require the declared psutil dependency.')

    def write_observation(self, component_name, **values):
        record = {'component': component_name, 'instance_id': 'temporary-test-instance',
                  'pid': self.pid, 'birth': self.birth, 'updated_at': self.now,
                  'state': 'ready', 'ready': True}
        if component_name == 'server':
            record.update(worker_pid=self.pid, worker_birth=self.birth, model_id='fixture-model')
        record.update(values)
        atomic_json(self.root / 'runtime' / f'{component_name}.json', record)
        return record

    def test_ready_requires_fresh_matching_process_and_instance_observation(self):
        invalid = (
            {'component': 'another-component'}, {'instance_id': ''}, {'birth': self.birth + 1},
            {'updated_at': self.now - 10}, {'updated_at': self.now + 1},
        )
        with patch.object(status.time, 'time', return_value=self.now):
            for values in invalid:
                with self.subTest(values=values):
                    self.write_observation('server', **values)
                    observed = status.read_status('server', self.root)
                    self.assertFalse(observed['alive'])
                    self.assertFalse(observed['ready'])
            self.write_observation('server')
            self.assertTrue(status.read_status('server', self.root)['ready'])

    def test_server_parent_alive_does_not_hide_missing_or_reused_worker_identity(self):
        with patch.object(status.time, 'time', return_value=self.now):
            for values in ({'worker_pid': None}, {'worker_birth': self.birth + 1}):
                with self.subTest(values=values):
                    self.write_observation('server', **values)
                    observed = status.read_status('server', self.root)
                    self.assertTrue(observed['alive'])
                    self.assertFalse(observed['ready'])
                    self.assertEqual(observed['state'], 'error')

    def test_dictation_requires_observed_client_capabilities_and_idle_file_inference(self):
        self.write_observation('server')
        with patch.object(status.time, 'time', return_value=self.now):
            for field in ('connected', 'microphone_ready', 'hotkeys_ready'):
                values = dict(connected=True, microphone_ready=True, hotkeys_ready=True)
                values[field] = False
                self.write_observation('client', **values)
                self.assertFalse(status.health_snapshot(self.root)['can_dictate'], field)
            self.write_observation('client', connected=True, microphone_ready=True, hotkeys_ready=True)
            self.assertTrue(status.health_snapshot(self.root)['can_dictate'])
            self.write_observation('inference', state='busy', task_type='file')
            snapshot = status.health_snapshot(self.root)
            self.assertTrue(snapshot['model_loaded'])
            self.assertTrue(snapshot['file_inference_busy'])
            self.assertFalse(snapshot['can_dictate'])

    def test_corrupt_runtime_record_is_not_promoted_to_ready(self):
        runtime = self.root / 'runtime'
        runtime.mkdir()
        (runtime / 'server.json').write_text('{not valid json', encoding='utf-8')
        observed = status.read_status('server', self.root)
        self.assertFalse(observed['alive'])
        self.assertFalse(observed['ready'])


class SetupIntentBoundaryTests(TemporaryDataTests):
    def test_skipped_setup_restores_step_and_only_explicit_candidate_can_auto_activate(self):
        store = SetupStore(self.root)
        store.update(step=4, status='skipped')
        restored = SetupStore(self.root)
        self.assertEqual((restored.snapshot()['step'], restored.snapshot()['status']), (4, 'skipped'))
        self.assertFalse(restored.may_auto_activate('download-completed-without-selection'))
        restored.select_first('explicit-first')
        self.assertIsNone(restored.snapshot()['default_model'])
        restored = SetupStore(self.root)
        self.assertTrue(restored.may_auto_activate('explicit-first'))
        self.assertFalse(restored.may_auto_activate('other-download'))
        # Download or a failed load has not called activated; intent alone is not readiness.
        self.assertIsNone(restored.snapshot()['default_model'])

    def test_existing_default_is_not_reset_by_new_store_or_other_download(self):
        store = SetupStore(self.root, existing_model='existing-model')
        before = store.path.read_bytes()
        restored = SetupStore(self.root, existing_model='different-detection')
        self.assertEqual(restored.snapshot()['default_model'], 'existing-model')
        self.assertEqual(restored.snapshot()['status'], 'existing')
        self.assertFalse(restored.may_auto_activate('another-completed-download'))
        with self.assertRaises(ValueError):
            restored.select_first('another-completed-download')
        with self.assertRaises(ValueError):
            restored.update(default_model='another-completed-download')
        self.assertEqual(store.path.read_bytes(), before)

    def test_failed_setup_write_preserves_in_memory_and_persisted_intent(self):
        store = SetupStore(self.root)
        store.select_first('original-candidate')
        original_bytes = store.path.read_bytes()
        original = store.snapshot()
        for action in ('select_first', 'activated', 'update'):
            with self.subTest(action=action):
                store = SetupStore(self.root)
                with patch.object(onboarding, 'atomic_json', side_effect=OSError('disk full')):
                    with self.assertRaises(OSError):
                        if action == 'update':
                            store.update(status='complete', step=6)
                        else:
                            getattr(store, action)('uncommitted-model')
                self.assertEqual(store.snapshot(), original)
                self.assertEqual(store.path.read_bytes(), original_bytes)

    def test_corrupt_setup_remains_available_for_diagnostics_and_is_not_reset(self):
        file = self.root / 'product' / 'setup.json'
        file.parent.mkdir()
        for content in (b'{not json', b'{"schema": 99}',
                        b'{"schema": 1, "status": "new", "step": 99, "microphone_test": "not_tested"}'):
            with self.subTest(content=content):
                file.write_bytes(content)
                with self.assertRaises(ValueError):
                    SetupStore(self.root, existing_model='do-not-hide-corruption')
                self.assertEqual(file.read_bytes(), content)


class CleanupBoundaryTests(TemporaryDataTests):
    def test_cleanup_deletes_only_unchanged_explicit_selection_and_reports_changed_files(self):
        selected = self.root / 'selected.log'
        changed = self.root / 'changed.log'
        unrelated = self.root / 'unselected.log'
        for file in (selected, changed, unrelated):
            file.write_bytes(b'original')
        preview = cleanup.preview_files(self.root, [selected, changed, selected])
        self.assertEqual(len(preview), 2)
        old_stat = changed.stat()
        changed.write_bytes(b'modified')  # Same length, restore mtime: SHA must still catch it.
        os.utime(changed, ns=(old_stat.st_atime_ns, old_stat.st_mtime_ns))
        self.assertEqual(cleanup.delete_preview(self.root, preview), (1, 1))
        self.assertFalse(selected.exists())
        self.assertEqual(changed.read_bytes(), b'modified')
        self.assertEqual(unrelated.read_bytes(), b'original')

    def test_outside_paths_root_directories_and_forged_previews_cannot_delete_data(self):
        data = self.root / 'data'
        data.mkdir()
        sibling = self.root / 'data-other'
        sibling.mkdir()
        outside = sibling / 'important.txt'
        outside.write_text('preserve outside', encoding='utf-8')
        for file in (outside, data):
            with self.subTest(file=file):
                with self.assertRaises(ValueError):
                    cleanup.preview_files(data, [file])
        forged = cleanup.preview_files(sibling, [outside])
        self.assertEqual(cleanup.delete_preview(data, forged), (0, 1))
        self.assertEqual(outside.read_text(), 'preserve outside')

    def test_symlink_files_and_parent_directories_are_not_cleanup_targets(self):
        data, outside = self.root / 'data', self.root / 'outside'
        data.mkdir()
        outside.mkdir()
        important = outside / 'important.txt'
        important.write_text('preserve link target', encoding='utf-8')
        linked_file, linked_dir = data / 'linked.txt', data / 'linked-folder'
        try:
            linked_file.symlink_to(important)
            linked_dir.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest('Creating symlinks is unavailable in this environment; Windows link behavior remains unverified.')
        for file in (linked_file, linked_dir / 'important.txt'):
            with self.subTest(file=file):
                with self.assertRaises(ValueError):
                    cleanup.preview_files(data, [file])
        self.assertEqual(important.read_text(), 'preserve link target')

    def test_replacing_previewed_file_with_link_does_not_delete_link_target(self):
        selected = self.root / 'selected.log'
        target = self.root / 'other.log'
        selected.write_bytes(b'original')
        target.write_bytes(b'protected')
        preview = cleanup.preview_files(self.root, [selected])
        selected.unlink()
        try:
            selected.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest('Creating symlinks is unavailable in this environment.')
        self.assertEqual(cleanup.delete_preview(self.root, preview), (0, 1))
        self.assertEqual(target.read_bytes(), b'protected')
        self.assertTrue(selected.is_symlink())


if __name__ == '__main__':
    unittest.main()
