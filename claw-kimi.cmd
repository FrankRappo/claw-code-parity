@echo off
setlocal
set "KIMI_GATEWAY_ROOT=%~dp0..\kimi-claw-gateway"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%KIMI_GATEWAY_ROOT%\windows\start-kimi-gateway.ps1"
if errorlevel 1 exit /b %errorlevel%
set "KIMI_API_KEY_FILE=%LOCALAPPDATA%\KimiClawGateway\api-key.txt"
if not exist "%KIMI_API_KEY_FILE%" (
  echo Kimi gateway API key is missing: "%KIMI_API_KEY_FILE%"
  exit /b 1
)
set /p "GROQ_API_KEY="<"%KIMI_API_KEY_FILE%"
for /f %%I in ('powershell.exe -NoProfile -Command "[guid]::NewGuid().ToString([char]78)"') do set "KIMI_CLAW_SESSION=%%I"
set "HOME=%USERPROFILE%"
set "GROQ_BASE_URL=http://127.0.0.1:18081/v1"
set "CLAUDE_CODE_AUTO_COMPACT_INPUT_TOKENS=16000"
"%~dp0rust\target\release\claw.exe" --model "kimi-k2d6-%KIMI_CLAW_SESSION%" --dangerously-skip-permissions %*
