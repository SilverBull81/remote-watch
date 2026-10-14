@echo off
setlocal
for /f "tokens=2 delims=:" %%P in ('chcp') do set "_RemoteWatchCodePage=%%P"
chcp 65001 >nul
@@PYTHON@@ -I -B -m remote_watch.gateway_server.service --config "%~dp0gateway_server.local.json" @@ACTION@@ %*
set "_RemoteWatchResult=%errorlevel%"
chcp %_RemoteWatchCodePage% >nul
exit /b %_RemoteWatchResult%
