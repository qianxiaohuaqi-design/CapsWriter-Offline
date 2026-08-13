# coding: utf-8
import asyncio
import os
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional

from core.client.state import console
from . import logger

class MediaTool:
    """媒体工具类：负责 FFmpeg 相关操作"""

    _ffmpeg_path: Optional[str] = None
    _ffprobe_path: Optional[str] = None

    @staticmethod
    def _candidate_dirs() -> List[Path]:
        """返回项目内置 FFmpeg 的候选目录。"""
        base_dir = Path(__file__).resolve().parents[3]
        candidates = [
            base_dir / 'tools' / 'ffmpeg' / 'bin',
            base_dir / 'ffmpeg' / 'bin',
            base_dir / 'ffmpeg',
        ]

        # PyInstaller onefile/onedir 运行时的资源目录
        meipass = getattr(__import__('sys'), '_MEIPASS', None)
        if meipass:
            bundle_dir = Path(meipass)
            candidates.extend([
                bundle_dir / 'tools' / 'ffmpeg' / 'bin',
                bundle_dir / 'ffmpeg' / 'bin',
                bundle_dir / 'ffmpeg',
            ])

        return candidates

    @classmethod
    def _find_executable(cls, name: str) -> Optional[str]:
        exe_name = f'{name}.exe' if os.name == 'nt' else name
        for folder in cls._candidate_dirs():
            candidate = folder / exe_name
            if candidate.exists():
                return str(candidate)
        return shutil.which(name)

    @classmethod
    def resolve_tools(cls) -> tuple[Optional[str], Optional[str]]:
        """解析 ffmpeg/ffprobe 路径，优先使用项目内置版本。"""
        cls._ffmpeg_path = cls._find_executable('ffmpeg')
        cls._ffprobe_path = cls._find_executable('ffprobe')
        return cls._ffmpeg_path, cls._ffprobe_path

    @staticmethod
    def check_environment() -> bool:
        """检查 FFmpeg 和 ffprobe 环境"""
        ffmpeg_path, ffprobe_path = MediaTool.resolve_tools()
        
        if ffmpeg_path is None:
            console.print('\n[bold red]错误：未检测到 FFmpeg 环境[/bold red]')
            console.print('    文件转录功能依赖 FFmpeg 来提取音视频中的音频。')
            console.print('    [cyan]建议处理方案：[/cyan]')
            console.print('    1. 推荐将 ffmpeg.exe / ffprobe.exe 放入 tools/ffmpeg/bin。')
            console.print('    2. 或者安装 FFmpeg 并将 bin 目录添加到系统 Path。')
            console.print('    3. 也可以前往官方下载：[u]https://ffmpeg.org/download.html[/u]\n')
            logger.error("未检测到 FFmpeg 环境，无法进行文件转录")
            return False
            
        if ffprobe_path is None:
            console.print('\n[bold yellow]提示：未检测到 ffprobe 环境[/bold yellow]')
            console.print('    程序将无法预先获取文件时长，进度条将只显示当前已发送时长。')
            console.print('    [cyan]建议：[/cyan]若需完整进度条，请在安装 FFmpeg 时确保 bin 目录下包含 ffprobe.exe。\n')
            logger.warning("未检测到 ffprobe 环境，进度显示将受到限制")
        else:
            logger.debug(f"FFmpeg 可用: ffmpeg={ffmpeg_path}, ffprobe={ffprobe_path}")
            
        return True

    @staticmethod
    async def get_audio_duration(file: Path) -> float:
        """获取音视频文件时长"""
        if MediaTool._ffprobe_path is None:
            MediaTool.resolve_tools()
        if MediaTool._ffprobe_path is None:
            return 0.0
        cmd = [
            MediaTool._ffprobe_path, "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(file)
        ]
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await process.communicate()
            if process.returncode == 0:
                return float(stdout.decode().strip())
        except Exception as e:
            logger.warning(f"无法通过 ffprobe 获取时长: {e}")
        return 0.0

    @staticmethod
    def build_ffmpeg_cmd(file: Path) -> List[str]:
        """构建提取音频的 FFmpeg 命令"""
        if MediaTool._ffmpeg_path is None:
            MediaTool.resolve_tools()
        ffmpeg = MediaTool._ffmpeg_path or "ffmpeg"
        return [
            ffmpeg, "-i", str(file),
            "-f", "f32le", "-ac", "1", "-ar", "16000", "-"
        ]
