"""
LLM 客户端池

功能：
1. 缓存 OpenAI 客户端实例
2. 根据 provider 和 api_url 创建和获取客户端
"""
from openai import OpenAI
from typing import Dict, Any, Union
import hashlib
from ollama import Client as OllamaClient
from .llm_constants import APIConfig


class ClientPool:
    """OpenAI 客户端池"""

    def __init__(self):
        self._clients: Dict[str, Any] = {}

    def get_client(self, provider: str, api_url: str = '', api_key: str = '') -> Any:
        """获取 LLM 客户端（带缓存）

        Args:
            provider: API 提供商（如 'ollama', 'openai'）
            api_url: API 地址（可选，优先使用此值）
            api_key: API Key（可选）

        Returns:
            OpenAI 或 ollama.Client 客户端实例
        """
        final_url = api_url or APIConfig.DEFAULT_API_URLS.get(provider)
        final_key = api_key or APIConfig.DEFAULT_API_KEYS.get(provider, '')
        key_digest = hashlib.sha256(final_key.encode('utf-8')).hexdigest()[:12] if final_key else 'no-key'
        cache_key = f"{provider}_{final_url}_{key_digest}"

        if cache_key not in self._clients:
            # 获取超时配置（根据 provider 选择，未配置则使用默认值）
            timeout = APIConfig.DEFAULT_TIMEOUTS.get(provider, APIConfig.DEFAULT_TIMEOUT)

            # 创建客户端
            if provider == 'ollama':
                self._clients[cache_key] = OllamaClient(
                    host=final_url,
                    timeout=timeout,
                )
            else:
                self._clients[cache_key] = OpenAI(
                    base_url=final_url,
                    api_key=final_key,
                    timeout=timeout,
                )

        return self._clients[cache_key]

    def clear(self):
        """清空客户端缓存"""
        self._clients.clear()
