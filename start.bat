@echo off
setlocal
cd /d "%~dp0"

set "PYTHON=.venv\Scripts\python.exe"
if not exist "%PYTHON%" (
    echo [ERROR] Project virtual environment not found: %PYTHON%
    echo Follow the Quick Start section in README.md to install dependencies.
    goto failed
)

if not exist "src\config\anita_go.ini" if not exist "src\config\dashgo.ini" (
    echo [ERROR] src\config\anita_go.ini not found.
    echo Copy src\config\anita_go.ini.example and configure its secrets first.
    goto failed
)

if not defined SMART_AGENT_SCHEDULER_RPC_SECRET (
    for /f "delims=" %%S in ('"%PYTHON%" -c "import secrets; print(secrets.token_urlsafe(48))"') do set "SMART_AGENT_SCHEDULER_RPC_SECRET=%%S"
)
if not defined SMART_AGENT_SCHEDULER_RPC_SECRET (
    echo [ERROR] Could not generate the scheduler RPC secret.
    goto failed
)

echo Starting Web and scheduler in paired_paused mode. Existing tasks will remain paused.
"%PYTHON%" scripts\start_ap.py --scheduler-mode paired_paused
if errorlevel 1 goto startup_failed
exit /b 0

:startup_failed
set "EXIT_CODE=%ERRORLEVEL%"
echo.
echo [ERROR] SI-Platform startup failed with exit code %EXIT_CODE%.
pause
exit /b %EXIT_CODE%

:failed
echo.
pause
exit /b 1
