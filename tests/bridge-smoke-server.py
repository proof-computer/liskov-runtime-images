#!/usr/bin/env python3
"""Abstract Unix-socket JSON-RPC server for PRoot smoke tests."""

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
    parser.add_argument("--valid-identity", action="store_true")
    args = parser.parse_args()

    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.settimeout(15)
    server.bind(f"\0{args.socket_name}")
    server.listen(4)
    args.ready_file.write_text("ready\n", encoding="utf-8")

    methods: list[str] = []
    request_count = 4 if args.valid_identity else 1
    public_key = "ab" * 32
    for _ in range(request_count):
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
            method = request.get("method", "")
            methods.append(method)
            results = {
                "deployment_id": {
                    "id": "7",
                    "origin": {"kind": "Acurast", "source": "smoke"},
                },
                "deployment_publicKeys": {
                    "publicKeys": {"ed25519": public_key}
                },
                "deployment_assignedProcessors": {
                    "processors": {
                        "processor-smoke": {"ed25519": public_key}
                    }
                },
                "signer_sign": {"bytes": "11" * 64},
            }
            response = {
                "jsonrpc": "2.0",
                "id": request.get("id"),
                "result": results.get(method) if args.valid_identity else None,
            }
            connection.sendall(
                (json.dumps(response, separators=(",", ":")) + "\n").encode()
            )

    args.method_file.write_text(
        "".join(f"{method}\n" for method in methods),
        encoding="utf-8",
    )
    server.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
