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
