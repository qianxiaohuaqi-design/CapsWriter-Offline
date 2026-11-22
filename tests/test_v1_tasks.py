import asyncio
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core.product.tasks import TaskConflict, TaskStore, TranscriptionQueue


class DurableTaskTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / '录音.wav'
        self.source.write_bytes(b'original audio bytes')
        self.sidecar = self.source.with_suffix('.srt')
        self.sidecar.write_text('existing subtitle', encoding='utf-8')
        self.store = TaskStore(self.root / 'data')
        self.owner = {'pid': 123, 'started': 1, 'instance': 'test-owner'}

    def create(self):
        return self.store.create(self.source, 'sensevoice', {'language': 'zh', 'file_save_txt': True})

    def complete(self, record, text='first result'):
        row = self.store.begin(record['id'], 'sensevoice', self.owner)
        output = Path(row['attempts'][-1]['output_dir']) / '录音.txt'
        output.write_text(text, encoding='utf-8')
        return self.store.finish(row['id'], row['attempt_id'], ok=True, message='完成', outputs={'txt': output}, text=text)

    def test_source_and_old_sidecar_preserved_and_restart_loads_results(self):
        done = self.complete(self.create())
        restarted = TaskStore(self.store.root).get(done['id'])
        self.assertEqual(restarted['state'], 'completed')
        self.assertEqual(Path(restarted['outputs']['txt']).read_text(), 'first result')
        self.assertEqual(self.source.read_bytes(), b'original audio bytes')
        self.assertEqual(self.sidecar.read_text(), 'existing subtitle')

    def test_repeated_click_has_one_active_task(self):
        first = self.create()
        second = self.create()
        self.assertEqual(first['id'], second['id'])
        begun = self.store.begin(first['id'], 'sensevoice', self.owner)
        with self.assertRaises(TaskConflict):
            self.store.begin(first['id'], 'sensevoice', self.owner)
        self.assertEqual(len(self.store.list()), 1)

    def test_retry_keeps_model_options_and_previous_attempt_outputs(self):
        first = self.complete(self.create())
        self.store.retry(first['id'], 'sensevoice')
        second = self.complete(first, 'second result')
        self.assertNotEqual(first['attempt_id'], second['attempt_id'])
        self.assertEqual(second['parameters']['language'], 'zh')
        self.assertEqual(Path(first['outputs']['txt']).read_text(), 'first result')
        self.store.finish(first['id'], first['attempt_id'], ok=False, message='late obsolete error')
        self.assertEqual(self.store.get(first['id'])['state'], 'completed')

    def test_retry_rejects_changed_source_and_different_model(self):
        done = self.complete(self.create())
        with self.assertRaises(TaskConflict):
            self.store.retry(done['id'], 'qwen')
        self.source.write_bytes(b'new audio')
        with self.assertRaises(TaskConflict):
            self.store.retry(done['id'], 'sensevoice')
        self.assertEqual(self.store.get(done['id'])['state'], 'completed')

    def test_cancel_waits_until_safe_completion_and_preserves_previous_result(self):
        first = self.complete(self.create())
        self.store.retry(first['id'], 'sensevoice')
        running = self.store.begin(first['id'], 'sensevoice', self.owner)
        self.assertEqual(self.store.cancel(first['id'])['state'], 'cancelling')
        self.store.finish(first['id'], running['attempt_id'], ok=True, message='drained')
        result = self.store.get(first['id'])
        self.assertEqual(result['state'], 'cancelled')
        self.assertEqual(result['outputs'], first['outputs'])

    def test_cancel_queued_does_not_start_and_owner_recovery_reattaches_live_task(self):
        queued = self.create()
        self.store.cancel(queued['id'])
        with self.assertRaises(TaskConflict):
            self.store.begin(queued['id'], 'sensevoice', self.owner)
        self.store.retry(queued['id'], 'sensevoice')
        running = self.store.begin(queued['id'], 'sensevoice', self.owner)
        self.assertEqual(self.store.recover_orphans(lambda _: True), 0)
        self.assertEqual(self.store.get(queued['id'])['attempt_id'], running['attempt_id'])
        self.assertEqual(self.store.recover_orphans(lambda _: False), 1)
        self.assertEqual(self.store.get(queued['id'])['state'], 'interrupted')

    def test_finish_rejects_source_path_as_output(self):
        row = self.store.begin(self.create()['id'], 'sensevoice', self.owner)
        with self.assertRaises(TaskConflict):
            self.store.finish(row['id'], row['attempt_id'], ok=True, message='bad', outputs={'txt': self.sidecar})
        self.assertEqual(self.sidecar.read_text(), 'existing subtitle')

    def test_background_queue_calls_real_adapter_contract_and_keeps_completed_rows(self):
        called = threading.Event()
        async def runner(path, *, progress, output_dir, options, cancel_requested):
            self.assertEqual(path, self.source)
            self.assertEqual(options, {'language': 'zh'})
            progress({'phase': 'saving', 'percent': 95, 'detail': 'saving'})
            output = output_dir / 'result.txt'
            output.write_text('output')
            called.set()
            return SimpleNamespace(ok=True, message='done', output_files={'txt': output}, text='output')
        queue = TranscriptionQueue(self.store.root, lambda: 'sensevoice', runner=runner)
        self.addCleanup(queue.shutdown)
        row = queue.submit(self.source, parameters={'language': 'zh'})
        self.assertTrue(called.wait(3))
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and queue.store.get(row['id'])['state'] != 'completed':
            time.sleep(0.02)
        self.assertEqual(queue.store.get(row['id'])['state'], 'completed')
        self.assertTrue(queue.shutdown())

    def test_dead_model_interrupts_awaiting_client_without_claiming_success(self):
        state = {'model': 'sensevoice'}
        started, released = threading.Event(), threading.Event()
        async def runner(*args, **kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                released.set()
        with patch('core.product.tasks.MODEL_LOST_GRACE_SECONDS', 0.01):
            queue = TranscriptionQueue(self.store.root, lambda: state['model'], runner=runner)
            self.addCleanup(queue.shutdown)
            row = queue.submit(self.source, parameters={})
            self.assertTrue(started.wait(3))
            state['model'] = None
            self.assertTrue(released.wait(3))
            self.assertTrue(queue.shutdown())
            result = queue.store.get(row['id'])
            self.assertEqual(result['state'], 'interrupted')
            self.assertEqual(result['outputs'], {})

    def test_busy_reservation_covers_source_verification_before_dispatch(self):
        started, release = threading.Event(), threading.Event()
        async def runner(*args, **kwargs):
            return SimpleNamespace(ok=False, message='test end', output_files={}, text='')
        queue = TranscriptionQueue(self.store.root, lambda: 'sensevoice', runner=runner)
        self.addCleanup(queue.shutdown)
        original = queue.store.validate_source
        def delayed(record):
            started.set()
            release.wait(3)
            original(record)
        with patch.object(queue.store, 'validate_source', side_effect=delayed):
            queue.submit(self.source, parameters={})
            self.assertTrue(started.wait(3))
            self.assertTrue(queue.busy())
            release.set()
            self.assertTrue(queue.shutdown())


if __name__ == '__main__':
    unittest.main()
