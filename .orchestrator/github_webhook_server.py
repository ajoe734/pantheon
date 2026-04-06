#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

THIS_DIR = Path(__file__).resolve().parent
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))

from common import append_jsonl, config_path, load_config, utc_now, write_activity_log


class Handler(BaseHTTPRequestHandler):
    server_version = "PantheonGitHubWebhook/1.0"

    def do_POST(self) -> None:  # noqa: N802
        server: "GitHubWebhookServer" = self.server  # type: ignore[assignment]
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        if not server.verify_signature(raw, self.headers.get("X-Hub-Signature-256")):
            self.send_error(HTTPStatus.UNAUTHORIZED, "Invalid signature")
            return
        try:
            payload = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            self.send_error(HTTPStatus.BAD_REQUEST, "Invalid JSON")
            return

        event = {
            "ts": utc_now(),
            "event": self.headers.get("X-GitHub-Event"),
            "delivery": self.headers.get("X-GitHub-Delivery"),
            "payload": payload,
        }
        append_jsonl(server.events_path, event)
        write_activity_log(
            server.config,
            {
                "type": "github_webhook_received",
                "message": f"Received GitHub webhook event {event['event']}",
                "github_delivery": event["delivery"],
            },
        )
        self.send_response(HTTPStatus.ACCEPTED)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
        return


class GitHubWebhookServer(ThreadingHTTPServer):
    def __init__(self, server_address: tuple[str, int], handler: type[BaseHTTPRequestHandler], *, config: dict[str, Any], events_path: Path, secret: str | None):
        super().__init__(server_address, handler)
        self.config = config
        self.events_path = events_path
        self.secret = secret

    def verify_signature(self, body: bytes, header_value: str | None) -> bool:
        if not self.secret:
            return True
        if not header_value or not header_value.startswith("sha256="):
            return False
        expected = hmac.new(self.secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        actual = header_value.split("=", 1)[1]
        return hmac.compare_digest(expected, actual)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Receive GitHub App/webhook events into the local Pantheon orchestrator inbox.")
    parser.add_argument("--config", default=".orchestrator/config.json")
    parser.add_argument("--listen", default=None, help="Override host:port, otherwise use config github_bus.phase3.webhook")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_config(args.config)
    webhook_cfg = ((config.get("github_bus") or {}).get("phase3") or {}).get("webhook") or {}
    listen = args.listen or f"{webhook_cfg.get('host', '127.0.0.1')}:{int(webhook_cfg.get('port', 8776))}"
    host, port_text = listen.rsplit(":", 1)
    secret = os.environ.get(webhook_cfg.get("secret_env", "PANTHEON_GITHUB_WEBHOOK_SECRET"))
    events_path = config_path(config, "github_webhook_events")
    events_path.parent.mkdir(parents=True, exist_ok=True)
    server = GitHubWebhookServer((host, int(port_text)), Handler, config=config, events_path=events_path, secret=secret)
    print(f"Listening on http://{host}:{port_text}")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
