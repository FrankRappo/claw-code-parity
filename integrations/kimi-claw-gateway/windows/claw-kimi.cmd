@echo off
setlocal
set "KIMI_GATEWAY_ROOT=%~dp0..\kimi-claw-gateway"
set "KIMI_GATEWAY_STATE=%LOCALAPPDATA%\KimiClawGateway"
set "KIMI_GATEWAY_PORT=18081"
if exist "%KIMI_GATEWAY_STATE%\port.txt" set /p "KIMI_GATEWAY_PORT="<"%KIMI_GATEWAY_STATE%\port.txt"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%KIMI_GATEWAY_ROOT%\windows\start-kimi-gateway.ps1" -Port %KIMI_GATEWAY_PORT%
if errorlevel 1 exit /b %errorlevel%
set "KIMI_API_KEY_FILE=%KIMI_GATEWAY_STATE%\api-key.txt"
if not exist "%KIMI_API_KEY_FILE%" (
  echo Kimi gateway API key is missing: "%KIMI_API_KEY_FILE%"
  exit /b 1
)
set /p "GROQ_API_KEY="<"%KIMI_API_KEY_FILE%"
if not defined KIMI_CLAW_SESSION for /f "usebackq delims=" %%I in (`powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%KIMI_GATEWAY_ROOT%\windows\get-kimi-session-marker.ps1" -WorkspacePath "%CD%"`) do set "KIMI_CLAW_SESSION=%%I"
if not defined KIMI_CLAW_SESSION (
  echo Failed to derive a stable Kimi session marker for "%CD%"
  exit /b 1
)
set "HOME=%USERPROFILE%"
set "GROQ_BASE_URL=http://127.0.0.1:%KIMI_GATEWAY_PORT%/v1"
set "CLAUDE_CODE_AUTO_COMPACT_INPUT_TOKENS=200000"
"%~dp0rust\target\release\claw.exe" --model "kimi-k2d6-%KIMI_CLAW_SESSION%" --dangerously-skip-permissions %*
