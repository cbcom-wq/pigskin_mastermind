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
