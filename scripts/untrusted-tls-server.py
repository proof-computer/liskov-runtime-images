#!/usr/bin/env python3
"""Serve HTTPS with the disposable fixture certificate until the smoke stops it."""

from __future__ import annotations

import argparse
import ssl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class QuietServer(ThreadingHTTPServer):
    def handle_error(self, request: object, client_address: object) -> None:
        return


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = b"untrusted-fixture\n"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cert", required=True, type=Path)
    parser.add_argument("--key", required=True, type=Path)
    parser.add_argument("--ready-file", required=True, type=Path)
    args = parser.parse_args(argv)

    server = QuietServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(args.cert, args.key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    server.timeout = 1
    port = server.server_address[1]
    args.ready_file.write_text(f"{port}\n", encoding="utf-8")
    while True:
        try:
            server.handle_request()
        except (ssl.SSLError, ConnectionError, TimeoutError, OSError):
            continue


if __name__ == "__main__":
    raise SystemExit(main())
