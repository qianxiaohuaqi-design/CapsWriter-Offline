"""Authenticated loopback IPC between windows and their persistent coordinator."""
from __future__ import annotations
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from .paths import data_root, resource_root, bootstrap
from .storage import read_json
from .status import same_process


def descriptor():
    try:
        value = read_json(data_root() / 'runtime' / 'coordinator.json', {})
        if (not isinstance(value, dict) or not same_process(value.get('pid'), value.get('birth'))
                or not isinstance(value.get('port'), int) or not 1 <= value['port'] <= 65535
                or not isinstance(value.get('token'), str) or len(value['token']) < 32):
            raise RuntimeError('后台管理器未运行，请重新打开 CapsWriter。')
        return value
    except (OSError, ValueError) as error:
        raise RuntimeError('后台连接记录不可读，原记录已保留。') from error


def request(action='snapshot', *, timeout=5, **payload):
    info = descriptor()
    body = json.dumps({'action': action, **payload}, ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(f"http://127.0.0.1:{info['port']}/api", data=body,
                                 headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + info['token']})
    # Loopback must never route through a user's system proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as response:
            result = json.loads(response.read(8 * 1024 * 1024))
    except (OSError, urllib.error.URLError, ValueError) as error:
        raise RuntimeError('后台暂时无法连接，请保留窗口并重试。') from error
    if not result.get('ok'):
        raise ValueError(result.get('message', '操作未完成。'))
    return result.get('data')


def ensure_coordinator(timeout=12):
    try:
        return request('snapshot', timeout=1)
    except Exception:
        pass
    bootstrap()
    env = dict(os.environ, CAPSWRITER_CONTROL_CENTER='1')
    if getattr(sys, 'frozen', False):
        cmd = [sys.executable, '--coordinator']
    else:
        cmd = [sys.executable, str(resource_root() / 'run_app.py'), '--coordinator']
    log = data_root() / 'logs' / 'coordinator-startup.log'
    with log.open('a', encoding='utf-8') as stream:
        subprocess.Popen(cmd, cwd=str(data_root()), env=env, stdout=stream, stderr=stream,
                         creationflags=0x08000000 if sys.platform == 'win32' else 0)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            return request('snapshot', timeout=1)
        except Exception:
            time.sleep(0.15)
    raise RuntimeError('后台管理器未能启动；请查看 coordinator-startup.log。')
