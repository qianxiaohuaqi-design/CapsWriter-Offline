"""
LLM 错误处理和用户提示

统一处理 LLM 异常，提供用户友好的错误提示
"""
from typing import Optional, Tuple
from .llm_exceptions import (
    APIConnectionError,
    APIResponseError,
    OpenAIErrorWrapper,
    AuthenticationErrorWrapper,
    RateLimitErrorWrapper,
    TimeoutErrorWrapper,
    ConnectionErrorWrapper,
    APIResponseErrorWrapper,
)
from . import logger


def get_user_friendly_message(error: Exception) -> str:
    """
    获取用户友好的错误消息

    Args:
        error: 异常对象

    Returns:
        用户友好的错误消息
    """
    # 已包装的 OpenAI 异常
    if isinstance(error, OpenAIErrorWrapper):
        return error.user_message

    if isinstance(error, (APIConnectionError, APIResponseError)):
        return str(error)

    # 其他异常
    return f"处理失败: {type(error).__name__}"


def should_fallback_to_original(error: Exception) -> bool:
    """
    判断是否应该降级到原文本（始终返回 True，确保任何异常下绝不吞字、安全回退用户语音识别原文）
    """
    return True


def get_fallback_reason(error: Exception) -> str:
    """
    提取标准的降级原因类别 ('timeout' | 'http_429' | 'http_401' | 'http_500' | 'empty_response' | 'connection_error' 等)
    """
    if error is None:
        return "empty_response"

    # 1. 空响应
    if isinstance(error, APIResponseError) and "返回空内容" in str(error):
        return "empty_response"

    # 2. 超时
    if isinstance(error, TimeoutErrorWrapper):
        return "timeout"
    err_type_name = type(error).__name__.lower()
    if 'timeout' in err_type_name:
        return "timeout"

    # 3. HTTP 状态码
    status_code = getattr(error, 'status_code', None)
    if hasattr(error, 'response') and hasattr(error.response, 'status_code'):
        status_code = error.response.status_code
    if hasattr(error, 'original_error'):
        orig = error.original_error
        if hasattr(orig, 'status_code'):
            status_code = orig.status_code
        elif hasattr(orig, 'response') and hasattr(orig.response, 'status_code'):
            status_code = orig.response.status_code

    if isinstance(error, RateLimitErrorWrapper) or status_code == 429 or '429' in str(error):
        return "http_429"
    if isinstance(error, AuthenticationErrorWrapper) or status_code == 401 or '401' in str(error):
        return "http_401"
    if status_code in (400, 403, 500, 502, 503, 504):
        return f"http_{status_code}"
    if '500' in str(error):
        return "http_500"
    if '502' in str(error):
        return "http_502"
    if '503' in str(error):
        return "http_503"
    if '504' in str(error):
        return "http_504"
    if '400' in str(error):
        return "http_400"
    if '403' in str(error):
        return "http_403"

    # 4. 连接异常
    if isinstance(error, (ConnectionErrorWrapper, APIConnectionError)):
        return "connection_error"
    if 'connect' in err_type_name or 'network' in err_type_name or 'socket' in err_type_name:
        return "connection_error"

    return "connection_error"


def show_error_notification(error: Exception, role_name: str = "LLM"):
    """
    显示错误通知（Toast 或控制台）

    Args:
        error: 异常对象
        role_name: 角色名称
    """
    user_msg = get_user_friendly_message(error)

    # 记录日志
    logger.warning(f"[{role_name}] {user_msg} - {error}")

    # 尝试显示 Toast 通知
    try:
        from core.ui.toast import ToastMessageManager, ToastMessage

        toast_manager = ToastMessageManager()

        # 错误提示使用红色背景，更大的尺寸
        msg = ToastMessage(
            text=f"❌ {role_name}: {user_msg}",
            font_size=16,           # 增大字体
            bg='#8B0000',           # 深红色
            fg='white',
            duration=5000,          # 显示 5 秒
            initial_width=0.6,      # 60% 屏幕宽度（使用百分比）
            initial_height=80,      # 固定最小高度 80 像素
            streaming=False,
            window_type='text'
        )
        toast_manager.add_message(msg)
        toast_manager.finish_last_toast()  # 自动销毁

    except Exception as e:
        # Toast 显示失败，回退到控制台
        logger.error(f"Toast 显示失败: {e}")


def handle_llm_error(error: Exception, original_text: str, role_name: str = "LLM",
                     fallback_text: Optional[str] = None) -> Tuple[str, bool, str]:
    """
    统一的 LLM 错误处理入口，保证任何错误下平滑降级到原始文本，绝不丢字

    Args:
        error: 异常对象
        original_text: 原始输入文本
        role_name: 角色名称
        fallback_text: 降级时使用的文本（None 则使用 original_text）

    Returns:
        (输出文本, 是否成功, fallback_reason)
    """
    fallback_reason = get_fallback_reason(error)
    result = fallback_text if fallback_text is not None else original_text
    logger.warning(f"[{role_name}] LLM 处理失败 ({fallback_reason})，优雅降级到原文本: {error}")
    return (result, False, fallback_reason)
