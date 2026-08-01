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
