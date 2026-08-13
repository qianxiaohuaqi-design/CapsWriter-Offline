@echo off
cd /d "%~dp0"
title CapsWriter-Offline

echo ========================================================
echo 正在启动 CapsWriter-Offline ...
echo ========================================================
echo 1. 正在启动服务端...
start "CapsWriter Server" python start_server.py

timeout /t 2 /nobreak >nul

echo 2. 正在启动客户端...
start "CapsWriter Client" python start_client.py

echo ========================================================
echo 启动完成！请按住 CapsLock 键说话打字上屏。
echo ========================================================
pause