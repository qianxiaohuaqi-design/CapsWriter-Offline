# coding: utf-8
"""
CapsWriter Offline 客户端主程序门面类 (Facade)

采用外观模式统一管理音频流 (AudioStreamManager)、
识别结果处理 (ResultProcessor) 和快捷键管理 (ShortcutManager)。
"""

import os
import sys
import asyncio
import time
from pathlib import Path

from .state import ClientState
from . import logger
from config_client import ClientConfig as Config, __version__
from core.tools.signal_handler import register_signal
from .state import console
from .connection import WebSocketManager
from typing import TYPE_CHECKING, Optional
from .manager import (
    TrayManager,
    MicRunner, FileRunner
)
from .audio.stream import AudioStreamManager
from .shortcut.shortcut_manager import ShortcutManager
from .shortcut.shortcut_config import Shortcut

from .udp.udp_control import UDPController

from .hotword.manager import HotwordManager
from .llm.llm_handler import LLMHandler
from .output.text_output import TextOutput
from .diary.diary_writer import DiaryWriter
from core.tools.empty_working_set import empty_current_working_set
from platform import system



class CapsWriterClient:
    """
    CapsWriter 客户端门面类
    
    管理的外部接口简洁：start()。
    """
    def __init__(self):
        t_init_start = time.perf_counter()
        # 确保正确的工作目录
        self.base_dir = Path(__file__).parents[2]
        os.chdir(self.base_dir)
            
        # 初始化事件循环
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
            
        # 初始化状态容器
        self.state = ClientState(app=self)

        # 初始化热词管理器
        self.hotword = HotwordManager(
            hotword_files=None,
            threshold=Config.hot_thresh,
            similar_threshold=Config.hot_similar
        )

        # 4. 初始化 LLM 润色系统
        self.llm = LLMHandler(app=self)
        
        self.output = TextOutput()
        self.diary = DiaryWriter(base_path=self.base_dir)

        # 初始化各管理器
        self.ws = WebSocketManager(self)
        self.tray = TrayManager(self)

        # 实例化硬件资源管理组件
        self.stream = AudioStreamManager(self)
        self.shortcut = ShortcutManager(self, [Shortcut(**sc) for sc in Config.shortcuts])
        self.udp = UDPController(self.shortcut)

        # 内存清理
        empty_current_working_set()
        logger.debug(f"CapsWriterClient 初始化完成，耗时: {time.perf_counter() - t_init_start:.3f}s")

    def stop(self):
        """
        统一释放所有资源（清理顺序：硬件 -> 托盘 -> WebSocket -> State）
        """
        logger.info("正在执行 CapsWriterClient 资源释放...")

        # 1. 停止核心运行组件
        self.udp.stop()
        self.shortcut.stop()
        self.stream.stop()

        try:
            from core.ui.modern_overlay.pill_overlay import close_pill_overlay
            close_pill_overlay()
        except Exception as e:
            logger.debug(f"关闭听写浮层时发生错误，已跳过: {e}")

        # 2. 托盘资源
        self.tray.stop()

        # 3. 关闭监控
        self.hotword.stop()
        self.llm.stop()

        # 4. 关闭 WebSocket 连接
        self.ws.close_sync()

        # 5. 重置 State
        try:
            self.state.reset()
        except Exception as e:
            logger.warning(f"重置状态时发生错误: {e}")

        # 6. 停止事件循环（最后一步，确保前面的异步操作已调度）
        self.loop.stop()

        logger.info("资源释放完成")
        console.print('[green4]再见！')


    def start(self):
        """
        启动客户端 (唯一入口)
        
        自动根据命令行参数识别模式。内部管理异步循环。
        """
        t_start = time.perf_counter()

        # 注册退出函数
        register_signal(self.stop)

        files = [Path(f) for f in sys.argv[1:] if os.path.exists(f)]

        if files:
            # 文件转录模式
            runner = FileRunner(self, files)
        else:
            # 麦克风实时模式
            runner = MicRunner(self)
        
        logger.info(f"CapsWriterClient 正在启动运行组件 (耗时: {time.perf_counter() - t_start:.3f}s)...")
        try:
            self.loop.run_until_complete(runner.run())
        except RuntimeError:
            ...

