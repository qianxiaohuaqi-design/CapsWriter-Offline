@echo off
chcp 936 > nul
title CapsWriter
cd /d "%~dp0"
start "" pythonw "%~dp0run_app.py"
exit /b
