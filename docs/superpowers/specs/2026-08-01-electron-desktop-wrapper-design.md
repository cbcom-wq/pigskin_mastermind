# Electron Desktop Wrapper — Design

Date: 2026-08-01
Status: Approved, ready for implementation planning

## Goal

Make Pigskin Mastermind launchable as a double-clickable desktop app on this machine, instead of
requiring a manual `uvicorn` invocation plus a browser tab.

## Scope decisions

These were settled during brainstorming and bound the design:

| Decision | Choice |
|---|---|
| Audience | Single user, this machine only. Not a distributable installer. |
| Python runtime | The existing `.venv`. No PyInstaller bundling. |
| Backend lifecycle | Electron spawns uvicorn on launch and kills it on quit. |
| Database | The existing repo DB, `D:\git\pigskin_mastermind\pigskin_mastermind.db`. |

The app therefore requires the repo and its `.venv` to be present at their current paths. That is
acceptable — it is a launcher, not a distribution.

## Layout

A new `desktop/` directory with its own `package.json`. The Python tree is untouched, and no
`package.json` is added at the repo root, so the project keeps its "no npm, no build step"
property: `pytest tests/`, `uvicorn`, and the `pigskin` CLI all behave exactly as they do today.
The Electron app is purely additive.

```
desktop/
├── package.json      # electron devDependency, "start" script
├── main.js           # process lifecycle + window
├── loading.html      # splash shown during backend boot
├── src/
│   ├── port.js       # pick a free ephemeral port
│   └── health.js     # poll until the server answers
└── test/
    ├── port.test.js
    └── health.test.js
```

Splitting `port.js` and `health.js` out of `main.js` is what makes any of this testable —
`main.js` itself can only be exercised by launching Electron.

The directory is `src/`, not `lib/`, deliberately: `.gitignore` line 13 is a blanket `lib/` rule
(it is what hides the vendored `espn_api`), and it matches at any depth. `desktop/lib/port.js`
would have been silently untracked. `desktop/node_modules/` is *not* currently ignored and must be
added.

## Launch sequence

1. **Pick a port.** `pickPort()` binds `127.0.0.1:0` and reads back the OS-assigned port. This
   guarantees no collision with a dev server on 8000 or 8010, which is a live concern on this
   machine.
2. **Spawn the backend.**
   `<repo>/.venv/Scripts/python.exe -m uvicorn pigskin_mastermind.api.main:app --host 127.0.0.1 --port <port>`
   with `cwd` set to the repo root and `DATABASE_URL` set to the **absolute**
   `sqlite:///D:/git/pigskin_mastermind/pigskin_mastermind.db`.

   Absolute is load-bearing. The current default in `api/database.py` is the cwd-relative
   `sqlite:///./pigskin_mastermind.db`, which would silently create and use an *empty* database if
   the working directory ever differed — presenting as a mysteriously blank app rather than an
   error.

   No `--reload`. The reloader spawns a child process that complicates shutdown and offers nothing
   to an end-user launch.
3. **Show the window immediately** on `loading.html`. Cold-importing pandas, numpy, scipy, and
   `nfl_data_py` takes several seconds; without a splash the app looks hung.
4. **Wait for readiness.** `waitForServer()` polls `GET /` until it returns 200, with a 60s
   ceiling.
5. **Swap to the app** via `loadURL` on success.

## Error handling

The backend's stdout and stderr are captured, with the last ~50 lines retained in a ring buffer.

- If the Python process exits before the health check passes, stop polling immediately and show a
  dialog containing those buffered lines.
- If the 60s timeout elapses, show the same dialog.

The failure mode this avoids is a blank white window with the actual traceback discarded — the
likely first-run failure here is `ModuleNotFoundError: No module named 'espn_api'` (the vendored
client is git-ignored), and that message needs to reach the user.

## Shutdown

Terminate the backend with `taskkill /PID <pid> /T /F` from a single `before-quit` handler.
`window-all-closed` calls `app.quit()`, which routes through `before-quit`, so one handler covers
both paths without a double-kill guard.

A plain `SIGTERM` to a `python -m uvicorn` child on Windows reliably leaves an orphaned process
holding both the port and the SQLite file. `/T` kills the tree; `/F` forces it.

## Window behavior

- `contextIsolation: true`, `nodeIntegration: false`. No preload script and no IPC bridge — the
  renderer is just the existing server-rendered app and needs no privileged surface.
- `setWindowOpenHandler` routes any non-`localhost` URL to `shell.openExternal`, so outbound links
  open in the real browser rather than trapping the user in a chromeless Electron window.
- Default Electron menu retained, which provides reload, zoom, and devtools at no cost.

## Known caveat

Do not run the Electron app and a development `uvicorn` against the database simultaneously.
Both are writers on one SQLite file and concurrent writes will raise `database is locked`.

No lockfile or already-running detection is being built for this. It is a documented constraint,
accepted deliberately for a single-user launcher.

## Testing

- **Unit tests** (`node:test`) for the two extracted modules:
  - `port.js` — returns a port that is genuinely bindable.
  - `health.js` — resolves on a 200, rejects on timeout, and gives up early when told the process
    died.
- **Manual verification** of `main.js`, which is process glue and not meaningfully unit-testable:
  launch the app, confirm the dashboard renders with real league data (not an empty DB), quit, and
  confirm no `python.exe` survives.

## Out of scope

Deliberately excluded, and belonging to a future "ship to others" effort:

- PyInstaller bundling of the Python backend
- electron-builder / NSIS installer
- Auto-update
- Custom titlebar or chrome
- IPC / preload bridge
