@echo off
chcp 936 > nul
title CapsWriter
cd /d "%~dp0"
start "" wscript.exe "%~dp0启动 CapsWriter 智能控制中心.vbs"
exit /b
