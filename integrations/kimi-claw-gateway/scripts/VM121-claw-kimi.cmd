@echo off
REM Opens Claw Code on VM121 using the local Kimi gateway.
where wt.exe >nul 2>nul
if %errorlevel%==0 (
  start "VM121 Claw Kimi" wt.exe wsl.exe -d Ubuntu-24.04 -u root -- bash -lc "/work/claster/vm121/vm121-claw-kimi.sh"
) else (
  wsl.exe -d Ubuntu-24.04 -u root -- bash -lc "/work/claster/vm121/vm121-claw-kimi.sh"
  pause
)
