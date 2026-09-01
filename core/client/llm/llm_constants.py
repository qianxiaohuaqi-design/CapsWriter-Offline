"""
LLM 常量配置

集中管理所有魔法数字、默认值和配置常量
"""

# ==================== 上下文管理常量 ====================
class ContextConstants:
    """上下文管理相关常量"""

    # Token 修剪阈值（使用 80% 阈值，保留 20% 给模型输出）
    TRIM_THRESHOLD_RATIO = 0.8

    # Token 估算常量
    CHARS_PER_TOKEN_CN = 1.5      # 中文字符：约 1.5 字符 = 1 token
    CHARS_PER_TOKEN_EN = 4.0      # 英文字符：约 4 字符 = 1 token

    # Unicode 中文字符范围
    CN_CHAR_START = '\u4e00'
    CN_CHAR_END = '\u9fff'


# ==================== RAG 常量 ====================
class RAGConstants:
    """热词 RAG 相关常量"""

    # 搜索参数
    DEFAULT_TOP_K = 5              # 默认返回前 5 个热词
    DEFAULT_THRESHOLD = 0.4        # 默认相似度阈值


# ==================== 文件监控常量 ====================
class WatcherConstants:
    """文件监控相关常量"""

    # 防抖延迟（秒）
    DEBOUNCE_DELAY = 3

    # 文件过滤
    PY_EXTENSION = '.py'
    INIT_FILE = '__init__.py'
    CACHE_DIR = '__pycache__'

    # 重载标记
    RELOAD_ALL_MARKER = '__reload_all__'


# ==================== 角色配置默认值 ====================


# ==================== 分阶段超时配置 ====================
class TimeoutConfig:
    """分阶段超时策略配置（秒）"""
    CONNECT = 5.0          # 建立连接超时
    WRITE = 10.0           # 写入请求数据超时
    READ = 30.0            # 普通读取响应超时
    STREAM_READ = 60.0     # 流式读取响应超时
    POOL = 5.0             # 连接池等待超时

    @classmethod
    def get_httpx_timeout(cls, is_stream: bool = False):
        import httpx
        read_timeout = cls.STREAM_READ if is_stream else cls.READ
        return httpx.Timeout(
            connect=cls.CONNECT,
            write=cls.WRITE,
            read=read_timeout,
            pool=cls.POOL,
        )


# ==================== API 配置 ====================
class APIConfig:
    """API 提供商配置"""

    # 默认 API URL
    DEFAULT_API_URLS = {
        'ollama': 'http://127.0.0.1:11434',
        'lmstudio': 'http://127.0.0.1:1234/v1',
        'openai': 'https://api.openai.com/v1',
        'deepseek': 'https://api.deepseek.com/v1',
        'moonshot': 'https://api.moonshot.cn/v1',
        'zhipu': 'https://open.bigmodel.cn/api/paas/v4',
        'volcengine': 'https://ark.cn-beijing.volces.com/api/v3',
        'cerebras': 'https://api.cerebras.ai/v1',
    }

    # 默认 API Keys
    DEFAULT_API_KEYS = {
        'ollama': 'ollama',
        'lmstudio': 'lmstudio',
        'openai': '',
        'deepseek': '',
        'moonshot': '',
        'zhipu': '',
        'cerebras': '',
    }

    # 请求超时配置（秒）- 分阶段超时策略
    DEFAULT_TIMEOUTS = {
        'ollama': 30.0,       # 本地模型
        'lmstudio': 30.0,     # LM Studio 本地模型
        'openai': 30.0,       # OpenAI API
        'deepseek': 30.0,     # DeepSeek API
        'moonshot': 30.0,     # Moonshot API
        'zhipu': 30.0,        # 智谱 API
        'cerebras': 30.0,     # Cerebras API
        'claude': 30.0,       # Claude API
        'gemini': 30.0,       # Gemini API
        'volcengine': 30.0,   # 火山引擎 API
    }

    # 默认超时（用于未列出的 provider）
    DEFAULT_TIMEOUT = 30.0

    @classmethod
    def get_staged_timeout(cls, provider: str = '', is_stream: bool = False):
        return TimeoutConfig.get_httpx_timeout(is_stream=is_stream)


# ==================== Token 估算工具 ====================
def estimate_tokens(text: str) -> int:
    """
    估算文本的 token 数量

    Args:
        text: 待估算的文本

    Returns:
        估算的 token 数量
    """
    if not text:
        return 0

    # 统计中文字符数量
    chinese_chars = sum(
        1 for c in text
        if ContextConstants.CN_CHAR_START <= c <= ContextConstants.CN_CHAR_END
    )

    # 统计非中文字符数量
    other_chars = len(text) - chinese_chars

    # 中文字符：约 1.5 字符 = 1 token
    # 英文和其他字符：约 4 字符 = 1 token
    tokens = int(
        chinese_chars / ContextConstants.CHARS_PER_TOKEN_CN +
        other_chars / ContextConstants.CHARS_PER_TOKEN_EN
    )

    return max(tokens, 1)  # 至少 1 个 token
