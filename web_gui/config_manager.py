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

from core.product.paths import data_root
BASE_DIR = data_root()
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
        from core.product.data_safety import atomic_write
        atomic_write(GUI_SETTINGS_PATH, json.dumps(settings, ensure_ascii=False, indent=2).encode('utf-8'))
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
            from core.product.data_safety import safe_role
            content = safe_role(content)
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
            if target.parent != LLM_DIR.resolve() or target.is_symlink():
                continue
            from core.product.data_safety import safe_role
            content = safe_role(content)
            ConfigManager.write_text_with_backup(target, content)
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
        if not path.is_file():
            return False
        from core.product.data_safety import replace_assignment
        return ConfigManager.write_text_with_backup(
            path, replace_assignment(path.read_text(encoding='utf-8'), var_name, value))

    @staticmethod
    def _set_python_class_var(path: Path, class_name: str, var_name: str, value) -> bool:
        if not path.is_file():
            return False
        from core.product.data_safety import replace_assignment
        try:
            content = replace_assignment(path.read_text(encoding='utf-8'), var_name, value, class_name)
        except ValueError:
            return False
        return ConfigManager.write_text_with_backup(path, content)

    @staticmethod
    def _backup_name(path: Path, timestamp: str) -> str:
        relative = path.relative_to(BASE_DIR).as_posix().replace('/', '__')
        return f'{relative}.{timestamp}.bak'

    @staticmethod
    def write_text_with_backup(path: Path, content: str) -> bool:
        """写配置前自动备份旧文件，并用临时文件原子替换。"""
        path = Path(path)
        if path.is_symlink() or BASE_DIR.resolve() not in path.resolve().parents:
            raise ValueError('配置路径超出本机数据目录或属于链接。')
        if path == HOT_RULE_TXT_PATH:
            from core.product.data_safety import validate_rules
            validate_rules(content)
        if path.suffix == '.py':
            ast.parse(content)
        if path.suffix == '.json':
            json.loads(content)
        old_content = path.read_text(encoding='utf-8') if path.exists() else None
        if old_content == content:
            return True

        if ConfigManager.get_auto_config_backup_enabled() and path.exists():
            CONFIG_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
            backup_path = CONFIG_BACKUP_DIR / ConfigManager._backup_name(path, timestamp)
            shutil.copy2(path, backup_path)

        from core.product.data_safety import atomic_write
        atomic_write(path, content.encode('utf-8'))
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
        if backup.is_symlink() or backup.resolve().parent != CONFIG_BACKUP_DIR.resolve() or not backup.is_file() or backup.suffix != '.bak':
            return False, '只能恢复本应用备份目录内的普通备份文件。'
        target_key = backup.name[:-4].rsplit('.', 1)[0].replace('__', '/')
        target = MANAGED_CONFIG_PATHS.get(target_key)
        if target is None:
            possible = BASE_DIR / target_key
            if possible.parent.resolve() != LLM_DIR.resolve() or possible.suffix != '.py' or possible.name in {'__init__.py', 'default.py'}:
                return False, '备份目标不是受管理的配置文件。'
            target = possible
        content = backup.read_text(encoding='utf-8')
        try:
            ConfigManager.write_text_with_backup(target, content)
        except (ValueError, OSError, SyntaxError) as error:
            return False, f'恢复被阻止：{error}'
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
            'history_auto_clear_on_start': ConfigManager.get_client_var('history_auto_clear_on_start', False),
            'history_max_items': ConfigManager.get_client_var('history_max_items', 200),
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
            'llm_role_output_destination': ConfigManager.get_client_var('llm_role_output_destination', 'overlay_preview'),
            'llm_role_preview_close_mode': ConfigManager.get_client_var('llm_role_preview_close_mode', 'auto'),
            'llm_role_preview_base_seconds': ConfigManager.get_client_var('llm_role_preview_base_seconds', 2),
            'llm_role_preview_seconds_per_20_chars': ConfigManager.get_client_var('llm_role_preview_seconds_per_20_chars', 1),
            'llm_role_preview_max_seconds': ConfigManager.get_client_var('llm_role_preview_max_seconds', 10),

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
        from core.product.data_safety import redact
        return redact(full_data)

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

    @staticmethod
    def _prepare_import(data):
        from core.product.data_safety import (fingerprint, pending_imports, redact,
            replace_assignment, safe_role, transaction_token, validate_rules)
        if isinstance(data, str):
            if len(data.encode('utf-8')) > 8 * 1024 * 1024:
                raise ValueError('配置备份过大，不能导入。')
            data = json.loads(data)
        if not isinstance(data, dict) or not isinstance(data.get('configs'), dict):
            raise ValueError('无效的配置备份结构。')
        if pending_imports(CONFIG_BACKUP_DIR):
            raise ValueError('存在上次未完成的配置导入，请先在诊断中恢复原配置。')
        data = redact(data)
        configs = data['configs']
        staged = {}
        warnings = ['私有 API Key 保留在本机，不从普通备份导入或清空。', '服务参数在确认后保存；运行中的模型不会被此操作自动重启。']
        def read(path):
            if path in staged:
                return staged[path]
            if not path.is_file() or path.is_symlink():
                raise ValueError(f'缺少可写的本地配置：{path.name}')
            return path.read_text(encoding='utf-8')
        def put(path, name, value, cls=None):
            staged[path] = replace_assignment(read(path), name, value, cls)
        aliases = {'paste_mode': 'paste', 'hot_enabled': 'hot', 'hot_rule_enabled': 'hot_rule'}
        client_fields = {
            'paste_mode', 'output_destination', 'preview_close_mode', 'preview_base_seconds',
            'preview_seconds_per_20_chars', 'preview_max_seconds', 'save_audio', 'save_diary',
            'paste_apps', 'enter_apps', 'traditional_convert', 'language', 'hot_enabled',
            'hot_rule_enabled', 'hot_thresh', 'hot_similar', 'mic_seg_duration', 'mic_seg_overlap',
            'file_seg_duration', 'file_seg_overlap', 'file_save_srt', 'file_save_txt', 'file_save_json',
            'file_save_merge', 'history_auto_clear_on_start', 'history_max_items',
            'llm_role_output_destination', 'llm_role_preview_close_mode',
            'llm_role_preview_base_seconds', 'llm_role_preview_seconds_per_20_chars', 'llm_role_preview_max_seconds',
        }
        bools = {'paste_mode', 'save_audio', 'save_diary', 'traditional_convert', 'hot_enabled',
                 'hot_rule_enabled', 'file_save_srt', 'file_save_txt', 'file_save_json', 'file_save_merge',
                 'history_auto_clear_on_start', 'hold_mode', 'pill_overlay', 'llm_enabled',
                 'format_num', 'format_spell', 'gpu_boost', 'fun_asr_llm_use_gpu', 'qwen_asr_llm_use_gpu', 'aligner_llm_use_gpu'}
        numeric = {'preview_base_seconds', 'preview_seconds_per_20_chars', 'preview_max_seconds',
                   'hot_thresh', 'hot_similar', 'mic_seg_duration', 'mic_seg_overlap', 'file_seg_duration',
                   'file_seg_overlap', 'history_max_items', 'llm_role_preview_base_seconds',
                   'llm_role_preview_seconds_per_20_chars', 'llm_role_preview_max_seconds',
                   'aligner_timeout', 'gpu_unboost_timeout', 'sensevoice_dml_pad_to', 'fun_asr_dml_pad_to',
                   'qwen_asr_dml_pad_to', 'aligner_dml_pad_to'}
        enums = {'output_destination': {'typing', 'overlay_preview'}, 'preview_close_mode': {'auto', 'manual'},
                 'llm_role_output_destination': {'typing', 'overlay_preview', 'toast', 'inherit'},
                 'llm_role_preview_close_mode': {'auto', 'manual'},
                 'model_type': {'sensevoice', 'paraformer', 'funasr', 'fun_asr', 'qwen3', 'qwen3_asr', 'fun_asr_nano', 'qwen_asr'},
                 'sensevoice_onnx_provider': {'CPU', 'CUDA', 'DML', 'DirectML', 'TensorRT'},
                 'fun_asr_onnx_provider': {'CPU', 'CUDA', 'DML', 'DirectML', 'TensorRT'},
                 'qwen_asr_onnx_provider': {'CPU', 'CUDA', 'DML', 'DirectML', 'TensorRT'},
                 'aligner_onnx_provider': {'CPU', 'CUDA', 'DML', 'DirectML', 'TensorRT'}}
        import math
        for key, value in configs.items():
            if key in bools and not isinstance(value, bool):
                raise ValueError(f'{key} 必须为布尔值。')
            if key in numeric and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0):
                raise ValueError(f'{key} 必须为有效的非负数。')
            if key in ('hot_thresh', 'hot_similar') and value > 1:
                raise ValueError(f'{key} 必须在 0 到 1 之间。')
            if key in ('file_seg_duration', 'mic_seg_duration') and value <= 0:
                raise ValueError('分段长度必须大于零。')
            if key in enums and (not isinstance(value, str) or value not in enums[key]):
                raise ValueError(f'{key} 不是支持的选项。')
            if key in ('shortcut', 'language') and (not isinstance(value, str) or len(value) > 128):
                raise ValueError(f'{key} 必须为有效的短文本。')
            if key == 'paste_apps' and (not isinstance(value, list) or not all(isinstance(v, str) for v in value)):
                raise ValueError('paste_apps 必须为程序名列表。')
            if key == 'enter_apps' and (not isinstance(value, list) or not all(
                    isinstance(v, (list, tuple)) and len(v) == 2 and isinstance(v[0], str)
                    and isinstance(v[1], (int, float)) and 0 <= v[1] <= 30 for v in value)):
                raise ValueError('enter_apps 必须为程序名与延迟列表。')
        for key in client_fields:
            if key in configs:
                put(CONFIG_CLIENT_PATH, aliases.get(key, key), configs[key], 'ClientConfig')
        if 'shortcut' in configs or 'hold_mode' in configs:
            tree = ast.parse(read(CONFIG_CLIENT_PATH))
            cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'ClientConfig')
            assignment = next(node for node in cls.body if isinstance(node, ast.Assign)
                              and any(isinstance(t, ast.Name) and t.id == 'shortcuts' for t in node.targets))
            shortcuts = ast.literal_eval(assignment.value)
            if not isinstance(shortcuts, list) or not shortcuts or not all(isinstance(item, dict) for item in shortcuts):
                raise ValueError('现有快捷键配置不可安全修改。')
            if 'shortcut' in configs:
                shortcuts[0]['key'] = configs['shortcut']
            if 'hold_mode' in configs:
                for item in shortcuts:
                    item['hold_mode'] = configs['hold_mode']
            put(CONFIG_CLIENT_PATH, 'shortcuts', shortcuts, 'ClientConfig')
        server_fields = {'model_type': 'model_type', 'format_num': 'format_num', 'format_spell': 'format_spell',
                         'gpu_boost': 'gpu_boost_enabled', 'gpu_unboost_timeout': 'gpu_unboost_timeout',
                         'aligner_timeout': 'aligner_idle_timeout'}
        for key, name in server_fields.items():
            if key in configs:
                put(CONFIG_SERVER_PATH, name, configs[key], 'ServerConfig')
        classes = {'sensevoice': 'SenseVoiceArgs', 'fun_asr': 'FunASRNanoGGUFArgs',
                   'qwen_asr': 'Qwen3ASRGGUFArgs', 'aligner': 'ForceAlignerGGUFArgs'}
        for prefix, cls in classes.items():
            for suffix in ('onnx_provider', 'llm_use_gpu', 'dml_pad_to'):
                key = f'{prefix}_{suffix}'
                if key in configs:
                    put(CONFIG_SERVER_PATH, suffix, configs[key], cls)
        if 'gpu_boost_cmd' in configs or 'gpu_unboost_cmd' in configs:
            warnings.append('普通备份不导入可执行 GPU 命令；本机已有命令保持不变。')
        if 'pill_overlay' in configs:
            content = PILL_CONFIG_PATH.read_text(encoding='utf-8') if PILL_CONFIG_PATH.exists() else 'enabled = True\n'
            staged[PILL_CONFIG_PATH] = replace_assignment(content, 'enabled', configs['pill_overlay'])
        if 'llm_enabled' in configs:
            put(LLM_DEFAULT_PATH, 'enabled', configs['llm_enabled'])
            put(CONFIG_CLIENT_PATH, 'llm_enabled', configs['llm_enabled'], 'ClientConfig')
            if configs['llm_enabled']:
                warnings.append('备份会启用在线 AI；启用后识别文字可能发送给已配置的服务。')
        if 'llm_config' in configs:
            if not isinstance(configs['llm_config'], dict):
                raise ValueError('llm_config 必须为对象。')
            for key in ('provider', 'api_url', 'model', 'output_mode', 'profile_id'):
                if key in configs['llm_config']:
                    value = configs['llm_config'][key]
                    if not isinstance(value, str):
                        raise ValueError(f'AI 的 {key} 必须为文字。')
                    put(LLM_DEFAULT_PATH, key, value)
        if 'llm_profiles' in configs:
            profiles = configs['llm_profiles']
            if not isinstance(profiles, dict) or not isinstance(profiles.get('profiles', []), list):
                raise ValueError('AI 档案列表无效。')
            seen = set()
            for profile in profiles.get('profiles', []):
                if not isinstance(profile, dict) or not isinstance(profile.get('id'), str) or profile['id'] in seen:
                    raise ValueError('AI 档案 ID 无效或重复。')
                seen.add(profile['id'])
                if not isinstance(profile.get('models', []), list) or not all(isinstance(v, str) for v in profile.get('models', [])):
                    raise ValueError('AI 档案模型列表无效。')
            staged[LLM_PROFILES_PATH] = json.dumps(profiles, ensure_ascii=False, indent=2)
        roles = data.get('llm_roles', [])
        if not isinstance(roles, list):
            raise ValueError('角色备份必须为列表。')
        for role in roles:
            if not isinstance(role, dict):
                raise ValueError('角色备份格式无效。')
            name = role.get('file', '')
            if (not isinstance(name, str) or Path(name).name != name or '/' in name or '\\' in name
                    or not name.endswith('.py') or name in {'__init__.py', 'default.py'} or '..' in name):
                raise ValueError('角色文件名无效。')
            content = role.get('content')
            if not isinstance(content, str):
                raise ValueError('角色内容必须为文字。')
            staged[LLM_DIR / name] = safe_role(content)
        for key, path in [('hot_words_content', HOT_TXT_PATH), ('hot_rules_content', HOT_RULE_TXT_PATH)]:
            if key in data:
                content = data[key]
                if not isinstance(content, str):
                    raise ValueError('热词内容必须为文字。')
                if key == 'hot_rules_content':
                    validate_rules(content)
                staged[path] = content  # Explicit empty string intentionally clears after preview.
        staged = {path: text for path, text in staged.items() if not path.exists() or path.read_text(encoding='utf-8') != text}
        if any(path.is_symlink() or BASE_DIR.resolve() not in path.resolve().parents for path in staged):
            raise ValueError('导入目标超出数据目录或属于链接。')
        fingerprints = {str(path): fingerprint(path) for path in staged}
        return data, staged, fingerprints, transaction_token(staged, fingerprints), warnings

    @staticmethod
    def preview_full_config(data) -> dict:
        try:
            data, staged, fingerprints, token, warnings = ConfigManager._prepare_import(data)
            from core.product.data_safety import redact
            current = redact(ConfigManager.get_all_config())
            differences = [{'setting': key, 'before': current.get(key), 'after': value}
                           for key, value in data['configs'].items() if current.get(key) != value]
            return {'ok': True, 'changes': [str(path.relative_to(BASE_DIR)) for path in staged],
                    'changed_settings': differences, 'warnings': warnings, 'token': token, 'data': data}
        except (ValueError, TypeError, OSError, StopIteration, SyntaxError) as error:
            return {'ok': False, 'message': str(error), 'changes': [], 'warnings': [], 'token': None}

    @staticmethod
    def import_full_config(data, expected_token: str | None = None):
        from core.product.data_safety import commit_transaction
        try:
            _, staged, fingerprints, token, _ = ConfigManager._prepare_import(data)
            if expected_token is not None and expected_token != token:
                return False, '配置或备份内容已变化，请重新预览并确认。'
            if not staged:
                return True, '配置与当前一致，没有需要写入的变更。'
            journal = commit_transaction(staged, CONFIG_BACKUP_DIR, fingerprints)
            return True, f'配置已保存并读回核验；原配置备份保留在 {journal.name}。运行参数将在重启对应服务后生效。'
        except Exception as error:
            return False, str(error)

    @staticmethod
    def pending_config_recovery() -> list[dict]:
        from core.product.data_safety import pending_imports
        return pending_imports(CONFIG_BACKUP_DIR)

    @staticmethod
    def recover_config_import(journal: str | Path) -> tuple[bool, str]:
        from core.product.data_safety import recover_transaction
        try:
            allowed = set(MANAGED_CONFIG_PATHS.values())
            # A valid imported role may not yet have been created when interrupted.
            manifest = json.loads((Path(journal) / 'manifest.json').read_text(encoding='utf-8'))
            for item in manifest.get('files', []):
                target = Path(item.get('path', ''))
                if target.parent.resolve() == LLM_DIR.resolve() and target.suffix == '.py' and target.name not in {'__init__.py', 'default.py'}:
                    allowed.add(target)
            recover_transaction(Path(journal), CONFIG_BACKUP_DIR, allowed)
            return True, '中断前的配置已恢复并校验；备份原字节仍保留。'
        except Exception as error:
            return False, str(error)

    @staticmethod
    def read_hotword_document(path: Path) -> dict:
        from core.product.data_safety import fingerprint
        path = Path(path)
        if path.resolve() not in {HOT_TXT_PATH.resolve(), HOT_RULE_TXT_PATH.resolve()} or path.is_symlink():
            raise ValueError('不是可编辑的热词文件。')
        return {'text': path.read_text(encoding='utf-8') if path.exists() else '', 'sha256': fingerprint(path)}

    @staticmethod
    def save_hotword_document(path: Path, text: str, expected_sha256: str) -> tuple[bool, str]:
        from core.product.data_safety import DATA_LOCK, fingerprint, validate_rules
        try:
            with DATA_LOCK:
                current = ConfigManager.read_hotword_document(path)
                if current['sha256'] != expected_sha256:
                    return False, '文件已被外部修改，请先重新载入；编辑内容仍保留。'
                if Path(path).resolve() == HOT_RULE_TXT_PATH.resolve():
                    validate_rules(text)
                ConfigManager.write_text_with_backup(Path(path), text)
            return True, '已校验并保存。'
        except (OSError, ValueError) as error:
            return False, str(error)

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
        val = ConfigManager._get_python_class_var(CONFIG_CLIENT_PATH, 'ClientConfig', var_name, None)
        if val is None:
            val = ConfigManager._get_python_var(CONFIG_CLIENT_PATH, var_name, default_val)
        return val

    @staticmethod
    def set_client_var(var_name, val):
        from config_client import ClientConfig as Config
        ok = ConfigManager._set_python_class_var(CONFIG_CLIENT_PATH, 'ClientConfig', var_name, val)
        if not ok:
            ok = ConfigManager._set_python_var(CONFIG_CLIENT_PATH, var_name, val)
        if ok:
            setattr(Config, var_name, val)
        return ok

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
        if not LLM_DEFAULT_PATH.is_file():
            return False
        from core.product.data_safety import replace_assignment
        content = LLM_DEFAULT_PATH.read_text(encoding='utf-8')
        for name, value in {'provider': provider, 'api_url': api_url, 'model': model,
                            'output_mode': output_mode, 'profile_id': profile_id}.items():
            if value is not None:
                content = replace_assignment(content, name, value)
        if api_key is not None:
            content = replace_assignment(content, 'api_key', '')
        ConfigManager.write_text_with_backup(LLM_DEFAULT_PATH, content)
        if api_key is not None:
            active_provider = provider or ConfigManager.get_llm_default_config().get('provider', 'deepseek')
            set_llm_api_key(active_provider, api_key)
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
                'output_mode': ConfigManager._get_python_var(path, 'output_mode', 'inherit'),
                'preview_close_mode': ConfigManager._get_python_var(path, 'preview_close_mode', ''),
                'preview_base_seconds': ConfigManager._get_python_var(path, 'preview_base_seconds', 0),
                'preview_seconds_per_20_chars': ConfigManager._get_python_var(path, 'preview_seconds_per_20_chars', 0),
                'preview_max_seconds': ConfigManager._get_python_var(path, 'preview_max_seconds', 0),
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
                'max_tokens', 'output_mode', 'preview_close_mode', 'preview_base_seconds',
                'preview_seconds_per_20_chars', 'preview_max_seconds', 'system_prompt',
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

output_mode = 'inherit'
preview_close_mode = ''
preview_base_seconds = 0
preview_seconds_per_20_chars = 0
preview_max_seconds = 0
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
