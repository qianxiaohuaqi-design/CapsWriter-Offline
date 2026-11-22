"""GUI-only close must preserve an authenticated background owner subtree."""
from __future__ import annotations

import ctypes
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from core.product.storage import atomic_json
from web_gui import process_manager as processes


class FakeProcess:
    def __init__(self, pid, parent, command, registry):
        self.pid = pid
        self.parent = parent
        self.command = command
        self.registry = registry
        self.birth = float(pid)
        self.terminate = Mock()
        self.kill = Mock()

    def cmdline(self):
        return self.command

    def create_time(self):
        return self.birth

    def children(self, recursive=False):
        children = [p for p in self.registry.values() if p.parent == self.pid]
        if recursive:
            children += [descendant for child in list(children) for descendant in child.children(recursive=True)]
        return children

    def parents(self):
        ancestors = []
        current = self
        while current.parent in self.registry:
            current = self.registry[current.parent]
            ancestors.append(current)
        return ancestors


class GUIBackgroundProtectionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.registry = {}
        commands = {
            100: [sys.executable, str(processes.BASE_DIR / 'web_gui' / 'app.py')],
            101: ['webview-renderer'],
            200: [sys.executable, str(processes.BASE_DIR / 'run_app.py'), '--coordinator'],
            201: [sys.executable, str(processes.BASE_DIR / 'start_server.py')],
            202: ['recognition-worker'],
        }
        for pid, parent in ((100, None), (101, 100), (200, 100), (201, 200), (202, 201)):
            self.registry[pid] = FakeProcess(pid, parent, commands[pid], self.registry)
        self.descriptor = self.root / 'runtime' / 'coordinator.json'
        atomic_json(self.descriptor, {'pid': 200, 'birth': 200.0})
        self.gui_pid_file = self.root / 'logs' / 'gui.pid'
        self.server_pid_file = self.root / 'logs' / 'server.pid'
        self.client_pid_file = self.root / 'logs' / 'client.pid'
        self.pid_files = {self.gui_pid_file: 100}
        for patcher in (
            patch.object(processes, 'data_root', return_value=self.root),
            patch.object(processes, 'GUI_PID_FILE', self.gui_pid_file),
            patch.object(processes, 'SERVER_PID_FILE', self.server_pid_file),
            patch.object(processes, 'CLIENT_PID_FILE', self.client_pid_file),
            patch.object(processes, 'is_process_alive', return_value=True),
            patch.object(processes, 'read_alive_pid', side_effect=lambda path, *args: self.pid_files.get(Path(path))),
            patch.object(processes, 'same_process', side_effect=lambda pid, birth:
                pid in self.registry and birth == self.registry[pid].birth),
            patch('psutil.Process', side_effect=lambda pid: self.registry[pid]),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def add_direct_runtime_children(self):
        for pid, parent, command in (
            (300, 100, [sys.executable, str(processes.BASE_DIR / 'start_server.py')]),
            (301, 300, ['recognition-worker']),
            (400, 100, [sys.executable, str(processes.BASE_DIR / 'start_client.py')]),
            (401, 400, ['dictation-overlay']),
        ):
            self.registry[pid] = FakeProcess(pid, parent, command, self.registry)
        for path, pid in ((self.server_pid_file, 300), (self.client_pid_file, 400)):
            self.pid_files[path] = pid
            atomic_json(processes._identity_file(path), {'pid': pid, 'birth': float(pid)})

    def test_gui_close_leaves_coordinator_and_its_worker_subtree_alive(self):
        with patch.object(processes, 'close_windows_by_pid'), \
             patch('psutil.wait_procs', side_effect=lambda procs, **kwargs: ([], procs)):
            self.assertTrue(processes.stop_gui())
        for pid in (100, 101):
            self.assertEqual(self.registry[pid].terminate.call_count, 1)
            self.assertEqual(self.registry[pid].kill.call_count, 1)
        for pid in (200, 201, 202):
            self.assertEqual(self.registry[pid].terminate.call_count, 0)
            self.assertEqual(self.registry[pid].kill.call_count, 0)

    def test_gui_window_close_excludes_background_windows(self):
        self.add_direct_runtime_children()
        posted = []
        window_pids = {1000: 100, 1010: 101, 2000: 200, 2020: 202, 3010: 301, 4010: 401}
        def enumerate_windows(callback, value):
            for hwnd in window_pids:
                callback(hwnd, value)
        def window_pid(hwnd, pointer):
            pointer._obj.value = window_pids[hwnd]
        user32 = SimpleNamespace(EnumWindows=enumerate_windows, GetWindowThreadProcessId=window_pid,
            PostMessageW=lambda hwnd, *args: posted.append(window_pids[hwnd]))
        with patch.object(processes.sys, 'platform', 'win32'), \
             patch.object(ctypes, 'windll', SimpleNamespace(user32=user32), create=True), \
             patch.object(ctypes, 'WINFUNCTYPE', lambda *args: lambda callback: callback, create=True):
            processes.close_windows_by_pid(100, include_children=True, preserve_background=True)
        self.assertEqual(set(posted), {100, 101})

    def test_gui_diagnostics_children_keep_running_after_gui_close(self):
        self.add_direct_runtime_children()
        with patch.object(processes, 'close_windows_by_pid'), \
             patch('psutil.wait_procs', side_effect=lambda procs, **kwargs: ([], procs)):
            self.assertTrue(processes.stop_gui())
        for pid in (300, 301, 400, 401):
            self.assertEqual(self.registry[pid].terminate.call_count, 0)
            self.assertEqual(self.registry[pid].kill.call_count, 0)

    def test_owner_requires_matching_birth_and_exact_coordinator_command(self):
        self.assertEqual(processes._background_tree_root(), (200, 200.0))
        atomic_json(self.descriptor, {'pid': 200, 'birth': 199.0})
        self.assertIsNone(processes._background_tree_root())
        atomic_json(self.descriptor, {'pid': 200, 'birth': 200.0})
        self.registry[200].command = [sys.executable, str(processes.BASE_DIR / 'run_app.py'), '--client']
        self.assertIsNone(processes._background_tree_root())

    def test_reused_child_pid_is_not_force_killed_after_wait(self):
        def wait_with_pid_reuse(procs, **kwargs):
            self.registry[101].birth += 1
            return [], procs
        with patch.object(processes, 'close_windows_by_pid'), \
             patch('psutil.wait_procs', side_effect=wait_with_pid_reuse):
            self.assertTrue(processes.stop_gui())
        self.assertEqual(self.registry[101].terminate.call_count, 1)
        self.assertEqual(self.registry[101].kill.call_count, 0)
        self.assertEqual(self.registry[100].kill.call_count, 1)


if __name__ == '__main__':
    unittest.main()
