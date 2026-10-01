@echo off
rem Paike Assistant - one-click regression checks (ASCII only; see run_checks.py)
rem Usage: double-click this file.  Full smoke (about 3 min): run_checks.py --full
cd /d "%~dp0"
set "PYTHONIOENCODING=utf-8"
set "PY=D:\python\python.exe"
if not exist "%PY%" set "PY=python"
echo ========================================
echo   Paike Assistant - regression checks
echo   (quick mode, about 1-2 minutes)
echo ========================================
echo.
"%PY%" run_checks.py %*
echo.
pause
