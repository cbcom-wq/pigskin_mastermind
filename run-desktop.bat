@echo off
rem Launch the Pigskin Mastermind Electron desktop app.
rem Electron spawns uvicorn itself on a free ephemeral port, so do NOT run a
rem dev `uvicorn` at the same time -- two writers on one SQLite file give
rem "database is locked".

setlocal
set "ROOT=%~dp0"

rem A shell spawned from VS Code inherits ELECTRON_RUN_AS_NODE=1, which makes
rem `electron .` run as plain Node -- require('electron') then yields the
rem binary path instead of the API, and main.js dies on `app.whenReady`.
set "ELECTRON_RUN_AS_NODE="

if not exist "%ROOT%.venv\Scripts\python.exe" (
    echo ERROR: no virtualenv at "%ROOT%.venv".
    echo Create it, then: pip install -r requirements-dev.txt ^&^& pip install -e .
    goto :fail
)

pushd "%ROOT%desktop" || goto :fail

if not exist "node_modules\electron" (
    echo Installing desktop dependencies...
    call npm install
    if errorlevel 1 (
        popd
        echo ERROR: npm install failed.
        goto :fail
    )
)

echo Starting Pigskin Mastermind...
call npm start
set "RC=%errorlevel%"
popd

if not "%RC%"=="0" (
    echo.
    echo App exited with code %RC%.
    goto :fail
)

endlocal
exit /b 0

:fail
echo.
pause
endlocal
exit /b 1
