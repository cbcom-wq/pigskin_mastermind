# Electron Desktop Wrapper Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Pigskin Mastermind launchable as a double-clickable desktop app that starts its own
FastAPI backend and shuts it down on quit.

**Architecture:** A new, self-contained `desktop/` directory holds an Electron main process. On
launch it picks a free ephemeral port, spawns `uvicorn` from the repo's existing `.venv` pointed at
the repo's SQLite database, shows a splash window while the backend boots, then loads the app once
the server answers. On quit it kills the backend process tree. No Python source is modified.

**Tech Stack:** Electron (latest), Node 24 built-in `node:test` runner, CommonJS modules. Backend
is the existing FastAPI/uvicorn app — unchanged.

## Global Constraints

- **Do not modify any file under `src/pigskin_mastermind/`.** This wrapper is purely additive; the
  existing `uvicorn`, `pytest tests/`, and `pigskin` CLI workflows must keep working unchanged.
- **Do not add a `package.json` at the repo root.** The project's "no npm, no build step" property
  is deliberate. Everything npm-related lives under `desktop/`.
- **Use `desktop/src/`, never `desktop/lib/`.** `.gitignore` line 13 is a blanket `lib/` rule that
  matches at any depth; files under a `lib/` directory would be silently untracked.
- Repo root is `D:\git\pigskin_mastermind`. Python interpreter is `.venv\Scripts\python.exe`.
  Database file is `pigskin_mastermind.db` at the repo root.
- CommonJS (`require`), not ESM. No `"type": "module"`.
- Windows-only. `taskkill` is used unconditionally; do not add cross-platform branching.

---

### Task 1: Scaffold the desktop package and the port picker

Creates the npm package, ignores `node_modules`, and delivers the first tested module.

**Files:**
- Create: `desktop/package.json`
- Create: `desktop/src/port.js`
- Create: `desktop/test/port.test.js`
- Modify: `.gitignore` (append a `desktop/node_modules/` rule)

**Interfaces:**
- Consumes: nothing.
- Produces: `pickPort(): Promise<number>` from `desktop/src/port.js` — resolves to a TCP port on
  `127.0.0.1` that was free at the moment of the call.

- [ ] **Step 1: Create `desktop/package.json`**

```json
{
  "name": "pigskin-mastermind-desktop",
  "version": "0.1.0",
  "private": true,
  "description": "Electron launcher for the Pigskin Mastermind FastAPI app",
  "main": "main.js",
  "scripts": {
    "start": "electron .",
    "test": "node --test test/"
  }
}
```

> **Note (post-implementation):** `node --test test/` fails on Node 24 — the runner treats
> `test/` as a file, not a directory glob. The implementation correctly uses
> `node --test test/*.test.js` instead; keep that form if this file is used as a reference.

- [ ] **Step 2: Ignore `node_modules`**

Append to `.gitignore`:

```
# Electron desktop wrapper
desktop/node_modules/
```

Verify it took effect:

Run: `git check-ignore -v desktop/node_modules`
Expected: prints a line naming the new rule. If it prints nothing, the rule is wrong — fix before
continuing, or a few hundred megabytes of dependencies land in the commit.

- [ ] **Step 3: Install Electron**

Run from the `desktop/` directory:

```bash
npm install --save-dev electron
```

This adds the `devDependencies` entry and `package-lock.json` automatically. Do not hand-write a
version number — take whatever npm resolves as latest.

- [ ] **Step 4: Write the failing test**

Create `desktop/test/port.test.js`:

```js
const test = require('node:test');
const assert = require('node:assert');
const net = require('node:net');
const { pickPort } = require('../src/port');

test('pickPort resolves to a plausible TCP port number', async () => {
  const port = await pickPort();
  assert.strictEqual(typeof port, 'number');
  assert.ok(port > 1024 && port < 65536, `port ${port} out of expected range`);
});

test('pickPort returns a port that can actually be bound', async () => {
  const port = await pickPort();
  await new Promise((resolve, reject) => {
    const server = net.createServer();
    server.on('error', reject);
    server.listen(port, '127.0.0.1', () => server.close(resolve));
  });
});
```

The second test is the one that matters: it proves the port was actually released, not just
reported. A `pickPort` that leaves its probe socket open would pass a range check and then make
uvicorn fail with `EADDRINUSE`.

- [ ] **Step 5: Run the test to verify it fails**

Run from `desktop/`: `npm test`
Expected: FAIL — `Cannot find module '../src/port'`

- [ ] **Step 6: Write the implementation**

Create `desktop/src/port.js`:

```js
const net = require('node:net');

/**
 * Ask the OS for a free ephemeral port on the loopback interface.
 * Binding to port 0 lets the kernel choose, which avoids racing a
 * development uvicorn already sitting on 8000 or 8010.
 */
function pickPort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.unref();
    server.on('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const { port } = server.address();
      server.close(() => resolve(port));
    });
  });
}

module.exports = { pickPort };
```

- [ ] **Step 7: Run the tests to verify they pass**

Run from `desktop/`: `npm test`
Expected: PASS — 2 tests passing.

- [ ] **Step 8: Commit**

```bash
git add .gitignore desktop/package.json desktop/package-lock.json desktop/src/port.js desktop/test/port.test.js
git commit -m "feat(desktop): scaffold Electron package and free-port picker"
```

Check `git status` before committing — if `desktop/node_modules` appears as untracked, Step 2 did
not work. Fix it rather than committing.

---

### Task 2: Backend readiness polling

**Files:**
- Create: `desktop/src/health.js`
- Create: `desktop/test/health.test.js`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: `waitForServer(url, options): Promise<void>` from `desktop/src/health.js`.
  - `url: string` — polled with GET.
  - `options.timeoutMs: number` — default `60000`.
  - `options.intervalMs: number` — default `250`.
  - `options.isAlive: () => boolean` — default `() => true`. Polling aborts when this returns false.
  - `options.fetchImpl: typeof fetch` — default global `fetch`. Injection point for tests.
  - Resolves on the first HTTP 200. Rejects with an `Error` on timeout or on `isAlive()` false.

- [ ] **Step 1: Write the failing test**

Create `desktop/test/health.test.js`:

```js
const test = require('node:test');
const assert = require('node:assert');
const { waitForServer } = require('../src/health');

test('resolves once the server returns 200', async () => {
  let calls = 0;
  const fetchImpl = async () => {
    calls += 1;
    return { status: calls < 3 ? 502 : 200 };
  };

  await waitForServer('http://127.0.0.1:1/', { fetchImpl, intervalMs: 1 });

  assert.strictEqual(calls, 3);
});

test('keeps polling through connection refusals', async () => {
  let calls = 0;
  const fetchImpl = async () => {
    calls += 1;
    if (calls < 3) throw new Error('ECONNREFUSED');
    return { status: 200 };
  };

  await waitForServer('http://127.0.0.1:1/', { fetchImpl, intervalMs: 1 });

  assert.strictEqual(calls, 3);
});

test('gives up as soon as the backend process dies', async () => {
  let alive = true;
  const fetchImpl = async () => {
    alive = false;
    throw new Error('ECONNREFUSED');
  };

  await assert.rejects(
    () => waitForServer('http://127.0.0.1:1/', {
      fetchImpl,
      intervalMs: 1,
      timeoutMs: 60000,
      isAlive: () => alive,
    }),
    /exited before it became ready/,
  );
});

test('rejects once the timeout elapses', async () => {
  const fetchImpl = async () => {
    throw new Error('ECONNREFUSED');
  };

  await assert.rejects(
    () => waitForServer('http://127.0.0.1:1/', { fetchImpl, intervalMs: 1, timeoutMs: 20 }),
    /did not respond/,
  );
});
```

The third test is the important one. Without the `isAlive` check, a backend that dies instantly —
the likely first-run failure, `ModuleNotFoundError: No module named 'espn_api'` — leaves the user
staring at a splash screen for the full 60 seconds before any error appears. Note it asserts with a
60s timeout, so it can only pass by way of the liveness check, not by timing out.

- [ ] **Step 2: Run the test to verify it fails**

Run from `desktop/`: `npm test`
Expected: FAIL — `Cannot find module '../src/health'`

- [ ] **Step 3: Write the implementation**

Create `desktop/src/health.js`:

```js
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/**
 * Poll `url` until it answers with HTTP 200.
 *
 * Rejects early if `isAlive()` goes false, so a backend that crashes on
 * import surfaces its error immediately instead of after the full timeout.
 */
async function waitForServer(url, options = {}) {
  const {
    timeoutMs = 60000,
    intervalMs = 250,
    isAlive = () => true,
    fetchImpl = fetch,
    now = Date.now,
  } = options;

  const deadline = now() + timeoutMs;

  for (;;) {
    if (!isAlive()) {
      throw new Error('Backend process exited before it became ready');
    }

    try {
      const response = await fetchImpl(url);
      if (response.status === 200) return;
    } catch {
      // Connection refused while uvicorn is still importing. Keep waiting.
    }

    if (now() >= deadline) {
      throw new Error(`Backend did not respond at ${url} within ${timeoutMs}ms`);
    }

    await sleep(intervalMs);
  }
}

module.exports = { waitForServer };
```

- [ ] **Step 4: Run the tests to verify they pass**

Run from `desktop/`: `npm test`
Expected: PASS — 6 tests passing (2 from Task 1, 4 from this task).

- [ ] **Step 5: Commit**

```bash
git add desktop/src/health.js desktop/test/health.test.js
git commit -m "feat(desktop): add backend readiness polling"
```

---

### Task 3: Electron main process and splash screen

The launchable deliverable. After this task, `npm start` opens the working app.

**Files:**
- Create: `desktop/loading.html`
- Create: `desktop/main.js`

**Interfaces:**
- Consumes: `pickPort()` from `desktop/src/port.js`; `waitForServer(url, options)` from
  `desktop/src/health.js`.
- Produces: `npm start` (in `desktop/`) as the launch command. No exported API.

- [ ] **Step 1: Create the splash page**

Create `desktop/loading.html`. It is shown instantly so the several-second cold import of pandas,
numpy, scipy, and `nfl_data_py` does not look like a hang.

```html
<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <title>Pigskin Mastermind</title>
    <style>
      html, body {
        height: 100%;
        margin: 0;
        display: flex;
        align-items: center;
        justify-content: center;
        background: #0f172a;
        color: #e2e8f0;
        font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
      }
      .box { text-align: center; }
      h1 { font-size: 1.5rem; font-weight: 600; margin: 0 0 0.5rem; }
      p { margin: 0; color: #94a3b8; font-size: 0.9rem; }
      .bar {
        margin: 1.5rem auto 0;
        width: 220px;
        height: 3px;
        border-radius: 3px;
        background: #1e293b;
        overflow: hidden;
      }
      .bar::after {
        content: "";
        display: block;
        width: 40%;
        height: 100%;
        border-radius: 3px;
        background: #38bdf8;
        animation: slide 1.1s ease-in-out infinite;
      }
      @keyframes slide {
        0%   { transform: translateX(-100%); }
        100% { transform: translateX(250%); }
      }
    </style>
  </head>
  <body>
    <div class="box">
      <h1>Pigskin Mastermind</h1>
      <p>Starting the server&hellip;</p>
      <div class="bar"></div>
    </div>
  </body>
</html>
```

- [ ] **Step 2: Write the main process**

Create `desktop/main.js`:

```js
const { app, BrowserWindow, dialog, shell } = require('electron');
const { spawn, execFile } = require('node:child_process');
const path = require('node:path');

const { pickPort } = require('./src/port');
const { waitForServer } = require('./src/health');

const REPO_ROOT = path.resolve(__dirname, '..');
const PYTHON = path.join(REPO_ROOT, '.venv', 'Scripts', 'python.exe');
const DB_PATH = path.join(REPO_ROOT, 'pigskin_mastermind.db');

const MAX_LOG_LINES = 200;
const REPORTED_LOG_LINES = 50;

let backend = null;
let backendExited = false;
let mainWindow = null;
const logLines = [];

function recordOutput(chunk) {
  for (const line of chunk.toString().split(/\r?\n/)) {
    if (line.trim()) logLines.push(line);
  }
  if (logLines.length > MAX_LOG_LINES) {
    logLines.splice(0, logLines.length - MAX_LOG_LINES);
  }
}

function recentLog() {
  return logLines.slice(-REPORTED_LOG_LINES).join('\n') || '(no output captured)';
}

function startBackend(port) {
  // Absolute DATABASE_URL is load-bearing: the app default is the
  // cwd-relative sqlite:///./pigskin_mastermind.db, which would quietly
  // create an empty database instead of failing visibly.
  const databaseUrl = `sqlite:///${DB_PATH.replace(/\\/g, '/')}`;

  const child = spawn(
    PYTHON,
    [
      '-m', 'uvicorn',
      'pigskin_mastermind.api.main:app',
      '--host', '127.0.0.1',
      '--port', String(port),
    ],
    {
      cwd: REPO_ROOT,
      env: { ...process.env, DATABASE_URL: databaseUrl, PYTHONUNBUFFERED: '1' },
    },
  );

  child.stdout.on('data', recordOutput);
  child.stderr.on('data', recordOutput);
  child.on('exit', (code) => {
    backendExited = true;
    recordOutput(`Backend process exited with code ${code}`);
  });
  child.on('error', (err) => {
    backendExited = true;
    recordOutput(`Failed to launch ${PYTHON}: ${err.message}`);
  });

  return child;
}

function stopBackend() {
  if (!backend || backendExited) {
    backend = null;
    return;
  }

  const { pid } = backend;
  backend = null;

  // A plain kill() leaves uvicorn orphaned on Windows, still holding the
  // port and the SQLite file. /T kills the tree, /F forces it.
  execFile('taskkill', ['/PID', String(pid), '/T', '/F'], () => {});
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    title: 'Pigskin Mastermind',
    backgroundColor: '#0f172a',
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  mainWindow.loadFile(path.join(__dirname, 'loading.html'));

  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith('http://127.0.0.1:')) return { action: 'allow' };
    shell.openExternal(url);
    return { action: 'deny' };
  });

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

app.whenReady().then(async () => {
  createWindow();

  const port = await pickPort();
  const url = `http://127.0.0.1:${port}/`;
  backend = startBackend(port);

  try {
    await waitForServer(url, { isAlive: () => !backendExited });
    if (mainWindow) mainWindow.loadURL(url);
  } catch (err) {
    dialog.showErrorBox(
      'Pigskin Mastermind failed to start',
      `${err.message}\n\nLast output from the backend:\n\n${recentLog()}`,
    );
    app.quit();
  }
});

app.on('window-all-closed', () => {
  app.quit();
});

app.on('before-quit', stopBackend);
```

Note there is exactly one shutdown path: `window-all-closed` calls `app.quit()`, which fires
`before-quit`, which kills the backend. Do not also call `stopBackend` from `window-all-closed`.

- [ ] **Step 3: Launch the app**

Run from `desktop/`: `npm start`

Expected: a dark splash window appears immediately, then within roughly 5–20 seconds it is replaced
by the Pigskin Mastermind dashboard.

If an error dialog appears instead, read it — it contains the backend's actual output. The most
likely cause is `ModuleNotFoundError: No module named 'espn_api'`, which means the git-ignored
vendored client is missing; `pip install espn_api` into the venv resolves it.

- [ ] **Step 4: Verify it is using the real database**

In the running window, confirm the dashboard shows your actual teams and players rather than an
empty state. An empty dashboard means `DATABASE_URL` did not take and a blank DB was created —
check `git status` for a stray new `.db` file.

- [ ] **Step 5: Verify shutdown leaves nothing behind**

Close the window, then run:

```bash
tasklist /FI "IMAGENAME eq python.exe"
```

Expected: no `python.exe` from the app remains. If one survives, `stopBackend` is not firing.

- [ ] **Step 6: Verify the unit tests still pass**

Run from `desktop/`: `npm test`
Expected: PASS — 6 tests.

- [ ] **Step 7: Commit**

```bash
git add desktop/main.js desktop/loading.html
git commit -m "feat(desktop): add Electron main process and splash screen"
```

---

### Task 4: Document the desktop launcher

**Files:**
- Modify: `CLAUDE.md` (the "Commands" section)
- Modify: `docs/PROJECT_STATUS.md` (the "Feature status" table)

**Interfaces:**
- Consumes: the working `npm start` from Task 3.
- Produces: nothing consumed by later tasks.

- [ ] **Step 1: Add a desktop section to `CLAUDE.md`**

In the "Commands" section, after the block that shows the `uvicorn` command, add:

````markdown
### Desktop app (Electron)

A local-only launcher lives in `desktop/`. It spawns uvicorn from `.venv` on a free ephemeral port,
points a window at it, and kills the backend on quit.

```bash
cd desktop
npm install     # first time only
npm start       # launch the desktop app
npm test        # unit tests for the port picker and health poll
```

It uses the repo's `pigskin_mastermind.db` via an absolute `DATABASE_URL`. **Do not run the desktop
app and a dev `uvicorn` at the same time** — two writers on one SQLite file produce
`database is locked`.

Helper modules live in `desktop/src/`, not `desktop/lib/`: the blanket `lib/` rule in `.gitignore`
matches at any depth and would leave them untracked.
````

- [ ] **Step 2: Add a row to the `docs/PROJECT_STATUS.md` feature table**

Add to the "Feature status" table, after the "Season animation charts" row:

```markdown
| Desktop app (Electron launcher) | Working, local-only; requires the repo and `.venv` in place |
```

- [ ] **Step 3: Verify the documented commands actually work**

Run from `desktop/`: `npm test`
Expected: PASS — 6 tests. This confirms the `npm test` script named in the docs is real.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md docs/PROJECT_STATUS.md
git commit -m "docs: document the Electron desktop launcher"
```

---

## Manual verification checklist

After all four tasks, confirm end to end:

- [ ] `cd desktop && npm start` opens the splash, then the dashboard with real data.
- [ ] Closing the window leaves no orphaned `python.exe`.
- [ ] `uvicorn pigskin_mastermind.api.main:app --port 8010` still works independently.
- [ ] `pytest tests/` still collects and runs as before (631 passed / 17 failed is the known
      baseline — this plan should not change those numbers).
- [ ] `git status` shows no `desktop/node_modules` and no stray `.db` file.
