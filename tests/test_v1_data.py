import ast
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.product import data_safety
from core.product.data_safety import (atomic_write, commit_transaction, fingerprint,
                                      safe_role, validate_rules, recover_transaction)
from web_gui import config_manager as cm
from web_gui import private_config as pc
from web_gui import transcription_history as history


class DataProtectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_half_import_rolls_back_every_file(self):
        one, two = self.root / 'one.py', self.root / 'two.py'
        one.write_bytes(b'one original')
        two.write_bytes(b'two original')
        expected = {str(path): fingerprint(path) for path in (one, two)}
        calls = []
        def fail_second(path, content):
            calls.append(path)
            atomic_write(path, content)
            if len(calls) == 2:
                raise OSError('simulated failed readback')
        with self.assertRaisesRegex(ValueError, '全部原配置已恢复'):
            commit_transaction({one: 'new one', two: 'new two'}, self.root / 'backups', expected, writer=fail_second)
        self.assertEqual(one.read_bytes(), b'one original')
        self.assertEqual(two.read_bytes(), b'two original')
        manifest = next((self.root / 'backups').glob('*/manifest.json'))
        self.assertEqual(json.loads(manifest.read_text())['state'], 'rolled_back')

    def test_external_conflict_prevents_any_write(self):
        path = self.root / 'config.py'
        path.write_text('before')
        expected = {str(path): fingerprint(path)}
        path.write_text('external edit')
        with self.assertRaises(ValueError):
            commit_transaction({path: 'new'}, self.root / 'backups', expected)
        self.assertEqual(path.read_text(), 'external edit')

    def test_regex_bad_pattern_and_group_reference_rejected(self):
        validate_rules('(word) = $1')
        validate_rules(r'([A-Za-z])\.(?=[A-Za-z](?![A-Za-z]))   =   \1')
        validate_rules(r'(?<=回车)回车   = \n')
        with self.assertRaisesRegex(ValueError, '第 1 行'):
            validate_rules('([ = replacement')
        with self.assertRaises(ValueError):
            validate_rules('(word) = $2')

    def test_role_export_strips_secret_quotes_and_nested_keys(self):
        cleaned = safe_role('api_key = "SECRET"\nextra_options = {"api_key": "NESTED", "temperature": 1}\nname = "角色"\n')
        self.assertNotIn('SECRET', cleaned)
        self.assertNotIn('NESTED', cleaned)
        self.assertIn('角色', cleaned)
        with self.assertRaises(ValueError):
            safe_role('import os\nos.remove("userfile")')

    def test_existing_role_arithmetic_is_preserved_without_executing_calls(self):
        clean = safe_role('max_context_length = 1024 * 1024\n')
        self.assertIn('1048576', clean)
        with self.assertRaises(ValueError):
            safe_role('max_context_length = __import__("os").getpid()')

    def test_history_delete_checks_path_boundaries_and_ownership(self):
        root = self.root / 'outputs'
        root.mkdir()
        sibling = self.root / 'outputs-private'
        sibling.mkdir()
        (sibling / 'precious.txt').write_text('keep')
        owned = root / 'task'
        owned.mkdir()
        (owned / '.capswriter-output.json').write_text('{}')
        with patch.object(history, 'OUTPUT_DIR', root):
            self.assertFalse(history.delete_transcription_output(sibling)[0])
            self.assertFalse(history.delete_transcription_output(root)[0])
            self.assertTrue(history.delete_transcription_output(owned)[0])
        self.assertTrue((sibling / 'precious.txt').exists())

    def test_interrupted_journal_requires_valid_hash_and_owned_target(self):
        backup = self.root / 'backups'
        journal = backup / 'import-test'
        journal.mkdir(parents=True)
        target = self.root / 'config.py'
        target.write_text('new')
        (journal / '0.original').write_text('old')
        import hashlib
        manifest = {'state': 'prepared', 'files': [{'path': str(target), 'backup': '0.original', 'existed': True,
            'old_sha256': hashlib.sha256(b'old').hexdigest(), 'new_sha256': hashlib.sha256(b'new').hexdigest()}]}
        (journal / 'manifest.json').write_text(json.dumps(manifest))
        with self.assertRaises(ValueError):
            recover_transaction(journal, backup, set())
        recover_transaction(journal, backup, {target})
        self.assertEqual(target.read_text(), 'old')

    def test_unreadable_transaction_blocks_progress_and_recovery_preserves_bytes(self):
        backup = self.root / 'backups'
        journal = backup / 'import-damaged'
        journal.mkdir(parents=True)
        target = self.root / 'config.py'
        target.write_bytes(b'current configuration')
        original = journal / '0.original'
        original.write_bytes(b'original backup')
        manifest = journal / 'manifest.json'
        for content in (b'{truncated', b'[]', b'{"state": "unknown"}'):
            with self.subTest(content=content):
                manifest.write_bytes(content)
                self.assertEqual(data_safety.pending_imports(backup),
                                 [{'journal': str(journal), 'state': 'unreadable'}])
                with self.assertRaises(ValueError):
                    recover_transaction(journal, backup, {target})
                self.assertEqual(target.read_bytes(), b'current configuration')
                self.assertEqual(original.read_bytes(), b'original backup')
                self.assertEqual(manifest.read_bytes(), content)


class ConfigImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        source = Path(cm.__file__).resolve().parents[1]
        names = {'BASE_DIR': '', 'CONFIG_CLIENT_PATH': 'config_client.py', 'CONFIG_SERVER_PATH': 'config_server.py',
                 'LLM_DEFAULT_PATH': 'LLM/default.py', 'LLM_DIR': 'LLM', 'LLM_PROFILES_PATH': 'web_gui/llm_profiles.json',
                 'GUI_SETTINGS_PATH': 'web_gui/gui_settings.json', 'HOT_TXT_PATH': 'hot.txt', 'HOT_RULE_TXT_PATH': 'hot-rule.txt',
                 'PILL_CONFIG_PATH': 'config_pill.py', 'CONFIG_BACKUP_DIR': 'web_gui/config_backups',
                 'CONFIG_EXPORT_DIR': 'web_gui/config_exports'}
        for attr, relative in names.items():
            path = self.root / relative
            patcher = patch.object(cm, attr, path)
            patcher.start(); self.addCleanup(patcher.stop)
            original = source / relative
            if relative and original.is_file():
                path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(original, path)
        patcher = patch.object(cm, 'MANAGED_CONFIG_PATHS', {path: self.root / path for path in cm.MANAGED_CONFIG_PATHS})
        patcher.start(); self.addCleanup(patcher.stop)
        secret = self.root / 'web_gui/private_config.json'
        secret.parent.mkdir(exist_ok=True)
        secret.write_text(json.dumps({'llm_api_keys': {'deepseek': 'KEEP-SECRET'}, 'llm_profile_api_keys': {'p': 'KEEP-PROFILE'}}))
        patcher = patch.object(pc, 'PRIVATE_CONFIG_PATH', secret)
        patcher.start(); self.addCleanup(patcher.stop)
        self.secret = secret

    def test_preview_is_readonly_invalid_rules_prevent_partial_import(self):
        before = cm.CONFIG_CLIENT_PATH.read_bytes()
        bad = {'configs': {'language': 'en'}, 'hot_rules_content': '(bad = wrong'}
        self.assertFalse(cm.ConfigManager.preview_full_config(bad)['ok'])
        self.assertFalse(cm.ConfigManager.import_full_config(bad)[0])
        self.assertEqual(cm.CONFIG_CLIENT_PATH.read_bytes(), before)

    def test_transaction_applies_empty_hotwords_and_preserves_secrets(self):
        before_keys = self.secret.read_bytes()
        data = {'configs': {'language': 'en', 'llm_config': {'provider': 'deepseek', 'api_key': 'IMPORTED-SECRET'},
                            'llm_profiles': {'active_profile': 'p', 'profiles': [{'id': 'p', 'name': 'Profile', 'models': ['model']}]},
                            'hold_mode': False}, 'hot_words_content': ''}
        preview = cm.ConfigManager.preview_full_config(data)
        self.assertTrue(preview['ok'], preview)
        ok, message = cm.ConfigManager.import_full_config(preview['data'], preview['token'])
        self.assertTrue(ok, message)
        self.assertEqual(cm.HOT_TXT_PATH.read_text(), '')
        self.assertEqual(cm.ConfigManager.get_client_var('language', ''), 'en')
        self.assertFalse(cm.ConfigManager.get_hold_mode())
        self.assertEqual(self.secret.read_bytes(), before_keys)

    def test_stale_preview_preserves_external_edits(self):
        data = {'configs': {'language': 'en'}}
        preview = cm.ConfigManager.preview_full_config(data)
        cm.CONFIG_CLIENT_PATH.write_text(cm.CONFIG_CLIENT_PATH.read_text() + '\n# external change\n')
        changed = cm.CONFIG_CLIENT_PATH.read_bytes()
        self.assertFalse(cm.ConfigManager.import_full_config(data, preview['token'])[0])
        self.assertEqual(cm.CONFIG_CLIENT_PATH.read_bytes(), changed)

    def test_hotword_external_edit_does_not_destroy_unsaved_work(self):
        document = cm.ConfigManager.read_hotword_document(cm.HOT_TXT_PATH)
        cm.HOT_TXT_PATH.write_text('external')
        ok, _ = cm.ConfigManager.save_hotword_document(cm.HOT_TXT_PATH, 'my changes', document['sha256'])
        self.assertFalse(ok)
        self.assertEqual(cm.HOT_TXT_PATH.read_text(), 'external')

    def test_import_role_cannot_execute_code_or_escape_directory(self):
        for role in ({'file': '../evil.py', 'content': 'name = "bad"'},
                     {'file': 'evil.py', 'content': 'import os\nos.remove("x")'}):
            self.assertFalse(cm.ConfigManager.preview_full_config({'configs': {}, 'llm_roles': [role]})['ok'])

    def test_corrupt_private_keys_not_silently_replaced(self):
        self.secret.write_text('{broken')
        with self.assertRaises(ValueError):
            pc.set_llm_api_key('deepseek', 'new')
        self.assertEqual(self.secret.read_text(), '{broken')


if __name__ == '__main__':
    unittest.main()
