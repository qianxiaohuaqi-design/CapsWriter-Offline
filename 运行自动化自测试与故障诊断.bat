@echo off
chcp 65001 >nul
title CapsWriter 自动化自测试与故障诊断系统
cd /d "%~dp0"
python tools/auto_self_test.py
pause
