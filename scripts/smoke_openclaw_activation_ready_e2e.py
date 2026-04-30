#!/usr/bin/env python3
"""OpenClaw activation-ready E2E smoke.

This smoke keeps all external dependencies fake and local, but exercises the
real adapter clients and gates:
- default adapter posture degrades safely when upstream is absent;
- fake upstream proves capabilities, sessions, tools, and workflows;
- fake runtime-manager + fake broker prove the gated paper adapter;
- live broker execution remains explicitly denied.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from unittest import mock

from fastapi.testclient import TestClient


REPO_ROOT = Path(__file__).resolve().parent.parent
ADAPTER_DIR = REPO_ROOT / "services" / "openclaw-gateway-adapter"
TASK_ID = "SVC-OPENCLAW-ACTIVATION-READY-E2E"


@dataclass
class SmokeRow:
    row: str
    status: str
    evidence: dict[str, Any] = field(default_factory=dict)
    error: str = ""


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _read_body(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    raw = handler.rfile.read(int(handler.headers.get("Content-Length", "0") or "0"))
    if not raw:
        return {}
    loaded = json.loads(raw.decode("utf-8"))
    return loaded if isinstance(loaded, dict) else {}


class _JsonFixtureServer:
    def __init__(self, handler: type[BaseHTTPRequestHandler]) -> None:
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.url = f"http://127.0.0.1:{self._server.server_port}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self) -> "_JsonFixtureServer":
        self._thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


class FakeOpenClawHandler(BaseHTTPRequestHandler):
    sessions: dict[str, dict[str, Any]] = {}
    jobs: dict[str, dict[str, Any]] = {}

    def do_GET(self) -> None:  # noqa: N802 - stdlib callback
        path, _, query = self.path.partition("?")
        params = urllib.parse.parse_qs(query)
        if path in {"/healthz", "/readyz"}:
            _json_response(self, 200, {"status": "ok", "service": "fake-openclaw"})
            return
        if path == "/api/capabilities":
            _json_response(
                self,
                200,
                {
                    "status": "ok",
                    "sessions": True,
                    "tools": ["research.search", "paper.execute"],
                    "workflows": ["research.daily_scan"],
                    "broker_execution": False,
                },
            )
            return
        if path == "/api/sessions":
            _json_response(self, 200, {"sessions": list(self.sessions.values())})
            return
        if path.startswith("/api/sessions/"):
            session_id = path.rsplit("/", 1)[-1]
            session = self.sessions.get(session_id)
            _json_response(self, 200 if session else 404, session or {"error_code": "UPSTREAM_NOT_FOUND"})
            return
        if path == "/api/tools":
            del params
            _json_response(
                self,
                200,
                {"tools": [{"name": "research.search"}, {"name": "paper.execute"}]},
            )
            return
        if path == "/api/tools/resolve":
            _json_response(self, 200, {"tools": [{"name": "research.search"}]})
            return
        if path.startswith("/api/jobs/"):
            job_id = path.rsplit("/", 1)[-1]
            _json_response(self, 200, self.jobs.get(job_id, {"job_id": job_id, "status": "queued"}))
            return
        _json_response(self, 404, {"error_code": "NOT_FOUND", "path": path})

    def do_POST(self) -> None:  # noqa: N802 - stdlib callback
        path = self.path
        body = _read_body(self)
        if path == "/api/sessions":
            session_id = f"fake-session-{len(self.sessions) + 1}"
            session = {
                "session_id": session_id,
                "agent_id": body.get("agent_id", "agent-alpha"),
                "session_type": body.get("session_type", "interactive"),
                "status": "active",
            }
            self.sessions[session_id] = session
            _json_response(self, 201, session)
            return
        if path.endswith("/cancel") and path.startswith("/api/sessions/"):
            session_id = path.split("/")[-2]
            session = self.sessions.setdefault(
                session_id,
                {"session_id": session_id, "agent_id": "unknown", "session_type": "interactive"},
            )
            session["status"] = "canceled"
            _json_response(self, 200, session)
            return
        if path == "/api/tools/invoke":
            _json_response(
                self,
                200,
                {
                    "status": "ok",
                    "tool_name": body.get("tool_name"),
                    "result": {"items": [{"symbol": "AAPL", "score": 1.0}]},
                },
            )
            return
        if path == "/api/workflows/trigger":
            job_id = "fake-job-1"
            self.jobs[job_id] = {"job_id": job_id, "status": "queued", "workflow_ref": body.get("workflow_ref")}
            _json_response(self, 202, self.jobs[job_id])
            return
        _json_response(self, 404, {"error_code": "NOT_FOUND", "path": path})

    def log_message(self, _format: str, *_args: Any) -> None:
        return


class FakeRuntimeManagerHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - stdlib callback
        path = urllib.parse.urlparse(self.path).path
        if path == "/readyz":
            _json_response(self, 200, {"status": "ok", "service": "fake-runtime-manager"})
            return
        if path.startswith("/api/runtimes/") and path.endswith("/active"):
            pool_id = urllib.parse.unquote(path.split("/")[-2])
            if pool_id == "pool-paper":
                _json_response(
                    self,
                    200,
                    {
                        "binding_id": "rb-paper-activation-ready",
                        "runtime_id": "rt-paper-activation-ready",
                        "capital_pool_id": "pool-paper",
                        "artifact_id": "strategy-alpha",
                        "artifact_version": "1.0.0",
                        "deployment_mode": "paper",
                        "status": "active",
                        "persona_capital_binding_id": "pcb-paper",
                        "metadata": {"strategy_id": "strategy-alpha"},
                    },
                )
                return
            _json_response(self, 404, {"error_code": "RUNTIME_BINDING_NOT_FOUND"})
            return
        _json_response(self, 404, {"error_code": "NOT_FOUND", "path": path})

    def log_message(self, _format: str, *_args: Any) -> None:
        return


class FakeBrokerHandler(BaseHTTPRequestHandler):
    orders: list[dict[str, Any]] = []

    def do_GET(self) -> None:  # noqa: N802 - stdlib callback
        path = urllib.parse.urlparse(self.path).path
        if path in {"/healthz", "/readyz", "/livez"}:
            _json_response(self, 200, {"status": "ok", "service": "fake-broker", "paper_enabled": True})
            return
        if path == "/api/broker/paper/orders":
            _json_response(self, 200, {"status": "ok", "orders": list(self.orders)})
            return
        if path.startswith("/api/broker/paper/orders/"):
            order_id = path.rsplit("/", 1)[-1]
            for order in self.orders:
                if order.get("order_id") == order_id:
                    _json_response(self, 200, {"status": "ok", "order": order})
                    return
            _json_response(self, 404, {"status": "broker_error", "error_code": "ORDER_NOT_FOUND"})
            return
        _json_response(self, 404, {"error_code": "NOT_FOUND", "path": path})

    def do_POST(self) -> None:  # noqa: N802 - stdlib callback
        path = self.path
        body = _read_body(self)
        if path == "/api/broker/paper/orders":
            order = {
                "order_id": f"paper-order-{len(self.orders) + 1}",
                "capital_pool_id": body.get("capital_pool_id"),
                "strategy_id": body.get("strategy_id"),
                "symbol": body.get("symbol"),
                "qty": body.get("qty"),
                "side": body.get("side"),
                "status": "filled",
                "fill_qty": body.get("qty"),
                "fill_price": 100.0,
                "sim_fill_flag": True,
            }
            self.orders.append(order)
            _json_response(self, 201, {"status": "ok", "order": order})
            return
        _json_response(self, 404, {"error_code": "NOT_FOUND", "path": path})

    def log_message(self, _format: str, *_args: Any) -> None:
        return


def _load_adapter_module(env: dict[str, str], module_name: str):
    for name in (
        "main",
        "session_lifecycle",
        "tool_workflow_bridge",
        "paper_broker_adapter",
        "live_gate_adapter",
    ):
        sys.modules.pop(name, None)
    sys.path.insert(0, str(REPO_ROOT))
    sys.path.insert(0, str(ADAPTER_DIR))
    with mock.patch.dict(os.environ, env, clear=False):
        spec = importlib.util.spec_from_file_location(module_name, ADAPTER_DIR / "main.py")
        if spec is None or spec.loader is None:
            raise RuntimeError("cannot load openclaw adapter module")
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    try:
        sys.path.remove(str(ADAPTER_DIR))
    except ValueError:
        pass
    try:
        sys.path.remove(str(REPO_ROOT))
    except ValueError:
        pass
    return module


def _client_json(client: TestClient, method: str, path: str, **kwargs: Any) -> tuple[int, dict[str, Any]]:
    response = client.request(method, path, **kwargs)
    try:
        payload = response.json()
    except Exception:
        payload = {"raw": response.text}
    return response.status_code, payload


def _pass(row: str, evidence: dict[str, Any] | None = None) -> SmokeRow:
    return SmokeRow(row=row, status="passed", evidence=evidence or {})


def _fail(row: str, error: str, evidence: dict[str, Any] | None = None) -> SmokeRow:
    return SmokeRow(row=row, status="failed", error=error, evidence=evidence or {})


def _check(row: str, condition: bool, evidence: dict[str, Any], error: str) -> SmokeRow:
    return _pass(row, evidence) if condition else _fail(row, error, evidence)


def _default_degraded_rows(tmp: Path) -> list[SmokeRow]:
    env = {
        "OPENCLAW_GATEWAY_URL": "",
        "OPENCLAW_PAPER_ADAPTER_ENABLED": "false",
        "OPENCLAW_LIVE_ADAPTER_ENABLED": "false",
        "OPENCLAW_PRODUCTION_BROKER_ENABLED": "false",
        "OPENCLAW_CAPITAL_BINDING_ENABLED": "false",
        "OPENCLAW_LIFECYCLE_STORE_PATH": str(tmp / "default" / "sessions.json"),
        "OPENCLAW_BRIDGE_AUDIT_PATH": str(tmp / "default" / "bridge_audit.jsonl"),
        "OPENCLAW_PAPER_BROKER_AUDIT_PATH": str(tmp / "default" / "paper_audit.jsonl"),
        "OPENCLAW_LIVE_GATE_AUDIT_PATH": str(tmp / "default" / "live_audit.jsonl"),
    }
    module = _load_adapter_module(env, "_openclaw_adapter_default_smoke")
    client = TestClient(module.app)
    rows: list[SmokeRow] = []

    status, livez = _client_json(client, "GET", "/livez")
    rows.append(_check("default:livez", status == 200 and livez.get("status") == "ok", {"status": status, "body": livez}, "livez must stay ok without upstream"))

    status, readyz = _client_json(client, "GET", "/readyz")
    rows.append(_check("default:readyz-degraded", status == 503 and readyz.get("status") == "degraded", {"status": status, "body": readyz}, "readyz must fail closed when upstream is absent"))

    status, caps = _client_json(client, "GET", "/api/openclaw-adapter/capabilities")
    rows.append(
        _check(
            "default:capabilities-fail-closed",
            status == 200
            and caps.get("broker_execution") == "deferred"
            and caps.get("paper_adapter") == "deferred"
            and caps.get("live_adapter") == "deferred"
            and (caps.get("upstream") or {}).get("status") == "degraded",
            {"status": status, "body": caps},
            "capability facade must remain deferred/degraded by default",
        )
    )

    status, paper = _client_json(
        client,
        "POST",
        "/api/openclaw-adapter/broker/paper/orders",
        headers={"X-Operator-Id": "op-smoke"},
        json={"capital_pool_id": "pool-paper", "strategy_id": "strategy-alpha", "symbol": "AAPL", "qty": 1, "side": "buy"},
    )
    rows.append(
        _check(
            "default:paper-denied",
            status == 503 and paper.get("error_code") == "PAPER_ADAPTER_DISABLED",
            {"status": status, "body": paper},
            "paper order path must be denied when paper gate is closed",
        )
    )

    status, live = _client_json(client, "POST", "/api/openclaw-adapter/broker/live/orders", headers={"X-Operator-Id": "op-smoke"})
    rows.append(
        _check(
            "default:live-denied",
            status == 403 and live.get("error_code") == "LIVE_EXECUTION_DISABLED",
            {"status": status, "body": live},
            "live order path must be explicitly denied",
        )
    )
    return rows


def _activation_ready_rows(tmp: Path) -> list[SmokeRow]:
    rows: list[SmokeRow] = []
    with (
        _JsonFixtureServer(FakeOpenClawHandler) as upstream,
        _JsonFixtureServer(FakeRuntimeManagerHandler) as runtime,
        _JsonFixtureServer(FakeBrokerHandler) as broker,
    ):
        env = {
            "OPENCLAW_GATEWAY_URL": upstream.url,
            "OPENCLAW_UPSTREAM_TIMEOUT": "2",
            "OPENCLAW_UPSTREAM_RETRIES": "0",
            "OPENCLAW_ALLOWED_TOOLS": "research.search",
            "OPENCLAW_ALLOWED_WORKFLOWS": "research.daily_scan",
            "OPENCLAW_PAPER_ADAPTER_ENABLED": "true",
            "OPENCLAW_LIVE_ADAPTER_ENABLED": "false",
            "OPENCLAW_PRODUCTION_BROKER_ENABLED": "false",
            "OPENCLAW_CAPITAL_BINDING_ENABLED": "false",
            "OPENCLAW_BROKER_SIDECAR_URL": broker.url,
            "OPENCLAW_RUNTIME_MANAGER_URL": runtime.url,
            "PANTHEON_RUNTIME_MANAGER_TOKEN": "runtime-control-internal",
            "OPENCLAW_LIFECYCLE_STORE_PATH": str(tmp / "enabled" / "sessions.json"),
            "OPENCLAW_BRIDGE_AUDIT_PATH": str(tmp / "enabled" / "bridge_audit.jsonl"),
            "OPENCLAW_PAPER_BROKER_AUDIT_PATH": str(tmp / "enabled" / "paper_audit.jsonl"),
            "OPENCLAW_LIVE_GATE_AUDIT_PATH": str(tmp / "enabled" / "live_audit.jsonl"),
        }
        module = _load_adapter_module(env, "_openclaw_adapter_activation_ready_smoke")
        client = TestClient(module.app)

        status, caps = _client_json(client, "GET", "/api/openclaw-adapter/capabilities")
        rows.append(
            _check(
                "activation:capabilities-upstream",
                status == 200
                and (caps.get("upstream") or {}).get("status") == "ok"
                and caps.get("paper_adapter") == "enabled"
                and caps.get("live_adapter") == "deferred",
                {"status": status, "body": caps},
                "activation profile must see fake upstream while live remains deferred",
            )
        )

        status, created = _client_json(
            client,
            "POST",
            "/api/openclaw-adapter/lifecycle/sessions",
            headers={"X-Operator-Id": "op-smoke", "X-Idempotency-Key": "openclaw-activation-ready-session"},
            json={"agent_id": "agent-alpha", "session_type": "interactive", "context_bundle": {"ticket_id": TASK_ID}},
        )
        session = created.get("session") or {}
        session_id = session.get("session_id", "")
        rows.append(
            _check(
                "activation:session-lifecycle",
                status == 201 and session.get("state") == "active" and bool(session.get("upstream_session_id")),
                {"status": status, "session": session},
                "lifecycle create must persist active session backed by fake upstream",
            )
        )

        status, tools = _client_json(
            client,
            "GET",
            f"/api/openclaw-adapter/tools?agent_id=agent-alpha&session_id={urllib.parse.quote(session_id)}",
            headers={"X-Operator-Id": "op-smoke"},
        )
        rows.append(
            _check(
                "activation:tool-resolution",
                status == 200 and tools.get("effective_tools") == ["research.search"],
                {"status": status, "body": tools},
                "effective tools must be policy allowlist intersected with upstream tools",
            )
        )

        status, tool_result = _client_json(
            client,
            "POST",
            "/api/openclaw-adapter/tools/invoke",
            headers={"X-Operator-Id": "op-smoke", "X-Trace-Id": "trace-tool-ok"},
            json={"session_id": session_id, "tool_name": "research.search", "args": {"query": "AAPL"}},
        )
        rows.append(
            _check(
                "activation:tool-invoke",
                status == 200 and tool_result.get("status") == "ok",
                {"status": status, "body": tool_result},
                "allowed tool must invoke through fake upstream",
            )
        )

        status, denied_tool = _client_json(
            client,
            "POST",
            "/api/openclaw-adapter/tools/invoke",
            headers={"X-Operator-Id": "op-smoke", "X-Trace-Id": "trace-tool-denied"},
            json={"session_id": session_id, "tool_name": "paper.execute", "args": {"symbol": "AAPL"}},
        )
        rows.append(
            _check(
                "activation:broker-tool-denied",
                status == 403 and denied_tool.get("error_code") == "BRIDGE_TOOL_DENIED",
                {"status": status, "body": denied_tool},
                "paper/broker tool prefixes must stay fail-closed",
            )
        )

        status, paper = _client_json(
            client,
            "POST",
            "/api/openclaw-adapter/broker/paper/orders",
            headers={"X-Operator-Id": "op-smoke", "X-Trace-Id": "trace-paper-ok"},
            json={"capital_pool_id": "pool-paper", "strategy_id": "strategy-alpha", "symbol": "AAPL", "qty": 3, "side": "buy"},
        )
        order = paper.get("order") or {}
        rows.append(
            _check(
                "activation:paper-order",
                status == 201 and paper.get("status") == "ok" and order.get("sim_fill_flag") is True,
                {"status": status, "body": paper},
                "paper order must pass only with explicit paper gate and active paper binding",
            )
        )

        status, live = _client_json(client, "POST", "/api/openclaw-adapter/broker/live/orders", headers={"X-Operator-Id": "op-smoke"})
        rows.append(
            _check(
                "activation:live-order-denied",
                status == 403 and live.get("error_code") == "LIVE_EXECUTION_DISABLED",
                {"status": status, "body": live},
                "live broker order must remain explicitly denied in activation-ready E2E",
            )
        )

        status, audit = _client_json(client, "GET", "/api/openclaw-adapter/broker/audit?operator_id=op-smoke")
        rows.append(
            _check(
                "activation:paper-audit",
                status == 200 and any(item.get("outcome") == "ok" for item in audit.get("entries", [])),
                {"status": status, "body": audit},
                "paper adapter audit must record successful simulated handoff",
            )
        )
    return rows


def run_smoke(output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = _default_degraded_rows(output_dir) + _activation_ready_rows(output_dir)
    passed = sum(1 for row in rows if row.status == "passed")
    return {
        "task_id": TASK_ID,
        "summary": {
            "passed": passed,
            "rows": len(rows),
            "smoke_passed": passed == len(rows),
            "production_broker_enabled": False,
            "live_execution_enabled": False,
        },
        "rows": [asdict(row) for row in rows],
    }


def _print_summary(report: dict[str, Any]) -> None:
    summary = report["summary"]
    print(f"OpenClaw activation-ready E2E: {summary['passed']}/{summary['rows']} passed")
    for row in report["rows"]:
        marker = "ok " if row["status"] == "passed" else "err"
        print(f"{marker} {row['row']}")
        if row["error"]:
            print(f"    {row['error']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run OpenClaw activation-ready E2E smoke.")
    parser.add_argument("--output-dir", default="", help="Directory for local smoke state and artifacts.")
    parser.add_argument("--json-out", default="", help="Optional path for JSON report.")
    args = parser.parse_args(argv)

    with tempfile.TemporaryDirectory(prefix="openclaw-activation-ready-") as tmp:
        output_dir = Path(args.output_dir) if args.output_dir else Path(tmp)
        report = run_smoke(output_dir)
        if args.json_out:
            out = Path(args.json_out)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        _print_summary(report)
        return 0 if report["summary"]["smoke_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
