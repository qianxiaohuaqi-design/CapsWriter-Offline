"""
LLM 客户端池

功能：
1. 缓存 OpenAI / Ollama 客户端实例
2. 根据 provider 和 api_url 创建和获取客户端
"""
import hashlib
from typing import Any, Dict
from .llm_constants import APIConfig


class ClientPool:
    """OpenAI / Ollama 客户端池"""

    def __init__(self):
        self._clients: Dict[str, Any] = {}

    def get_client(self, provider: str, api_url: str = '', api_key: str = '') -> Any:
        """获取 LLM 客户端（带缓存与按需延迟加载）"""
        final_url = api_url or APIConfig.DEFAULT_API_URLS.get(provider)
        final_key = api_key or APIConfig.DEFAULT_API_KEYS.get(provider, '')
        key_digest = hashlib.sha256(final_key.encode('utf-8')).hexdigest()[:12] if final_key else 'no-key'
        cache_key = f"{provider}_{final_url}_{key_digest}"

        if cache_key not in self._clients:
            timeout = APIConfig.DEFAULT_TIMEOUTS.get(provider, APIConfig.DEFAULT_TIMEOUT)

            # 按需延迟创建客户端，避免在 App 启动阶段触发依赖库初始化延时
            if provider == 'ollama':
                from ollama import Client as OllamaClient
                self._clients[cache_key] = OllamaClient(
                    host=final_url,
                    timeout=timeout,
                )
            else:
                import httpx
                from openai import OpenAI
                # 检查是否为国内 API 端点；如果是国内服务（如 api.deepseek.com），默认跳过代理直连，避免梯子节点绕路海外
                is_domestic = any(domain in (final_url or '').lower() for domain in ['deepseek.com', 'aliyun.com', 'baidubce.com', 'volces.com', 'bigmodel.cn', 'zhipuai.cn', 'minimax.chat'])
                http_client = httpx.Client(timeout=timeout, trust_env=not is_domestic) if is_domestic else None

                self._clients[cache_key] = OpenAI(
                    base_url=final_url,
                    api_key=final_key,
                    timeout=timeout,
                    http_client=http_client,
                )

        return self._clients[cache_key]

    def clear(self):
        """清空客户端缓存"""
        self._clients.clear()

