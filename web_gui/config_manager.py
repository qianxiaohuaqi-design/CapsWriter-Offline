# coding: utf-8
"""
CapsWriter 全量配置管理器 (Full Spectrum Config Manager)
全面管控 CapsWriter-Offline 客户端、服务端及 LLM 润色功能的所有配置项。
包含全量配置的导出打包与导入解析还原能力。
"""

import os
import re
import json
import shutil
import ast
import uuid
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

from web_gui.private_config import (
    get_llm_api_key,
    get_llm_profile_api_key,
    get_llm_role_api_key,
    set_llm_api_key,
    set_llm_profile_api_key,
    set_llm_role_api_key,
)

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_CLIENT_PATH = BASE_DIR / 'config_client.py'
CONFIG_SERVER_PATH = BASE_DIR / 'config_server.py'
LLM_DEFAULT_PATH = BASE_DIR / 'LLM' / 'default.py'
LLM_DIR = BASE_DIR / 'LLM'
LLM_PROFILES_PATH = BASE_DIR / 'web_gui' / 'llm_profiles.json'
GUI_SETTINGS_PATH = BASE_DIR / 'web_gui' / 'gui_settings.json'
HOT_TXT_PATH = BASE_DIR / 'hot.txt'
HOT_RULE_TXT_PATH = BASE_DIR / 'hot-rule.txt'
PILL_CONFIG_PATH = BASE_DIR / 'config_pill.py'
CONFIG_BACKUP_DIR = BASE_DIR / 'web_gui' / 'config_backups'
CONFIG_EXPORT_DIR = BASE_DIR / 'web_gui' / 'config_exports'

PROVIDER_OPTIONS = {
    'deepseek': 'DeepSeek API',
    'openai': 'OpenAI API',
    'ollama': '本地 Ollama',
    'lmstudio': 'LM Studio',
    'moonshot': 'Moonshot',
    'zhipu': '智谱',
    'volcengine': '火山方舟',
    'cerebras': 'Cerebras',
    'custom': '自定义 OpenAI 兼容 / 中转站',
}

PROVIDER_DEFAULT_URLS = {
    'ollama': 'http://127.0.0.1:11434',
    'lmstudio': 'http://127.0.0.1:1234/v1',
    'openai': 'https://api.openai.com/v1',
    'deepseek': 'https://api.deepseek.com/v1',
    'moonshot': 'https://api.moonshot.cn/v1',
    'zhipu': 'https://open.bigmodel.cn/api/paas/v4',
    'volcengine': 'https://ark.cn-beijing.volces.com/api/v3',
    'cerebras': 'https://api.cerebras.ai/v1',
    'custom': '',
}

MANAGED_CONFIG_PATHS = {
    'config_client.py': CONFIG_CLIENT_PATH,
    'config_server.py': CONFIG_SERVER_PATH,
    'LLM/default.py': LLM_DEFAULT_PATH,
    'web_gui/llm_profiles.json': LLM_PROFILES_PATH,
    'hot.txt': HOT_TXT_PATH,
    'hot-rule.txt': HOT_RULE_TXT_PATH,
    'config_pill.py': PILL_CONFIG_PATH,
}


class ConfigManager:
    @staticmethod
    def load_gui_settings() -> dict:
        defaults = {'auto_config_backup_enabled': True}
        if not GUI_SETTINGS_PATH.exists():
            return defaults
        try:
            data = json.loads(GUI_SETTINGS_PATH.read_text(encoding='utf-8'))
            return {**defaults, **data}
        except Exception:
            return defaults

    @staticmethod
    def save_gui_settings(settings: dict) -> bool:
        GUI_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        GUI_SETTINGS_PATH.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding='utf-8')
        return True

    @staticmethod
    def get_auto_config_backup_enabled() -> bool:
        return bool(ConfigManager.load_gui_settings().get('auto_config_backup_enabled', True))

    @staticmethod
    def set_auto_config_backup_enabled(enabled: bool) -> bool:
        settings = ConfigManager.load_gui_settings()
        settings['auto_config_backup_enabled'] = bool(enabled)
        return ConfigManager.save_gui_settings(settings)

    @staticmethod
    def provider_options():
        return PROVIDER_OPTIONS.copy()

    @staticmethod
    def default_api_url(provider: str) -> str:
        return PROVIDER_DEFAULT_URLS.get(provider, '')

    @staticmethod
    def _profile_id(name: str) -> str:
        slug = re.sub(r'[^a-zA-Z0-9_-]+', '-', (name or '').strip()).strip('-').lower()
        return slug or f'profile-{uuid.uuid4().hex[:8]}'

    @staticmethod
    def _default_profiles_data():
        return {
            'active_profile': '',
            'profiles': [],
        }

    @staticmethod
    def load_llm_profiles(include_keys: bool = False) -> dict:
        if not LLM_PROFILES_PATH.exists():
            data = ConfigManager._default_profiles_data()
            ConfigManager.write_text_with_backup(LLM_PROFILES_PATH, json.dumps(data, ensure_ascii=False, indent=2))
        else:
            try:
                data = json.loads(LLM_PROFILES_PATH.read_text(encoding='utf-8'))
            except Exception:
                data = ConfigManager._default_profiles_data()

        profiles = data.setdefault('profiles', [])
        if profiles:
            data.setdefault('active_profile', profiles[0]['id'])
        else:
            data['active_profile'] = ''

        for profile in profiles:
            profile.setdefault('id', ConfigManager._profile_id(profile.get('name', 'profile')))
            profile.setdefault('name', profile['id'])
            profile.setdefault('provider', 'deepseek')
            profile.setdefault('api_url', '')
            profile.setdefault('models', [])
            profile.setdefault('default_model', profile['models'][0] if profile['models'] else '')
            if include_keys:
                profile['api_key'] = get_llm_profile_api_key(profile['id'])
        return data

    @staticmethod
    def save_llm_profiles(data: dict) -> bool:
        cleaned = {
            'active_profile': data.get('active_profile', ''),
            'profiles': [],
        }
        seen = set()
        for profile in data.get('profiles', []):
            profile_id = profile.get('id') or ConfigManager._profile_id(profile.get('name', 'profile'))
            if profile_id in seen:
                profile_id = f'{profile_id}-{uuid.uuid4().hex[:4]}'
            seen.add(profile_id)
            models = [str(model).strip() for model in profile.get('models', []) if str(model).strip()]
            default_model = str(profile.get('default_model') or (models[0] if models else '')).strip()
            if default_model and default_model not in models:
                models.insert(0, default_model)
            cleaned['profiles'].append({
                'id': profile_id,
                'name': str(profile.get('name') or profile_id).strip(),
                'provider': str(profile.get('provider') or 'deepseek').strip(),
                'api_url': str(profile.get('api_url') or '').strip(),
                'models': models,
                'default_model': default_model,
            })
        if cleaned['profiles'] and cleaned['active_profile'] not in {p['id'] for p in cleaned['profiles']}:
            cleaned['active_profile'] = cleaned['profiles'][0]['id']
        ConfigManager.write_text_with_backup(LLM_PROFILES_PATH, json.dumps(cleaned, ensure_ascii=False, indent=2))
        return True

    @staticmethod
    def export_llm_roles() -> list[dict]:
        roles = []
        if not LLM_DIR.exists():
            return roles
        for path in sorted(LLM_DIR.glob('*.py')):
            if path.name in {'__init__.py', 'default.py'}:
                continue
            content = path.read_text(encoding='utf-8')
            content = re.sub(r"(^api_key\s*=\s*')[^']*(')", r"\1\2", content, flags=re.MULTILINE)
            roles.append({'file': path.name, 'content': content})
        return roles

    @staticmethod
    def import_llm_roles(roles: list[dict]) -> int:
        if not isinstance(roles, list):
            return 0
        imported = 0
        LLM_DIR.mkdir(parents=True, exist_ok=True)
        for role in roles:
            name = str(role.get('file') or '').strip()
            content = str(role.get('content') or '')
            if not name.endswith('.py') or name in {'__init__.py', 'default.py'} or not content:
                continue
            safe_name = re.sub(r'[\\/:*?"<>|\s]+', '_', name).strip('._')
            if not safe_name.endswith('.py'):
                continue
            target = (LLM_DIR / safe_name).resolve()
            if not str(target).startswith(str(LLM_DIR.resolve())):
                continue
            content = re.sub(r"(^api_key\s*=\s*')[^']*(')", r"\1\2", content, flags=re.MULTILINE)
            ConfigManager.write_text_with_backup(target, content)
            set_llm_role_api_key(f'LLM.{target.stem}', '')
            imported += 1
        return imported

    @staticmethod
    def get_llm_profile(profile_id: str | None = None, include_key: bool = False) -> dict | None:
        data = ConfigManager.load_llm_profiles(include_keys=include_key)
        target_id = profile_id or data.get('active_profile')
        return next((p for p in data.get('profiles', []) if p.get('id') == target_id), None)

    @staticmethod
    def upsert_llm_profile(profile: dict, api_key: str | None = None, set_active: bool = False) -> str:
        data = ConfigManager.load_llm_profiles()
        profiles = data.get('profiles', [])
        profile_id = profile.get('id') or ConfigManager._profile_id(profile.get('name', 'profile'))
        current = next((p for p in profiles if p.get('id') == profile_id), None)
        if current is None:
            current = {'id': profile_id}
            profiles.append(current)
        current.update({
            'name': profile.get('name') or profile_id,
            'provider': profile.get('provider') or 'deepseek',
            'api_url': profile.get('api_url') or '',
            'models': profile.get('models') or [],
            'default_model': profile.get('default_model') or '',
        })
        if set_active or not data.get('active_profile'):
            data['active_profile'] = profile_id
        ConfigManager.save_llm_profiles(data)
        if api_key is not None:
            set_llm_profile_api_key(profile_id, api_key)
        return profile_id

    @staticmethod
    def delete_llm_profile(profile_id: str) -> bool:
        data = ConfigManager.load_llm_profiles()
        profiles = [p for p in data.get('profiles', []) if p.get('id') != profile_id]
        data['profiles'] = profiles
        if data.get('active_profile') == profile_id:
            data['active_profile'] = profiles[0]['id'] if profiles else ''
        ConfigManager.save_llm_profiles(data)
        set_llm_profile_api_key(profile_id, '')
        return True

    @staticmethod
    def fetch_llm_models(provider: str, api_url: str = '', api_key: str = '') -> tuple[bool, list[str] | str]:
        base_url = (api_url or ConfigManager.default_api_url(provider)).rstrip('/')
        if not base_url:
            return False, '缺少 API 地址'

        if provider == 'ollama':
            url = f'{base_url}/api/tags'
            headers = {}
        else:
            url = f'{base_url}/models'
            headers = {'Authorization': f'Bearer {api_key}'} if api_key else {}

        try:
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=8) as response:
                payload = json.loads(response.read().decode('utf-8', errors='ignore'))
        except urllib.error.HTTPError as e:
            return False, f'模型列表请求失败：HTTP {e.code}'
        except Exception as e:
            return False, f'模型列表请求失败：{e}'

        if provider == 'ollama':
            models = [item.get('name') for item in payload.get('models', []) if item.get('name')]
        else:
            models = [item.get('id') for item in payload.get('data', []) if item.get('id')]
        models = sorted(dict.fromkeys(models))
        return (True, models) if models else (False, '没有从接口返回可用模型')

    @staticmethod
    def apply_llm_profile_to_default(profile_id: str, model: str | None = None) -> bool:
        profile = ConfigManager.get_llm_profile(profile_id, include_key=True)
        if not profile:
            return False
        selected_model = model or profile.get('default_model') or (profile.get('models') or [''])[0]
        ConfigManager.set_llm_default_config(
            provider=profile.get('provider'),
            api_url=profile.get('api_url'),
            api_key=profile.get('api_key', ''),
            model=selected_model,
        )
        ConfigManager._set_python_var(LLM_DEFAULT_PATH, 'profile_id', profile_id)
        return True

    @staticmethod
    def _get_python_var(path: Path, var_name: str, default):
        if not path.exists():
            return default
        try:
            tree = ast.parse(path.read_text(encoding='utf-8'))
            assignment = next(
                (
                    node
                    for node in ast.walk(tree)
                    if isinstance(node, (ast.Assign, ast.AnnAssign))
                    and any(
                        isinstance(target, ast.Name) and target.id == var_name
                        for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
                    )
                ),
                None,
            )
            return ast.literal_eval(assignment.value) if assignment is not None else default
        except (OSError, SyntaxError, ValueError, TypeError):
            return default

    @staticmethod
    def _get_python_class_var(path: Path, class_name: str, var_name: str, default):
        if not path.exists():
            return default
        try:
            tree = ast.parse(path.read_text(encoding='utf-8'))
            class_node = next(
                (node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name),
                None,
            )
            if class_node is None:
                return default
            assignment = next(
                (
                    node
                    for node in class_node.body
                    if isinstance(node, (ast.Assign, ast.AnnAssign))
                    and any(
                        isinstance(target, ast.Name) and target.id == var_name
                        for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
                    )
                ),
                None,
            )
            return ast.literal_eval(assignment.value) if assignment is not None else default
        except (OSError, SyntaxError, ValueError, TypeError):
            return default

    @staticmethod
    def _set_python_var(path: Path, var_name: str, value) -> bool:
        """Replace one Python assignment, including a multiline value."""
        if not path.exists():
            return False

        content = path.read_text(encoding='utf-8')
        tree = ast.parse(content)
        assignment = next(
            (
                node
                for node in ast.walk(tree)
                if isinstance(node, (ast.Assign, ast.AnnAssign))
                and any(
                    isinstance(target, ast.Name) and target.id == var_name
                    for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
                )
            ),
            None,
        )

        value_text = repr(value)
        if assignment is None:
            content += f'\n{var_name} = {value_text}\n'
        else:
            lines = content.splitlines(keepends=True)
            start = sum(len(line) for line in lines[:assignment.lineno - 1]) + assignment.col_offset
            end = sum(len(line) for line in lines[:assignment.end_lineno - 1]) + assignment.end_col_offset
            content = f'{content[:start]}{var_name} = {value_text}{content[end:]}'

        ConfigManager.write_text_with_backup(path, content)
        return True

    @staticmethod
    def _set_python_class_var(path: Path, class_name: str, var_name: str, value) -> bool:
        """Replace one assignment inside a Python class body."""
        if not path.exists():
            return False

        content = path.read_text(encoding='utf-8')
        tree = ast.parse(content)
        class_node = next(
            (node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name),
            None,
        )
        if class_node is None:
            return False

        assignment = next(
            (
                node
                for node in class_node.body
                if isinstance(node, (ast.Assign, ast.AnnAssign))
                and any(
                    isinstance(target, ast.Name) and target.id == var_name
                    for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
                )
            ),
            None,
        )

        value_text = repr(value)
        lines = content.splitlines(keepends=True)
        if assignment is None:
            insert_at = sum(len(line) for line in lines[:class_node.end_lineno - 1])
            content = f'{content[:insert_at]}    {var_name} = {value_text}\n{content[insert_at:]}'
        else:
            start = sum(len(line) for line in lines[:assignment.lineno - 1]) + assignment.col_offset
            end = sum(len(line) for line in lines[:assignment.end_lineno - 1]) + assignment.end_col_offset
            content = f'{content[:start]}{var_name} = {value_text}{content[end:]}'

        ConfigManager.write_text_with_backup(path, content)
        return True

    @staticmethod
    def _backup_name(path: Path, timestamp: str) -> str:
        relative = path.relative_to(BASE_DIR).as_posix().replace('/', '__')
        return f'{relative}.{timestamp}.bak'

    @staticmethod
    def write_text_with_backup(path: Path, content: str) -> bool:
        """写配置前自动备份旧文件，并用临时文件原子替换。"""
        path = Path(path)
        old_content = path.read_text(encoding='utf-8') if path.exists() else None
        if old_content == content:
            return True

        if ConfigManager.get_auto_config_backup_enabled() and path.exists():
            CONFIG_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
            backup_path = CONFIG_BACKUP_DIR / ConfigManager._backup_name(path, timestamp)
            shutil.copy2(path, backup_path)

        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_name(f'{path.name}.tmp')
        tmp_path.write_text(content, encoding='utf-8')
        os.replace(tmp_path, path)
        return True

    @staticmethod
    def list_config_backups(limit: int = 30):
        if not CONFIG_BACKUP_DIR.exists():
            return []
        backups = []
        for path in CONFIG_BACKUP_DIR.glob('*.bak'):
            target_key = path.name[:-4].rsplit('.', 1)[0].replace('__', '/')
            backups.append({
                'backup': path,
                'target_key': target_key,
                'target_path': MANAGED_CONFIG_PATHS.get(target_key, BASE_DIR / target_key),
                'created_at': datetime.fromtimestamp(path.stat().st_mtime).strftime('%Y-%m-%d %H:%M:%S'),
            })
        return sorted(backups, key=lambda item: item['backup'].stat().st_mtime, reverse=True)[:limit]

    @staticmethod
    def clear_config_backups() -> tuple[int, int]:
        if not CONFIG_BACKUP_DIR.exists():
            return 0, 0
        deleted = 0
        failed = 0
        for path in CONFIG_BACKUP_DIR.glob('*.bak'):
            if not path.is_file():
                continue
            try:
                path.unlink()
                deleted += 1
            except OSError:
                failed += 1
        return deleted, failed

    @staticmethod
    def restore_config_backup(backup_path: str | Path):
        backup = Path(backup_path)
        if not backup.exists() or backup.suffix != '.bak':
            return False, f'备份文件不存在：{backup}'

        target_key = backup.name[:-4].rsplit('.', 1)[0].replace('__', '/')
        target = MANAGED_CONFIG_PATHS.get(target_key, BASE_DIR / target_key)
        content = backup.read_text(encoding='utf-8')
        ConfigManager.write_text_with_backup(target, content)
        return True, f'已恢复：{target_key}'

    @staticmethod
    def get_all_config():
        return {
            # 通用与按键外观
            'shortcut': ConfigManager.get_active_shortcut(),
            'hold_mode': ConfigManager.get_hold_mode(),
            'paste_mode': ConfigManager.get_client_var('paste', False),
            'output_destination': ConfigManager.get_client_var('output_destination', 'typing'),
            'preview_close_mode': ConfigManager.get_client_var('preview_close_mode', 'auto'),
            'preview_base_seconds': ConfigManager.get_client_var('preview_base_seconds', 8),
            'preview_seconds_per_20_chars': ConfigManager.get_client_var('preview_seconds_per_20_chars', 1),
            'preview_max_seconds': ConfigManager.get_client_var('preview_max_seconds', 60),
            'paste_apps': ConfigManager.get_client_var('paste_apps', ['WeiXin.exe', 'Telegram.exe']),
            'enter_apps': ConfigManager.get_client_var('enter_apps', [('happ.exe', 0.5), ('hexin.exe', 0.5)]),
            'save_audio': ConfigManager.get_client_var('save_audio', False),
            'save_diary': ConfigManager.get_client_var('save_diary', False),
            'pill_overlay': ConfigManager.get_pill_overlay_enabled(),

            # 语音引擎与硬件
            'model_type': ConfigManager.get_server_var('model_type', 'sensevoice'),
            'format_num': ConfigManager.get_server_var('format_num', True),
            'format_spell': ConfigManager.get_server_var('format_spell', True),
            'aligner_timeout': ConfigManager.get_server_var('aligner_idle_timeout', 10),
            'gpu_boost': ConfigManager.get_server_var('gpu_boost_enabled', False),
            'gpu_boost_cmd': ConfigManager.get_server_var('gpu_boost_cmd', 'nvidia-smi -lmc 9000'),
            'gpu_unboost_cmd': ConfigManager.get_server_var('gpu_unboost_cmd', 'nvidia-smi -rmc'),
            'gpu_unboost_timeout': ConfigManager.get_server_var('gpu_unboost_timeout', 1),
            'sensevoice_onnx_provider': ConfigManager.get_server_class_var('SenseVoiceArgs', 'onnx_provider', 'CPU'),
            'sensevoice_dml_pad_to': ConfigManager.get_server_class_var('SenseVoiceArgs', 'dml_pad_to', 30),
            'fun_asr_onnx_provider': ConfigManager.get_server_class_var('FunASRNanoGGUFArgs', 'onnx_provider', 'CPU'),
            'fun_asr_llm_use_gpu': ConfigManager.get_server_class_var('FunASRNanoGGUFArgs', 'llm_use_gpu', True),
            'fun_asr_dml_pad_to': ConfigManager.get_server_class_var('FunASRNanoGGUFArgs', 'dml_pad_to', 30),
            'qwen_asr_onnx_provider': ConfigManager.get_server_class_var('Qwen3ASRGGUFArgs', 'onnx_provider', 'CPU'),
            'qwen_asr_llm_use_gpu': ConfigManager.get_server_class_var('Qwen3ASRGGUFArgs', 'llm_use_gpu', True),
            'qwen_asr_dml_pad_to': ConfigManager.get_server_class_var('Qwen3ASRGGUFArgs', 'dml_pad_to', 30),
            'aligner_onnx_provider': ConfigManager.get_server_class_var('ForceAlignerGGUFArgs', 'onnx_provider', 'CPU'),
            'aligner_llm_use_gpu': ConfigManager.get_server_class_var('ForceAlignerGGUFArgs', 'llm_use_gpu', False),
            'aligner_dml_pad_to': ConfigManager.get_server_class_var('ForceAlignerGGUFArgs', 'dml_pad_to', 30),
            'traditional_convert': ConfigManager.get_client_var('traditional_convert', False),
            'language': ConfigManager.get_client_var('language', 'auto'),
            'mic_seg_duration': ConfigManager.get_client_var('mic_seg_duration', 60),
            'mic_seg_overlap': ConfigManager.get_client_var('mic_seg_overlap', 4),
            'file_seg_duration': ConfigManager.get_client_var('file_seg_duration', 60),
            'file_seg_overlap': ConfigManager.get_client_var('file_seg_overlap', 4),

            # AI 润色与大模型
            'llm_enabled': ConfigManager.get_llm_enabled(),
            'llm_config': ConfigManager.get_llm_default_config(),
            'llm_profiles': ConfigManager.load_llm_profiles(include_keys=False),

            # 热词与正则
            'hot_enabled': ConfigManager.get_client_var('hot', True),
            'hot_rule_enabled': ConfigManager.get_client_var('hot_rule', True),
            'hot_thresh': ConfigManager.get_client_var('hot_thresh', 0.85),
            'hot_similar': ConfigManager.get_client_var('hot_similar', 0.6),

            # 字幕与转录
            'file_save_srt': ConfigManager.get_client_var('file_save_srt', True),
            'file_save_txt': ConfigManager.get_client_var('file_save_txt', True),
            'file_save_json': ConfigManager.get_client_var('file_save_json', True),
            'file_save_merge': ConfigManager.get_client_var('file_save_merge', False),
        }

    # --- 全量配置导出 (Export) ---
    @staticmethod
    def export_full_config():
        hot_text = HOT_TXT_PATH.read_text(encoding='utf-8') if HOT_TXT_PATH.exists() else ""
        hot_rule_text = HOT_RULE_TXT_PATH.read_text(encoding='utf-8') if HOT_RULE_TXT_PATH.exists() else ""
        configs = ConfigManager.get_all_config()
        if isinstance(configs.get('llm_config'), dict):
            configs['llm_config'].pop('api_key', None)
        
        full_data = {
            'app_name': 'CapsWriter-ControlCenter',
            'version': '2.0',
            'export_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'contains_api_keys': False,
            'excluded_data': ['api_keys', 'local_models', 'recordings', 'input_history', 'diary_markdown', 'transcription_outputs', 'logs'],
            'configs': configs,
            'hot_words_content': hot_text,
            'hot_rules_content': hot_rule_text,
            'llm_roles': ConfigManager.export_llm_roles(),
        }
        return full_data

    @staticmethod
    def export_full_config_to_file() -> Path:
        CONFIG_EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        target = CONFIG_EXPORT_DIR / f'caps_writer_config_backup_{timestamp}.json'
        data = ConfigManager.export_full_config()
        target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        return target

    @staticmethod
    def import_full_config_file(path: str | Path):
        path = Path(path)
        if not path.exists():
            return False, f'文件不存在：{path}'
        try:
            return ConfigManager.import_full_config(path.read_text(encoding='utf-8'))
        except Exception as e:
            return False, f'读取导入文件失败：{e}'

    # --- 全量配置导入 (Import) ---
    @staticmethod
    def import_full_config(data):
        """
        解析并校验配置字典或 JSON 文本，回写所有配置文件
        """
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except Exception as e:
                return False, f"JSON 解析失败: {str(e)}"
        
        if not isinstance(data, dict) or 'configs' not in data:
            return False, "无效的配置备份数据结构"

        configs = data.get('configs', {})
        
        # 1. 还原按键与触发模式
        if 'shortcut' in configs:
            ConfigManager.set_active_shortcut(configs['shortcut'])
        if 'hold_mode' in configs:
            ConfigManager.set_hold_mode(configs['hold_mode'])

        # 2. 还原客户端变量
        if 'paste_mode' in configs:
            ConfigManager.set_client_var('paste', configs['paste_mode'])
        if 'output_destination' in configs:
            ConfigManager.set_client_var('output_destination', configs['output_destination'])
        for key in ('preview_close_mode', 'preview_base_seconds', 'preview_seconds_per_20_chars', 'preview_max_seconds'):
            if key in configs:
                ConfigManager.set_client_var(key, configs[key])
        if 'save_audio' in configs:
            ConfigManager.set_client_var('save_audio', configs['save_audio'])
        if 'save_diary' in configs:
            ConfigManager.set_client_var('save_diary', configs['save_diary'])
        if 'paste_apps' in configs:
            ConfigManager.set_client_var('paste_apps', configs['paste_apps'])
        if 'enter_apps' in configs:
            ConfigManager.set_client_var('enter_apps', configs['enter_apps'])
        if 'traditional_convert' in configs:
            ConfigManager.set_client_var('traditional_convert', configs['traditional_convert'])
        if 'language' in configs:
            ConfigManager.set_client_var('language', configs['language'])
        if 'hot_enabled' in configs:
            ConfigManager.set_client_var('hot', configs['hot_enabled'])
        if 'hot_rule_enabled' in configs:
            ConfigManager.set_client_var('hot_rule', configs['hot_rule_enabled'])
        if 'hot_thresh' in configs:
            ConfigManager.set_client_var('hot_thresh', configs['hot_thresh'])
        if 'hot_similar' in configs:
            ConfigManager.set_client_var('hot_similar', configs['hot_similar'])
        for key in ('mic_seg_duration', 'mic_seg_overlap', 'file_seg_duration', 'file_seg_overlap'):
            if key in configs:
                ConfigManager.set_client_var(key, configs[key])
        for key in ('file_save_srt', 'file_save_txt', 'file_save_json', 'file_save_merge'):
            if key in configs:
                ConfigManager.set_client_var(key, configs[key])

        # 3. 还原服务端变量
        if 'model_type' in configs:
            ConfigManager.set_model_type(configs['model_type'])
        if 'format_num' in configs:
            ConfigManager.set_server_var('format_num', configs['format_num'])
        if 'format_spell' in configs:
            ConfigManager.set_server_var('format_spell', configs['format_spell'])
        if 'gpu_boost' in configs:
            ConfigManager.set_server_var('gpu_boost_enabled', configs['gpu_boost'])
        for key in ('gpu_boost_cmd', 'gpu_unboost_cmd', 'gpu_unboost_timeout'):
            if key in configs:
                ConfigManager.set_server_var(key, configs[key])
        if 'aligner_timeout' in configs:
            ConfigManager.set_server_var('aligner_idle_timeout', configs['aligner_timeout'])
        server_class_fields = {
            'sensevoice_onnx_provider': ('SenseVoiceArgs', 'onnx_provider'),
            'sensevoice_dml_pad_to': ('SenseVoiceArgs', 'dml_pad_to'),
            'fun_asr_onnx_provider': ('FunASRNanoGGUFArgs', 'onnx_provider'),
            'fun_asr_llm_use_gpu': ('FunASRNanoGGUFArgs', 'llm_use_gpu'),
            'fun_asr_dml_pad_to': ('FunASRNanoGGUFArgs', 'dml_pad_to'),
            'qwen_asr_onnx_provider': ('Qwen3ASRGGUFArgs', 'onnx_provider'),
            'qwen_asr_llm_use_gpu': ('Qwen3ASRGGUFArgs', 'llm_use_gpu'),
            'qwen_asr_dml_pad_to': ('Qwen3ASRGGUFArgs', 'dml_pad_to'),
            'aligner_onnx_provider': ('ForceAlignerGGUFArgs', 'onnx_provider'),
            'aligner_llm_use_gpu': ('ForceAlignerGGUFArgs', 'llm_use_gpu'),
            'aligner_dml_pad_to': ('ForceAlignerGGUFArgs', 'dml_pad_to'),
        }
        for config_key, (class_name, var_name) in server_class_fields.items():
            if config_key in configs:
                ConfigManager.set_server_class_var(class_name, var_name, configs[config_key])

        # 4. 还原听写状态浮层
        if 'pill_overlay' in configs:
            ConfigManager.set_pill_overlay_enabled(configs['pill_overlay'])

        # 5. 还原 LLM 润色
        if 'llm_enabled' in configs:
            ConfigManager.set_llm_enabled(configs['llm_enabled'])
        if 'llm_config' in configs:
            llm_c = configs['llm_config']
            ConfigManager.set_llm_default_config(
                provider=llm_c.get('provider'),
                api_key=None,
                model=llm_c.get('model')
            )
            if llm_c.get('provider'):
                set_llm_api_key(llm_c.get('provider'), '')
        if 'llm_profiles' in configs:
            ConfigManager.save_llm_profiles(configs['llm_profiles'])
            for profile in configs['llm_profiles'].get('profiles', []):
                if profile.get('id'):
                    set_llm_profile_api_key(profile['id'], '')
        if 'llm_roles' in data:
            ConfigManager.import_llm_roles(data['llm_roles'])

        # 6. 还原热词与正则文本
        if 'hot_words_content' in data and data['hot_words_content']:
            ConfigManager.write_text_with_backup(HOT_TXT_PATH, data['hot_words_content'])
        if 'hot_rules_content' in data and data['hot_rules_content']:
            ConfigManager.write_text_with_backup(HOT_RULE_TXT_PATH, data['hot_rules_content'])

        return True, "配置导入解析成功，全量偏好与热词已成功恢复！"

    # --- 快捷键与触发模式 ---
    @staticmethod
    def get_active_shortcut():
        if not CONFIG_CLIENT_PATH.exists():
            return 'alt_gr'
        content = CONFIG_CLIENT_PATH.read_text(encoding='utf-8')
        match = re.search(r"'key':\s*'([^']+)'", content)
        return match.group(1) if match else 'alt_gr'

    @staticmethod
    def set_active_shortcut(key_name):
        if not CONFIG_CLIENT_PATH.exists():
            return False
        content = CONFIG_CLIENT_PATH.read_text(encoding='utf-8')
        new_content = re.sub(r"('key':\s*')([^']+)(')", f"\\1{key_name}\\3", content, count=1)
        ConfigManager.write_text_with_backup(CONFIG_CLIENT_PATH, new_content)
        return True

    @staticmethod
    def get_hold_mode():
        if not CONFIG_CLIENT_PATH.exists():
            return True
        content = CONFIG_CLIENT_PATH.read_text(encoding='utf-8')
        match = re.search(r"'hold_mode':\s*(True|False)", content)
        return match.group(1) == 'True' if match else True

    @staticmethod
    def set_hold_mode(enabled: bool):
        if not CONFIG_CLIENT_PATH.exists():
            return False
        content = CONFIG_CLIENT_PATH.read_text(encoding='utf-8')
        val_str = 'True' if enabled else 'False'
        new_content = re.sub(r"('hold_mode':\s*)(True|False)", f"\\1{val_str}", content)
        ConfigManager.write_text_with_backup(CONFIG_CLIENT_PATH, new_content)
        return True

    @staticmethod
    def set_model_type(model_type_name):
        return ConfigManager.set_server_var('model_type', model_type_name)

    # --- 客户端变量读写助手 ---
    @staticmethod
    def get_client_var(var_name, default_val):
        return ConfigManager._get_python_var(CONFIG_CLIENT_PATH, var_name, default_val)

    @staticmethod
    def set_client_var(var_name, val):
        return ConfigManager._set_python_var(CONFIG_CLIENT_PATH, var_name, val)

    # --- 服务端变量读写助手 ---
    @staticmethod
    def get_server_var(var_name, default_val):
        return ConfigManager._get_python_var(CONFIG_SERVER_PATH, var_name, default_val)

    @staticmethod
    def set_server_var(var_name, val):
        return ConfigManager._set_python_var(CONFIG_SERVER_PATH, var_name, val)

    @staticmethod
    def get_server_class_var(class_name, var_name, default_val):
        return ConfigManager._get_python_class_var(CONFIG_SERVER_PATH, class_name, var_name, default_val)

    @staticmethod
    def set_server_class_var(class_name, var_name, val):
        return ConfigManager._set_python_class_var(CONFIG_SERVER_PATH, class_name, var_name, val)

    # --- LLM 润色读写 ---
    @staticmethod
    def get_llm_enabled():
        if not LLM_DEFAULT_PATH.exists():
            return False
        content = LLM_DEFAULT_PATH.read_text(encoding='utf-8')
        match = re.search(r"^enabled\s*=\s*(True|False)", content, re.MULTILINE)
        return match.group(1) == 'True' if match else False

    @staticmethod
    def set_llm_enabled(enabled: bool):
        if not LLM_DEFAULT_PATH.exists():
            return False
        content = LLM_DEFAULT_PATH.read_text(encoding='utf-8')
        val_str = 'True' if enabled else 'False'
        new_content = re.sub(r"(^enabled\s*=\s*)(True|False)", f"\\1{val_str}", content, flags=re.MULTILINE)
        ConfigManager.write_text_with_backup(LLM_DEFAULT_PATH, new_content)
        return True

    @staticmethod
    def get_llm_default_config():
        if not LLM_DEFAULT_PATH.exists():
            return {'provider': 'deepseek', 'api_url': '', 'api_key': '', 'model': 'deepseek-chat', 'output_mode': 'typing', 'role_prompt': ''}
        content = LLM_DEFAULT_PATH.read_text(encoding='utf-8')
        profile_id = ConfigManager._get_python_var(LLM_DEFAULT_PATH, 'profile_id', '')
        provider = re.search(r"^provider\s*=\s*'([^']*)'", content, re.MULTILINE)
        api_url = re.search(r"^api_url\s*=\s*'([^']*)'", content, re.MULTILINE)
        api_key = re.search(r"^api_key\s*=\s*'([^']*)'", content, re.MULTILINE)
        model = re.search(r"^model\s*=\s*'([^']*)'", content, re.MULTILINE)
        output_mode = re.search(r"^output_mode\s*=\s*'([^']*)'", content, re.MULTILINE)
        provider_value = provider.group(1) if provider else 'deepseek'
        source_key = api_key.group(1) if api_key else ''
        return {
            'provider': provider_value,
            'profile_id': profile_id,
            'api_url': api_url.group(1) if api_url else '',
            'api_key': get_llm_api_key(provider_value, source_key),
            'model': model.group(1) if model else 'deepseek-chat',
            'output_mode': output_mode.group(1) if output_mode else 'typing',
        }

    @staticmethod
    def set_llm_default_config(provider=None, api_url=None, api_key=None, model=None, output_mode=None, profile_id=None):
        if not LLM_DEFAULT_PATH.exists():
            return False
        content = LLM_DEFAULT_PATH.read_text(encoding='utf-8')
        if provider is not None:
            content = re.sub(r"(^provider\s*=\s*')([^']*)(')", f"\\1{provider}\\3", content, flags=re.MULTILINE)
        if api_url is not None:
            content = re.sub(r"(^api_url\s*=\s*')([^']*)(')", f"\\1{api_url}\\3", content, flags=re.MULTILINE)
        if api_key is not None:
            active_provider = provider or ConfigManager.get_llm_default_config().get('provider', 'deepseek')
            set_llm_api_key(active_provider, api_key)
            content = re.sub(r"(^api_key\s*=\s*')([^']*)(')", "\\1\\3", content, flags=re.MULTILINE)
        if model is not None:
            content = re.sub(r"(^model\s*=\s*')([^']*)(')", f"\\1{model}\\3", content, flags=re.MULTILINE)
        if output_mode is not None:
            content = re.sub(r"(^output_mode\s*=\s*')([^']*)(')", f"\\1{output_mode}\\3", content, flags=re.MULTILINE)
        ConfigManager.write_text_with_backup(LLM_DEFAULT_PATH, content)
        if profile_id is not None:
            ConfigManager._set_python_var(LLM_DEFAULT_PATH, 'profile_id', profile_id)
        return True

    @staticmethod
    def list_llm_roles():
        roles = []
        if not LLM_DIR.exists():
            return roles
        for path in sorted(LLM_DIR.glob('*.py')):
            if path.name in {'__init__.py', 'default.py'}:
                continue
            role_id = f'LLM.{path.stem}'
            profile_id = ConfigManager._get_python_var(path, 'profile_id', '')
            profile = ConfigManager.get_llm_profile(profile_id, include_key=True) if profile_id else None
            provider = ConfigManager._get_python_var(path, 'provider', 'ollama')
            source_key = ConfigManager._get_python_var(path, 'api_key', '')
            raw_name = ConfigManager._get_python_var(path, 'name', path.stem)
            display_name = next((part.strip() for part in str(raw_name).split('|') if part.strip()), path.stem)
            raw_enabled = ConfigManager._get_python_var(path, 'enabled', True)
            has_stale_profile = bool(profile_id) and profile is None
            roles.append({
                'id': role_id,
                'file': path.name,
                'stem': path.stem,
                'name': raw_name,
                'display_name': display_name,
                'profile_id': profile_id,
                'profile_name': profile.get('name', '') if profile else '',
                'enabled': bool(raw_enabled and not has_stale_profile),
                'provider': profile.get('provider', provider) if profile else provider,
                'api_url': profile.get('api_url', ConfigManager._get_python_var(path, 'api_url', '')) if profile else ConfigManager._get_python_var(path, 'api_url', ''),
                'api_key': profile.get('api_key', get_llm_role_api_key(role_id, provider, source_key)) if profile else get_llm_role_api_key(role_id, provider, source_key),
                'models': profile.get('models', []) if profile else [],
                'model': ConfigManager._get_python_var(path, 'model', '') or (profile.get('default_model', '') if profile else ''),
                'enable_hotwords': ConfigManager._get_python_var(path, 'enable_hotwords', False),
                'enable_history': ConfigManager._get_python_var(path, 'enable_history', False),
                'enable_read_selection': ConfigManager._get_python_var(path, 'enable_read_selection', False),
                'enable_thinking': ConfigManager._get_python_var(path, 'enable_thinking', False),
                'max_context_length': ConfigManager._get_python_var(path, 'max_context_length', 4096),
                'selection_max_length': ConfigManager._get_python_var(path, 'selection_max_length', 1000),
                'temperature': ConfigManager._get_python_var(path, 'temperature', 0.7),
                'top_p': ConfigManager._get_python_var(path, 'top_p', 0.9),
                'max_tokens': ConfigManager._get_python_var(path, 'max_tokens', 4096),
                'system_prompt': ConfigManager._get_python_var(path, 'system_prompt', ''),
            })
        return roles

    @staticmethod
    def set_llm_role_config(stem: str, **updates):
        role_path = (LLM_DIR / f'{stem}.py').resolve()
        if not str(role_path).startswith(str(LLM_DIR.resolve())) or not role_path.exists() or role_path.name == 'default.py':
            return False

        role_id = f'LLM.{role_path.stem}'
        api_key = updates.pop('api_key', None)
        for key, value in updates.items():
            if key in {
                'name', 'enabled', 'profile_id', 'provider', 'api_url', 'model',
                'enable_hotwords', 'enable_history', 'enable_read_selection', 'enable_thinking',
                'max_context_length', 'selection_max_length', 'temperature', 'top_p',
                'max_tokens', 'system_prompt',
            }:
                ConfigManager._set_python_var(role_path, key, value)

        if api_key is not None:
            set_llm_role_api_key(role_id, api_key)
            ConfigManager._set_python_var(role_path, 'api_key', '')
        return True

    @staticmethod
    def create_llm_role(name: str = '新角色', profile_id: str = '', model: str = '', system_prompt: str = ''):
        safe_name = re.sub(r'[\\/:*?"<>|\s]+', '_', (name or '新角色').strip()).strip('._')
        safe_name = safe_name or f'role_{uuid.uuid4().hex[:6]}'
        role_path = LLM_DIR / f'{safe_name}.py'
        if role_path.exists():
            role_path = LLM_DIR / f'{safe_name}_{uuid.uuid4().hex[:4]}.py'
        trigger_name = name or role_path.stem
        prompt = system_prompt or '你是一个自定义 AI 助理。请根据用户输入完成任务，只输出最终结果，不要添加多余解释。'
        content = f'''"""
{trigger_name}
"""

name = {trigger_name!r}
enabled = True
profile_id = {profile_id!r}

provider = 'deepseek'
api_url = ''
api_key = ''
model = {model!r}

max_context_length = 4096
enable_hotwords = False
enable_thinking = False
enable_history = True
enable_read_selection = False
selection_max_length = 1000

output_mode = 'toast'
toast_initial_width = 0.5
toast_initial_height = 0
toast_font_family = '楷体'
toast_font_size = 23
toast_font_color = 'white'
toast_bg_color = '#075077'
toast_duration = 3000
toast_editable = True

temperature = 0.7
top_p = 0.9
max_tokens = 4096
stop = ''
extra_options = {{}}

prompt_prefix_hotwords = '热词列表：'
prompt_prefix_selection = '选中文字：'
prompt_prefix_input = '用户输入：'

system_prompt = {prompt!r}
'''
        ConfigManager.write_text_with_backup(role_path, content)
        return role_path.stem

    @staticmethod
    def delete_llm_role(stem: str) -> bool:
        role_path = (LLM_DIR / f'{stem}.py').resolve()
        if not str(role_path).startswith(str(LLM_DIR.resolve())) or not role_path.exists() or role_path.name in {'default.py', '__init__.py'}:
            return False
        CONFIG_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        shutil.copy2(role_path, CONFIG_BACKUP_DIR / ConfigManager._backup_name(role_path, timestamp))
        role_path.unlink()
        return True

    @staticmethod
    def preview_llm_role_match(text: str):
        text = (text or '').strip()
        for role in ConfigManager.list_llm_roles():
            if not role.get('enabled'):
                continue
            for name in [part.strip() for part in str(role.get('name', '')).split('|') if part.strip()]:
                if text.startswith(name):
                    content = text[len(name):].lstrip('：，。?. ')
                    return {
                        'matched': True,
                        'role': role.get('display_name') or role.get('stem'),
                        'content': content,
                        'provider': role.get('provider'),
                        'model': role.get('model'),
                        'profile': role.get('profile_name') or role.get('profile_id'),
                    }
        return {'matched': False, 'role': '默认润色' if ConfigManager.get_llm_enabled() else '直接输出', 'content': text}

    # --- 听写状态浮层配置 ---
    @staticmethod
    def get_pill_overlay_enabled():
        if not PILL_CONFIG_PATH.exists():
            return True
        content = PILL_CONFIG_PATH.read_text(encoding='utf-8')
        match = re.search(r"enabled\s*=\s*(True|False)", content)
        return match.group(1) == 'True' if match else True

    @staticmethod
    def set_pill_overlay_enabled(enabled: bool):
        val_str = 'True' if enabled else 'False'
        if not PILL_CONFIG_PATH.exists():
            ConfigManager.write_text_with_backup(PILL_CONFIG_PATH, f"enabled = {val_str}\n")
        else:
            content = PILL_CONFIG_PATH.read_text(encoding='utf-8')
            new_content = re.sub(r"(enabled\s*=\s*)(True|False)", f"\\1{val_str}", content)
            ConfigManager.write_text_with_backup(PILL_CONFIG_PATH, new_content)
        return True

    @staticmethod
    def get_pill_overlay_mode():
        return 'wave'

    @staticmethod
    def set_pill_overlay_mode(mode: str):
        if not PILL_CONFIG_PATH.exists():
            ConfigManager.write_text_with_backup(PILL_CONFIG_PATH, "enabled = True\n")
            return True
        content = PILL_CONFIG_PATH.read_text(encoding='utf-8')
        new_content = re.sub(r"^mode\s*=\s*'[^']+'\s*\n?", "", content, flags=re.MULTILINE)
        ConfigManager.write_text_with_backup(PILL_CONFIG_PATH, new_content)
        return True
