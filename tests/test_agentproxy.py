"""`houselib.agentproxy`: the CONNECT-only allowlist proxy in front of Signal job containers."""
import socket
import threading
import unittest

from houselib import agentproxy


class AllowTest(unittest.TestCase):
    def test_allowed(self):
        allow = {"api.anthropic.com", "pypi.org"}
        self.assertTrue(agentproxy.allowed("api.anthropic.com", 443, allow))
        self.assertTrue(agentproxy.allowed("API.Anthropic.com.", 443, allow))
        for host, port in (("api.anthropic.com.evil.io", 443), ("evil.io", 443), ("pypi.org", 80),
                           ("xapi.anthropic.com", 443), ("", 443)):
            self.assertFalse(agentproxy.allowed(host, port, allow), (host, port))


class ServeTest(unittest.TestCase):
    def setUp(self):
        self.server = agentproxy.make_server("127.0.0.1", 0, {"allowed.invalid"})
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def ask(self, request: bytes) -> bytes:
        with socket.create_connection(("127.0.0.1", self.port), timeout=5) as s:
            s.sendall(request)
            return s.recv(200)

    def test_refused_host_gets_403(self):
        self.assertTrue(self.ask(b"CONNECT example.com:443 HTTP/1.1\r\nHost: example.com:443\r\n\r\n")
                        .startswith(b"HTTP/1.1 403"))

    def test_plain_get_gets_405(self):
        self.assertTrue(self.ask(b"GET http://example.com/ HTTP/1.1\r\nHost: example.com\r\n\r\n")
                        .startswith(b"HTTP/1.1 405"))

    def test_allowed_host_tunnels(self):
        upstream = socket.socket()
        upstream.bind(("127.0.0.1", 0))
        upstream.listen(1)
        up_port = upstream.getsockname()[1]

        def echo():
            conn, _ = upstream.accept()
            conn.sendall(b"hello from upstream")
            conn.close()
        threading.Thread(target=echo, daemon=True).start()
        self.server.resolve = lambda host, port: ("127.0.0.1", up_port)
        self.server.ports = {443}
        reply = self.ask(b"CONNECT allowed.invalid:443 HTTP/1.1\r\nHost: allowed.invalid:443\r\n\r\n")
        self.assertTrue(reply.startswith(b"HTTP/1.1 200"), reply)
        upstream.close()


if __name__ == "__main__":
    unittest.main()
