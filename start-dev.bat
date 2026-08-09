@echo off
rem Dev launcher: clear out stale runs of this repo, then start the Electron app.
rem
rem Stale processes are the usual cause of a blank window or "database is
rem locked": a previous Electron run that was force-killed leaves its uvicorn
rem child orphaned, still holding pigskin_mastermind.db, and a hand-started dev
rem uvicorn is a second writer on the same file.

setlocal
set "PM_ROOT=%~dp0"

echo Checking for stale Pigskin Mastermind processes...

rem Two passes, in this order on purpose: taskkill /F skips Electron's
rem 'before-quit' handler, so its uvicorn child is orphaned rather than stopped.
rem The Python pass afterwards is what actually cleans that up.
rem 'Kill' is an alias for Stop-Process, which cannot take a CIM instance --
rem the helper needs a name of its own. Killing the parent takes its uvicorn
rem worker with it, so re-check liveness before shooting at a stale PID.
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$root = $env:PM_ROOT.TrimEnd('\'); $desk = Join-Path $root 'desktop'; $n = 0;" ^
  "function Stop-Stale($p) { if (-not (Get-Process -Id $p.ProcessId -ErrorAction SilentlyContinue)) { return $false }; Write-Host ('  killing ' + $p.Name + ' (PID ' + $p.ProcessId + ')'); taskkill /PID $p.ProcessId /T /F 2>&1 | Out-Null; return $true }" ^
  "foreach ($p in Get-CimInstance Win32_Process) { if ($p.Name -eq 'electron.exe' -and $p.ExecutablePath -and $p.ExecutablePath.StartsWith($desk, [StringComparison]::OrdinalIgnoreCase)) { if (Stop-Stale $p) { $n++ } } }" ^
  "foreach ($p in Get-CimInstance Win32_Process) { if ($p.Name -like 'python*.exe' -and $p.CommandLine -and $p.CommandLine -like '*pigskin_mastermind.api.main:app*') { if (Stop-Stale $p) { $n++ } } }" ^
  "exit $n"

if errorlevel 1 (
    echo Waiting for the database and ports to be released...
    rem Not `timeout`: it reads the console and dies when stdin is redirected.
    ping -n 3 127.0.0.1 >nul
) else (
    echo   none found.
)

call "%PM_ROOT%run-desktop.bat"
set "RC=%errorlevel%"

endlocal & exit /b %RC%
