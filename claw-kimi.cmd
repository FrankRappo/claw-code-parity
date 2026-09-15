@echo off
setlocal

rem Optional machine-local overrides. This file is gitignored.
if exist "%~dp0claw-kimi.local.cmd" call "%~dp0claw-kimi.local.cmd"
if errorlevel 1 exit /b %errorlevel%

if not defined KIMI_PROVIDER_MODE set "KIMI_PROVIDER_MODE=gateway"
if not defined KIMI_GATEWAY_STATE set "KIMI_GATEWAY_STATE=%LOCALAPPDATA%\KimiClawGateway"
if not defined KIMI_CLAW_AUTO_COMPACT_TOKENS set "KIMI_CLAW_AUTO_COMPACT_TOKENS=200000"
if not defined CLAW_BASH_BACKEND set "CLAW_BASH_BACKEND=wsl"
if not defined CLAW_WSL_DISTRO set "CLAW_WSL_DISTRO=Ubuntu-24.04"

if /I "%KIMI_PROVIDER_MODE%"=="direct" goto direct_api
if /I not "%KIMI_PROVIDER_MODE%"=="gateway" (
  echo Unsupported KIMI_PROVIDER_MODE: "%KIMI_PROVIDER_MODE%". Use gateway or direct.
  exit /b 2
)

if not defined KIMI_GATEWAY_ROOT (
  if exist "%~dp0integrations\kimi-claw-gateway\.venv\Scripts\python.exe" set "KIMI_GATEWAY_ROOT=%~dp0integrations\kimi-claw-gateway"
)
if not defined KIMI_GATEWAY_ROOT (
  if exist "%~dp0..\kimi-claw-gateway\.venv\Scripts\python.exe" set "KIMI_GATEWAY_ROOT=%~dp0..\kimi-claw-gateway"
)
if not defined KIMI_GATEWAY_ROOT (
  if exist "%~dp0integrations\kimi-claw-gateway\windows\start-kimi-gateway.ps1" set "KIMI_GATEWAY_ROOT=%~dp0integrations\kimi-claw-gateway"
)
if not defined KIMI_GATEWAY_ROOT (
  echo Kimi gateway was not found. Run integrations\kimi-claw-gateway\windows\install.ps1 or set KIMI_GATEWAY_ROOT in claw-kimi.local.cmd.
  exit /b 1
)
if not defined KIMI_GATEWAY_PORT (
  set "KIMI_GATEWAY_PORT=18081"
  if exist "%KIMI_GATEWAY_STATE%\port.txt" set /p "KIMI_GATEWAY_PORT="<"%KIMI_GATEWAY_STATE%\port.txt"
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%KIMI_GATEWAY_ROOT%\windows\start-kimi-gateway.ps1" -Port %KIMI_GATEWAY_PORT%
if errorlevel 1 exit /b %errorlevel%
if not defined KIMI_API_KEY_FILE set "KIMI_API_KEY_FILE=%KIMI_GATEWAY_STATE%\api-key.txt"
if not exist "%KIMI_API_KEY_FILE%" (
  echo Kimi gateway API key is missing: "%KIMI_API_KEY_FILE%"
  exit /b 1
)
set /p "GROQ_API_KEY="<"%KIMI_API_KEY_FILE%"
set "GROQ_BASE_URL=http://127.0.0.1:%KIMI_GATEWAY_PORT%/v1"
if not defined KIMI_CLAW_SESSION for /f "usebackq delims=" %%I in (`powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%KIMI_GATEWAY_ROOT%\windows\get-kimi-session-marker.ps1" -WorkspacePath "%CD%"`) do set "KIMI_CLAW_SESSION=%%I"
if not defined KIMI_CLAW_SESSION (
  echo Failed to derive a stable Kimi session marker for "%CD%"
  exit /b 1
)
if not defined KIMI_CLAW_MODEL set "KIMI_CLAW_MODEL=kimi-k2d6-%KIMI_CLAW_SESSION%"
goto run_claw

:direct_api
if not defined KIMI_DIRECT_BASE_URL set "KIMI_DIRECT_BASE_URL=https://api.kimi.com/coding/v1"
if not defined KIMI_DIRECT_MODEL set "KIMI_DIRECT_MODEL=kimi-for-coding"
if not defined KIMI_DIRECT_API_KEY_FILE set "KIMI_DIRECT_API_KEY_FILE=%KIMI_GATEWAY_STATE%\direct-api-key.txt"
if not exist "%KIMI_DIRECT_API_KEY_FILE%" (
  echo Direct Kimi API key is missing: "%KIMI_DIRECT_API_KEY_FILE%"
  echo Store the key as one line in that file; do not put it in a tracked config.
  exit /b 1
)
set /p "GROQ_API_KEY="<"%KIMI_DIRECT_API_KEY_FILE%"
set "GROQ_BASE_URL=%KIMI_DIRECT_BASE_URL%"
set "KIMI_CLAW_MODEL=%KIMI_DIRECT_MODEL%"

:run_claw
set "HOME=%USERPROFILE%"
set "CLAUDE_CODE_AUTO_COMPACT_INPUT_TOKENS=%KIMI_CLAW_AUTO_COMPACT_TOKENS%"
"%~dp0rust\target\release\claw.exe" --model "%KIMI_CLAW_MODEL%" --dangerously-skip-permissions %*
