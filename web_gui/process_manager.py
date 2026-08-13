# coding: utf-8
"""控制中心后台进程状态与重启工具。"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
LOG_DIR = BASE_DIR / 'logs'
SERVER_PID_FILE = LOG_DIR / 'control_center_server.pid'
CLIENT_PID_FILE = LOG_DIR / 'control_center_client.pid'
GUI_PID_FILE = LOG_DIR / 'control_center_gui.pid'
CREATE_NO_WINDOW = 0x08000000 if sys.platform == 'win32' else 0


def python_executable(*, prefer_console: bool = True) -> str:
    """Return a Python runtime suitable for hidden child processes on Windows."""
    executable = Path(sys.executable)
    if sys.platform == 'win32' and prefer_console and executable.name.lower() == 'pythonw.exe':
        console_exe = executable.with_name('python.exe')
        if console_exe.exists():
            return str(console_exe)
    if sys.platform == 'win32' and not prefer_console and executable.name.lower() == 'python.exe':
        windowless_exe = executable.with_name('pythonw.exe')
        if windowless_exe.exists():
            return str(windowless_exe)
    return str(executable)


def is_frozen_app() -> bool:
    return bool(getattr(sys, 'frozen', False))


def component_keyword(script_name: str) -> str:
    if is_frozen_app():
        mapping = {
            'run_app.py': 'capswriter',
            'start_server.py': '--server',
            'start_client.py': '--client',
            'web_gui/app.py': '--gui',
            'web_gui\\app.py': '--gui',
            'app.py': '--gui',
        }
        return mapping.get(script_name, script_name)
    return script_name


def is_port_open(host: str, port: int, timeout: float = 0.2) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        return sock.connect_ex((host, port)) == 0


def is_process_alive(pid: int, keyword: str = '') -> bool:
    if pid <= 0:
        return False
    try:
        import psutil
        if not psutil.pid_exists(pid):
            return False
        p = psutil.Process(pid)
        if not p.is_running() or p.status() == psutil.STATUS_ZOMBIE:
            return False
        cmd = ' '.join(p.cmdline()).lower()
        proc_name = p.name().lower()
        if 'python' in proc_name or 'python' in cmd or 'capswriter' in proc_name or 'capswriter' in cmd:
            if not keyword or keyword.lower() in cmd:
                return True
            return False
        return False
    except Exception:
        if keyword:
            return False
        if sys.platform == 'win32':
            try:
                import ctypes
                handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
                if not handle:
                    return False
                exit_code = ctypes.c_ulong()
                ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
                ctypes.windll.kernel32.CloseHandle(handle)
                return exit_code.value == 259
            except Exception:
                return False
        return False


def read_alive_pid(pid_file: Path, keyword: str = '') -> int | None:
    try:
        pid = int(pid_file.read_text(encoding='utf-8').strip())
    except Exception:
        return None
    return pid if is_process_alive(pid, keyword) else None


def read_alive_control_pid(pid_file: Path) -> int | None:
    try:
        pid = int(pid_file.read_text(encoding='utf-8').strip())
    except Exception:
        return None
    if not is_frozen_app():
        return pid if is_process_alive(pid, component_keyword('run_app.py')) else None
    try:
        import psutil
        if not psutil.pid_exists(pid):
            return None
        proc = psutil.Process(pid)
        if not proc.is_running() or proc.status() == psutil.STATUS_ZOMBIE:
            return None
        cmd_parts = [part.lower() for part in proc.cmdline()]
        cmd = ' '.join(cmd_parts)
        if 'capswriter' not in proc.name().lower() and 'capswriter' not in cmd:
            return None
        child_modes = ('--server', '--client', '--gui')
        return None if any(mode in cmd_parts for mode in child_modes) else pid
    except Exception:
        return None


def write_pid(pid_file: Path, pid: int) -> None:
    LOG_DIR.mkdir(exist_ok=True)
    pid_file.write_text(str(pid), encoding='utf-8')


def find_listening_pid(port: int) -> int | None:
    if sys.platform != 'win32':
        return None
    if not is_port_open('127.0.0.1', port, timeout=0.15):
        return None
    try:
        import psutil
        for conn in psutil.net_connections(kind='inet'):
            if conn.status == psutil.CONN_LISTEN and conn.laddr.port == port:
                return conn.pid
    except Exception:
        pass
    # psutil 不可用时退回 PowerShell 查询
    try:
        result = subprocess.run(
            [
                'powershell',
                '-NoProfile',
                '-Command',
                f"(Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty OwningProcess)",
            ],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='ignore',
            creationflags=CREATE_NO_WINDOW,
            timeout=5,
        )
        raw = result.stdout.strip()
        return int(raw) if raw else None
    except Exception:
        return None


def close_windows_by_pid(pid: int, include_children: bool = False) -> None:
    """向指定进程的窗口发送 WM_CLOSE，可包含 Native GUI 子进程。"""
    if sys.platform != 'win32' or not pid:
        return
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        WM_CLOSE = 0x0010
        target_pids = {pid}
        if include_children:
            try:
                import psutil
                target_pids.update(child.pid for child in psutil.Process(pid).children(recursive=True))
            except Exception:
                pass

        def enum_windows_callback(hwnd, extra):
            proc_id = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(proc_id))
            if proc_id.value in target_pids:
                user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
            return True

        WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        user32.EnumWindows(WNDENUMPROC(enum_windows_callback), 0)
    except Exception:
        pass


def stop_pid(pid: int, force_self: bool = False) -> bool:
    if not pid or not is_process_alive(pid):
        return False
    if pid == os.getpid() and not force_self:
        return False

    close_windows_by_pid(pid)

    try:
        import psutil
        proc = psutil.Process(pid)
        for child in proc.children(recursive=True):
            try:
                close_windows_by_pid(child.pid)
                child.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        try:
            proc.kill()
        except psutil.NoSuchProcess:
            return True
        proc.wait(timeout=2)
        return True
    except psutil.NoSuchProcess:
        return True
    except Exception:
        pass
    # psutil 不可用或失败时退回 PowerShell
    try:
        command = f"""
function Stop-Tree([int]$Id) {{
    Get-CimInstance Win32_Process -Filter "ParentProcessId=$Id" -ErrorAction SilentlyContinue |
        ForEach-Object {{ Stop-Tree ([int]$_.ProcessId) }}
    Stop-Process -Id $Id -Force -ErrorAction SilentlyContinue
}}
Stop-Tree {pid}
"""
        subprocess.run(
            ['powershell', '-NoProfile', '-Command', command],
            capture_output=True,
            text=True,
            creationflags=CREATE_NO_WINDOW,
            timeout=8,
        )
        return True
    except Exception:
        return False


def find_project_processes(patterns: tuple[str, ...], include_self: bool = False) -> list[int]:
    if sys.platform != 'win32':
        return []
    try:
        import psutil
        base = str(BASE_DIR).lower()
        normalized = [pattern.lower() for pattern in patterns]
        pids = []
        for proc in psutil.process_iter(['pid', 'cmdline']):
            try:
                if proc.info['pid'] == os.getpid() and not include_self:
                    continue
                cmd = ' '.join(proc.info.get('cmdline') or []).lower()
                if base in cmd and any(pattern in cmd for pattern in normalized):
                    pids.append(proc.info['pid'])
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return pids
    except Exception:
        pass
    # psutil 不可用时退回 PowerShell 查询
    try:
        escaped_base = str(BASE_DIR).replace('\\', '\\\\')
        escaped_patterns = [pattern.replace('\\', '\\\\') for pattern in patterns]
        pattern_expr = ' -or '.join(f'$_.CommandLine -match "{pattern}"' for pattern in escaped_patterns)
        self_expr = "" if include_self else f"$_.ProcessId -ne {os.getpid()} -and"
        command = f"""
Get-CimInstance Win32_Process |
    Where-Object {{
        {self_expr}
        $_.CommandLine -match "{escaped_base}" -and
        ({pattern_expr})
    }} |
    Select-Object -ExpandProperty ProcessId
"""
        result = subprocess.run(
            ['powershell', '-NoProfile', '-Command', command],
            capture_output=True,
            text=True,
            creationflags=CREATE_NO_WINDOW,
            timeout=5,
        )
        return [int(line) for line in result.stdout.splitlines() if line.strip().isdigit()]
    except Exception:
        return []


def _launch(script_name: str, pid_file: Path) -> int | None:
    env = os.environ.copy()
    env['CAPSWRITER_CONTROL_CENTER'] = '1'
    if is_frozen_app():
        mode = {
            'start_server.py': '--server',
            'start_client.py': '--client',
        }.get(script_name)
        if not mode:
            return None
        command = [str(Path(sys.executable)), mode]
    else:
        script = BASE_DIR / script_name
        if not script.exists():
            return None
        command = [python_executable(prefer_console=True), str(script)]

    process = subprocess.Popen(
        command,
        cwd=str(BASE_DIR),
        creationflags=CREATE_NO_WINDOW,
        env=env,
    )
    write_pid(pid_file, process.pid)
    return process.pid


def launch_server() -> tuple[bool, str]:
    if is_port_open('127.0.0.1', 6016):
        pid = find_listening_pid(6016)
        return True, f'ASR 服务端已在运行。PID: {pid or "未知"}'
    pid = _launch('start_server.py', SERVER_PID_FILE)
    return (True, f'已拉起 ASR 服务端。PID: {pid}') if pid else (False, '未找到 start_server.py')


def launch_client() -> tuple[bool, str]:
    pid = read_alive_pid(CLIENT_PID_FILE, component_keyword('start_client.py'))
    if pid:
        return True, f'听写客户端已在运行。PID: {pid}'
    pid = _launch('start_client.py', CLIENT_PID_FILE)
    return (True, f'已拉起听写客户端。PID: {pid}') if pid else (False, '未找到 start_client.py')


def launch_missing_components() -> tuple[bool, str]:
    """Start ASR server and dictation client only when they are not already alive."""
    results = []
    ok = True
    server_alive = is_port_open('127.0.0.1', 6016)
    client_alive = bool(read_alive_pid(CLIENT_PID_FILE, component_keyword('start_client.py')))

    if server_alive:
        pid = find_listening_pid(6016)
        results.append(f'ASR 服务端已在线（PID: {pid or "未知"}）')
    else:
        server_ok, server_msg = launch_server()
        ok = ok and server_ok
        results.append(server_msg)

    if client_alive:
        pid = read_alive_pid(CLIENT_PID_FILE, component_keyword('start_client.py'))
        results.append(f'听写客户端已在线（PID: {pid}）')
    else:
        client_ok, client_msg = launch_client()
        ok = ok and client_ok
        results.append(client_msg)

    return ok, '；'.join(results)


def restart_server() -> tuple[bool, str]:
    pid = find_listening_pid(6016) or read_alive_pid(SERVER_PID_FILE, component_keyword('start_server.py'))
    if pid:
        stop_pid(pid)
    return launch_server()


def restart_client() -> tuple[bool, str]:
    pid = read_alive_pid(CLIENT_PID_FILE, component_keyword('start_client.py'))
    if pid:
        stop_pid(pid)
    return launch_client()


def stop_gui(include_self: bool = False) -> bool:
    """Stop the native GUI process, even if it was not launched by this tray instance."""
    stopped = False
    candidates = [
        read_alive_pid(GUI_PID_FILE, component_keyword('app.py')),
        find_listening_pid(6017),
        *find_project_processes(('web_gui\\\\app.py', 'web_gui/app.py'), include_self=include_self),
    ]
    for pid in dict.fromkeys(pid for pid in candidates if pid):
        stopped = stop_pid(pid, force_self=include_self) or stopped
    return stopped


def stop_all(include_self: bool = False) -> dict[str, bool]:
    """Stop GUI, dictation client, and ASR server as one product-level exit."""
    # 1. 强制释放在 OS 中被卡住的修饰键
    try:
        from core.tools.key_reset import release_all_modifier_keys
        release_all_modifier_keys()
    except Exception:
        pass

    client_pid = read_alive_pid(CLIENT_PID_FILE, component_keyword('start_client.py'))
    server_pid = find_listening_pid(6016) or read_alive_pid(SERVER_PID_FILE, component_keyword('start_server.py'))
    gui_stopped = stop_gui(include_self=include_self)
    client_stopped = stop_pid(client_pid) if client_pid else False
    server_stopped = stop_pid(server_pid) if server_pid else False
    launcher_stopped = False
    for pid in find_project_processes(('run_app.py',), include_self=include_self):
        if pid != os.getpid() or include_self:
            launcher_stopped = stop_pid(pid, force_self=include_self) or launcher_stopped

    # 清理 PID 缓存文件
    for pf in (SERVER_PID_FILE, CLIENT_PID_FILE, GUI_PID_FILE, LOG_DIR / 'control_center.pid'):
        try:
            if pf.exists():
                pf.unlink()
        except Exception:
            pass

    return {
        'gui': gui_stopped,
        'client': client_stopped,
        'server': server_stopped,
        'launcher': launcher_stopped,
    }


def _tail_lines(path: Path, max_lines: int = 80, max_bytes: int = 65536) -> list[str]:
    try:
        with path.open('rb') as file:
            file.seek(0, os.SEEK_END)
            size = file.tell()
            file.seek(max(0, size - max_bytes))
            data = file.read()
        return data.decode('utf-8', errors='ignore').splitlines()[-max_lines:]
    except Exception:
        return []


def get_health_status() -> dict:
    server_pid = find_listening_pid(6016) or read_alive_pid(SERVER_PID_FILE)
    client_pid = read_alive_pid(CLIENT_PID_FILE)
    server_alive = bool(server_pid and is_process_alive(server_pid) and is_port_open('127.0.0.1', 6016))
    client_alive = bool(client_pid and is_process_alive(client_pid))
    latest_error = ''
    if LOG_DIR.exists():
        logs = sorted(LOG_DIR.glob('*.log'), key=lambda p: p.stat().st_mtime, reverse=True)
        for log in logs[:4]:
            lines = _tail_lines(log)
            for line in reversed(lines):
                upper = line.upper()
                if (
                    ' ERROR ' in upper
                    or ' CRITICAL ' in upper
                    or 'TRACEBACK' in upper
                ):
                    latest_error = f'{log.name}: {line.strip()}'
                    break
            if latest_error:
                break
    return {
        'server_alive': server_alive,
        'server_pid': server_pid,
        'client_alive': client_alive,
        'client_pid': client_pid,
        'model_loaded': server_alive,
        'latest_error': latest_error or '暂无最近错误',
    }
