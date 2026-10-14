@echo off
rem Instalador para Windows: instala uv si falta y ejecuta install.py.
cd /d "%~dp0"

where uv >nul 2>nul
if errorlevel 1 (
    echo Instalando uv...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
    set "PATH=%USERPROFILE%\.local\bin;%PATH%"
)

uv run --project whatsapp-mcp-server python install.py %*
pause
