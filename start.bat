@echo off
setlocal
cd /d "%~dp0"

echo.
echo   PromptMeter
echo   -----------
echo.

where py >nul 2>nul
if %errorlevel%==0 (
    py -3 -m promptmeter %*
    goto :done
)

where python >nul 2>nul
if %errorlevel%==0 (
    python -m promptmeter %*
    goto :done
)

echo   Python was not found on this machine.
echo.
echo   Install it from https://www.python.org/downloads/ and tick
echo   "Add python.exe to PATH" during setup, then run this file again.
echo.
pause
exit /b 1

:done
echo.
pause
