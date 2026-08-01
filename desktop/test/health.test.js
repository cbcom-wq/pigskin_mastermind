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
