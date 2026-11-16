"""CONNECT-only allowlist proxy for Signal job containers (S7.1): the only way out of `house-agent-int`.

Runs in the `house-agent-proxy` container (`python3 -m houselib.agentproxy`). It tunnels TLS (`CONNECT host:443`)
to hosts on the allowlist and refuses everything else: other hosts and ports get 403, plain HTTP methods 405. It
never sees plaintext (no TLS interception) and logs host names only. The allowlist comes from
`$HOUSE_AGENT_ALLOW` (comma-separated), else DEFAULT_ALLOW.
"""
import os
import select
import socket
import socketserver
import sys

DEFAULT_ALLOW = {"api.anthropic.com", "statsig.anthropic.com", "pypi.org", "files.pythonhosted.org",
                 "download.pytorch.org"}
PORTS = {443}
HEADER_MAX = 8192
IDLE_S = 300


def allowed(host: str, port: int, allow: set[str], ports: set[int] = frozenset(PORTS)) -> bool:
    """Exact host match (case and a trailing dot ignored) on an allowed port; no suffix or wildcard matching."""
    host = (host or "").strip().lower().rstrip(".")
    return bool(host) and host in {a.lower() for a in allow} and port in ports


class _Handler(socketserver.BaseRequestHandler):
    def _reply(self, status: str) -> None:
        self.request.sendall(f"HTTP/1.1 {status}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".encode())

    def handle(self) -> None:
        data = b""
        while b"\r\n\r\n" not in data and len(data) < HEADER_MAX:
            chunk = self.request.recv(1024)
            if not chunk:
                return
            data += chunk
        line = data.split(b"\r\n", 1)[0].decode("latin-1", errors="replace").split()
        if len(line) < 2 or line[0].upper() != "CONNECT":
            self._reply("405 Method Not Allowed")
            return
        host, _, port_text = line[1].rpartition(":")
        port = int(port_text) if port_text.isdigit() else 0
        server = self.server
        if not allowed(host, port, server.allow, server.ports):
            print(f"agentproxy: refused {host}:{port}", file=sys.stderr, flush=True)
            self._reply("403 Forbidden")
            return
        try:
            upstream = socket.create_connection(server.resolve(host, port), timeout=15)
        except OSError:
            self._reply("502 Bad Gateway")
            return
        print(f"agentproxy: tunnel {host}:{port}", file=sys.stderr, flush=True)
        self.request.sendall(b"HTTP/1.1 200 Connection established\r\n\r\n")
        rest = data.split(b"\r\n\r\n", 1)[1]
        if rest:
            upstream.sendall(rest)
        try:
            pair = {self.request: upstream, upstream: self.request}
            while True:
                ready, _, _ = select.select(list(pair), [], [], IDLE_S)
                if not ready:
                    return
                for s in ready:
                    chunk = s.recv(65536)
                    if not chunk:
                        return
                    pair[s].sendall(chunk)
        finally:
            upstream.close()


class _Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address) -> None:
        """One line instead of a traceback: clients that reset or time out are normal."""
        print(f"agentproxy: connection error ({sys.exc_info()[0].__name__})", file=sys.stderr, flush=True)


def make_server(host: str, port: int, allow: set[str]) -> _Server:
    """A ready proxy server (not yet serving); `resolve` and `ports` can be replaced in tests."""
    server = _Server((host, port), _Handler)
    server.allow = set(allow)
    server.ports = set(PORTS)
    server.resolve = lambda h, p: (h, p)
    return server


def main(argv: list[str]) -> int:
    """`python3 -m houselib.agentproxy [port]`: serve on 0.0.0.0 (default 3128) until killed."""
    port = int(argv[0]) if argv[:1] and argv[0].isdigit() else 3128
    allow = {a.strip() for a in os.environ.get("HOUSE_AGENT_ALLOW", "").split(",") if a.strip()} or DEFAULT_ALLOW
    print(f"agentproxy: :{port} allow {', '.join(sorted(allow))}", file=sys.stderr, flush=True)
    make_server("0.0.0.0", port, allow).serve_forever()  # inside its container, on its two networks only
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
