import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from core.client.transcribe.result_handler import ResultHandler
from web_gui.transcription_service import regenerate_srt_from_txt


class TranscriptionOutputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / '原视频.mp4'
        self.source.write_bytes(b'original media')
        self.old = []
        for suffix in ('.txt', '.json', '.srt', '.merge.txt'):
            path = self.source.with_suffix(suffix)
            path.write_text('keep existing ' + suffix)
            self.old.append((path, path.read_bytes()))
        self.options = dict(file_save_txt=True, file_save_json=True, file_save_srt=True, file_save_merge=True)
        self.message = SimpleNamespace(text='你好。', text_accu='你好。', tokens=['你', '好'], timestamps=[0.2, 0.4])

    def test_all_formats_only_write_to_attempt_and_rebuild_preserves_srt(self):
        target = self.root / 'outputs' / 'attempt'
        ResultHandler.save_results(self.source, self.message, output_dir=target, options=self.options)
        for path, original in self.old:
            self.assertEqual(path.read_bytes(), original)
        self.assertEqual(self.source.read_bytes(), b'original media')
        for ext in ('.txt', '.json', '.srt', '.merge.txt'):
            self.assertTrue((target / self.source.name).with_suffix(ext).is_file())
        srt = target / '原视频.srt'
        before = srt.read_bytes()
        ok, message, rebuilt = regenerate_srt_from_txt(target / '原视频.txt')
        self.assertTrue(ok, message)
        self.assertNotEqual(rebuilt, srt)
        self.assertEqual(srt.read_bytes(), before)

    def test_missing_timestamps_omits_srt_and_rebuild_is_explained(self):
        target = self.root / 'outputs'
        self.message.tokens = []
        self.message.timestamps = []
        ResultHandler.save_results(self.source, self.message, output_dir=target, options=self.options)
        self.assertFalse((target / '原视频.srt').exists())
        self.assertTrue((target / '原视频.txt').exists())
        ok, message, result = regenerate_srt_from_txt(target / '原视频.txt')
        self.assertFalse(ok)
        self.assertIn('时间戳', message)
        self.assertIsNone(result)

    def test_existing_output_and_disabled_formats_are_never_overwritten_or_deleted(self):
        target = self.root / 'outputs'
        ResultHandler.save_results(self.source, self.message, output_dir=target, options=self.options)
        before = {path: path.read_bytes() for path in target.iterdir()}
        with self.assertRaises(FileExistsError):
            ResultHandler.save_results(self.source, self.message, output_dir=target,
                                       options={**self.options, 'file_save_srt': False})
        self.assertEqual(before, {path: path.read_bytes() for path in target.iterdir()})

    def test_invalid_timestamp_stream_does_not_create_fake_srt(self):
        target = self.root / 'outputs'
        self.message.timestamps = [2.0, float('nan')]
        ResultHandler.save_results(self.source, self.message, output_dir=target, options=self.options)
        self.assertFalse((target / '原视频.srt').exists())
        self.assertTrue((target / '原视频.txt').is_file())


if __name__ == '__main__':
    unittest.main()
