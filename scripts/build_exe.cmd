@echo off
setlocal

where pwsh.exe >nul 2>nul
if errorlevel 1 (
    echo PowerShell 7 was not found. Install it or run build_exe.ps1 with Windows PowerShell.
    exit /b 1
)

pwsh.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_exe.ps1"
exit /b %errorlevel%
