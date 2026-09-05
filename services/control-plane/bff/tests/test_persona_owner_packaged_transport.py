"""Exercise provisioning transport through the production package import."""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import sys
import threading


def test_packaged_transport_handles_owner_readback_and_compensation() -> None:
    calls: list[tuple[str, str, object]] = []

    class Owner(BaseHTTPRequestHandler):
        def log_message(self, *_args: object) -> None:
            pass

        def respond(self, status: int, value: object) -> None:
            body = json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            calls.append(("GET", self.path, None))
            if self.path == "/missing":
                self.respond(404, {"error": "not_found"})
            elif self.path == "/unavailable":
                self.respond(503, {"error": "unavailable"})
            else:
                self.respond(200, {"id": "binding", "status": "suspended"})

        def write_receipt(self) -> None:
            value = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append((self.command, self.path, value))
            self.respond(200, {"id": "binding", **value})

        do_POST = write_receipt
        do_PATCH = write_receipt

    server = ThreadingHTTPServer(("127.0.0.1", 0), Owner)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        # A fresh process avoids legacy test path aliases and main.py's test
        # facade masking missing imports in the canonical Persona service.
        result = subprocess.run(
            [sys.executable, "-c", """
import os, sys
from urllib.error import HTTPError
from services.control_plane.bff.personas.service import (
    _PersonaOwnerHttpTransport, build_pm12_allocation_policy_input,
)
policy = build_pm12_allocation_policy_input({'overall_score': 80, 'tier': 'tier-2'})
assert policy['rank_score'] == 80
assert policy['allocation_tier'] == 'a'
os.environ['PANTHEON_CAPITAL_API_URL'] = sys.argv[1]
transport = _PersonaOwnerHttpTransport()
assert transport.get('capital', '/missing') is None
try:
    transport.get('capital', '/unavailable')
except HTTPError as exc:
    assert exc.code == 503
else:
    raise AssertionError('owner failure was swallowed')
assert transport.post('capital', '/binding', {'status': 'active'})['status'] == 'active'
assert transport.patch('capital', '/binding', {'status': 'suspended'})['status'] == 'suspended'
assert transport.get('capital', '/binding')['status'] == 'suspended'
""", f"http://127.0.0.1:{server.server_port}"],
            cwd=Path(__file__).resolve().parents[4],
            capture_output=True,
            text=True,
            timeout=40,
        )
        assert result.returncode == 0, result.stderr
        assert calls == [
            ("GET", "/missing", None),
            ("GET", "/unavailable", None),
            ("POST", "/binding", {"status": "active"}),
            ("PATCH", "/binding", {"status": "suspended"}),
            ("GET", "/binding", None),
        ]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
