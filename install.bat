@echo off
setlocal

echo.
echo Iniciando setup de MCP AnyDesk Server...
echo.

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" %*

if %ERRORLEVEL% neq 0 (
    echo.
    echo Setup finalizo con errores. Revisar los mensajes anteriores.
    echo.
    pause
)
