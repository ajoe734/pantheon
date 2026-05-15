#!/usr/bin/env python3
"""MGMT-SAFE-003 OpenClaw broker tool denial smoke.

The smoke loads the real openclaw-gateway-adapter FastAPI app with a fake
upstream OpenClaw gateway that advertises broker/live/paper/capital tools.
Even when those tools are present in OPENCLAW_ALLOWED_TOOLS and upstream
metadata, the Pantheon adapter must keep them out of effective_tools and deny
every invocation before dispatching to upstream.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import tempfile
import threading
import urllib.parse
from dataclasses import asdict, dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from unittest import mock

from fastapi.testclient import TestClient


REPO_ROOT = Path(__file__).resolve().parent.parent
ADAPTER_DIR = REPO_ROOT / "services" / "openclaw-gateway-adapter"
TASK_ID = "MGMT-SAFE-003"
SAFE_TOOL = "research.search"
SAFE_WORKFLOW = "research.daily_scan"
BLOCKED_TOOLS = [
    "broker.submit",
    "broker_order",
    "submit_order",
    "paper.execute",
    "live.order",
    "canary.order",
    "capital.bind",
    "lean.deploy",
]
BLOCKED_WORKFLOWS = [
    "broker.submit",
    "paper.execute",
    "live.order",
    "canary.deploy",
    "capital.bind",
    "lean.deploy",
]


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
    tool_invocations: list[dict[str, Any]] = []
    workflow_invocations: list[dict[str, Any]] = []

    def do_GET(self) -> None:  # noqa: N802 - stdlib callback
        path = urllib.parse.urlparse(self.path).path
        if path in {"/healthz", "/readyz"}:
            _json_response(self, 200, {"status": "ok", "service": "fake-openclaw-safe-003"})
            return
        if path == "/api/capabilities":
            _json_response(
                self,
                200,
                {
                    "status": "ok",
                    "tools": [SAFE_TOOL, *BLOCKED_TOOLS],
                    "workflows": [SAFE_WORKFLOW, *BLOCKED_WORKFLOWS],
                    "broker_execution": True,
                },
            )
            return
        if path in {"/api/tools", "/api/tools/resolve"}:
            _json_response(self, 200, {"tools": [{"name": name} for name in [SAFE_TOOL, *BLOCKED_TOOLS]]})
            return
        if path.startswith("/api/jobs/"):
            job_id = path.rsplit("/", 1)[-1]
            _json_response(self, 200, {"job_id": job_id, "status": "queued"})
            return
        _json_response(self, 404, {"error_code": "NOT_FOUND", "path": path})

    def do_POST(self) -> None:  # noqa: N802 - stdlib callback
        path = urllib.parse.urlparse(self.path).path
        body = _read_body(self)
        if path == "/api/tools/invoke":
            self.tool_invocations.append(body)
            _json_response(
                self,
                200,
                {"status": "ok", "tool_name": body.get("tool_name"), "result": {"fixture": True}},
            )
            return
        if path == "/api/workflows/trigger":
            self.workflow_invocations.append(body)
            _json_response(
                self,
                202,
                {"job_id": "job-safe-003", "status": "queued", "workflow_ref": body.get("workflow_ref")},
            )
            return
        _json_response(self, 404, {"error_code": "NOT_FOUND", "path": path})

    def log_message(self, _format: str, *_args: Any) -> None:
        return


def _load_adapter_module(env: dict[str, str], module_name: str):
    for name in (
        module_name,
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
    for path in (str(ADAPTER_DIR), str(REPO_ROOT)):
        try:
            sys.path.remove(path)
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


def _run_rows(output_dir: Path) -> list[SmokeRow]:
    FakeOpenClawHandler.tool_invocations = []
    FakeOpenClawHandler.workflow_invocations = []
    rows: list[SmokeRow] = []

    with _JsonFixtureServer(FakeOpenClawHandler) as upstream:
        env = {
            "OPENCLAW_GATEWAY_URL": upstream.url,
            "OPENCLAW_UPSTREAM_TIMEOUT": "2",
            "OPENCLAW_UPSTREAM_RETRIES": "0",
            "OPENCLAW_ALLOWED_TOOLS": ",".join([SAFE_TOOL, *BLOCKED_TOOLS]),
            "OPENCLAW_ALLOWED_WORKFLOWS": ",".join([SAFE_WORKFLOW, *BLOCKED_WORKFLOWS]),
            "OPENCLAW_PAPER_ADAPTER_ENABLED": "false",
            "OPENCLAW_LIVE_ADAPTER_ENABLED": "false",
            "OPENCLAW_CANARY_ADAPTER_ENABLED": "false",
            "OPENCLAW_PRODUCTION_BROKER_ENABLED": "false",
            "OPENCLAW_CAPITAL_BINDING_ENABLED": "false",
            "OPENCLAW_LIFECYCLE_STORE_PATH": str(output_dir / "sessions.json"),
            "OPENCLAW_BRIDGE_AUDIT_PATH": str(output_dir / "bridge_audit.jsonl"),
            "OPENCLAW_PAPER_BROKER_AUDIT_PATH": str(output_dir / "paper_audit.jsonl"),
            "OPENCLAW_LIVE_GATE_AUDIT_PATH": str(output_dir / "live_audit.jsonl"),
        }
        module = _load_adapter_module(env, "_openclaw_adapter_mgmt_safe_003_smoke")
        client = TestClient(module.app)
        headers = {"X-Operator-Id": "op-safe-003"}
        session_id = "session-safe-003"

        status, caps = _client_json(client, "GET", "/api/openclaw-adapter/capabilities")
        rows.append(
            _check(
                "capabilities-remain-fail-closed",
                status == 200
                and caps.get("activation_state") == "upstream_client_ready"
                and caps.get("broker_execution") == "deferred"
                and caps.get("paper_adapter") == "deferred"
                and caps.get("live_adapter") == "deferred"
                and caps.get("canary_adapter") == "deferred"
                and caps.get("capital_binding") == "deferred",
                {"status": status, "broker_execution": caps.get("broker_execution"), "upstream_status": (caps.get("upstream") or {}).get("status")},
                "capability facade must stay fail-closed even when fake upstream advertises broker tools",
            )
        )

        status, tools = _client_json(
            client,
            "GET",
            f"/api/openclaw-adapter/tools?agent_id=agent-safe-003&session_id={session_id}",
            headers=headers,
        )
        rows.append(
            _check(
                "effective-tools-exclude-broker-tools",
                status == 200
                and tools.get("effective_tools") == [SAFE_TOOL]
                and sorted(tools.get("policy_blocked_tools") or []) == sorted(BLOCKED_TOOLS),
                {
                    "status": status,
                    "effective_tools": tools.get("effective_tools"),
                    "policy_blocked_tools": tools.get("policy_blocked_tools"),
                },
                "effective_tools must exclude broker/live/paper/capital tools even if allowlisted and upstream-reported",
            )
        )

        status, safe_tool = _client_json(
            client,
            "POST",
            "/api/openclaw-adapter/tools/invoke",
            headers={**headers, "X-Trace-Id": "trace-safe-tool"},
            json={"session_id": session_id, "tool_name": SAFE_TOOL, "args": {"query": "TWSE"}},
        )
        rows.append(
            _check(
                "safe-control-tool-dispatches",
                status == 200 and safe_tool.get("status") == "ok",
                {"status": status, "tool_name": safe_tool.get("tool_name")},
                "safe control tool should dispatch so the fake upstream path is proven live",
            )
        )

        for tool_name in BLOCKED_TOOLS:
            status, body = _client_json(
                client,
                "POST",
                "/api/openclaw-adapter/tools/invoke",
                headers={**headers, "X-Trace-Id": f"trace-denied-tool-{tool_name}"},
                json={"session_id": session_id, "tool_name": tool_name, "args": {"symbol": "2330.TW", "qty": 1}},
            )
            rows.append(
                _check(
                    f"tool-denied:{tool_name}",
                    status == 403
                    and body.get("error_code") == "BRIDGE_TOOL_DENIED"
                    and (body.get("details") or {}).get("policy_class") == "always_blocked",
                    {
                        "status": status,
                        "error_code": body.get("error_code"),
                        "policy_class": (body.get("details") or {}).get("policy_class"),
                    },
                    f"{tool_name} must be denied by adapter policy before upstream dispatch",
                )
            )

        status, safe_workflow = _client_json(
            client,
            "POST",
            "/api/openclaw-adapter/workflows/trigger",
            headers={**headers, "X-Trace-Id": "trace-safe-workflow"},
            json={"workflow_ref": SAFE_WORKFLOW, "context": {"ticket_id": TASK_ID}},
        )
        rows.append(
            _check(
                "safe-control-workflow-dispatches",
                status == 200 and safe_workflow.get("status") == "ok",
                {"status": status, "workflow_ref": safe_workflow.get("workflow_ref")},
                "safe control workflow should dispatch so the fake upstream path is proven live",
            )
        )

        for workflow_ref in BLOCKED_WORKFLOWS:
            status, body = _client_json(
                client,
                "POST",
                "/api/openclaw-adapter/workflows/trigger",
                headers={**headers, "X-Trace-Id": f"trace-denied-workflow-{workflow_ref}"},
                json={"workflow_ref": workflow_ref, "context": {"symbol": "2330.TW", "qty": 1}},
            )
            rows.append(
                _check(
                    f"workflow-denied:{workflow_ref}",
                    status == 403
                    and body.get("error_code") == "BRIDGE_WORKFLOW_DENIED"
                    and (body.get("details") or {}).get("policy_class") == "always_blocked",
                    {
                        "status": status,
                        "error_code": body.get("error_code"),
                        "policy_class": (body.get("details") or {}).get("policy_class"),
                    },
                    f"{workflow_ref} must be denied by adapter policy before upstream dispatch",
                )
            )

        status, audit = _client_json(
            client,
            "GET",
            "/api/openclaw-adapter/audit/invocations?operator_id=op-safe-003&limit=200",
        )
        entries = audit.get("entries") if isinstance(audit.get("entries"), list) else []
        denied_tools = sorted(
            entry.get("tool_name")
            for entry in entries
            if entry.get("request_type") == "tool_invoke" and entry.get("outcome") == "denied"
        )
        denied_workflows = sorted(
            entry.get("workflow_ref")
            for entry in entries
            if entry.get("request_type") == "workflow_trigger" and entry.get("outcome") == "denied"
        )
        upstream_tool_names = [item.get("tool_name") for item in FakeOpenClawHandler.tool_invocations]
        upstream_workflow_refs = [item.get("workflow_ref") for item in FakeOpenClawHandler.workflow_invocations]
        rows.append(
            _check(
                "audit-denials-without-upstream-dispatch",
                status == 200
                and denied_tools == sorted(BLOCKED_TOOLS)
                and denied_workflows == sorted(BLOCKED_WORKFLOWS)
                and upstream_tool_names == [SAFE_TOOL]
                and upstream_workflow_refs == [SAFE_WORKFLOW],
                {
                    "status": status,
                    "denied_tools": denied_tools,
                    "denied_workflows": denied_workflows,
                    "upstream_tool_invocations": upstream_tool_names,
                    "upstream_workflow_invocations": upstream_workflow_refs,
                },
                "denied broker tool/workflow attempts must be audited and never dispatched upstream",
            )
        )

    return rows


def run_smoke(output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = _run_rows(output_dir)
    passed = sum(1 for row in rows if row.status == "passed")
    return {
        "task_id": TASK_ID,
        "summary": {
            "passed": passed,
            "rows": len(rows),
            "smoke_passed": passed == len(rows),
            "blocked_tool_count": len(BLOCKED_TOOLS),
            "blocked_workflow_count": len(BLOCKED_WORKFLOWS),
            "production_broker_enabled": False,
            "live_execution_enabled": False,
            "canary_execution_enabled": False,
            "capital_binding_enabled": False,
        },
        "assertions": {
            "broker_tools_denied_by_adapter_policy": passed == len(rows),
            "denied_tools_not_dispatched_upstream": FakeOpenClawHandler.tool_invocations == [
                {
                    "session_id": "session-safe-003",
                    "tool_name": SAFE_TOOL,
                    "args": {"query": "TWSE"},
                    "operator_context": {
                        "pantheon_operator_id": "op-safe-003",
                        "pantheon_trace_id": "trace-safe-tool",
                        "pantheon_source": "openclaw-gateway-adapter",
                    },
                }
            ],
            "denied_workflows_not_dispatched_upstream": [
                item.get("workflow_ref") for item in FakeOpenClawHandler.workflow_invocations
            ] == [SAFE_WORKFLOW],
        },
        "rows": [asdict(row) for row in rows],
    }


def _print_summary(report: dict[str, Any]) -> None:
    summary = report["summary"]
    print(f"OpenClaw broker tool denial smoke: {summary['passed']}/{summary['rows']} passed")
    for row in report["rows"]:
        marker = "ok " if row["status"] == "passed" else "err"
        print(f"{marker} {row['row']}")
        if row["error"]:
            print(f"    {row['error']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run MGMT-SAFE-003 OpenClaw broker tool denial smoke.")
    parser.add_argument("--output-dir", default="", help="Directory for local smoke state and temporary audit logs.")
    parser.add_argument("--json-out", default="", help="Optional path for the JSON evidence report.")
    args = parser.parse_args(argv)

    with tempfile.TemporaryDirectory(prefix="openclaw-broker-tool-denial-") as tmp:
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
