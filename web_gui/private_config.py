# coding: utf-8
"""本地私有配置，避免 API Key 进入源码文件。"""

from __future__ import annotations

import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
PRIVATE_CONFIG_PATH = BASE_DIR / 'web_gui' / 'private_config.json'


def load_private_config() -> dict:
    if not PRIVATE_CONFIG_PATH.exists():
        return {'llm_api_keys': {}, 'llm_role_api_keys': {}, 'llm_profile_api_keys': {}}
    try:
        data = json.loads(PRIVATE_CONFIG_PATH.read_text(encoding='utf-8'))
        data.setdefault('llm_api_keys', {})
        data.setdefault('llm_role_api_keys', {})
        data.setdefault('llm_profile_api_keys', {})
        return data
    except Exception:
        return {'llm_api_keys': {}, 'llm_role_api_keys': {}, 'llm_profile_api_keys': {}}


def save_private_config(data: dict) -> None:
    PRIVATE_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    PRIVATE_CONFIG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def get_llm_api_key(provider: str, fallback: str = '') -> str:
    data = load_private_config()
    return data.get('llm_api_keys', {}).get(provider) or fallback


def set_llm_api_key(provider: str, api_key: str) -> None:
    data = load_private_config()
    data.setdefault('llm_api_keys', {})
    if api_key:
        data['llm_api_keys'][provider] = api_key
    else:
        data['llm_api_keys'].pop(provider, None)
    save_private_config(data)


def resolve_llm_api_key(role_id: str = '', profile_id: str = '', provider: str = '', fallback: str = '') -> str:
    """
    统一 API Key 优先级解析：
    Role-specific Key (角色私有 Key) > Bound Profile Key (绑定档案 Key) > Provider Global Key (全局提供商 Key) > fallback
    """
    data = load_private_config()

    # 1. 角色私有 Key
    if role_id:
        role_key = data.get('llm_role_api_keys', {}).get(role_id)
        if role_key and role_key.strip():
            return role_key.strip()

    # 2. 绑定档案 Key
    if profile_id:
        profile_key = data.get('llm_profile_api_keys', {}).get(profile_id)
        if profile_key and profile_key.strip():
            return profile_key.strip()

    # 3. 全局提供商 Key
    if provider:
        provider_key = data.get('llm_api_keys', {}).get(provider)
        if provider_key and provider_key.strip():
            return provider_key.strip()

    # 4. 代码中指定的 fallback Key
    return fallback.strip() if fallback else ''


def get_llm_role_api_key(role_id: str, provider: str, fallback: str = '', profile_id: str = '') -> str:
    return resolve_llm_api_key(role_id=role_id, profile_id=profile_id, provider=provider, fallback=fallback)


def set_llm_role_api_key(role_id: str, api_key: str) -> None:
    data = load_private_config()
    data.setdefault('llm_role_api_keys', {})
    if api_key:
        data['llm_role_api_keys'][role_id] = api_key
    else:
        data['llm_role_api_keys'].pop(role_id, None)
    save_private_config(data)


def get_llm_profile_api_key(profile_id: str, fallback: str = '') -> str:
    data = load_private_config()
    return data.get('llm_profile_api_keys', {}).get(profile_id) or fallback


def set_llm_profile_api_key(profile_id: str, api_key: str) -> None:
    data = load_private_config()
    data.setdefault('llm_profile_api_keys', {})
    if api_key:
        data['llm_profile_api_keys'][profile_id] = api_key
    else:
        data['llm_profile_api_keys'].pop(profile_id, None)
    save_private_config(data)


def mask_key(api_key: str) -> str:
    if not api_key:
        return ''
    if len(api_key) <= 8:
        return '*' * len(api_key)
    return f'{api_key[:4]}{"*" * 8}{api_key[-4:]}'
