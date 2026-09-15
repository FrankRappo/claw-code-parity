@echo off
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0fake-claw.ps1" -LogPath "%FAKE_CLAW_LOG%"
