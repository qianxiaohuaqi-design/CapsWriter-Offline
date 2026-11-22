# coding: utf-8
"""Owned process control and independently verified Worker/client health.

A listening port is diagnostic information, never proof of ownership/readiness.
"""
from __future__ import annotations
import os
import socket
import subprocess
import sys
from pathlib import Path
from core.product.paths import resource_root, data_root, bootstrap
from core.product.storage import atomic_json, read_json
from core.product.status import health_snapshot, process_birth, same_process

BASE_DIR = resource_root()
LOG_DIR = data_root() / 'logs'
SERVER_PID_FILE = LOG_DIR / 'control_center_server.pid'
CLIENT_PID_FILE = LOG_DIR / 'control_center_client.pid'
GUI_PID_FILE = LOG_DIR / 'control_center_gui.pid'
CREATE_NO_WINDOW = 0x08000000 if sys.platform == 'win32' else 0


def python_executable(*, prefer_console=True):
    exe = Path(sys.executable)
    if sys.platform == 'win32' and not is_frozen_app():
        alternative = exe.with_name('python.exe' if prefer_console else 'pythonw.exe')
        if alternative.exists():
            return str(alternative)
    return str(exe)


def is_frozen_app():
    return bool(getattr(sys, 'frozen', False))


def component_keyword(script_name):
    return ({'run_app.py': 'capswriter', 'start_server.py': '--server',
             'start_client.py': '--client', 'web_gui/app.py': '--gui',
             'web_gui\\app.py': '--gui', 'app.py': '--gui'}.get(script_name, script_name)
            if is_frozen_app() else script_name)


def is_port_open(host, port, timeout=0.2):
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def _owned_command(proc):
    """Require this exact application installation, not a generic Python name."""
    args = proc.cmdline()
    if is_frozen_app():
        return Path(proc.exe()).resolve() == Path(sys.executable).resolve()
    scripts = {str((BASE_DIR / name).resolve()) for name in
               ('run_app.py', 'start_client.py', 'start_server.py', 'web_gui/app.py')}
    return any(str((Path(arg) if Path(arg).is_absolute() else Path(proc.cwd()) / arg).resolve()) in scripts
               for arg in args[1:] if not arg.startswith('-'))


def is_process_alive(pid, keyword=''):
    try:
        import psutil
        proc = psutil.Process(int(pid))
        return (proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
                and _owned_command(proc) and (not keyword or keyword.lower() in ' '.join(proc.cmdline()).lower()))
    except Exception:
        return False


def _identity_file(pid_file):
    return Path(str(pid_file) + '.identity.json')


def read_alive_pid(pid_file, keyword=''):
    try:
        pid = int(Path(pid_file).read_text(encoding='utf-8').strip())
        identity = read_json(_identity_file(pid_file))
        if identity is not None and (identity.get('pid') != pid or not same_process(pid, identity.get('birth'))):
            return None
        return pid if is_process_alive(pid, keyword) else None
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def read_alive_control_pid(pid_file):
    pid = read_alive_pid(pid_file)
    if not pid:
        return None
    try:
        import psutil
        args = psutil.Process(pid).cmdline()
        return None if any(mode in args for mode in ('--server', '--client', '--gui', '--coordinator')) else pid
    except Exception:
        return None


def write_pid(pid_file, pid):
    Path(pid_file).parent.mkdir(parents=True, exist_ok=True)
    atomic_json(_identity_file(pid_file), {'pid': pid, 'birth': process_birth(pid), 'root': str(BASE_DIR)})
    Path(pid_file).write_text(str(pid), encoding='utf-8')


def find_listening_pid(port):
    try:
        import psutil
        for conn in psutil.net_connections(kind='inet'):
            if conn.status == psutil.CONN_LISTEN and conn.laddr.port == int(port):
                return conn.pid
    except Exception:
        pass
    return None


def _background_tree_root():
    """Return only this installation's live, descriptor-bound coordinator."""
    try:
        import psutil
        info = read_json(data_root() / 'runtime' / 'coordinator.json', {})
        if not isinstance(info, dict) or not same_process(info.get('pid'), info.get('birth')):
            return None
        process = psutil.Process(info['pid'])
        if not _owned_command(process) or '--coordinator' not in process.cmdline():
            return None
        return info['pid'], info['birth']
    except Exception:
        return None


def _background_tree_roots():
    """ASR/client can also be direct GUI children after a diagnostics restart."""
    owners = []
    coordinator = _background_tree_root()
    if coordinator is not None:
        owners.append(coordinator)
    for pid_file, script in ((SERVER_PID_FILE, 'start_server.py'), (CLIENT_PID_FILE, 'start_client.py')):
        try:
            identity = read_json(_identity_file(pid_file), {})
            if not isinstance(identity, dict):
                continue
            pid = read_alive_pid(pid_file, component_keyword(script))
            if (pid and identity.get('pid') == pid
                    and same_process(pid, identity.get('birth'))):
                owners.append((pid, identity['birth']))
        except Exception:
            pass
    return owners


def _in_background_tree(process, owners):
    if not owners:
        return False
    try:
        for ancestor in [process, *process.parents()]:
            for owner_pid, owner_birth in owners:
                if ancestor.pid == owner_pid and abs(ancestor.create_time() - owner_birth) < 0.01:
                    return True
    except Exception:
        # During GUI-only close, uncertainty must not kill a background task.
        return True
    return False


def _child_identities(process, preserved_owners):
    result = []
    for child in process.children(recursive=True):
        if not _in_background_tree(child, preserved_owners):
            try:
                result.append((child, child.create_time()))
            except Exception:
                continue
    return result


def close_windows_by_pid(pid, include_children=False, *, preserve_background=False):
    if sys.platform != 'win32' or not is_process_alive(pid):
        return
    try:
        import ctypes
        from ctypes import wintypes
        import psutil
        process = psutil.Process(pid)
        owners = _background_tree_roots() if preserve_background else []
        if _in_background_tree(process, owners):
            return
        identities = {pid: process.create_time()}
        if include_children:
            identities.update({child.pid: birth for child, birth in _child_identities(process, owners)})
        def callback(hwnd, _):
            value = wintypes.DWORD()
            ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(value))
            if value.value in identities and same_process(value.value, identities[value.value]):
                ctypes.windll.user32.PostMessageW(hwnd, 0x0010, 0, 0)
            return True
        cb = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(callback)
        ctypes.windll.user32.EnumWindows(cb, 0)
    except Exception:
        pass


def stop_pid(pid, force_self=False, *, preserve_background=False):
    if not pid or (pid == os.getpid() and not force_self) or not is_process_alive(pid):
        return False
    try:
        import psutil
        process = psutil.Process(pid)
        birth = process.create_time()
        owners = _background_tree_roots() if preserve_background else []
        if _in_background_tree(process, owners):
            return False
        children = _child_identities(process, owners)
        close_windows_by_pid(pid, include_children=True, preserve_background=preserve_background)
        for child, created in reversed(children):
            if same_process(child.pid, created):
                child.terminate()
        if same_process(pid, birth):
            process.terminate()
        _, alive = psutil.wait_procs([p for p, _ in children] + [process], timeout=2)
        identities = {child.pid: created for child, created in children}
        identities[pid] = birth
        for proc in alive:
            if same_process(proc.pid, identities.get(proc.pid)):
                proc.kill()
        return True
    except Exception:
        return False


def find_project_processes(patterns, include_self=False):
    try:
        import psutil
        result = []
        for proc in psutil.process_iter(['pid', 'cmdline']):
            try:
                if (proc.pid != os.getpid() or include_self) and _owned_command(proc):
                    cmd = ' '.join(proc.cmdline()).replace('\\', '/').lower()
                    if any(p.replace('\\', '/').lower() in cmd for p in patterns):
                        result.append(proc.pid)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        return result
    except Exception:
        return []


def _launch(script_name, pid_file, model_id=None):
    bootstrap()
    env = dict(os.environ, CAPSWRITER_CONTROL_CENTER='1')
    if model_id:
        env['CAPSWRITER_MODEL_ID'] = model_id
    else:
        env.pop('CAPSWRITER_MODEL_ID', None)
    if is_frozen_app():
        mode = {'start_server.py': '--server', 'start_client.py': '--client'}.get(script_name)
        if not mode:
            return None
        command = [sys.executable, mode]
    else:
        script = BASE_DIR / script_name
        if not script.is_file():
            return None
        command = [python_executable(prefer_console=False), str(script)]
    process = subprocess.Popen(command, cwd=str(data_root()), env=env, creationflags=CREATE_NO_WINDOW)
    write_pid(pid_file, process.pid)
    return process.pid


def launch_server(model_id=None):
    owned = read_alive_pid(SERVER_PID_FILE, component_keyword('start_server.py'))
    if owned:
        return True, f'ASR 进程已启动，模型状态请查看实时状态。PID: {owned}'
    if is_port_open('127.0.0.1', 6016):
        return False, '6016 端口被其他或未确认归属的程序占用；不会接管或终止它。'
    pid = _launch('start_server.py', SERVER_PID_FILE, model_id)
    return bool(pid), f'正在加载 ASR 模型。PID: {pid}' if pid else '未找到服务端入口。'


def launch_client():
    pid = read_alive_pid(CLIENT_PID_FILE, component_keyword('start_client.py'))
    if pid:
        return True, f'听写客户端已启动。PID: {pid}'
    if not health_snapshot()['model_loaded']:
        return False, '模型尚未就绪，请先在模型中心加载；控制中心仍可使用。'
    pid = _launch('start_client.py', CLIENT_PID_FILE)
    return bool(pid), f'正在启动听写客户端。PID: {pid}' if pid else '未找到客户端入口。'


def launch_missing_components():
    ok, message = launch_server()
    if ok and health_snapshot()['model_loaded']:
        ok2, message2 = launch_client()
        return ok and ok2, message + '；' + message2
    return ok, message


def _busy():
    from core.product.status import read_status
    client = read_status('client')
    if client.get('recording') or client.get('pending_results'):
        return True
    try:
        from core.product.bridge import request
        return bool(request('snapshot').get('tasks_busy'))
    except Exception:
        return read_status('inference').get('state') == 'busy'


def restart_server():
    if _busy():
        return False, '正在录音、处理听写结果或转写，请等待任务完成/安全停止后重启。'
    pid = read_alive_pid(SERVER_PID_FILE, component_keyword('start_server.py'))
    if pid and not stop_pid(pid):
        return False, '无法安全停止所属服务端。'
    return launch_server()


def restart_client():
    from core.product.status import read_status
    client = read_status('client')
    if client.get('recording') or client.get('pending_results'):
        return False, '正在录音或处理听写结果，请先完成本次听写。'
    pid = read_alive_pid(CLIENT_PID_FILE, component_keyword('start_client.py'))
    if pid and not stop_pid(pid):
        return False, '无法安全停止所属客户端。'
    return launch_client()


def stop_gui(include_self=False):
    pid = read_alive_pid(GUI_PID_FILE, component_keyword('app.py'))
    return stop_pid(pid, force_self=include_self, preserve_background=True) if pid else False


def stop_all(include_self=False, *, coordinator_checked=False):
    if not coordinator_checked:
        try:
            from core.product.bridge import request
            result = request('shutdown', timeout=4)
            if result.get('draining'):
                return {'waiting': True, 'message': '正在安全停止转写，完成前保留控制中心。'}
        except Exception:
            if _busy():
                return {'waiting': True, 'message': '无法确认任务已安全停止，请保留窗口检查。'}
    result = {}
    for name, path in (('client', CLIENT_PID_FILE), ('server', SERVER_PID_FILE)):
        pid = read_alive_pid(path)
        result[name] = bool(pid and stop_pid(pid))
    result['gui'] = stop_gui(include_self=include_self)
    launcher = read_alive_control_pid(LOG_DIR / 'control_center.pid')
    result['launcher'] = bool(launcher and launcher != os.getpid() and stop_pid(launcher))
    return result


def get_health_status():
    return health_snapshot()
