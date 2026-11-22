"""Resource paths and writable per-installation data, with legacy source support.

No migration is implicit. Explicit environment overrides make tests/worktrees
independent of a user's real settings and models.
"""
from __future__ import annotations

import ast
import os
import shutil
import sys
from pathlib import Path


def resource_root() -> Path:
    return Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[2])).resolve()


def app_root() -> Path:
    return Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else resource_root()


def is_portable() -> bool:
    return (app_root() / 'portable.flag').is_file()


def data_root() -> Path:
    override = os.environ.get('CAPSWRITER_DATA_DIR')
    if override:
        return Path(override).expanduser().resolve()
    if not getattr(sys, 'frozen', False):
        return resource_root()
    if is_portable():
        return app_root() / 'data'
    local_app_data = os.environ.get('LOCALAPPDATA')
    if local_app_data:
        return Path(local_app_data) / 'CapsWriter'
    return Path.home() / '.local' / 'share' / 'CapsWriter'


def model_root() -> Path:
    override = os.environ.get('CAPSWRITER_MODEL_DIR')
    if override:
        return Path(override).expanduser().resolve()
    if is_portable() and not os.environ.get('CAPSWRITER_DATA_DIR'):
        return app_root() / 'models'
    return data_root() / 'models'


def bootstrap() -> Path:
    """Create missing defaults only; never replace user configuration or data."""
    root = data_root()
    for part in ('logs', 'web_gui', 'runtime', 'product'):
        (root / part).mkdir(parents=True, exist_ok=True)
    source = resource_root()
    defaults = source / 'packaging' / 'release_defaults'
    names = ['config_client.py', 'config_server.py', 'config_pill.py',
             'hot.txt', 'hot-rule.txt', 'hot-server.txt',
             'LLM/default.py', 'LLM/__init__.py',
             'web_gui/private_config.json', 'web_gui/llm_profiles.json']
    # Install clean, disabled built-in roles on a new profile only. Existing
    # role edits and deliberate deletions must not be undone at every launch.
    if not (root / 'LLM').exists():
        names.extend(('LLM/小助理.py', 'LLM/大助理.py', 'LLM/翻译.py'))
    for name in names:
        target = root / name
        if target.exists():
            continue
        candidate = defaults / name
        if not candidate.is_file() and not getattr(sys, 'frozen', False):
            candidate = source / name
        if not candidate.is_file() or candidate.resolve() == target.resolve():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation keeps two startup components from replacing defaults.
        try:
            with target.open('xb') as out, candidate.open('rb') as inp:
                shutil.copyfileobj(inp, out)
        except FileExistsError:
            pass
    return root


def apply_config_overrides(module_name: str, namespace: dict) -> None:
    """Read literal settings from editable config copies in a frozen build.

    Never execute an imported backup or arbitrary code from the writable folder.
    Resource-relative ModelPaths remain computed by the installed application.
    """
    path = data_root() / (module_name.rsplit('.', 1)[-1] + '.py')
    if not path.is_file() or path.resolve() == Path(namespace.get('__file__', '')).resolve():
        return
    tree = ast.parse(path.read_text(encoding='utf-8-sig'), filename=str(path))
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name == 'ModelPaths':
            continue
        target = namespace.get(node.name)
        if not isinstance(target, type):
            continue
        for statement in node.body:
            if not isinstance(statement, ast.Assign) or len(statement.targets) != 1:
                continue
            name = statement.targets[0]
            if not isinstance(name, ast.Name) or name.id.startswith('_') or not hasattr(target, name.id):
                continue
            try:
                value = ast.literal_eval(statement.value)
            except (ValueError, TypeError):
                continue
            setattr(target, name.id, value)
