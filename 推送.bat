@echo off
chcp 65001 >nul
REM ============================================================
REM  Paike Assistant - one click: commit + push to GitHub
REM
REM  Please keep these two things (both were tested on 2026-10-06):
REM   1) chcp 65001  -> git prints UTF-8; a GBK console garbles it.
REM   2) ASCII only  -> cmd cannot reliably parse a batch file that
REM      contains non-ASCII characters (Chinese comment lines got
REM      executed as commands). Chinese text lives in push-notes.txt
REM      and is shown with `type`.
REM ============================================================
cd /d "%~dp0"

echo ============================================================
echo   Paike Assistant : commit and push to GitHub
echo ============================================================
echo.

where git >nul 2>nul
if errorlevel 1 (
  echo [ERROR] git not found in PATH.
  echo         Install "Git for Windows" and keep "Add to PATH" checked.
  echo.
  call :notes
  pause
  exit /b 1
)

echo [1/4] Changed files:
git -c core.quotepath=false status --short
echo.

git add -A

git diff --cached --quiet
if not errorlevel 1 (
  echo Nothing to commit. Going straight to push.
  echo.
  goto PUSH
)

REM ---- guard: .exe / .dll / .pdf usually should NOT be committed ----
git diff --cached --name-only | findstr /i /r "\.exe$ \.dll$ \.pdf$" >"%TEMP%\paike_push_scan.txt"
for %%A in ("%TEMP%\paike_push_scan.txt") do if %%~zA GTR 0 (
  echo [WARN] These files are usually NOT meant to be committed:
  type "%TEMP%\paike_push_scan.txt"
  echo.
  set "go="
  set /p "go=  Commit anyway? type y then Enter (Enter alone = cancel): "
  if /i not "%go%"=="y" (
    echo Cancelled. Nothing was committed.
    del "%TEMP%\paike_push_scan.txt" >nul 2>nul
    echo.
    pause
    exit /b 1
  )
)
del "%TEMP%\paike_push_scan.txt" >nul 2>nul

echo [2/4] Write one line about this change (Enter = use default):
set "msg="
set /p "msg=  message: "
if "%msg%"=="" set "msg=chore: update (%date% %time%)"
echo.

echo [3/4] Committing ...
git commit -m "%msg%"
if errorlevel 1 (
  echo.
  echo [ERROR] Commit failed, see the git output above.
  call :notes
  pause
  exit /b 1
)
echo.

:PUSH
echo [4/4] Pushing to GitHub ...
git push
if errorlevel 1 (
  echo.
  echo [ERROR] Push failed. See the git output above.
  call :notes
  pause
  exit /b 1
)

echo.
echo [DONE] Pushed. Last 3 commits:
git -c core.quotepath=false log --oneline -3
call :notes
pause
exit /b 0

REM ---- print the Chinese notes (UTF-8 file, shown via type) ----
:notes
if not exist "%~dp0push-notes.txt" exit /b 0
echo.
echo ------------------------------------------------------------
type "%~dp0push-notes.txt"
echo ------------------------------------------------------------
exit /b 0
