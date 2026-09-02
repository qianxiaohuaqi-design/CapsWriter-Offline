"""
LLM 客户端池

功能：
1. 缓存 OpenAI / Ollama 客户端实例（基于 Provider + Profile + URL + Key 哈希隔离）
2. 分阶段超时策略（connect=5.0s, write=10.0s, read=30.0s, pool=5.0s）
3. 严格资源释放（clear/close 遍历关闭连接池，杜绝 Socket 泄漏）
"""
import hashlib
from typing import Any, Dict
from .llm_constants import APIConfig, TimeoutConfig


class ClientPool:
    """OpenAI / Ollama 客户端池"""

    def __init__(self):
        self._clients: Dict[str, Any] = {}

    def get_client(self, provider: str, api_url: str = '', api_key: str = '', profile_id: str = '') -> Any:
        """获取 LLM 客户端（带缓存与按需延迟加载）"""
        final_url = api_url or APIConfig.DEFAULT_API_URLS.get(provider)
        final_key = api_key or APIConfig.DEFAULT_API_KEYS.get(provider, '')
        key_digest = hashlib.sha256(final_key.encode('utf-8')).hexdigest()[:12] if final_key else 'no-key'
        cache_key = f"{provider}_{profile_id}_{final_url}_{key_digest}"

        if cache_key not in self._clients:
            import httpx
            staged_timeout = TimeoutConfig.get_httpx_timeout(is_stream=False)

            # 按需延迟创建客户端，避免在 App 启动阶段触发依赖库初始化延时
            if provider == 'ollama':
                from ollama import Client as OllamaClient
                self._clients[cache_key] = OllamaClient(
                    host=final_url,
                    timeout=staged_timeout,
                )
            else:
                from openai import OpenAI
                # 检查是否为国内 API 端点或厂商；如果是国内服务（如 智谱/DeepSeek/月之暗面/火山引擎 等），强制跳过代理直连，避免梯子节点绕路海外
                domestic_providers = {'zhipu', 'deepseek', 'moonshot', 'volcengine', 'siliconflow', 'qwen', 'baichuan', 'minimax', 'yi', 'stepfun', 'doubao'}
                domestic_domains = ['deepseek.com', 'aliyun.com', 'baidubce.com', 'volces.com', 'bigmodel.cn', 'zhipuai.cn', 'minimax.chat', 'moonshot.cn', 'siliconflow.cn', 'lingyiwanwu.com', 'stepfun.com']
                is_domestic = (provider in domestic_providers) or any(domain in (final_url or '').lower() for domain in domestic_domains)
                http_client = httpx.Client(timeout=staged_timeout, trust_env=not is_domestic) if is_domestic else None

                self._clients[cache_key] = OpenAI(
                    base_url=final_url,
                    api_key=final_key,
                    timeout=staged_timeout,
                    http_client=http_client,
                )

        return self._clients[cache_key]



    def clear(self):
        """清空客户端缓存，旧客户端由垃圾回收自动清理，避免强行 close 中断活动请求"""
        self._clients = {}

    def close(self):
        """关闭客户端池"""
        self.clear()


