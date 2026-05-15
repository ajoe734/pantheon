#!/usr/bin/env python3
"""Run the BFF-down operator fallback smoke drill.

The drill intentionally avoids the BFF. It starts the deployable
runtime-manager Flask app in-process, exposes it on a local WSGI port, and
executes the fallback surfaces operators use when the frontend is unavailable:

* S-IAPI: direct protected internal API pause
* S-CLI: pantheon-admin runtime pause and kill-switch liquidate through the internal API
* S-EMRG: direct runtime-manager kill-switch replace fast path
"""
from __future__ import annotations

import argparse
import contextlib
import importlib
import io
import json
import os
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from wsgiref.simple_server import WSGIRequestHandler, make_server


ROOT = Path(__file__).resolve().parents[1]
SERVICE_DIR = ROOT / "services" / "runtime-manager"
EXEC_RUNTIME_DIR = ROOT / "services" / "execution" / "runtime-manager"

for path in (ROOT, SERVICE_DIR, EXEC_RUNTIME_DIR):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

from tools.pantheon_admin import cli as pantheon_admin_cli  # noqa: E402


TASK_ID = "SVC-BLUEPRINT-OPERATOR-FALLBACK-DRILLS"
AUTH_TOKEN = "operator-oncall:operator,admin,risk_owner"
MFA_TOKEN = "123456"


class SmokeError(RuntimeError):
    """Raised when a drill step fails acceptance checks."""


class QuietRequestHandler(WSGIRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        return


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def dump_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def valid_deploy_request(**overrides: Any) -> dict[str, Any]:
    request: dict[str, Any] = {
        "plan_id": "plan-operator-fallback",
        "plan_status": "approved",
        "target_stage": "paper",
        "artifact_id": "artifact-operator-fallback",
        "artifact_version": "1.0.0",
        "capital_pool_id": "pool-operator-fallback",
        "persona_capital_binding_id": "pcb-operator-fallback",
        "persona_capital_binding_status": "active",
        "allowed_deployment_scope": "live",
        "loader_checks_passed": True,
        "runtime_id": "rt-operator-fallback",
    }
    request.update(overrides)
    return request


@dataclass
class CliResult:
    exit_code: int
    stdout: str
    stderr: str
    payload: dict[str, Any]


class RuntimeManagerServer:
    def __init__(self, app: Any) -> None:
        self._server = make_server("127.0.0.1", 0, app, handler_class=QuietRequestHandler)
        self.base_url = f"http://127.0.0.1:{self._server.server_port}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self) -> "RuntimeManagerServer":
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        self._server.shutdown()
        self._thread.join(timeout=5)
        self._server.server_close()


class EnvSnapshot:
    KEYS = (
        "PANTHEON_EXEC_RUNTIME_MANAGER_DIR",
        "PANTHEON_RUNTIME_BINDING_STORE_PATH",
        "PANTHEON_SINGLE_RUNTIME_ENFORCED",
        "PANTHEON_COMMAND_STATE_FILE",
        "PANTHEON_RUNTIME_MFA_REQUIRED",
    )

    def __init__(self) -> None:
        self._saved = {key: os.environ.get(key) for key in self.KEYS}

    def restore(self) -> None:
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def load_runtime_manager_module(store_path: Path, command_state_path: Path) -> Any:
    os.environ["PANTHEON_EXEC_RUNTIME_MANAGER_DIR"] = str(EXEC_RUNTIME_DIR)
    os.environ["PANTHEON_RUNTIME_BINDING_STORE_PATH"] = str(store_path)
    os.environ["PANTHEON_SINGLE_RUNTIME_ENFORCED"] = "true"
    os.environ["PANTHEON_COMMAND_STATE_FILE"] = str(command_state_path)
    os.environ["PANTHEON_RUNTIME_MFA_REQUIRED"] = "false"
    sys.modules.pop("main", None)
    module = importlib.import_module("main")
    module._svc = None
    return module


def request_json(
    base_url: str,
    method: str,
    path: str,
    *,
    body: dict[str, Any] | None = None,
    expected_status: int | tuple[int, ...] = 200,
) -> dict[str, Any]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {AUTH_TOKEN}",
        "X-MFA-Token": MFA_TOKEN,
    }
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(f"{base_url}{path}", data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            raw = response.read().decode("utf-8")
            payload = json.loads(raw) if raw else {}
            status = response.status
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        payload = json.loads(raw) if raw else {}
        status = exc.code

    expected = (expected_status,) if isinstance(expected_status, int) else expected_status
    if status not in expected:
        raise SmokeError(f"{method} {path} returned {status}, expected {expected}: {payload}")
    return payload


def run_cli(argv: list[str]) -> CliResult:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        exit_code = pantheon_admin_cli.main(argv)
    out = stdout.getvalue()
    payload: dict[str, Any] = {}
    if out.strip():
        payload = json.loads(out)
    return CliResult(exit_code=exit_code, stdout=out, stderr=stderr.getvalue(), payload=payload)


def assert_equal(actual: Any, expected: Any, message: str) -> None:
    if actual != expected:
        raise SmokeError(f"{message}: expected {expected!r}, got {actual!r}")


def run_smoke(output_dir: Path | None = None) -> dict[str, Any]:
    env = EnvSnapshot()
    try:
        with tempfile.TemporaryDirectory(prefix="pantheon-operator-fallback-") as tmpdir:
            tmp = Path(tmpdir)
            main = load_runtime_manager_module(
                tmp / "bindings.json",
                tmp / "commands.json",
            )

            with RuntimeManagerServer(main.app) as server:
                base_url = server.base_url
                artifacts: dict[str, Any] = {}

                pause_binding = request_json(
                    base_url,
                    "POST",
                    "/api/runtimes/deploy",
                    body=valid_deploy_request(
                        plan_id="plan-iapi-pause",
                        capital_pool_id="pool-iapi-pause",
                        persona_capital_binding_id="pcb-iapi-pause",
                        runtime_id="rt-iapi-pause",
                    ),
                    expected_status=201,
                )
                direct_pause = request_json(
                    base_url,
                    "POST",
                    f"/api/internal/v1/runtimes/{pause_binding['binding_id']}/pause",
                    body={
                        "pause_action": "pause",
                        "duration_seconds": 900,
                        "reason": "bff-down direct internal api drill",
                    },
                    expected_status=202,
                )
                assert_equal(direct_pause["status_after"], "paused", "S-IAPI pause status")
                pause_readback = request_json(
                    base_url,
                    "GET",
                    f"/api/runtime-bindings/{pause_binding['binding_id']}",
                )
                assert_equal(pause_readback["status"], "paused", "S-IAPI canonical readback")
                command_record = request_json(
                    base_url,
                    "GET",
                    f"/api/internal/v1/commands/{direct_pause['command_id']}",
                )
                assert_equal(command_record["status"], "executed", "S-IAPI command record")

                cli_pause_binding = request_json(
                    base_url,
                    "POST",
                    "/api/runtimes/deploy",
                    body=valid_deploy_request(
                        plan_id="plan-cli-pause",
                        capital_pool_id="pool-cli-pause",
                        persona_capital_binding_id="pcb-cli-pause",
                        runtime_id="rt-cli-pause",
                    ),
                    expected_status=201,
                )
                cli_pause = run_cli(
                    [
                        "runtime",
                        "pause",
                        cli_pause_binding["binding_id"],
                        "--base-url",
                        base_url,
                        "--auth-token",
                        AUTH_TOKEN,
                        "--mfa-token",
                        MFA_TOKEN,
                        "--duration",
                        "900",
                        "--reason",
                        "bff-down cli pause drill",
                        "--output",
                        "json",
                    ]
                )
                assert_equal(cli_pause.exit_code, 0, f"S-CLI pause stderr={cli_pause.stderr}")
                assert_equal(cli_pause.payload["status_after"], "paused", "S-CLI pause status")
                cli_pause_readback = request_json(
                    base_url,
                    "GET",
                    f"/api/runtime-bindings/{cli_pause_binding['binding_id']}",
                )
                assert_equal(cli_pause_readback["status"], "paused", "S-CLI pause readback")

                liquidate_binding = request_json(
                    base_url,
                    "POST",
                    "/api/runtimes/deploy",
                    body=valid_deploy_request(
                        plan_id="plan-cli-liquidate",
                        capital_pool_id="pool-cli-liquidate",
                        persona_capital_binding_id="pcb-cli-liquidate",
                        runtime_id="rt-cli-liquidate",
                    ),
                    expected_status=201,
                )
                cli_liquidate = run_cli(
                    [
                        "kill-switch",
                        "activate",
                        "--base-url",
                        base_url,
                        "--auth-token",
                        AUTH_TOKEN,
                        "--output",
                        "json",
                        "--scope",
                        "pool",
                        "--scope-id",
                        "pool-cli-liquidate",
                        "--rationale",
                        "operator_emergency_stop",
                        "--action-override",
                        "liquidate",
                        "--force",
                        "--mfa-token",
                        MFA_TOKEN,
                    ]
                )
                assert_equal(cli_liquidate.exit_code, 0, f"S-CLI liquidate stderr={cli_liquidate.stderr}")
                assert_equal(cli_liquidate.payload["action"], "liquidate", "S-CLI liquidate action")
                liquidate_readback = request_json(
                    base_url,
                    "GET",
                    f"/api/runtime-bindings/{liquidate_binding['binding_id']}",
                )
                assert_equal(liquidate_readback["status"], "retired", "S-CLI liquidate readback")

                replace_binding = request_json(
                    base_url,
                    "POST",
                    "/api/runtimes/deploy",
                    body=valid_deploy_request(
                        plan_id="plan-emrg-replace",
                        capital_pool_id="pool-emrg-replace",
                        persona_capital_binding_id="pcb-emrg-replace",
                        runtime_id="rt-emrg-replace",
                    ),
                    expected_status=201,
                )
                emrg_replace = request_json(
                    base_url,
                    "POST",
                    "/api/kill-switch/dispatch",
                    body={
                        "reason": "operator_emergency_stop",
                        "capital_pool_id": "pool-emrg-replace",
                        "actor_id": "operator-oncall",
                        "action_override": "replace",
                        "fallback_artifact_id": "artifact-operator-fallback-safe",
                        "fallback_artifact_version": "1.1.0",
                        "idempotency_key": "operator-fallback-emrg-replace",
                        "context": {"drill": TASK_ID, "bff_surface_used": False},
                    },
                )
                assert_equal(emrg_replace["command"]["action_type"], "replace", "S-EMRG replace action")
                assert_equal(
                    emrg_replace["binding_action"]["binding"]["status"],
                    "retired",
                    "S-EMRG old binding retired",
                )
                assert_equal(
                    emrg_replace["binding_action"]["replacement_binding"]["status"],
                    "active",
                    "S-EMRG replacement active",
                )
                assert_equal(
                    emrg_replace["telemetry_ack"]["ack_status"],
                    "acknowledged",
                    "S-EMRG telemetry ack",
                )
                replace_old_readback = request_json(
                    base_url,
                    "GET",
                    f"/api/runtime-bindings/{replace_binding['binding_id']}",
                )
                assert_equal(replace_old_readback["status"], "retired", "S-EMRG old readback")

                audit_log = request_json(base_url, "GET", "/api/kill-switch/audit-log")
                if audit_log["count"] < 2:
                    raise SmokeError(f"Expected at least 2 kill-switch audit entries: {audit_log}")
                action_types = {entry["action_type"] for entry in audit_log["entries"]}
                if not {"liquidate", "replace"}.issubset(action_types):
                    raise SmokeError(f"Audit log missing liquidate/replace entries: {audit_log}")

                bff_modules_loaded = sorted(
                    name for name in sys.modules if name.startswith("services.control_plane.bff")
                )
                if bff_modules_loaded:
                    raise SmokeError(f"BFF modules were imported during fallback drill: {bff_modules_loaded}")

                artifacts.update(
                    {
                        "runtime_deploy_iapi_pause_response": pause_binding,
                        "s_iapi_pause_response": direct_pause,
                        "s_iapi_command_record_response": command_record,
                        "s_cli_pause_stdout": cli_pause.payload,
                        "s_cli_pause_binding_readback": cli_pause_readback,
                        "s_cli_liquidate_stdout": cli_liquidate.payload,
                        "s_cli_liquidate_binding_readback": liquidate_readback,
                        "s_emrg_replace_response": emrg_replace,
                        "s_emrg_replace_old_readback": replace_old_readback,
                        "kill_switch_audit_log_response": audit_log,
                    }
                )

                summary = {
                    "task_id": TASK_ID,
                    "status": "passed",
                    "generated_at": iso_now(),
                    "bff_surface_used": False,
                    "bff_modules_loaded": bff_modules_loaded,
                    "runtime_manager_surface": base_url,
                    "surfaces": {
                        "S-IAPI": {
                            "action": "pause",
                            "command_id": direct_pause["command_id"],
                            "status_after": direct_pause["status_after"],
                            "audit_id": command_record["audit"]["audit_id"],
                        },
                        "S-CLI": {
                            "actions": ["pause", "liquidate"],
                            "pause_command_id": cli_pause.payload["command_id"],
                            "pause_status_after": cli_pause.payload["status_after"],
                            "pause_binding_status_after": cli_pause_readback["status"],
                            "liquidate_command_id": cli_liquidate.payload["command_id"],
                            "liquidate_audit_id": cli_liquidate.payload["audit_id"],
                            "liquidate_binding_status_after": liquidate_readback["status"],
                        },
                        "S-EMRG": {
                            "action": "replace",
                            "command_id": emrg_replace["command"]["command_id"],
                            "audit_id": emrg_replace["audit_entry"]["audit_id"],
                            "old_binding_status_after": replace_old_readback["status"],
                            "replacement_binding_status": emrg_replace["binding_action"]["replacement_binding"]["status"],
                            "telemetry_ack_status": emrg_replace["telemetry_ack"]["ack_status"],
                        },
                    },
                    "audit_evidence": {
                        "kill_switch_audit_count": audit_log["count"],
                        "kill_switch_actions": sorted(action_types),
                        "internal_api_command_record_status": command_record["status"],
                    },
                    "production_bff_ha_changed": False,
                }

                if output_dir is not None:
                    output_dir.mkdir(parents=True, exist_ok=True)
                    for name, payload in artifacts.items():
                        dump_json(output_dir / f"{name}.json", payload)
                    dump_json(output_dir / "summary.json", summary)
                return summary
    finally:
        env.restore()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run BFF-down operator fallback smoke drill")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Optional directory for JSON evidence artifacts",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        summary = run_smoke(output_dir=args.output_dir)
    except SmokeError as exc:
        print(json.dumps({"task_id": TASK_ID, "status": "failed", "error": str(exc)}, indent=2), file=sys.stderr)
        return 1
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
