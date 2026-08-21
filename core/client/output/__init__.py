# coding: utf-8
"""
output 子模块

包含识别结果输出相关功能。
"""

from .. import logger

def __getattr__(name: str):
    if name == 'ResultProcessor':
        from core.client.output.result_processor import ResultProcessor
        return ResultProcessor
    if name == 'TextOutput':
        from core.client.output.text_output import TextOutput
        return TextOutput
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")

__all__ = [
    'logger',
    'ResultProcessor',
    'TextOutput',
]
