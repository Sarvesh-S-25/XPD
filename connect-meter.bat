@echo off
setlocal
cd /d "%~dp0"

echo.
echo   PromptMeter - connect the live meter
echo.
echo   This edits one setting in your Claude Code settings file so
echo   PromptMeter can read your real usage. Your other settings are
echo   kept, and a backup is saved first.
echo.

where py >nul 2>nul
if %errorlevel%==0 (
    py -3 -m promptmeter --connect %*
    goto :done
)

where python >nul 2>nul
if %errorlevel%==0 (
    python -m promptmeter --connect %*
    goto :done
)

echo   Python was not found on this machine.
echo.
echo   Install it from https://www.python.org/downloads/ and tick
echo   "Add python.exe to PATH" during setup, then run this file again.
echo.

:done
echo.
pause
