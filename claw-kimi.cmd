@echo off
setlocal
set "KIMI_GATEWAY_ROOT=%~dp0..\kimi-claw-gateway"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%KIMI_GATEWAY_ROOT%\windows\start-kimi-gateway.ps1"
if errorlevel 1 exit /b %errorlevel%
set "HOME=%USERPROFILE%"
set "GROQ_API_KEY=local-kimi"
set "GROQ_BASE_URL=http://127.0.0.1:18081/v1"
"%~dp0rust\target\release\claw.exe" --model kimi-web --dangerously-skip-permissions %*
