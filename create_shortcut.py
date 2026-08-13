# coding: utf-8
"""
CapsWriter 智能控制中心 - 桌面原生客户端快捷方式生成器
在 Windows 桌面上生成原生桌面客户端图标 CapsWriter 智能控制中心.lnk
"""

import os
import sys
from pathlib import Path

base_dir = Path(__file__).resolve().parent
desktop_dir = Path(r"C:\Users\reina\Desktop")
icon_path = base_dir / "assets" / "source" / "capswriter.ico"

bat_path = base_dir / "启动 CapsWriter 智能控制中心.bat"
old_lnk_path = desktop_dir / "CapsWriter 智能控制中心.lnk"
lnk_path = desktop_dir / "CapsWriter.lnk"

# 1. 写入 GBK CRLF 批处理文件
bat_lines = [
    "@echo off",
    "chcp 936 > nul",
    "title CapsWriter",
    "cd /d \"%~dp0\"",
    "start \"\" pythonw \"%~dp0run_app.py\"",
    "exit /b",
]
bat_content = "\r\n".join(bat_lines) + "\r\n"

with open(bat_path, "wb") as f:
    f.write(bat_content.encode("gbk"))

# 2. 通过 VBScript 创建快捷方式
vbs_path = base_dir / "make_shortcut.vbs"
target_path = sys.executable.replace('python.exe', 'pythonw.exe')
arguments = str(base_dir / 'run_app.py')

vbs_content = f'''
Set WshShell = CreateObject("WScript.Shell")
If CreateObject("Scripting.FileSystemObject").FileExists("{old_lnk_path}") Then
    CreateObject("Scripting.FileSystemObject").DeleteFile "{old_lnk_path}"
End If
If CreateObject("Scripting.FileSystemObject").FileExists("{lnk_path}") Then
    CreateObject("Scripting.FileSystemObject").DeleteFile "{lnk_path}"
End If
Set oShortcut = WshShell.CreateShortcut("{lnk_path}")
oShortcut.TargetPath = "{target_path}"
oShortcut.Arguments = "{arguments}"
oShortcut.WorkingDirectory = "{base_dir}"
oShortcut.IconLocation = "{icon_path},0"
oShortcut.Description = "CapsWriter"
oShortcut.Save
'''

with open(vbs_path, "wb") as f:
    f.write(vbs_content.encode("gbk"))

os.system(f'cscript //nologo "{vbs_path}"')
if vbs_path.exists():
    vbs_path.unlink()

print("Created native desktop app shortcut:", lnk_path.exists())
