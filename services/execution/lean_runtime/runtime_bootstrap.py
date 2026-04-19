"""Execution-plane entrypoint with backward-compatible role dispatch.

Legacy bootstrap-only behavior is retired for the paper runtime role. This
entrypoint now dispatches paper execution roles to the truthful paper runtime
package while keeping lightweight health-only sidecars for broker/exchange
adapters and legacy live placeholders.
"""

from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.execution.lean_runtime.paper_runtime import main
from services.execution.lean_runtime.runtime_identity import RuntimeIdentity


def _as_bool(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _masked_secret_status(env_key: str) -> dict[str, Any]:
    value = os.getenv(env_key, "")
    return {
        "env": env_key,
        "configured": bool(value),
        "length": len(value),
    }


def _load_sidecar_state(role: str) -> dict[str, Any]:
    required_secrets = [
        key.strip()
        for key in os.getenv("PANTHEON_REQUIRED_SECRET_KEYS", "").split(",")
        if key.strip()
    ]
    identity = RuntimeIdentity.from_env()
    return {
        **identity.to_health_payload(),
        "runtime_role": role,
        "adapter_mode": os.getenv("PANTHEON_ADAPTER_MODE", "mock"),
        "upstream_runtime_manager_url": identity.runtime_manager_url or "http://runtime-manager:8081",
        "required_secret_status": [_masked_secret_status(key) for key in required_secrets],
        "secrets_optional": _as_bool(os.getenv("PANTHEON_SECRETS_OPTIONAL", "false")),
        "runtime_package": "execution_sidecar",
        "stub_mode": False,
    }


class _SidecarHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return

    def do_GET(self) -> None:  # noqa: N802
        body: dict[str, Any]
        status_code: int
        if self.path in {"/", "/__health__", "/health"}:
            status_code = 200
            body = {
                "status": "ok",
                **_load_sidecar_state(os.getenv("PANTHEON_RUNTIME_ROLE", "execution-sidecar")),
            }
        else:
            status_code = 404
            body = {"status": "not_found", "path": self.path}
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


if __name__ == "__main__":
    role = os.getenv("PANTHEON_RUNTIME_ROLE", "pantheon-paper-execution-runtime")
    if role in {"pantheon-paper-execution-runtime", "pantheon-lean-paper-runtime"}:
        main()
    else:
        host = os.getenv("HOST", "0.0.0.0")
        port = int(os.getenv("PORT", "8010"))
        server = ThreadingHTTPServer((host, port), _SidecarHandler)
        print(
            json.dumps(
                {
                    "message": "execution sidecar ready",
                    "role": role,
                    "port": port,
                    "stub_mode": False,
                }
            ),
            flush=True,
        )
        server.serve_forever()
