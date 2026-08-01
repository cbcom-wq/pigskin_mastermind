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
