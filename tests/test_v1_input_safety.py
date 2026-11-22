"""Pure behavior tests; these do not claim Windows keyboard/IME acceptance."""
import os
import asyncio
import sys
import unittest
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

os.environ.setdefault('PYNPUT_BACKEND', 'dummy')
from core.product import focus
from core.ui.modern_overlay.pill_overlay import FloatingPillWindow


class FocusProtectionTests(unittest.TestCase):
    def test_original_target_guard_stops_after_focus_changes(self):
        first = {'hwnd': 12, 'pid': 23, 'started': 4}
        other = {'hwnd': 90, 'pid': 23, 'started': 4}
        with patch.object(focus, '_capture_window', return_value=first):
            target = focus.capture_input_target()
            with focus.output_target(target):
                self.assertTrue(focus.target_allows_output())
                with patch.object(focus, '_capture_window', return_value=other):
                    self.assertFalse(focus.target_allows_output())
                # Focusing back does not resume a half-written sentence silently.
                self.assertFalse(focus.target_allows_output())

    def test_reused_pid_or_new_recording_invalidates_old_output(self):
        first = {'hwnd': 12, 'pid': 23, 'started': 4}
        with patch.object(focus, '_capture_window', return_value=first):
            target = focus.capture_input_target()
            with focus.output_target(target):
                with patch.object(focus, '_capture_window', return_value={**first, 'started': 9}):
                    self.assertFalse(focus.target_allows_output())
            previous = focus.capture_input_target()
            focus.capture_input_target()
            with focus.output_target(previous):
                self.assertFalse(focus.target_allows_output())

    def test_long_text_checks_again_between_keyboard_batches(self):
        first = {'hwnd': 12, 'pid': 23, 'started': 4}
        written = []
        with patch.object(focus, '_capture_window', return_value=first) as capture:
            target = focus.capture_input_target()
            def write(text):
                written.append(text)
                capture.return_value = {'hwnd': 99, 'pid': 99, 'started': 1}
            with focus.output_target(target):
                self.assertFalse(focus.guarded_write('x' * 100, write))
        self.assertEqual(written, ['x' * 24])

    def test_missing_original_target_never_types(self):
        written = []
        with focus.output_target(None):
            self.assertFalse(focus.guarded_write('text', written.append))
        self.assertEqual(written, [])


class FakeText:
    def __init__(self, value):
        self.value = value
        self.modified = True
    def edit_modified(self, value=None):
        if value is not None:
            self.modified = value
        return self.modified
    def get(self, *_):
        return self.value


class OverlayProtectionTests(unittest.TestCase):
    def make_overlay(self):
        obj = object.__new__(FloatingPillWindow)
        obj.state = 'preview'
        obj._preview_edited = False
        obj._preview_user_editing = True
        obj._preview_last_text = 'original'
        obj.text = 'original'
        obj._preview_remaining_seconds = 2
        obj._preview_close_after_id = None
        obj.root = SimpleNamespace(after_cancel=lambda *_: None)
        obj.preview_text = FakeText('  编辑后的文字\n第二行  ')
        obj.preview_status = SimpleNamespace(config=lambda **_: None)
        obj._preview_config = {}
        return obj

    def test_edit_preserves_exact_text_and_stops_timer_after_focus_out(self):
        obj = self.make_overlay()
        obj._on_preview_modified()
        self.assertTrue(obj._preview_edited)
        self.assertIsNone(obj._preview_remaining_seconds)
        self.assertEqual(obj.text, '  编辑后的文字\n第二行  ')
        obj._set_clipboard = lambda _: True
        obj._resume_preview_autoclose()
        self.assertTrue(obj._preview_edited)
        self.assertIsNone(obj._preview_remaining_seconds)

    def test_revert_to_original_and_empty_are_still_edits(self):
        obj = self.make_overlay()
        obj._on_preview_modified()
        for value in ('original', ''):
            obj.preview_text.value = value
            obj.preview_text.modified = True
            obj._on_preview_modified()
            self.assertTrue(obj._preview_edited)
            self.assertEqual(obj.text, value)

    def test_late_update_or_recording_does_not_replace_edit(self):
        obj = self.make_overlay()
        obj._on_preview_modified()
        before = obj.text
        obj._do_update_text('late AI', True)
        obj._do_show_preview('late final')
        obj._do_show('new recording')
        self.assertEqual(obj.text, before)

    def test_auto_hide_and_paste_without_target_keep_text_visible(self):
        obj = self.make_overlay()
        obj._on_preview_modified()
        hidden = []
        obj.hide = lambda: hidden.append(True)
        obj._auto_hide_preview()
        obj._target_identity = None
        obj._paste_and_hide()
        self.assertEqual(hidden, [])


class PendingResultLifecycleTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # The audio package also imports the stream module. These lifecycle
        # tests never open a device: isolate that native import for machines
        # without PortAudio, and restore the original module table afterwards.
        hardware = ModuleType('sounddevice')
        hardware.InputStream = object
        hardware.CallbackFlags = object
        isolation = patch.dict(sys.modules, {'sounddevice': hardware})
        isolation.start()
        cls.addClassCleanup(isolation.stop)

    @staticmethod
    def make_app():
        state = SimpleNamespace(input_targets={'other-ai': {'epoch': 'keep'}},
                                processing_input_tasks={'other-ai'},
                                pending_input_target=None, queue_in=asyncio.Queue(),
                                stop_recording=Mock(), pop_audio_file=Mock(), websocket=None)
        ws = SimpleNamespace(is_connected=True, send=AsyncMock(return_value=True))
        return SimpleNamespace(state=state, ws=ws)

    async def test_cancelled_recording_clears_only_its_unfinished_marker(self):
        from core.client.audio import recorder
        app = self.make_app()
        session = recorder.AudioRecorder(app)
        with patch.object(recorder.Config, 'save_audio', False):
            task = asyncio.create_task(session.record_and_send())
            await asyncio.sleep(0)
            self.assertIn(session.task_id, app.state.input_targets)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertNotIn(session.task_id, app.state.input_targets)
        self.assertIn('other-ai', app.state.input_targets)

    async def test_recording_error_does_not_leave_permanent_pending_result(self):
        from core.client.audio import recorder
        app = self.make_app()
        app.state.queue_in = SimpleNamespace(get=AsyncMock(side_effect=OSError('capture stopped')))
        session = recorder.AudioRecorder(app)
        with patch.object(recorder.Config, 'save_audio', False):
            await session.record_and_send()
        self.assertNotIn(session.task_id, app.state.input_targets)
        self.assertIn('other-ai', app.state.input_targets)

    async def test_failed_final_send_clears_wait_but_success_stays_pending_for_real_result(self):
        from core.client.audio.recorder import AudioRecorder
        app = self.make_app()
        session = AudioRecorder(app)
        app.state.input_targets['result'] = {'epoch': 'original'}
        message = SimpleNamespace(task_id='result', is_final=True)
        await session._send_message(message)
        self.assertIn('result', app.state.input_targets)
        app.ws.send.return_value = False
        await session._send_message(message)
        self.assertNotIn('result', app.state.input_targets)
        self.assertIn('other-ai', app.state.input_targets)

    async def test_disconnect_cleanup_preserves_current_ai_until_handler_finishes(self):
        from core.client.output.result_processor import ResultProcessor
        app = self.make_app()
        app.state.processing_input_tasks.clear()
        app.state.input_targets['current'] = {'epoch': 'current'}
        processor = ResultProcessor(app)
        entered, release = asyncio.Event(), asyncio.Event()
        async def process(_message):
            entered.set()
            await release.wait()
        processor._handle_message_impl = process
        task = asyncio.create_task(processor._handle_message(SimpleNamespace(task_id='current', is_final=True)))
        await asyncio.wait_for(entered.wait(), 1)
        processor._cleanup()
        self.assertEqual(set(app.state.input_targets), {'current'})
        self.assertEqual(app.state.processing_input_tasks, {'current'})
        release.set()
        await task
        self.assertEqual(app.state.input_targets, {})
        self.assertEqual(app.state.processing_input_tasks, set())

    async def test_result_exception_releases_only_its_pending_marker(self):
        from core.client.output.result_processor import ResultProcessor
        app = self.make_app()
        app.state.input_targets['broken'] = None
        processor = ResultProcessor(app)
        processor._handle_message_impl = AsyncMock(side_effect=ValueError('processing failed'))
        with self.assertRaises(ValueError):
            await processor._handle_message(SimpleNamespace(task_id='broken', is_final=True))
        self.assertEqual(set(app.state.input_targets), {'other-ai'})
        self.assertEqual(app.state.processing_input_tasks, {'other-ai'})

    async def test_published_status_includes_results_after_recording_has_stopped(self):
        from core.client.manager import mic_runner
        from core.product import status
        app = self.make_app()
        app.state.recording = False
        app.state.stream = SimpleNamespace(active=True)
        app.stream = Mock()
        app.tray = Mock()
        app.shortcut = Mock()
        app.shortcut.keyboard_listener.is_alive.return_value = True
        app.hotword, app.llm, app.udp = Mock(), Mock(), Mock()
        captures = []
        def publisher(_component, collect):
            captures.append(collect)
            return SimpleNamespace(start=lambda: None)
        with patch.object(status, 'StatusPublisher', side_effect=publisher), \
             patch.object(mic_runner.TipsDisplay, 'show_mic_tips'):
            mic_runner.MicRunner(app).start_resources()
        observed = captures[0]()
        self.assertFalse(observed['recording'])
        self.assertTrue(observed['pending_results'])
        app.state.input_targets.clear()
        self.assertFalse(captures[0]()['pending_results'])


if __name__ == '__main__':
    unittest.main()
