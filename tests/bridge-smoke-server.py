#!/usr/bin/env python3
"""One-request abstract Unix-socket JSON-RPC server for PRoot smoke tests."""

from __future__ import annotations

import argparse
import json
import socket
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket-name", required=True)
    parser.add_argument("--ready-file", required=True, type=Path)
    parser.add_argument("--method-file", required=True, type=Path)
    args = parser.parse_args()

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.settimeout(15)
    server.bind(f"\0{args.socket_name}")
    server.listen(1)
    args.ready_file.write_text("ready\n", encoding="utf-8")
    connection, _ = server.accept()
    with connection:
        connection.settimeout(10)
        data = bytearray()
        while not data.endswith(b"\n"):
            chunk = connection.recv(4096)
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > 65536:
                raise RuntimeError("oversized smoke request")
        request = json.loads(bytes(data))
        args.method_file.write_text(f"{request.get('method', '')}\n", encoding="utf-8")
        response = {
            "jsonrpc": "2.0",
            "id": request.get("id"),
            "result": None,
        }
        connection.sendall(
            (json.dumps(response, separators=(",", ":")) + "\n").encode()
        )
    server.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
