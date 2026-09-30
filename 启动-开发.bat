@echo off
cd /d "%~dp0src-tauri"
set "PATH=E:\webpaper\.workbuddy\rust\installed\bin;D:\python;%PATH%"
echo ========================================
echo   Paike Assistant - dev mode
echo ========================================
echo.
cargo run
pause
