from __future__ import annotations

import json
import io
import os
import re
import shutil
import subprocess
import sys
import threading
import tempfile
import time as time_mod
import urllib.error
import urllib.parse
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from unittest.mock import patch, MagicMock

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "dev-tw-market-refresh.yml"
DEPLOY_SCRIPT = ROOT / "scripts" / "deploy_nonprod_vm.sh"


def test_workflow_file_exists_and_parses():
    assert WORKFLOW_PATH.exists(), f"Workflow file missing: {WORKFLOW_PATH}"
    data = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    assert data.get("name") == "Dev Taiwan Market Daily Refresh"


def test_workflow_triggers():
    data = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    on = data.get("on") or data.get(True)  # YAML true can map to True
    assert on is not None, "Missing 'on' trigger specification in workflow"

    # Schedule trigger: 07:00 UTC (15:00 Asia/Taipei) on trading days (Monday to Friday)
    schedule = on.get("schedule")
    assert schedule is not None, "Missing schedule trigger"
    cron_exprs = [item.get("cron") for item in schedule if isinstance(item, dict)]
    assert "0 7 * * 1-5" in cron_exprs, f"Expected cron '0 7 * * 1-5', got: {cron_exprs}"

    # workflow_dispatch trigger with force boolean input
    wf_dispatch = on.get("workflow_dispatch")
    assert wf_dispatch is not None, "Missing workflow_dispatch trigger"
    inputs = wf_dispatch.get("inputs") or {}
    assert "force" in inputs, "workflow_dispatch must declare 'force' input"
    force_input = inputs["force"]
    assert force_input.get("type") == "boolean", f"force input must be boolean, got {force_input}"
    assert force_input.get("default") is False, f"force default must be false, got {force_input}"


def test_finmind_refresh_skipped_before_broker_publication_crons():
    data = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    steps = data["jobs"]["refresh"]["steps"]
    fm = next(s for s in steps if s.get("id") == "finmind_refresh")
    cond = str(fm.get("if") or "")

    def runs(schedule):
        # Evaluate the simple `!=` / `&&` condition for a given schedule value.
        expr = cond.replace("${{", "").replace("}}", "").strip()
        parts = [p.strip() for p in expr.split("&&")]
        for part in parts:
            m = re.fullmatch(r"github\.event\.schedule != '([^']+)'", part)
            assert m, f"unexpected condition term: {part}"
            if schedule == m.group(1):
                return False
        return True

    assert not runs("0 7 * * 1-5")
    assert not runs("0 8 * * 1-5")
    assert runs("30 10 * * 1-5")
    assert runs("30 12 * * 1-5")
    assert runs(None)  # workflow_dispatch


def test_workflow_strict_absence_of_hardcoded_ips_and_fallbacks():
    content = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "34.81.52.222" not in content, "Workflow must not contain hardcoded IP 34.81.52.222"
    assert "35.201.239.38" not in content, "Workflow must not contain legacy dev IP"
    assert "35.201.204.12" not in content, "Workflow must not contain legacy dev IP"
    assert "34.81.75.241" not in content, "Workflow must not contain legacy dev IP"
    assert not re.search(r"vars\.DEV_DEPLOY_SSH_HOST\s*\|\|", content), "Workflow must fail closed without IP fallback"
    assert not re.search(r"vars\.DEV_REMOTE_DIR\s*\|\|", content), "Workflow must fail closed without directory fallback"
    assert not re.search(r"/home/lupin", content), "Workflow must not reference legacy /home/lupin"


def test_workflow_variables_and_remote_execution():
    content = WORKFLOW_PATH.read_text(encoding="utf-8")
    required_vars = [
        "DEV_DEPLOY_SSH_HOST",
        "NONPROD_REMOTE_USER",
        "DEV_DEPLOY_SSH_KNOWN_HOSTS",
        "DEV_DEPLOY_SSH_PRIVATE_KEY",
    ]
    for var in required_vars:
        assert var in content, f"Workflow must reference § 3.1 variable/secret: {var}"

    assert "scripts/dev_vm_ssh.sh prepare" in content, "Workflow must prepare SSH credentials via dev_vm_ssh.sh"
    assert "./scripts/deploy_nonprod_vm.sh --refresh-only" in content, (
        "Workflow must invoke deploy_nonprod_vm.sh with --refresh-only"
    )
    assert "--force" in content, "Workflow must propagate force argument to deploy_nonprod_vm.sh"


def test_no_second_script_created():
    second_script = ROOT / "scripts" / "run_dev_bounded_source_refresh.sh"
    assert not second_script.exists(), (
        f"Design requires reusing deploy_nonprod_vm.sh --refresh-only; {second_script} must not exist"
    )


def test_deploy_script_help_includes_refresh_only_options():
    content = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    assert "--refresh-only" in content, "deploy_nonprod_vm.sh must expose --refresh-only"
    assert "--force" in content, "deploy_nonprod_vm.sh must expose --force"
    assert "--output" in content, "deploy_nonprod_vm.sh must expose --output"


def test_deploy_script_refresh_only_argument_parsing():
    content = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    assert "--refresh-only) REFRESH_ONLY=true" in content or "--refresh-only)" in content
    assert 'execute_bounded_source_refresh_entrypoint "${FORCE_REFRESH:-false}"' in content


class _MockSourceIngestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        state_file = getattr(self.server, "state_file", None)
        state = json.loads(state_file.read_text(encoding="utf-8")) if (state_file and state_file.exists()) else {}
        if self.path == "/readyz":
            failures = state.get("readiness_failures_remaining", 0)
            status = 503 if failures or state.get("readiness_always_fails") else 200
            state["readiness_failures_remaining"] = max(0, failures - 1)
            state_file.write_text(json.dumps(state), encoding="utf-8")
            with self.server.events_file.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"event": "readyz", "status": status}) + "\n")
            self.send_response(status)
            self.end_headers()
            self.wfile.write(b'{"ready": true}' if status == 200 else b'{"ready": false}')
        elif self.path.startswith("/api/source-ingest/snapshots/latest"):
            import urllib.parse
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            sym = (qs.get("symbol") or [""])[0]
            snapshots = state.get("snapshots") or {}
            snap = snapshots.get(sym)
            if snap is not None:
                body = json.dumps(snap).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b'{"error": "not found"}')
        elif self.path.startswith("/api/source-ingest/receipts"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            receipts = state.get("receipts")
            if receipts is None:
                receipts = [{
                    "connector_id": "tw-twse-tpex-official-market",
                    "status": "completed",
                    "typed_failure": None,
                    "source_timestamp": "2026-10-06T12:00:00Z",
                    "source_timestamp_status": "valid",
                    "created_at": "2030-01-01T00:00:00Z",
                    "finished_at": "2030-01-01T00:00:00Z",
                    "ingest_run_id": "run-1"
                }]
            self.wfile.write(json.dumps({"receipts": receipts}).encode("utf-8"))
        elif self.path.startswith("/api/source-ingest/controller/readback"):
            with self.server.events_file.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"event": "connector_readback"}) + "\n")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            dlq = state.get("dlq_entries") or []
            total_dlq = len(dlq)
            unresolved = sum(1 for e in dlq if e.get("status") in ("pending", "replay_failed", "schema_rejected"))
            connectors = state.get("readback_connectors")
            if connectors is None:
                connectors = [{
                    "connector_id": "tw-twse-tpex-official-market",
                    "freshness": {
                        "latest_receipt": {"ingest_run_id": "run-1"},
                        "source_timestamp_status": "valid"
                    },
                    "latest_source_record": {
                        "provenance": {"source_ingest_run_id": "run-1"},
                        "source_id": "src-1"
                    }
                }]
            self.wfile.write(json.dumps({
                "unresolved_dlq_count": unresolved,
                "dlq_count": total_dlq,
                "connectors": connectors,
            }).encode("utf-8"))
        elif self.path.startswith("/api/source-ingest/dlq"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            state_file = getattr(self.server, "state_file", None)
            entries = []
            if state_file and state_file.exists():
                state = json.loads(state_file.read_text(encoding="utf-8"))
                entries = state.get("dlq_entries") or []
            status_counts = {"pending": 0, "replayed": 0, "duplicate_skipped": 0, "replay_failed": 0, "schema_rejected": 0}
            for e in entries:
                st = e.get("status", "pending")
                status_counts[st] = status_counts.get(st, 0) + 1
            unresolved = status_counts["pending"] + status_counts["replay_failed"] + status_counts["schema_rejected"]
            self.wfile.write(json.dumps({
                "entries": entries,
                "entry_count": len(entries),
                "pending_count": status_counts["pending"],
                "unresolved_count": unresolved,
                "status_counts": status_counts,
            }).encode("utf-8"))
        elif self.path.startswith("/api/source-ingest/frontier"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            state_file = getattr(self.server, "state_file", None)
            frontiers = []
            if state_file and state_file.exists():
                state = json.loads(state_file.read_text(encoding="utf-8"))
                frontiers = state.get("frontier") or []
            self.wfile.write(json.dumps({"frontier": frontiers}).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path.startswith("/api/source-ingest/dlq/replay"):
            content_len = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(content_len).decode("utf-8")) if content_len > 0 else {}
            state_file = getattr(self.server, "state_file", None)
            replayed_entries = []
            correlated_resolutions = []
            target_status = "replayed"
            if state_file and state_file.exists():
                state = json.loads(state_file.read_text(encoding="utf-8"))
                delay = state.get("mock_replay_delay_seconds")
                if delay:
                    time_mod.sleep(float(delay))
                target_status = state.get("mock_replay_target_status", "replayed")
                drop_entries = state.get("mock_replay_drop_entries", False)
                req_ids = set(body.get("entry_ids") or [])
                entries = state.get("dlq_entries") or []
                if drop_entries:
                    state["dlq_entries"] = [e for e in entries if e.get("entry_id") not in req_ids]
                else:
                    for e in entries:
                        if e.get("entry_id") in req_ids or (not req_ids and e.get("status") == "pending"):
                            prev_st = e.get("status", "pending")
                            e["status"] = target_status
                            e["replay_attempts"] = e.get("replay_attempts", 0) + 1
                            replayed_entries.append(e.get("entry_id"))
                            correlated_resolutions.append({
                                "entry_id": e.get("entry_id"),
                                "previous_status": prev_st,
                                "status": target_status,
                                "replay_attempts": e["replay_attempts"],
                            })
                for f in state.get("frontier") or []:
                    if f.get("connector_id") == "tw-twse-tpex-official-market" and f.get("status") == "failed":
                        if target_status in ("replayed", "duplicate_skipped"):
                            f["status"] = "done"
                state.setdefault("replay_calls", []).append(body)
                state.setdefault("audit_records", []).append({
                    "action_type": "source_ingestion.scheduled_run.recovered",
                    "entry_ids": replayed_entries,
                    "reason": body.get("reason"),
                    "actor_id": body.get("actor_id"),
                    "tag": body.get("tag"),
                })
                state_file.write_text(json.dumps(state), encoding="utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            applied_count = len(replayed_entries) if target_status in ("replayed", "duplicate_skipped") else 0
            failed_count = len(replayed_entries) if target_status not in ("replayed", "duplicate_skipped") else 0
            self.wfile.write(json.dumps({
                "summary": {
                    "total_replayed": len(replayed_entries),
                    "applied": applied_count,
                    "failed": failed_count,
                    "correlated_resolution_count": len(correlated_resolutions) if target_status in ("replayed", "duplicate_skipped") else 0,
                },
                "selected_entry_ids": replayed_entries[:1] if replayed_entries else [],
                "correlated_resolutions": correlated_resolutions,
            }).encode("utf-8"))
        elif self.path == "/api/source-ingest/connectors":
            content_len = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(content_len).decode("utf-8")) if content_len > 0 else {}
            state_file = getattr(self.server, "state_file", None)
            if state_file and state_file.exists():
                state = json.loads(state_file.read_text(encoding="utf-8"))
                state.setdefault("configured_connectors", []).append(body)
                state_file.write_text(json.dumps(state), encoding="utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"connector": body.get("connector") or {}}).encode("utf-8"))
        elif self.path == "/api/source-ingest/active-universe/schedule":
            content_len = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(content_len).decode("utf-8")) if content_len > 0 else {}
            state_file = getattr(self.server, "state_file", None)
            enqueued_items = []
            for rule in body.get("rules") or []:
                if not isinstance(rule, dict):
                    continue
                contracts = [
                    str(m.get("symbol", "")).strip()
                    for m in (body.get("members") or [])
                    if isinstance(m, dict) and str(m.get("symbol", "")).strip()
                ]
                enqueued_items.append({
                    "connector_id": rule.get("connector_id"),
                    "job_parameters": {
                        "connector_id": rule.get("connector_id"),
                        "dataset": rule.get("dataset"),
                        "contracts": contracts,
                        "symbols": contracts,
                    },
                })
            if state_file and state_file.exists():
                state = json.loads(state_file.read_text(encoding="utf-8"))
                state.setdefault("active_universe_schedules", []).append(body)
                state_file.write_text(json.dumps(state), encoding="utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "enqueued_count": len(enqueued_items),
                "enqueued_items": enqueued_items,
            }).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def do_PUT(self):
        if self.path.startswith("/api/source-ingest/connectors/") and self.path.endswith("/schedule"):
            content_len = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(content_len).decode("utf-8")) if content_len > 0 else {}
            state_file = getattr(self.server, "state_file", None)
            if state_file and state_file.exists():
                state = json.loads(state_file.read_text(encoding="utf-8"))
                state.setdefault("configured_schedules", []).append({"path": self.path, **body})
                state_file.write_text(json.dumps(state), encoding="utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"schedule": body}).encode("utf-8"))
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass


def _setup_refresh_stub_docker(tmp_path: Path, initial_state: dict[str, Any]) -> tuple[Path, Path, Path, Path, int]:
    server = HTTPServer(("127.0.0.1", 0), _MockSourceIngestHandler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    state_file = tmp_path / "docker_state.json"
    server.state_file = state_file
    events_file = tmp_path / "docker_events.jsonl"
    server.events_file = events_file
    output_file = tmp_path / "refresh_output.json"

    state_file.write_text(json.dumps(initial_state))
    # Optional real Compose config interpolation/merge, without a Docker daemon.
    # This catches a new default that a no-op mock of compose would silently miss.
    compose_fixture = tmp_path / "compose.json"
    compose_fixture.write_text(json.dumps({"services": {"source-ingest": {
        "image": "pantheon-source-ingest",
        "environment": initial_state.get("compose_environment_defaults", {}),
        "volumes": ["source-ingest-data:/data/source-ingest"],
    }}, "volumes": {"source-ingest-data": {}}}))
    real_docker = shutil.which("docker")

    docker_script = f"""#!/usr/bin/env python3
import json, os, sys, subprocess, time
from pathlib import Path

state_file = Path({repr(str(state_file))})
events_file = Path({repr(str(events_file))})

def log_event(name, **kwargs):
    with events_file.open("a", encoding="utf-8") as f:
        f.write(json.dumps({{"event": name, "t": time.time(), **kwargs}}) + "\\n")

args = sys.argv[1:]
if not args:
    sys.exit(0)

state = json.loads(state_file.read_text(encoding="utf-8"))

if args[0] == "compose":
    sub = args[5:] if len(args) > 5 else []
    overrides = []
    # The deploy script may add further compose files (-f <override>), e.g. the
    # source-ingest image pin override; skip them to reach the compose subcommand.
    while len(sub) > 1 and sub[0] == "-f":
        overrides.extend(sub[:2])
        sub = sub[2:]
    if not sub:
        sys.exit(0)
    if sub[0] == "run":
        if "runtime-manager" in sub:
            if state.get("runtime_manager_output") is not None:
                print(state["runtime_manager_output"])
            else:
                code = sys.stdin.read()
                proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=os.environ)
                sys.stdout.write(proc.stdout)
                sys.stderr.write(proc.stderr)
                sys.exit(proc.returncode)
        else:
            if "source-ingest-scheduler" in sub:
                changed = False
                if "post_run_receipts" in state:
                    state["receipts"] = state["post_run_receipts"]
                    changed = True
                if "post_run_readback_connectors" in state:
                    state["readback_connectors"] = state["post_run_readback_connectors"]
                    changed = True
                if changed:
                    state_file.write_text(json.dumps(state), encoding="utf-8")
            log_event(
                "compose_run",
                args=sub,
                active_symbols=os.environ.get("SOURCE_INGEST_ACTIVE_PAPER_SYMBOLS"),
                interval=os.environ.get("SOURCE_INGEST_CONTROLLER_INTERVAL_SECONDS"),
            )
        sys.exit(0)
    elif sub[0] == "ps":
        target = sub[-1]
        custom_containers = state.get("compose_service_containers", {{}}).get(target)
        if custom_containers is not None:
            status_filter = None
            if "--status" in sub:
                status_filter = sub[sub.index("--status") + 1]
            is_all = "-a" in sub or "--all" in sub
            matching = []
            for c in custom_containers:
                c_status = c.get("status", "running")
                if status_filter:
                    if c_status == status_filter:
                        matching.append(c["id"])
                elif is_all:
                    matching.append(c["id"])
                elif c_status == "running":
                    matching.append(c["id"])
            for mid in matching:
                print(mid)
            sys.exit(0)
        if target == "source-ingest":
            print(state.get("source_ingest_cid", "cid-source-ingest"))
            sys.exit(0)
        print(f"cid-{{target}}")
        sys.exit(0)
    elif sub[0] == "images":
        target_service = sub[-1] if len(sub) > 1 and not sub[-1].startswith("-") else ""
        if target_service == "source-ingest-agora-projector":
            projector_compose_id = state.get("projector_container_compose_image_id", "")
            if projector_compose_id:
                print(projector_compose_id)
            sys.exit(0)
        print(state["compose_image_id"])
        sys.exit(0)
    elif sub[0] == "config":
        if "compose_environment_defaults" in state:
            model = subprocess.check_output([{real_docker!r}, "compose", "-f", {str(compose_fixture)!r}, *overrides, "config", "--format", "json"], env=os.environ, text=True)
            print(model)
            sys.exit(0)
        print(json.dumps({{"services": {{"source-ingest": {{"image": "pantheon-source-ingest"}}}}}}))
        sys.exit(0)
    elif sub[0] == "up":
        services = sub[4:]
        log_event("compose_up", services=services, env={{k: os.environ[k] for k in os.environ if k.startswith("PANTHEON_") or k.startswith("SOURCE_INGEST_")}})
        if "source-ingest" in services:
            if state.get("mutate_image_on_up"):
                state["image_id"] = state["mutate_image_on_up"]
            # Model Compose interpolation: the recreated container carries the
            # egress/controller-mode keys the deploy script exported for this up.
            controlled_keys = ("PANTHEON_EXTERNAL_EGRESS", "PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS", "SOURCE_INGEST_CONTROLLER_MODE")
            # A restore is egress deny, or the steady collector-host allowlist with a steady
            # (non-bounded) controller mode; the bounded window is allowlist + reconcile_and_pull.
            is_restore = os.environ.get("PANTHEON_EXTERNAL_EGRESS") == "deny" or (
                os.environ.get("PANTHEON_EXTERNAL_EGRESS") == "allowlist"
                and os.environ.get("SOURCE_INGEST_CONTROLLER_MODE") in ("reconcile_only", "collector_steady_pull"))
            if is_restore:
                kept = [e for e in state["container_env"] if e.split("=", 1)[0] not in controlled_keys]
                # Real compose passes SOURCE_INGEST_CONTROLLER_MODE only to the scheduler,
                # so the source-ingest container carries it only if it did before.
                had_mode = any(e.startswith("SOURCE_INGEST_CONTROLLER_MODE=") for e in state["container_env"])
                state["container_env"] = kept + [f"{{k}}={{os.environ[k]}}" for k in controlled_keys if k in os.environ and (k != "SOURCE_INGEST_CONTROLLER_MODE" or had_mode)]
            if os.environ.get("PANTHEON_EXTERNAL_EGRESS") == "allowlist" and not is_restore:
                state["source_ingest_cid"] = state.get("recreated_source_ingest_cid", "cid-source-ingest-recreated")
            elif is_restore:
                state["source_ingest_cid"] = state.get("restored_source_ingest_cid", "cid-source-ingest-restored")
                if "compose_environment_defaults" in state:
                    model = json.loads(subprocess.check_output([{real_docker!r}, "compose", "-f", {str(compose_fixture)!r}, *overrides, "config", "--format", "json"], env=os.environ, text=True))
                    service = model["services"]["source-ingest"]
                    # `config` serializes dollar signs escaped for reloading;
                    # container creation consumes the resolved literal values.
                    state["container_env"] = [f"{{k}}={{v.replace('$$', '$')}}" for k, v in service["environment"].items()]
                    state["restored_image_reference"] = service["image"]
                    state["restored_volumes"] = service["volumes"]
                if "mutate_env_on_restore" in state:
                    state["container_env"] = state["mutate_env_on_restore"]
            state_file.write_text(json.dumps(state))
        sys.exit(0)
    elif sub[0] == "rm":
        services = sub[4:]
        log_event("compose_rm", services=services)
        sys.exit(0)

elif args[0] == "ps":
    filter_arg = next((arg.split("name=^")[-1].rstrip("$") for arg in args if "name=^" in arg), "")
    if filter_arg:
        print(f"cid-{{filter_arg}}")
    else:
        print("bounded-container-id")
    sys.exit(0)

elif args[0] == "rm":
    log_event("docker_rm", names=args[2:])
    sys.exit(0)

elif args[0] == "stop":
    log_event("docker_stop", names=args[1:])
    sys.exit(0)

elif args[0] == "start":
    log_event("docker_start", names=args[1:])
    sys.exit(0)

elif args[0] == "image" and len(args) > 1 and args[1] == "inspect":
    print(state.get("resolved_compose_image_id", state.get("image_id", "sha256:" + "a" * 64)))
    sys.exit(0)

elif args[0] == "inspect":
    fmt = args[2] if len(args) > 2 and args[1] == "--format" else ""
    target = args[-1]
    if len(target.splitlines()) > 1:
        print("Error: No such object: " + str(target), file=sys.stderr)
        sys.exit(1)
    container_map = {{}}
    for svc_list in state.get("compose_service_containers", {{}}).values():
        for c in svc_list:
            container_map[c["id"]] = c
            if "name" in c:
                container_map[c["name"].lstrip("/")] = c
    if target in container_map:
        c = container_map[target]
        if "{{.Image}}" in fmt:
            print(c.get("image", state.get("image_id", "sha256:" + "a" * 64)))
            sys.exit(0)
        elif "Config.Env" in fmt:
            if "env" in c:
                print(json.dumps(c["env"]))
            elif c.get("status") == "running":
                print(json.dumps(["PANTHEON_TENANT_ID=tenant-dev", "PANTHEON_ENV=dev", "GIT_SHA=abc123", *state.get("steady_lease_env", ["SOURCE_INGEST_CONTROLLER_INTERVAL_SECONDS=1"])]))
            else:
                print(json.dumps([]))
            sys.exit(0)
        elif "State.Status" in fmt:
            log_event("docker_inspect_status", target=target)
            print(c.get("status", "running"))
            sys.exit(0)
    if target == "pantheon-source-ingest-agora-projector:latest":
        if not state.get("built_projector_image_present", True):
            sys.stderr.write("Error: No such image: " + target + "\\n")
            sys.exit(1)
        if "{{.Id}}" in fmt:
            print(state.get("built_projector_image_id", "sha256:" + "b" * 64))
            sys.exit(0)
    if not target:
        sys.stderr.write("Error: target required\\n")
        sys.exit(1)
    if "{{.Image}}" in fmt:
        print(state["image_id"])
        sys.exit(0)
    elif "org.opencontainers.image.revision" in fmt:
        revisions = state.get("image_revisions", {{}})
        if target in revisions:
            print(revisions[target])
            sys.exit(0)
        print(state.get("projector_image_sha", "abc123"))
        sys.exit(0)
    elif "Config.Env" in fmt and target == "cid-source-ingest-scheduler":
        print(json.dumps(["PANTHEON_TENANT_ID=tenant-dev", "PANTHEON_ENV=dev", "GIT_SHA=abc123", *state.get("steady_lease_env", ["SOURCE_INGEST_CONTROLLER_INTERVAL_SECONDS=1"])]))
        sys.exit(0)
    elif "Config.Env" in fmt:
        print(json.dumps(state["container_env"]))
        sys.exit(0)
    elif "State.Status" in fmt:
        log_event("docker_inspect_status", target=target)
        print("exited")
        sys.exit(0)
    elif "State.ExitCode" in fmt:
        state = json.loads(state_file.read_text(encoding="utf-8"))
        dlq = state.get("dlq_entries") or []
        unresolved = sum(1 for e in dlq if e.get("status") in ("pending", "replay_failed", "schema_rejected"))
        if ("source-ingest-scheduler" in target or target == "bounded-container-id") and unresolved > 0:
            print("1")
        else:
            print("0")
        sys.exit(0)

elif args[0] == "cp":
    dest = Path(args[2])
    projection_data = state.get("agora_projection", {{
        "row1": {{
            "connectorId": "tw-twse-tpex-official-market",
            "ingestRunId": "run-1",
            "sourceId": "src-1",
            "asOf": "2026-10-06T12:00:00Z",
            "freshness": {{
                "sourceTimestamp": "2026-10-06T12:00:00Z",
                "sourceTimeStatus": "valid",
                "status": "fresh",
                "stale": False
            }}
        }}
    }})
    dest.write_text(json.dumps(projection_data))
    sys.exit(0)

elif args[0] == "exec":
    target_cid = args[2] if len(args) > 2 and args[1] == "-i" else args[1]
    cmd_args = args[3:] if len(args) > 2 and args[1] == "-i" else args[2:]
    cmd_str = " ".join(cmd_args)
    log_event("docker_exec", cid=target_cid, cmd=cmd_args)
    live_source_cid = state.get("source_ingest_cid", "cid-source-ingest")
    if target_cid != live_source_cid:
        sys.stderr.write(f"Error response from daemon: No such container: {{target_cid}}\\n")
        sys.exit(1)
    if "controller_token" in cmd_str:
        if state.get("fail_token_exec"):
            sys.stderr.write("failed reading controller_token\\n")
            sys.exit(1)
        if state.get("empty_token_exec"):
            sys.stdout.write("")
            sys.exit(0)
        print(state.get("controller_token", "stub-controller-token-12345"))
        sys.exit(0)
    if "desired_state.json" in cmd_str:
        if state.get("fail_desired_state_exec"):
            sys.stderr.write("failed writing desired_state.json\\n")
            sys.exit(1)
        payload_raw = sys.stdin.read()
        if not payload_raw.strip():
            sys.exit(1)
        state["written_desired_state"] = json.loads(payload_raw)
        state["written_desired_state_cid"] = target_cid
        state_file.write_text(json.dumps(state))
        sys.exit(0)
    sys.exit(0)

elif args[0] == "logs":
    if state.get("container_logs"):
        sys.stdout.write(str(state["container_logs"]))
    sys.exit(0)

sys.exit(0)
"""
    (bin_dir / "docker").write_text(docker_script)
    (bin_dir / "docker").chmod(0o755)

    return bin_dir, state_file, events_file, output_file, port


def _deploy_script_without_readback(tmp_path: Path) -> Path:
    """Copy of the deploy script with the HTTP/Agora readback stubbed out; these tests cover restore, not readback."""
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for entry in DEPLOY_SCRIPT.parent.iterdir():
        if entry != DEPLOY_SCRIPT:
            (scripts / entry.name).symlink_to(entry)
    content = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    needle = '  verify_bounded_source_refresh_readback "${refresh_started_at}" || refresh_rc=$?\n'
    assert needle in content
    content = content.replace(needle, "")
    preflight_needle = '  preflight_output="$(check_taiwan_refresh_preflight "${force}")"'
    assert preflight_needle in content
    content = content.replace(preflight_needle, '  preflight_output=\'{"status": "proceed"}\'')
    patched = scripts / DEPLOY_SCRIPT.name
    patched.write_text(content, encoding="utf-8")
    return patched


def _deploy_script_with_readback(tmp_path: Path) -> Path:
    """Copy of the deploy script with real readback preserved and only preflight bypassed."""
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for entry in DEPLOY_SCRIPT.parent.iterdir():
        if entry != DEPLOY_SCRIPT:
            (scripts / entry.name).symlink_to(entry)
    content = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    preflight_needle = '  preflight_output="$(check_taiwan_refresh_preflight "${force}")"'
    assert preflight_needle in content
    content = content.replace(preflight_needle, '  preflight_output=\'{"status": "proceed"}\'')
    patched = scripts / DEPLOY_SCRIPT.name
    patched.write_text(content, encoding="utf-8")
    return patched


def test_refresh_entrypoint_restores_egress_deny_and_preserves_env(tmp_path: Path):
    initial_env = [
        "PANTHEON_EXTERNAL_EGRESS=deny",
        "PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS=",
        "PORT=8097",
        "DATABASE_URL=postgresql://pantheon_app:pantheon_app@postgres:5432/pantheon",
        "CUSTOM_SECRET=foo$bar",
    ]
    initial_state = {
        "image_id": "sha256:" + "a" * 64,
        "compose_image_id": "a" * 64,  # bare 64-hex to verify Compose images -q normalization
        "container_env": initial_env,
    }
    bin_dir, state_file, events_file, output_file, port = _setup_refresh_stub_docker(tmp_path, initial_state)

    test_env = dict(os.environ)
    test_env["PATH"] = f"{bin_dir}:{os.environ['PATH']}"
    test_env["SOURCE_INGEST_API_URL"] = f"http://127.0.0.1:{port}"
    test_env["SOURCE_INGEST_BOUNDED_RUN_TIMEOUT_SECONDS"] = "30"
    test_env["PANTHEON_REMOTE_DIR"] = str(ROOT)

    proc = subprocess.run(
        ["bash", str(_deploy_script_without_readback(tmp_path)), "--refresh-only", "--force", "--output", str(output_file)],
        env=test_env,
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert proc.returncode == 0, f"Refresh failed: {proc.stderr}\n{proc.stdout}"
    assert output_file.exists()
    out_data = json.loads(output_file.read_text(encoding="utf-8"))
    assert out_data.get("status") == "completed"

    events = [json.loads(line) for line in events_file.read_text(encoding="utf-8").splitlines() if line]
    bounded_up = next(
        e for e in events
        if e.get("event") == "compose_up"
        and "source-ingest" in e.get("services", [])
        and e["env"].get("PANTHEON_EXTERNAL_EGRESS") == "allowlist"
    )
    assert bounded_up["env"]["PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS"] == "openapi.twse.com.tw,www.tpex.org.tw"

    restore_up = next(
        e for e in events
        if e.get("event") == "compose_up"
        and "source-ingest" in e.get("services", [])
        and e["env"].get("PANTHEON_EXTERNAL_EGRESS") == "deny"
    )
    assert restore_up["env"]["PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS"] == ""
    assert "SOURCE_INGEST_CONTROLLER_TIMEOUT_SECONDS" not in restore_up["env"]
    assert "SOURCE_INGEST_CONTROLLER_FORCE_CONNECTOR_IDS" not in restore_up["env"]
    assert "SOURCE_INGEST_CONTROLLER_EXCLUSIVE_CONNECTOR_IDS" not in restore_up["env"]

    steady = {"source-ingest-scheduler", "source-ingest-agora-projector"}
    assert not [e for e in events if e.get("event") in ("compose_rm", "compose_up") and steady & set(e.get("services", []))]
    assert len([e for e in events if e.get("event") == "compose_run"]) == 2
    assert any(
        n.startswith("pantheon-bounded-refresh-") for e in events if e.get("event") == "docker_rm" for n in e["names"]
    )


def test_refresh_entrypoint_image_id_guard_pre_recreate_mismatch(tmp_path: Path):
    initial_state = {
        "image_id": "sha256:" + "1" * 64,
        "compose_image_id": "sha256:" + "2" * 64,
        "container_env": ["PANTHEON_EXTERNAL_EGRESS=deny"],
    }
    bin_dir, state_file, events_file, output_file, port = _setup_refresh_stub_docker(tmp_path, initial_state)

    test_env = dict(os.environ)
    test_env["PATH"] = f"{bin_dir}:{os.environ['PATH']}"
    test_env["SOURCE_INGEST_API_URL"] = f"http://127.0.0.1:{port}"
    test_env["SOURCE_INGEST_BOUNDED_RUN_TIMEOUT_SECONDS"] = "30"
    test_env["PANTHEON_REMOTE_DIR"] = str(ROOT)

    proc = subprocess.run(
        ["bash", str(DEPLOY_SCRIPT), "--refresh-only", "--force", "--output", str(output_file)],
        env=test_env,
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert proc.returncode != 0
    assert "running image ID" in proc.stderr
    assert "!= compose image ID" in proc.stderr
    assert not events_file.exists() or not any(
        e.get("event") == "compose_up"
        for e in [json.loads(line) for line in events_file.read_text().splitlines() if line]
    )


def test_refresh_entrypoint_image_id_guard_post_recreate_mismatch(tmp_path: Path):
    initial_state = {
        "image_id": "sha256:" + "1" * 64,
        "compose_image_id": "sha256:" + "1" * 64,
        "container_env": ["PANTHEON_EXTERNAL_EGRESS=deny"],
        "mutate_image_on_up": "sha256:" + "3" * 64,
    }
    bin_dir, state_file, events_file, output_file, port = _setup_refresh_stub_docker(tmp_path, initial_state)

    test_env = dict(os.environ)
    test_env["PATH"] = f"{bin_dir}:{os.environ['PATH']}"
    test_env["SOURCE_INGEST_API_URL"] = f"http://127.0.0.1:{port}"
    test_env["SOURCE_INGEST_BOUNDED_RUN_TIMEOUT_SECONDS"] = "30"
    test_env["PANTHEON_REMOTE_DIR"] = str(ROOT)

    proc = subprocess.run(
        ["bash", str(DEPLOY_SCRIPT), "--refresh-only", "--force", "--output", str(output_file)],
        env=test_env,
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert proc.returncode != 0
    assert "source-ingest container image ID" in proc.stderr
    assert "!= expected" in proc.stderr


def test_refresh_entrypoint_env_equality_fails_closed_on_mismatch(tmp_path: Path):
    initial_state = {
        "image_id": "sha256:" + "1" * 64,
        "compose_image_id": "sha256:" + "1" * 64,
        "container_env": ["PANTHEON_EXTERNAL_EGRESS=deny", "PORT=8097"],
        "mutate_env_on_restore": ["PANTHEON_EXTERNAL_EGRESS=deny", "PORT=8097", "LEAKED_VAR=leaked"],
    }
    bin_dir, state_file, events_file, output_file, port = _setup_refresh_stub_docker(tmp_path, initial_state)

    test_env = dict(os.environ)
    test_env["PATH"] = f"{bin_dir}:{os.environ['PATH']}"
    test_env["SOURCE_INGEST_API_URL"] = f"http://127.0.0.1:{port}"
    test_env["SOURCE_INGEST_BOUNDED_RUN_TIMEOUT_SECONDS"] = "30"
    test_env["PANTHEON_REMOTE_DIR"] = str(ROOT)

    proc = subprocess.run(
        ["bash", str(_deploy_script_without_readback(tmp_path)), "--refresh-only", "--force", "--output", str(output_file)],
        env=test_env,
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert proc.returncode != 0
    assert "restored container env mismatch" in proc.stderr


def _run_exact_restore_helper(tmp_path: Path, *, root_value=None, compose_defaults_match_capture=False, **state_changes):
    """Execute maintained shell + Python + retry helper against real Compose config.

    Only Docker runtime operations and sleep are stubbed. The next readback uses
    the same HTTP fixture as connector preflight, after a successful restore.
    """
    if not shutil.which("docker"):
        pytest.skip("Docker Compose config is required for environment merge regression")
    captured = [
        "PANTHEON_EXTERNAL_EGRESS=deny", "PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS=",
        "PORT=8097", "CUSTOM_SECRET=old$token${UNSET_SECRET}\nquoted=\"value\"",
        "FINMIND_API_TOKEN=prior$secret", "SOURCE_INGEST_DATA_DIR=/data/source-ingest",
        "SOURCE_INGEST_CONTROLLER_TOKEN_FILE=/data/source-ingest/controller_token",
    ]
    if root_value is not None:
        captured.append(f"INTRADAY_STORE_ROOT={root_value}")
    defaults = dict(item.split("=", 1) for item in captured) if compose_defaults_match_capture else {
        "INTRADAY_STORE_ROOT": "/data/source-ingest", "NEW_DEFAULT": "must-not-appear",
        "FINMIND_API_TOKEN": "wrong-default", "PORT": "8097",
    }
    initial = {
        "image_id": "sha256:" + "a" * 64, "container_env": captured,
        "compose_environment_defaults": {k: v.replace("$", "$$") for k, v in defaults.items()},
        **state_changes,
    }
    bin_dir, state_file, events_file, _, port = _setup_refresh_stub_docker(tmp_path, initial)
    # Keep the production retry count but shorten sleep for a bounded local test.
    (bin_dir / "sleep").write_text("#!/bin/sh\n/bin/sleep 0.01\n")
    (bin_dir / "sleep").chmod(0o755)
    captured_file = tmp_path / "captured.json"
    captured_file.write_text(json.dumps(captured))
    text = DEPLOY_SCRIPT.read_text()
    def function(name):
        return text.split(name + "() {", 1)[1].split("\n}\n", 1)[0]
    script = tmp_path / "restore.sh"
    script.write_text(
        "set -euo pipefail\n"
        + "curl_with_retry() {" + function("curl_with_retry") + "\n}\n"
        + "manage_source_ingest_refresh_runtime() {" + function("manage_source_ingest_refresh_runtime") + "\n}\n"
        + 'manage_source_ingest_refresh_runtime "$1" restore "$2" || exit $?\n'
        + 'curl -fsS "$SOURCE_INGEST_API_URL/api/source-ingest/controller/readback" >/dev/null\n'
    )
    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}",
               SOURCE_INGEST_API_URL=f"http://127.0.0.1:{port}",
               FINMIND_API_TOKEN="bounded-secret-must-not-survive", PANTHEON_COLLECTOR_HOST="",
               SOURCE_INGEST_RESTORE_CONTROLLER_MODE="reconcile_only")
    proc = subprocess.run(["bash", str(script), str(captured_file), initial["image_id"]],
                          env=env, cwd=ROOT, capture_output=True, text=True, timeout=15)
    events = [json.loads(line) for line in events_file.read_text().splitlines()] if events_file.exists() else []
    return proc, json.loads(state_file.read_text()), events, captured


@pytest.mark.parametrize("root_value", [None, "", "/captured/custom-root"])
def test_exact_restore_replaces_new_compose_defaults_and_preserves_secrets(tmp_path, root_value):
    proc, state, events, captured = _run_exact_restore_helper(tmp_path, root_value=root_value)
    assert proc.returncode == 0, proc.stderr
    assert set(state["container_env"]) == set(captured)
    assert state["restored_image_reference"] == state["image_id"]
    assert state["restored_volumes"][0]["source"] == "source-ingest-data"
    assert state["restored_volumes"][0]["target"] == "/data/source-ingest"
    assert [e["status"] for e in events if e["event"] == "readyz"] == [200]


def test_exact_restore_waits_for_api_before_next_connector_readback(tmp_path):
    proc, _, events, _ = _run_exact_restore_helper(tmp_path, compose_defaults_match_capture=True, readiness_failures_remaining=2)
    assert proc.returncode == 0, proc.stderr
    http = [e for e in events if e["event"] in {"readyz", "connector_readback"}]
    assert http == [{"event": "readyz", "status": 503}, {"event": "readyz", "status": 503},
                    {"event": "readyz", "status": 200}, {"event": "connector_readback"}]


def test_exact_restore_readiness_timeout_prevents_success_and_next_connector(tmp_path):
    proc, _, events, _ = _run_exact_restore_helper(tmp_path, compose_defaults_match_capture=True, readiness_always_fails=True)
    assert proc.returncode != 0
    assert len([e for e in events if e["event"] == "readyz"]) == 13  # unchanged retry budget
    assert not any(e["event"] == "connector_readback" for e in events)


@pytest.mark.parametrize("mutation,expected_keys", [
    (["PORT=wrong-sensitive-value"], ["PORT", "CUSTOM_SECRET", "FINMIND_API_TOKEN"]),
    (["LEAKED_VAR=secret-added-value"], ["LEAKED_VAR", "CUSTOM_SECRET"]),
])
def test_exact_restore_environment_drift_fails_without_disclosing_values(tmp_path, mutation, expected_keys):
    proc, _, events, _ = _run_exact_restore_helper(tmp_path, mutate_env_on_restore=mutation)
    assert proc.returncode != 0
    assert "restored container env mismatch" in proc.stderr
    assert all(key in proc.stderr for key in expected_keys)
    assert not any(secret in proc.stderr + proc.stdout for secret in
                   ["wrong-sensitive-value", "secret-added-value", "old$token", "prior$secret"])
    assert not any(e["event"] in {"readyz", "connector_readback"} for e in events)


@pytest.mark.parametrize("changes", [
    {"resolved_compose_image_id": "sha256:" + "b" * 64},
    {"mutate_image_on_up": "sha256:" + "c" * 64},
])
def test_exact_restore_wrong_image_stays_failed_before_readiness(tmp_path, changes):
    proc, _, events, _ = _run_exact_restore_helper(tmp_path, **changes)
    assert proc.returncode != 0
    assert "image" in proc.stderr
    assert not any(e["event"] in {"readyz", "connector_readback"} for e in events)


def test_exact_restore_readiness_failure_is_not_nonblocking_connector_success(tmp_path):
    initial = {
        "image_id": "sha256:" + "a" * 64, "compose_image_id": "sha256:" + "a" * 64,
        "container_env": ["PANTHEON_EXTERNAL_EGRESS=deny", "PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS="],
        "readiness_always_fails": True,
    }
    bin_dir, _, events_file, output, port = _setup_refresh_stub_docker(tmp_path, initial)
    (bin_dir / "sleep").write_text("#!/bin/sh\n/bin/sleep 0.01\n")
    (bin_dir / "sleep").chmod(0o755)
    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}",
               SOURCE_INGEST_API_URL=f"http://127.0.0.1:{port}",
               SOURCE_INGEST_BOUNDED_RUN_TIMEOUT_SECONDS="30", PANTHEON_REMOTE_DIR=str(ROOT))
    # Default force=false, through the normal caller and its non-blocking cleanup.
    proc = subprocess.run([
        "bash", str(_deploy_script_without_readback(tmp_path)), "--refresh-only", "--non-blocking",
        "--connector-id", "tw-taifex-futures-options-chip", "--allowed-hosts",
        "openapi.taifex.com.tw,www.taifex.com.tw", "--dataset", "taifex_ssf_contracts",
        "--output", str(output),
    ], env=env, cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert proc.returncode != 0
    assert json.loads(output.read_text())["status"] == "failed"
    events = [json.loads(line) for line in events_file.read_text().splitlines()]
    assert len([e for e in events if e["event"] == "readyz"]) == 13
    assert any(e["event"] == "docker_start" for e in events)  # steady scheduler restored


CHIP_PROFILES = [
    ("tw-taifex-futures-options-chip", "taifex_ssf_contracts"),
    ("tw-finmind-broker-daily-report", "tw_broker_top"),
]


def _run_preflight_script(
    force: bool,
    now_dt: datetime,
    snapshot_json: dict[str, Any] | None,
    http_status: int = 200,
    *,
    profile=("tw-twse-tpex-official-market", "tw_price_daily"),
    readback=None,
    readback_http_status=200,
    source_records=None,
    readback_error=None,
    script_text=None,
) -> dict[str, Any]:
    """Execute the maintained shell heredoc; only I/O and the test clock are injected.

    script_text permits a local old/new comparison against a git blob, without
    copying or rewriting the preflight decision logic.
    """
    content = script_text if script_text is not None else DEPLOY_SCRIPT.read_text(encoding="utf-8")
    code = content.split("<<'PREFLIGHT_PY'\n", 1)[1].split("\nPREFLIGHT_PY", 1)[0]
    connector, dataset = profile

    def urlopen(request, *, timeout):
        parsed = urllib.parse.urlparse(request.full_url)
        assert parsed.hostname == "preflight.test", "tests must never reach a service/provider"
        assert timeout == 3  # Keep the production read timeout bounded.
        assert request.get_method() == "GET"
        if parsed.path.endswith("/snapshots/latest"):
            status, payload = (404, {}) if snapshot_json is None and http_status == 200 else (http_status, snapshot_json)
        else:
            assert parsed.path == "/api/source-ingest/controller/readback"
            assert urllib.parse.parse_qs(parsed.query) == {"connector_id": [connector], "dataset": [dataset]}
            if readback_error:
                raise readback_error
            status, payload = readback_http_status, readback or {"connectors": []}
        if status != 200:
            raise urllib.error.HTTPError(request.full_url, status, "test response", {}, None)
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(payload).encode()
        return response

    with tempfile.NamedTemporaryFile(mode="w+", encoding="utf-8") as records_file:
        json.dump({"source_records": source_records or []}, records_file)
        records_file.flush()
        env = {
            "SOURCE_INGEST_API_URL": "http://preflight.test",
            "SOURCE_INGEST_ACTIVE_PAPER_SYMBOLS": "2330.TW,2317.TW",
            "SOURCE_INGEST_BOUNDED_CONNECTOR_ID": connector,
            "SOURCE_INGEST_BOUNDED_DATASET": dataset,
            "PANTHEON_REFRESH_PREFLIGHT_NOW": now_dt.isoformat(),
            "FINMIND_SOURCE_RECORDS_PATH": records_file.name if source_records is not None else "",
        }
        output = io.StringIO()
        with patch.dict(os.environ, env), patch.object(sys, "argv", ["-", str(force)]), \
             patch("urllib.request.urlopen", side_effect=urlopen), redirect_stdout(output), \
             pytest.raises(SystemExit) as stopped:
            exec(compile(code, str(DEPLOY_SCRIPT) + ":PREFLIGHT_PY", "exec"), {"__name__": "__main__"})
        assert stopped.value.code is None
        return json.loads(output.getvalue())


def _preflight_snapshot(session="2026-11-12", *, calendar_day="2026-11-13"):
    from services.source_ingestion.connectors.taiwan_official import governed_taiwan_calendar_evidence
    cal = governed_taiwan_calendar_evidence(venue="TWSE", trade_date=calendar_day)
    assert cal is not None
    return {
        "snapshot_id": "test-official-close",
        "event_time": f"{session}T05:30:00Z",
        "observed_at": f"{session}T11:00:00Z",
        "calendar_evidence": cal,
        "lineage": {"connector_ids": ["tw-twse-tpex-official-market"], "calendar_evidence": cal},
    }


def _preflight_readback(profile, session="2026-11-12", *, observed_at=None):
    connector, dataset = profile
    return {"connectors": [{
        "connector_id": connector,
        "latest_source_record": {
            "source_id": f"tw-taifex:{dataset}:test" if profile == CHIP_PROFILES[0] else "finmind:test",
            "status": "normalized",
            "provenance": {"dataset": dataset, "trade_date": session},
            "metadata": {"date": session},
        },
        "freshness": {"last_success_at": observed_at or f"{session}T11:00:00Z"},
    }]}


def _preflight_broker_coverage(session="2026-11-12", covered=("2330", "2317"), missing=()):
    return [{
        "connector_id": CHIP_PROFILES[1][0],
        "metadata": {"refresh_coverage": {
            "trade_date": session, "covered_symbols": list(covered), "missing_symbols": list(missing),
        }},
    }]


@pytest.mark.parametrize("profile", CHIP_PROFILES)
@pytest.mark.parametrize("state,reason", [("missing", "dataset_missing"), ("stale", "closed_session_missing"), ("fresh", "already_fresh")])
def test_preflight_morning_previous_session(profile, state, reason):
    # Official run's ordinary next-morning time. Fixtures describe controlled
    # missing/stale/fresh alternatives, never asserted historical database state.
    readback = None if state == "missing" else _preflight_readback(profile, "2026-11-11" if state == "stale" else "2026-11-12")
    res = _run_preflight_script(
        False, datetime.fromisoformat("2026-11-13T00:39:00+00:00"), _preflight_snapshot(),
        profile=profile, readback=readback, source_records=_preflight_broker_coverage(),
    )
    assert res["status"] == ("noop" if state == "fresh" else "proceed")
    assert res["reason"] == reason
    assert res["session_date"] == "2026-11-12"
    assert res["taipei_date"] == "2026-11-13"
    assert (res["connector_id"], res["dataset"]) == profile


@pytest.mark.parametrize("profile", CHIP_PROFILES)
def test_preflight_chip_without_equity_snapshot(profile):
    res = _run_preflight_script(False, datetime.fromisoformat("2026-11-13T00:39:00+00:00"), None, profile=profile)
    assert (res["status"], res["reason"], res["session_date"]) == ("proceed", "dataset_missing", "2026-11-12")


@pytest.mark.parametrize("profile", CHIP_PROFILES)
@pytest.mark.parametrize("now,session", [
    ("2026-11-16T00:30:00+00:00", "2026-11-13"),  # Monday walks over weekend.
    ("2026-10-27T00:30:00+00:00", "2026-10-22"),  # Governed emergency Friday + Monday holiday.
    ("2026-11-12T16:30:00+00:00", "2026-11-12"),  # Taipei date already Friday.
])
def test_preflight_morning_calendar_boundaries(profile, now, session):
    dt = datetime.fromisoformat(now)
    cal_day = dt.astimezone(timezone(timedelta(hours=8))).date().isoformat()
    res = _run_preflight_script(False, dt, _preflight_snapshot(session, calendar_day=cal_day), profile=profile)
    assert (res["status"], res["session_date"]) == ("proceed", session)


@pytest.mark.parametrize("profile", CHIP_PROFILES)
@pytest.mark.parametrize("now,reason", [
    ("2026-11-14T00:30:00+00:00", "weekend"),
    ("2026-10-26T00:30:00+00:00", "holiday"),
])
def test_preflight_chip_nontrading_day_safety(profile, now, reason):
    dt = datetime.fromisoformat(now)
    cal_day = dt.astimezone(timezone(timedelta(hours=8))).date().isoformat()
    res = _run_preflight_script(False, dt, _preflight_snapshot(calendar_day=cal_day), profile=profile)
    assert (res["status"], res["reason"]) == ("skipped", reason)


@pytest.mark.parametrize("now,taifex_session,broker_session", [
    ("2026-11-13T05:29:59+00:00", "2026-11-12", "2026-11-12"),
    ("2026-11-13T05:30:00+00:00", "2026-11-13", "2026-11-12"),
    ("2026-11-13T09:59:59+00:00", "2026-11-13", "2026-11-12"),
    ("2026-11-13T10:00:00+00:00", "2026-11-13", "2026-11-13"),
])
def test_preflight_distinct_close_and_publication_cutoffs(now, taifex_session, broker_session):
    for profile, expected in zip(CHIP_PROFILES, (taifex_session, broker_session)):
        res = _run_preflight_script(False, datetime.fromisoformat(now), _preflight_snapshot(), profile=profile)
        assert (res["status"], res["session_date"]) == ("proceed", expected)


@pytest.mark.parametrize("records,reason", [
    (None, "coverage_readback_unavailable"),
    ([], "coverage_incomplete"),
    (_preflight_broker_coverage(covered=("2330",), missing=("2317",)), "coverage_incomplete"),
    (_preflight_broker_coverage(session="2026-11-11"), "coverage_incomplete"),
    (_preflight_broker_coverage(covered=("2330.TW",)) + _preflight_broker_coverage(covered=("2317.TWSE",)), "already_fresh"),
])
def test_preflight_morning_broker_coverage(records, reason):
    res = _run_preflight_script(
        False, datetime.fromisoformat("2026-11-13T00:39:00+00:00"), _preflight_snapshot(),
        profile=CHIP_PROFILES[1], readback=_preflight_readback(CHIP_PROFILES[1]), source_records=records,
    )
    assert res["reason"] == reason
    assert res["status"] == ("noop" if reason == "already_fresh" else "proceed")


@pytest.mark.parametrize("profile", CHIP_PROFILES)
@pytest.mark.parametrize("mutation", ["wrong_dataset", "invalid_status", "prepublication_receipt", "unpublished_date"])
def test_preflight_chip_invalid_record_cannot_noop(profile, mutation):
    readback = _preflight_readback(profile)
    conn = readback["connectors"][0]
    if mutation == "wrong_dataset":
        conn["latest_source_record"]["provenance"]["dataset"] = "tw_price_daily"
    elif mutation == "invalid_status":
        conn["latest_source_record"]["status"] = "invalid"
    elif mutation == "unpublished_date":
        conn["latest_source_record"]["metadata"]["date"] = "2026-11-13"
    else:
        conn["freshness"]["last_success_at"] = "2026-11-12T05:00:00Z"
    res = _run_preflight_script(
        False, datetime.fromisoformat("2026-11-13T00:39:00+00:00"), _preflight_snapshot(),
        profile=profile, readback=readback, source_records=_preflight_broker_coverage(),
    )
    assert res["status"] == "proceed"
    assert res["session_date"] == "2026-11-12"


@pytest.mark.parametrize("profile", CHIP_PROFILES)
def test_preflight_morning_real_http_errors_remain_errors(profile):
    now = datetime.fromisoformat("2026-11-13T00:39:00+00:00")
    snapshot_error = _run_preflight_script(False, now, _preflight_snapshot(), 503, profile=profile)
    assert (snapshot_error["status"], snapshot_error["reason"]) == ("error", "snapshot_lookup_failed")
    readback_error = _run_preflight_script(False, now, _preflight_snapshot(), profile=profile, readback_http_status=503)
    assert (readback_error["status"], readback_error["reason"]) == ("error", "readback_lookup_failed")
    missing = _run_preflight_script(False, now, _preflight_snapshot(), profile=profile, readback_http_status=404)
    assert (missing["status"], missing["reason"]) == ("proceed", "dataset_missing")


def test_preflight_calendar_pin_and_equity_admission_stay_enforced():
    now = datetime.fromisoformat("2026-11-13T00:39:00+00:00")
    snap = _preflight_snapshot()
    snap["calendar_evidence"]["checksum"] = "0" * 64
    res = _run_preflight_script(False, now, snap, profile=CHIP_PROFILES[0])
    assert (res["status"], res["reason"]) == ("proceed", "calendar_evidence_refresh_needed")
    snap = _preflight_snapshot()
    snap["lineage"] = {"connector_ids": ["unofficial"]}
    res = _run_preflight_script(False, now, snap)
    assert (res["status"], res["reason"]) == ("error", "existing_snapshot_admission_failed")


def test_preflight_equity_missing_and_fresh_are_distinct():
    now = datetime.fromisoformat("2026-11-13T00:39:00+00:00")
    stale = _run_preflight_script(False, now, _preflight_snapshot("2026-11-11"))
    assert (stale["status"], stale["reason"], stale["session_date"]) == ("proceed", "closed_session_missing", "2026-11-12")
    missing = _run_preflight_script(False, now, None)
    assert (missing["status"], missing["reason"]) == ("proceed", "snapshot_missing")
    fresh = _run_preflight_script(False, now, _preflight_snapshot())
    # Preserve equity's existing skip semantics: this is NOT stale-data evidence.
    assert (fresh["status"], fresh["reason"]) == ("skipped", "session_not_closed")
    after_close = _run_preflight_script(False, datetime.fromisoformat("2026-11-13T11:00:00+00:00"), _preflight_snapshot("2026-11-13"))
    assert (after_close["status"], after_close["reason"]) == ("noop", "already_fresh")
    forced = _run_preflight_script(True, now, _preflight_snapshot())
    assert forced["status"] == "proceed"


def test_refresh_only_with_no_bindings_runs_bounded_pull_for_baseline_symbol(tmp_path: Path):
    initial_state = {
        "image_id": "sha256:" + "a" * 64,
        "compose_image_id": "sha256:" + "a" * 64,
        "container_env": ["PANTHEON_EXTERNAL_EGRESS=deny"],
    }
    bin_dir, state_file, events_file, output_file, port = _setup_refresh_stub_docker(tmp_path, initial_state)

    test_env = dict(os.environ)
    test_env["PATH"] = f"{bin_dir}:{os.environ['PATH']}"
    test_env["SOURCE_INGEST_API_URL"] = f"http://127.0.0.1:{port}"
    test_env["SOURCE_INGEST_BOUNDED_RUN_TIMEOUT_SECONDS"] = "30"
    test_env["PANTHEON_REMOTE_DIR"] = str(ROOT)

    proc = subprocess.run(
        ["bash", str(_deploy_script_without_readback(tmp_path)), "--refresh-only", "--force", "--output", str(output_file)],
        env=test_env,
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert proc.returncode == 0, f"Refresh failed: {proc.stderr}\n{proc.stdout}"
    assert output_file.exists()
    out_data = json.loads(output_file.read_text(encoding="utf-8"))
    assert out_data.get("status") == "completed"

    events = [json.loads(line) for line in events_file.read_text(encoding="utf-8").splitlines() if line]
    compose_run_events = [e for e in events if e.get("event") == "compose_run"]
    assert len(compose_run_events) == 2
    assert all(e.get("active_symbols") == "2330.TW" for e in compose_run_events)


def test_refresh_only_with_no_bindings_skips_when_no_baseline_declared(tmp_path: Path):
    initial_state = {
        "image_id": "sha256:" + "a" * 64,
        "compose_image_id": "sha256:" + "a" * 64,
        "container_env": ["PANTHEON_EXTERNAL_EGRESS=deny"],
    }
    bin_dir, state_file, events_file, output_file, port = _setup_refresh_stub_docker(tmp_path, initial_state)

    test_env = dict(os.environ)
    test_env["PATH"] = f"{bin_dir}:{os.environ['PATH']}"
    test_env["SOURCE_INGEST_API_URL"] = f"http://127.0.0.1:{port}"
    test_env["SOURCE_INGEST_BOUNDED_RUN_TIMEOUT_SECONDS"] = "30"
    test_env["PANTHEON_REMOTE_DIR"] = str(ROOT)
    test_env["PANTHEON_DEV_PAPER_BASELINE_SYMBOL"] = ""

    proc = subprocess.run(
        ["bash", str(_deploy_script_without_readback(tmp_path)), "--refresh-only", "--force", "--output", str(output_file)],
        env=test_env,
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert proc.returncode == 0, f"Refresh failed: {proc.stderr}\n{proc.stdout}"
    assert output_file.exists()
    out_data = json.loads(output_file.read_text(encoding="utf-8"))
    assert out_data.get("status") == "skipped"
    assert out_data.get("reason") == "no_active_taiwan_symbols"


def _run_refresh_with_steady_lease_env(tmp_path: Path, steady_lease_env: list[str]) -> tuple[subprocess.CompletedProcess[str], list[dict[str, Any]]]:
    initial_state = {
        "image_id": "sha256:" + "a" * 64,
        "compose_image_id": "a" * 64,
        "container_env": ["PANTHEON_EXTERNAL_EGRESS=deny", "PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS="],
        "steady_lease_env": steady_lease_env,
    }
    bin_dir, _state_file, events_file, output_file, port = _setup_refresh_stub_docker(tmp_path, initial_state)
    test_env = dict(os.environ)
    test_env["PATH"] = f"{bin_dir}:{os.environ['PATH']}"
    test_env["SOURCE_INGEST_API_URL"] = f"http://127.0.0.1:{port}"
    test_env["SOURCE_INGEST_BOUNDED_RUN_TIMEOUT_SECONDS"] = "30"
    test_env["PANTHEON_REMOTE_DIR"] = str(ROOT)
    proc = subprocess.run(
        ["bash", str(_deploy_script_without_readback(tmp_path)), "--refresh-only", "--force", "--output", str(output_file)],
        env=test_env,
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    events = [json.loads(line) for line in events_file.read_text(encoding="utf-8").splitlines() if line]
    return proc, events


def _steady_stop_to_bounded_run_seconds(events: list[dict[str, Any]]) -> float:
    stop = next(e for e in events if e["event"] == "docker_stop")
    first_run = next(e for e in events if e["event"] == "compose_run")
    return first_run["t"] - stop["t"]


def test_refresh_waits_for_stopped_steady_lease_before_bounded_run(tmp_path: Path):
    proc, events = _run_refresh_with_steady_lease_env(
        tmp_path, ["SOURCE_INGEST_CONTROLLER_INTERVAL_SECONDS=1", "SOURCE_INGEST_CONTROLLER_LEASE_SECONDS=3"]
    )
    assert proc.returncode == 0, f"Refresh failed: {proc.stderr}\n{proc.stdout}"
    assert _steady_stop_to_bounded_run_seconds(events) >= 3
    assert "for the stopped steady source controller lease to expire" in proc.stdout + proc.stderr


def test_refresh_lease_wait_defaults_to_twice_steady_interval(tmp_path: Path):
    proc, events = _run_refresh_with_steady_lease_env(tmp_path, ["SOURCE_INGEST_CONTROLLER_INTERVAL_SECONDS=2"])
    assert proc.returncode == 0, f"Refresh failed: {proc.stderr}\n{proc.stdout}"
    assert _steady_stop_to_bounded_run_seconds(events) >= 4


def test_bounded_refresh_one_shot_uses_ten_second_controller_interval(tmp_path: Path):
    proc, events = _run_refresh_with_steady_lease_env(tmp_path, ["SOURCE_INGEST_CONTROLLER_INTERVAL_SECONDS=1"])
    assert proc.returncode == 0, f"Refresh failed: {proc.stderr}\n{proc.stdout}"
    runs = [e for e in events if e["event"] == "compose_run"]
    assert len(runs) == 2
    assert {e["interval"] for e in runs} == {"10"}


def _bounded_run_env(compose: dict, run_args: list[str], data_root: Path) -> dict[str, str]:
    """Environment a bounded controller container would see: compose service env plus `-e` overrides."""
    service = next(a for a in run_args if a in compose["services"] and a.startswith("source-ingest-"))
    env = {k: str(v) for k, v in compose["services"][service]["environment"].items() if "${" not in str(v)}
    flags = [run_args[i + 1] for i, a in enumerate(run_args[:-1]) if a == "-e"]
    env.update(dict(f.split("=", 1) for f in flags))
    return {k: v.replace("/data/source-ingest", str(data_root)) if v.startswith("/data/source-ingest") else v for k, v in env.items()}


def _writer_then_authoritative_readback(compose: dict, writer_env: dict[str, str], data_root: Path):
    from services.source_ingestion.controller_state import ControllerStateStore
    from services.source_ingestion.controller_worker import config_from_env, _new_state
    from services.source_ingestion.runtime import SourceIngestionRuntime

    api_env = {
        k: str(v).replace("/data/source-ingest", str(data_root))
        for k, v in compose["services"]["source-ingest"]["environment"].items()
        if "${" not in str(v)
    }
    with patch.dict(os.environ, {**writer_env, "DATABASE_URL": "postgresql://x/x", "SOURCE_INGEST_CONTROLLER_TOKEN": "t" * 48}, clear=False):
        config = config_from_env()
    # Authoritative state previously written by the steady controller: retained history, old identity.
    authoritative = ControllerStateStore(api_env["SOURCE_INGEST_CONTROLLER_STATE_PATH"])
    prior = _new_state()
    prior.controller_id = "steady-old"
    prior.sequence_no = 41
    prior.recent_operations = {"op-1": {"status": "succeeded"}}
    authoritative.save(prior)
    # The bounded controller loads its own configured path, advances a fresh identity/sequence, and saves.
    writer = ControllerStateStore(config.state_path)
    state = writer.load() or _new_state()
    state.controller_id = "bounded-fresh"
    state.sequence_no = 42
    writer.save(state)
    with patch.dict(os.environ, api_env, clear=False):
        runtime = SourceIngestionRuntime(data_dir=data_root)
        return runtime, runtime.CONTROLLER_STATE_PATH, config


def test_bounded_refresh_controller_writes_the_authoritative_readback_state(tmp_path: Path):
    from services.source_ingestion.controller_state import read_controller_state

    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    proc, events = _run_refresh_with_steady_lease_env(tmp_path, ["SOURCE_INGEST_CONTROLLER_INTERVAL_SECONDS=1"])
    assert proc.returncode == 0, f"Refresh failed: {proc.stderr}\n{proc.stdout}"
    all_runs = [e for e in events if e["event"] == "compose_run"]
    assert len(all_runs) == 2
    # Only the scheduler is a state-writing controller; the Agora projector never writes controller state.
    runs = [e for e in all_runs if "source-ingest-scheduler" in e["args"]]
    assert len(runs) == 1

    def readback(run_args: list[str], root: Path) -> dict:
        _, api_path, config = _writer_then_authoritative_readback(
            compose, _bounded_run_env(compose, run_args, root), root
        )
        # bounded liveness stays separate from the authoritative state
        assert str(config.alive_path).endswith("bounded-refresh/controller_alive")
        return read_controller_state(api_path)

    # Original defect: a separate writer STATE_PATH leaves the API reading the stale identity/sequence.
    for i, run in enumerate(runs):
        split_args = list(run["args"])
        split_args[split_args.index("--name"):split_args.index("--name")] = [
            "-e", "SOURCE_INGEST_CONTROLLER_STATE_PATH=/data/source-ingest/bounded-refresh/controller_state.json",
        ]
        stale = readback(split_args, tmp_path / f"split{i}")
        assert (stale["controller_id"], stale["sequence_no"]) == ("steady-old", 41)

    # Fixed path: the shared native journal carries the fresh identity/sequence and retained history.
    for i, run in enumerate(runs):
        fresh = readback(run["args"], tmp_path / f"shared{i}")
        assert (fresh["controller_id"], fresh["sequence_no"]) == ("bounded-fresh", 42)
        assert fresh["recent_operations"] == {"op-1": {"status": "succeeded"}}


CANONICAL_TW_FAILED_FRONTIER = {
    "frontier_id": "frontier-94a821378db3",
    "connector_id": "tw-twse-tpex-official-market",
    "status": "failed",
    "attempts": 2,
    "max_attempts": 2,
    "ingest_run_id": "ingest-3b2dc4570fc4",
    "last_error": "external egress denied: code=host_not_allowlisted host='openapi.twse.com.tw' mode=deny caller=source_ingest.taiwan_official detail=set PANTHEON_EXTERNAL_EGRESS=allowlist and add the exact host to PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS url=https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL",
    "created_at": "2026-10-07T03:12:13Z",
    "updated_at": "2026-10-07T08:45:12Z",
}

CANONICAL_TW_PENDING_DLQ_ENTRIES = [
    {
        "entry_id": "dlq-264e0aeb6bf7459fa069f50723f02066",
        "status": "pending",
        "reason": "external egress denied: code=host_not_allowlisted host='openapi.twse.com.tw' mode=deny caller=source_ingest.taiwan_official detail=set PANTHEON_EXTERNAL_EGRESS=allowlist and add the exact host to PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS url=https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL",
        "rejected_at": "2026-10-07T03:12:13Z",
        "replay_attempts": 0,
        "source_ref": "source_ingest_run:ingest-81f516962093",
        "event": {
            "event_type": "source_ingestion.scheduled_run_failed",
            "aggregate_id": "ingest-81f516962093",
            "payload": {
                "connector_id": "tw-twse-tpex-official-market",
                "frontier_id": "frontier-94a821378db3",
                "ingest_run_id": "ingest-81f516962093",
                "error": "external egress denied: code=host_not_allowlisted host='openapi.twse.com.tw' mode=deny caller=source_ingest.taiwan_official detail=set PANTHEON_EXTERNAL_EGRESS=allowlist and add the exact host to PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS url=https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL",
                "attempts": 2,
                "trigger_type": "scheduled",
            },
        },
    },
    {
        "entry_id": "dlq-ab7005f4703c448ab7bc22e8cfc48331",
        "status": "pending",
        "reason": "external egress denied: code=host_not_allowlisted host='openapi.twse.com.tw' mode=deny caller=source_ingest.taiwan_official detail=set PANTHEON_EXTERNAL_EGRESS=allowlist and add the exact host to PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS url=https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL",
        "rejected_at": "2026-10-07T08:45:12Z",
        "replay_attempts": 0,
        "source_ref": "source_ingest_run:ingest-3b2dc4570fc4",
        "event": {
            "event_type": "source_ingestion.scheduled_run_failed",
            "aggregate_id": "ingest-3b2dc4570fc4",
            "payload": {
                "connector_id": "tw-twse-tpex-official-market",
                "frontier_id": "frontier-94a821378db3",
                "ingest_run_id": "ingest-3b2dc4570fc4",
                "error": "external egress denied: code=host_not_allowlisted host='openapi.twse.com.tw' mode=deny caller=source_ingest.taiwan_official detail=set PANTHEON_EXTERNAL_EGRESS=allowlist and add the exact host to PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS url=https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL",
                "attempts": 2,
                "trigger_type": "scheduled",
            },
        },
    },
]


def test_bounded_refresh_reproduces_2_entry_dlq_blocking_terminal_readback(tmp_path: Path):
    initial_state = {
        "image_id": "sha256:" + "a" * 64,
        "compose_image_id": "sha256:" + "a" * 64,
        "container_env": ["PANTHEON_EXTERNAL_EGRESS=deny"],
        "dlq_entries": [dict(e) for e in CANONICAL_TW_PENDING_DLQ_ENTRIES],
        "frontier": [dict(CANONICAL_TW_FAILED_FRONTIER)],
    }
    bin_dir, state_file, _events_file, output_file, port = _setup_refresh_stub_docker(tmp_path, initial_state)

    test_env = dict(os.environ)
    test_env["PATH"] = f"{bin_dir}:{os.environ['PATH']}"
    test_env["SOURCE_INGEST_API_URL"] = f"http://127.0.0.1:{port}"
    test_env["SOURCE_INGEST_BOUNDED_RUN_TIMEOUT_SECONDS"] = "30"
    test_env["PANTHEON_REMOTE_DIR"] = str(ROOT)

    # Unforced run: DLQ recovery must not run, so the 2 pending DLQ entries block terminal readback
    proc = subprocess.run(
        ["bash", str(_deploy_script_without_readback(tmp_path)), "--refresh-only", "--output", str(output_file)],
        env=test_env,
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert proc.returncode != 0, f"Expected unforced refresh to fail closed on unresolved DLQ, got 0:\n{proc.stdout}"
    assert "bounded source refresh service source-ingest-scheduler exited with code 1" in proc.stderr
    state = json.loads(state_file.read_text(encoding="utf-8"))
    assert not state.get("replay_calls"), "Unforced run must not invoke DLQ replay API"
    unresolved = [e for e in state.get("dlq_entries", []) if e.get("status") == "pending"]
    assert len(unresolved) == 2, f"Both DLQ entries must remain pending: {unresolved}"


def test_bounded_refresh_force_recovers_canonical_dlq_entries_via_native_api(tmp_path: Path):
    initial_state = {
        "image_id": "sha256:" + "a" * 64,
        "compose_image_id": "sha256:" + "a" * 64,
        "container_env": ["PANTHEON_EXTERNAL_EGRESS=deny"],
        "dlq_entries": [dict(e) for e in CANONICAL_TW_PENDING_DLQ_ENTRIES],
        "frontier": [dict(CANONICAL_TW_FAILED_FRONTIER)],
    }
    bin_dir, state_file, _events_file, output_file, port = _setup_refresh_stub_docker(tmp_path, initial_state)

    test_env = dict(os.environ)
    test_env["PATH"] = f"{bin_dir}:{os.environ['PATH']}"
    test_env["SOURCE_INGEST_API_URL"] = f"http://127.0.0.1:{port}"
    test_env["SOURCE_INGEST_BOUNDED_RUN_TIMEOUT_SECONDS"] = "30"
    test_env["PANTHEON_REMOTE_DIR"] = str(ROOT)

    proc = subprocess.run(
        ["bash", str(_deploy_script_without_readback(tmp_path)), "--refresh-only", "--force", "--output", str(output_file)],
        env=test_env,
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert proc.returncode == 0, f"Refresh failed: {proc.stderr}\n{proc.stdout}"
    assert output_file.exists()
    out_data = json.loads(output_file.read_text(encoding="utf-8"))
    assert out_data.get("status") == "completed"

    state = json.loads(state_file.read_text(encoding="utf-8"))
    replay_calls = state.get("replay_calls", [])
    assert len(replay_calls) == 1, f"Expected exactly 1 DLQ replay call, got: {replay_calls}"
    assert set(replay_calls[0].get("entry_ids", [])) == {
        "dlq-264e0aeb6bf7459fa069f50723f02066",
        "dlq-ab7005f4703c448ab7bc22e8cfc48331",
    }
    assert replay_calls[0].get("actor_id") == "bounded-source-refresh"
    assert replay_calls[0].get("tag") == "retry_exhausted"

    audits = state.get("audit_records", [])
    assert len(audits) >= 1
    assert audits[0]["action_type"] == "source_ingestion.scheduled_run.recovered"

    post_entries = state.get("dlq_entries", [])
    assert all(e.get("status") == "replayed" for e in post_entries)
    frontier = (state.get("frontier") or [])[0]
    assert frontier.get("status") == "done"


def test_bounded_refresh_different_connector_dlq_refused_or_left_for_strict_failure(tmp_path: Path):
    initial_state = {
        "image_id": "sha256:" + "a" * 64,
        "compose_image_id": "sha256:" + "a" * 64,
        "container_env": ["PANTHEON_EXTERNAL_EGRESS=deny"],
        "dlq_entries": [
            {
                "entry_id": "dlq-other-conn-12345",
                "status": "pending",
                "reason": "external egress denied: code=host_not_allowlisted host='api.polygon.io'",
                "event": {
                    "event_type": "source_ingestion.scheduled_run_failed",
                    "payload": {
                        "connector_id": "us-polygon-market",
                        "frontier_id": "frontier-us-12345",
                        "error": "external egress denied: code=host_not_allowlisted host='api.polygon.io'",
                    },
                },
            }
        ],
        "frontier": [
            {
                "frontier_id": "frontier-us-12345",
                "connector_id": "us-polygon-market",
                "status": "failed",
                "last_error": "external egress denied: code=host_not_allowlisted host='api.polygon.io'",
            }
        ],
    }
    bin_dir, state_file, _events_file, output_file, port = _setup_refresh_stub_docker(tmp_path, initial_state)

    test_env = dict(os.environ)
    test_env["PATH"] = f"{bin_dir}:{os.environ['PATH']}"
    test_env["SOURCE_INGEST_API_URL"] = f"http://127.0.0.1:{port}"
    test_env["SOURCE_INGEST_BOUNDED_RUN_TIMEOUT_SECONDS"] = "30"
    test_env["PANTHEON_REMOTE_DIR"] = str(ROOT)

    proc = subprocess.run(
        ["bash", str(_deploy_script_without_readback(tmp_path)), "--refresh-only", "--force", "--output", str(output_file)],
        env=test_env,
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert proc.returncode != 0, "Refresh must fail closed when DLQ has different connector failures"
    state = json.loads(state_file.read_text(encoding="utf-8"))
    assert not state.get("replay_calls"), "Must not replay DLQ entries for different connectors"
    unresolved = [e for e in state.get("dlq_entries", []) if e.get("status") == "pending"]
    assert len(unresolved) == 1


def test_bounded_refresh_different_failure_reason_dlq_refused_or_left_for_strict_failure(tmp_path: Path):
    initial_state = {
        "image_id": "sha256:" + "a" * 64,
        "compose_image_id": "sha256:" + "a" * 64,
        "container_env": ["PANTHEON_EXTERNAL_EGRESS=deny"],
        "dlq_entries": [
            {
                "entry_id": "dlq-schema-fail-12345",
                "status": "pending",
                "reason": "schema validation error: unexpected null close in daily series",
                "event": {
                    "event_type": "source_ingestion.scheduled_run_failed",
                    "payload": {
                        "connector_id": "tw-twse-tpex-official-market",
                        "frontier_id": "frontier-94a821378db3",
                        "error": "schema validation error: unexpected null close in daily series",
                    },
                },
            }
        ],
        "frontier": [
            {
                "frontier_id": "frontier-94a821378db3",
                "connector_id": "tw-twse-tpex-official-market",
                "status": "failed",
                "last_error": "schema validation error: unexpected null close in daily series",
            }
        ],
    }
    bin_dir, state_file, _events_file, output_file, port = _setup_refresh_stub_docker(tmp_path, initial_state)

    test_env = dict(os.environ)
    test_env["PATH"] = f"{bin_dir}:{os.environ['PATH']}"
    test_env["SOURCE_INGEST_API_URL"] = f"http://127.0.0.1:{port}"
    test_env["SOURCE_INGEST_BOUNDED_RUN_TIMEOUT_SECONDS"] = "30"
    test_env["PANTHEON_REMOTE_DIR"] = str(ROOT)

    proc = subprocess.run(
        ["bash", str(_deploy_script_without_readback(tmp_path)), "--refresh-only", "--force", "--output", str(output_file)],
        env=test_env,
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert proc.returncode != 0, "Refresh must fail closed when DLQ has non-egress failure reasons"
    state = json.loads(state_file.read_text(encoding="utf-8"))
    assert not state.get("replay_calls"), "Must not replay DLQ entries with non-egress failure reasons"


def test_bounded_refresh_running_frontier_dlq_refused_or_left_for_strict_failure(tmp_path: Path):
    initial_state = {
        "image_id": "sha256:" + "a" * 64,
        "compose_image_id": "sha256:" + "a" * 64,
        "container_env": ["PANTHEON_EXTERNAL_EGRESS=deny"],
        "dlq_entries": [dict(CANONICAL_TW_PENDING_DLQ_ENTRIES[0])],
        "frontier": [
            {
                "frontier_id": "frontier-94a821378db3",
                "connector_id": "tw-twse-tpex-official-market",
                "status": "running",
                "last_error": CANONICAL_TW_FAILED_FRONTIER["last_error"],
            }
        ],
    }
    bin_dir, state_file, _events_file, output_file, port = _setup_refresh_stub_docker(tmp_path, initial_state)

    test_env = dict(os.environ)
    test_env["PATH"] = f"{bin_dir}:{os.environ['PATH']}"
    test_env["SOURCE_INGEST_API_URL"] = f"http://127.0.0.1:{port}"
    test_env["SOURCE_INGEST_BOUNDED_RUN_TIMEOUT_SECONDS"] = "30"
    test_env["PANTHEON_REMOTE_DIR"] = str(ROOT)

    proc = subprocess.run(
        ["bash", str(_deploy_script_without_readback(tmp_path)), "--refresh-only", "--force", "--output", str(output_file)],
        env=test_env,
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert proc.returncode != 0, "Refresh must fail closed when frontier is in running state"
    state = json.loads(state_file.read_text(encoding="utf-8"))
    assert not state.get("replay_calls"), "Must not replay DLQ entries for running frontiers"


def test_recover_bounded_source_refresh_dlq_http_contract(tmp_path: Path):
    initial_state = {
        "image_id": "sha256:" + "a" * 64,
        "compose_image_id": "sha256:" + "a" * 64,
        "container_env": ["PANTHEON_EXTERNAL_EGRESS=deny"],
        "dlq_entries": [dict(e) for e in CANONICAL_TW_PENDING_DLQ_ENTRIES],
        "frontier": [dict(CANONICAL_TW_FAILED_FRONTIER)],
    }
    bin_dir, state_file, _events_file, _output_file, port = _setup_refresh_stub_docker(tmp_path, initial_state)

    test_env = dict(os.environ)
    test_env["PATH"] = f"{bin_dir}:{os.environ['PATH']}"
    test_env["SOURCE_INGEST_API_URL"] = f"http://127.0.0.1:{port}"

    # Extract recover_bounded_source_refresh_dlq function from deploy_nonprod_vm.sh and run it directly
    code = f"""
eval "$(sed -n '/^recover_bounded_source_refresh_dlq()/,/^execute_bounded_source_refresh_entrypoint()/p' "{DEPLOY_SCRIPT}" | sed '$d')"
recover_bounded_source_refresh_dlq "true" "tw-twse-tpex-official-market"
"""
    proc = subprocess.run(
        ["bash", "-c", code],
        env=test_env,
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert proc.returncode == 0, f"Direct recover_bounded_source_refresh_dlq call failed: {proc.stderr}\n{proc.stdout}"
    assert "recovering 2 eligible egress-denied DLQ entries" in proc.stdout
    assert "DLQ recovery completed: all 2 entries terminalized" in proc.stdout

    state = json.loads(state_file.read_text(encoding="utf-8"))
    assert len(state.get("replay_calls", [])) == 1
    call = state["replay_calls"][0]
    assert call["tag"] == "retry_exhausted"
    assert call["actor_id"] == "bounded-source-refresh"
    assert set(call["entry_ids"]) == {
        "dlq-264e0aeb6bf7459fa069f50723f02066",
        "dlq-ab7005f4703c448ab7bc22e8cfc48331",
    }


def test_recover_bounded_source_refresh_dlq_overlimit_refused_before_post(tmp_path: Path):
    # 21 eligible DLQ entries (> 20 limit)
    dlq_entries = []
    for i in range(21):
        dlq_entries.append({
            "entry_id": f"dlq-entry-{i:03d}",
            "status": "pending",
            "reason": CANONICAL_TW_PENDING_DLQ_ENTRIES[0]["reason"],
            "event": {
                "event_type": "source_ingestion.scheduled_run_failed",
                "payload": {
                    "connector_id": "tw-twse-tpex-official-market",
                    "frontier_id": "frontier-94a821378db3",
                    "error": CANONICAL_TW_PENDING_DLQ_ENTRIES[0]["event"]["payload"]["error"],
                },
            },
        })
    initial_state = {
        "image_id": "sha256:" + "a" * 64,
        "compose_image_id": "sha256:" + "a" * 64,
        "container_env": ["PANTHEON_EXTERNAL_EGRESS=deny"],
        "dlq_entries": dlq_entries,
        "frontier": [dict(CANONICAL_TW_FAILED_FRONTIER)],
    }
    bin_dir, state_file, _events_file, _output_file, port = _setup_refresh_stub_docker(tmp_path, initial_state)

    test_env = dict(os.environ)
    test_env["PATH"] = f"{bin_dir}:{os.environ['PATH']}"
    test_env["SOURCE_INGEST_API_URL"] = f"http://127.0.0.1:{port}"

    code = f"""
eval "$(sed -n '/^recover_bounded_source_refresh_dlq()/,/^execute_bounded_source_refresh_entrypoint()/p' "{DEPLOY_SCRIPT}" | sed '$d')"
recover_bounded_source_refresh_dlq "true" "tw-twse-tpex-official-market"
"""
    proc = subprocess.run(
        ["bash", "-c", code],
        env=test_env,
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert proc.returncode != 0, f"Expected overlimit to fail closed, got returncode 0:\n{proc.stdout}"
    assert "exceeds maximum limit 20; refusing replay before mutation" in proc.stderr
    state = json.loads(state_file.read_text(encoding="utf-8"))
    assert not state.get("replay_calls"), "Must not execute any DLQ replay POST mutation when count exceeds limit"
    unresolved = [e for e in state.get("dlq_entries", []) if e.get("status") == "pending"]
    assert len(unresolved) == 21


def test_recover_bounded_source_refresh_dlq_fails_closed_on_non_success_readback(tmp_path: Path):
    # (a) Replay returns replay_failed (reproducing counterexample source-dlq-6407-replay-failed-counterexample.json)
    initial_state = {
        "image_id": "sha256:" + "a" * 64,
        "compose_image_id": "sha256:" + "a" * 64,
        "container_env": ["PANTHEON_EXTERNAL_EGRESS=deny"],
        "dlq_entries": [dict(e) for e in CANONICAL_TW_PENDING_DLQ_ENTRIES],
        "frontier": [dict(CANONICAL_TW_FAILED_FRONTIER)],
        "mock_replay_target_status": "replay_failed",
    }
    bin_dir, state_file, _events_file, _output_file, port = _setup_refresh_stub_docker(tmp_path, initial_state)

    test_env = dict(os.environ)
    test_env["PATH"] = f"{bin_dir}:{os.environ['PATH']}"
    test_env["SOURCE_INGEST_API_URL"] = f"http://127.0.0.1:{port}"

    code = f"""
eval "$(sed -n '/^recover_bounded_source_refresh_dlq()/,/^execute_bounded_source_refresh_entrypoint()/p' "{DEPLOY_SCRIPT}" | sed '$d')"
recover_bounded_source_refresh_dlq "true" "tw-twse-tpex-official-market"
"""
    proc = subprocess.run(
        ["bash", "-c", code],
        env=test_env,
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert proc.returncode != 0, f"Expected non-success status to fail closed, got 0:\n{proc.stdout}"
    assert "DLQ recovery failed: selected entries not successfully terminalized" in proc.stderr
    assert "non-success status 'replay_failed'" in proc.stderr
    assert "all 2 entries terminalized" not in proc.stdout

    # (b) Selected entry is dropped / missing from post-replay readback
    state = json.loads(state_file.read_text(encoding="utf-8"))
    state["mock_replay_target_status"] = "replayed"
    state["mock_replay_drop_entries"] = True
    for e in state["dlq_entries"]:
        e["status"] = "pending"
    state_file.write_text(json.dumps(state), encoding="utf-8")

    proc_missing = subprocess.run(
        ["bash", "-c", code],
        env=test_env,
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert proc_missing.returncode != 0, f"Expected missing entry in readback to fail closed, got 0:\n{proc_missing.stdout}"
    assert "missing from post-replay DLQ readback" in proc_missing.stderr
    assert "all 2 entries terminalized" not in proc_missing.stdout


def test_recover_bounded_source_refresh_dlq_fails_closed_on_timeout_budget(tmp_path: Path):
    initial_state = {
        "image_id": "sha256:" + "a" * 64,
        "compose_image_id": "sha256:" + "a" * 64,
        "container_env": ["PANTHEON_EXTERNAL_EGRESS=deny"],
        "dlq_entries": [dict(e) for e in CANONICAL_TW_PENDING_DLQ_ENTRIES],
        "frontier": [dict(CANONICAL_TW_FAILED_FRONTIER)],
        "mock_replay_delay_seconds": 1.0,
    }
    bin_dir, _state_file, _events_file, _output_file, port = _setup_refresh_stub_docker(tmp_path, initial_state)

    test_env = dict(os.environ)
    test_env["PATH"] = f"{bin_dir}:{os.environ['PATH']}"
    test_env["SOURCE_INGEST_API_URL"] = f"http://127.0.0.1:{port}"

    code = f"""
eval "$(sed -n '/^recover_bounded_source_refresh_dlq()/,/^execute_bounded_source_refresh_entrypoint()/p' "{DEPLOY_SCRIPT}" | sed '$d')"
recover_bounded_source_refresh_dlq "true" "tw-twse-tpex-official-market" "0.2"
"""
    proc = subprocess.run(
        ["bash", "-c", code],
        env=test_env,
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert proc.returncode != 0, f"Expected timeout budget exhaustion to fail closed, got 0:\n{proc.stdout}"
    assert ("DLQ replay API request failed:" in proc.stderr or "timeout budget exhausted" in proc.stderr)
    assert "all 2 entries terminalized" not in proc.stdout


def test_recover_bounded_source_refresh_dlq_refuses_flat_legacy_api_shape(tmp_path: Path):
    flat_dlq_entries = [
        {
            "entry_id": "dlq-flat-legacy-1",
            "status": "pending",
            "reason": "external egress denied: code=host_not_allowlisted host='openapi.twse.com.tw'",
            "event_type": "source_ingestion.scheduled_run_failed",
            "payload": {
                "connector_id": "tw-twse-tpex-official-market",
                "frontier_id": "frontier-94a821378db3",
                "error": "external egress denied: code=host_not_allowlisted host='openapi.twse.com.tw'",
            },
        }
    ]
    initial_state = {
        "image_id": "sha256:" + "a" * 64,
        "compose_image_id": "sha256:" + "a" * 64,
        "container_env": ["PANTHEON_EXTERNAL_EGRESS=deny"],
        "dlq_entries": flat_dlq_entries,
        "frontier": [dict(CANONICAL_TW_FAILED_FRONTIER)],
    }
    bin_dir, state_file, _events_file, _output_file, port = _setup_refresh_stub_docker(tmp_path, initial_state)

    test_env = dict(os.environ)
    test_env["PATH"] = f"{bin_dir}:{os.environ['PATH']}"
    test_env["SOURCE_INGEST_API_URL"] = f"http://127.0.0.1:{port}"

    code = f"""
eval "$(sed -n '/^recover_bounded_source_refresh_dlq()/,/^execute_bounded_source_refresh_entrypoint()/p' "{DEPLOY_SCRIPT}" | sed '$d')"
recover_bounded_source_refresh_dlq "true" "tw-twse-tpex-official-market"
"""
    proc = subprocess.run(
        ["bash", "-c", code],
        env=test_env,
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert "missing nested event structure in DLQ entry" in proc.stdout
    assert "no eligible egress-denied DLQ entries found for recovery" in proc.stdout
    state = json.loads(state_file.read_text(encoding="utf-8"))
    assert not state.get("replay_calls"), "Must not call replay API for entries with missing nested event structure"


def test_bounded_refresh_stale_projector_image_guard_fails_closed(tmp_path: Path):
    initial_state = {
        "image_id": "sha256:" + "a" * 64,
        "compose_image_id": "sha256:" + "a" * 64,
        "container_env": ["PANTHEON_EXTERNAL_EGRESS=deny"],
        "projector_image_sha": "stale-sha-2026-10-05",
    }
    bin_dir, state_file, events_file, output_file, port = _setup_refresh_stub_docker(tmp_path, initial_state)

    test_env = dict(os.environ)
    test_env["PATH"] = f"{bin_dir}:{os.environ['PATH']}"
    test_env["SOURCE_INGEST_API_URL"] = f"http://127.0.0.1:{port}"
    test_env["SOURCE_INGEST_BOUNDED_RUN_TIMEOUT_SECONDS"] = "30"
    test_env["PANTHEON_REMOTE_DIR"] = str(ROOT)

    proc = subprocess.run(
        ["bash", str(_deploy_script_without_readback(tmp_path)), "--refresh-only", "--force", "--output", str(output_file)],
        env=test_env,
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert proc.returncode != 0, f"Expected refresh to fail closed on stale projector image, got 0:\n{proc.stdout}"
    assert "stale_projector_image" in proc.stderr
    assert "source-ingest-agora-projector image revision stale-sha-2026-10-05 != expected abc123" in proc.stderr
    events = [json.loads(line) for line in events_file.read_text().splitlines() if line]
    assert not any(e.get("event") == "compose_run" for e in events), "One-shot containers must not start on stale projector image"


def test_bounded_refresh_accepts_valid_built_projector_image_when_container_absent(tmp_path: Path):
    # Regression for run 38212356787: genuine projector image was built with matching revision,
    # but no projector container was created, so compose images -q returns empty.
    # The post-build lookup must inspect the built image and proceed.
    initial_state = {
        "image_id": "sha256:" + "a" * 64,
        "compose_image_id": "sha256:" + "a" * 64,
        "projector_container_compose_image_id": "",  # absent projector container
        "built_projector_image_id": "sha256:0fedd6c77271dc29bf6425461cac284df85a6454d656e363d67646ec9ee24b2e",
        "projector_image_sha": "abc123",  # matching GIT_SHA
        "container_env": ["PANTHEON_EXTERNAL_EGRESS=deny"],
    }
    bin_dir, state_file, events_file, output_file, port = _setup_refresh_stub_docker(tmp_path, initial_state)

    test_env = dict(os.environ)
    test_env["PATH"] = f"{bin_dir}:{os.environ['PATH']}"
    test_env["SOURCE_INGEST_API_URL"] = f"http://127.0.0.1:{port}"
    test_env["SOURCE_INGEST_BOUNDED_RUN_TIMEOUT_SECONDS"] = "30"
    test_env["PANTHEON_REMOTE_DIR"] = str(ROOT)

    proc = subprocess.run(
        ["bash", str(_deploy_script_without_readback(tmp_path)), "--refresh-only", "--force", "--output", str(output_file)],
        env=test_env,
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert proc.returncode == 0, f"Expected refresh to succeed with absent projector container, failed:\n{proc.stderr}"
    assert "stale_projector_image" not in proc.stderr
    events = [json.loads(line) for line in events_file.read_text().splitlines() if line]
    assert any(e.get("event") == "compose_run" and "source-ingest-agora-projector" in e.get("args", []) for e in events)


def test_bounded_refresh_built_projector_image_used_over_old_container_image(tmp_path: Path):
    # An old projector container image exists with a stale OCI revision,
    # but the newly built projector image has the valid matching revision.
    # The lookup must inspect the built image, not the old container image.
    old_container_img_id = "sha256:" + "9" * 64
    new_built_img_id = "sha256:" + "b" * 64
    initial_state = {
        "image_id": "sha256:" + "a" * 64,
        "compose_image_id": "sha256:" + "a" * 64,
        "projector_container_compose_image_id": old_container_img_id,
        "built_projector_image_id": new_built_img_id,
        "image_revisions": {
            old_container_img_id: "stale-container-rev-20261001",
            new_built_img_id: "abc123",
        },
        "container_env": ["PANTHEON_EXTERNAL_EGRESS=deny"],
    }
    bin_dir, state_file, events_file, output_file, port = _setup_refresh_stub_docker(tmp_path, initial_state)

    test_env = dict(os.environ)
    test_env["PATH"] = f"{bin_dir}:{os.environ['PATH']}"
    test_env["SOURCE_INGEST_API_URL"] = f"http://127.0.0.1:{port}"
    test_env["SOURCE_INGEST_BOUNDED_RUN_TIMEOUT_SECONDS"] = "30"
    test_env["PANTHEON_REMOTE_DIR"] = str(ROOT)

    proc = subprocess.run(
        ["bash", str(_deploy_script_without_readback(tmp_path)), "--refresh-only", "--force", "--output", str(output_file)],
        env=test_env,
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert proc.returncode == 0, f"Expected refresh to succeed using built image, failed:\n{proc.stderr}"
    assert "stale_projector_image" not in proc.stderr
    events = [json.loads(line) for line in events_file.read_text().splitlines() if line]
    assert any(e.get("event") == "compose_run" and "source-ingest-agora-projector" in e.get("args", []) for e in events)


def test_bounded_refresh_missing_built_projector_image_fails_closed(tmp_path: Path):
    # Built projector image is missing (e.g. build failure or prune).
    # The lookup must fail closed before one-shot start.
    initial_state = {
        "image_id": "sha256:" + "a" * 64,
        "compose_image_id": "sha256:" + "a" * 64,
        "built_projector_image_present": False,
        "container_env": ["PANTHEON_EXTERNAL_EGRESS=deny"],
    }
    bin_dir, state_file, events_file, output_file, port = _setup_refresh_stub_docker(tmp_path, initial_state)

    test_env = dict(os.environ)
    test_env["PATH"] = f"{bin_dir}:{os.environ['PATH']}"
    test_env["SOURCE_INGEST_API_URL"] = f"http://127.0.0.1:{port}"
    test_env["SOURCE_INGEST_BOUNDED_RUN_TIMEOUT_SECONDS"] = "30"
    test_env["PANTHEON_REMOTE_DIR"] = str(ROOT)

    proc = subprocess.run(
        ["bash", str(_deploy_script_without_readback(tmp_path)), "--refresh-only", "--force", "--output", str(output_file)],
        env=test_env,
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert proc.returncode != 0, f"Expected refresh to fail closed on missing built image, got 0:\n{proc.stdout}"
    assert "stale_projector_image" in proc.stderr
    assert "source-ingest-agora-projector image revision missing != expected abc123" in proc.stderr
    events = [json.loads(line) for line in events_file.read_text().splitlines() if line]
    assert not any(e.get("event") == "compose_run" for e in events), "One-shot containers must not start when image is missing"


def test_bounded_refresh_phase_ordering_waits_for_scheduler_before_projector_launch(tmp_path: Path):
    # Regression for run 38240659167: scheduler and projector were launched concurrently;
    # Agora projector finished before new source was persisted.
    # The phase loop must sequentially wait for source-ingest-scheduler before launching source-ingest-agora-projector.
    initial_state = {
        "image_id": "sha256:" + "a" * 64,
        "compose_image_id": "sha256:" + "a" * 64,
        "built_projector_image_id": "sha256:0fedd6c77271dc29bf6425461cac284df85a6454d656e363d67646ec9ee24b2e",
        "projector_image_sha": "abc123",
        "container_env": ["PANTHEON_EXTERNAL_EGRESS=deny"],
    }
    bin_dir, state_file, events_file, output_file, port = _setup_refresh_stub_docker(tmp_path, initial_state)

    test_env = dict(os.environ)
    test_env["PATH"] = f"{bin_dir}:{os.environ['PATH']}"
    test_env["SOURCE_INGEST_API_URL"] = f"http://127.0.0.1:{port}"
    test_env["SOURCE_INGEST_BOUNDED_RUN_TIMEOUT_SECONDS"] = "30"
    test_env["PANTHEON_REMOTE_DIR"] = str(ROOT)

    proc = subprocess.run(
        ["bash", str(_deploy_script_without_readback(tmp_path)), "--refresh-only", "--force", "--output", str(output_file)],
        env=test_env,
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert proc.returncode == 0, f"Refresh failed: {proc.stderr}\n{proc.stdout}"
    events = [json.loads(line) for line in events_file.read_text().splitlines() if line]
    runs = [e for e in events if e.get("event") == "compose_run"]
    assert len(runs) == 2, f"Expected 2 compose_run events, got {len(runs)}"
    assert "source-ingest-scheduler" in runs[0]["args"], "First run must be source-ingest-scheduler"
    assert "source-ingest-agora-projector" in runs[1]["args"], "Second run must be source-ingest-agora-projector"

    # Verify that inspects (waiting for scheduler completion) happened before projector was launched
    inspect_events = [e for e in events if e.get("event") == "docker_inspect_status"]
    scheduler_inspects = [e for e in inspect_events if "source-ingest-scheduler" in str(e.get("target", ""))]
    assert scheduler_inspects, "Must inspect scheduler status during bounded wait"
    assert scheduler_inspects[0]["t"] < runs[1]["t"], "Scheduler must be waited on before projector is launched"


def test_bounded_refresh_reproduces_run38240659167_causal_defect_concurrent_projector_rejected_by_readback(tmp_path: Path):
    # Causal defect reproduction from actual run 38240659167:
    # Projector completed at 11:11:11 before new source persisted at 11:11:22, so Agora projection
    # carried older run ingest-e53a86463995, rejecting the binding gate at 11:11:46.
    # The readback gate must strictly reject when Agora projection does not bind new receipt/run/source.
    initial_state = {
        "image_id": "sha256:" + "a" * 64,
        "compose_image_id": "sha256:" + "a" * 64,
        "built_projector_image_id": "sha256:0fedd6c77271dc29bf6425461cac284df85a6454d656e363d67646ec9ee24b2e",
        "projector_image_sha": "abc123",
        "container_env": ["PANTHEON_EXTERNAL_EGRESS=deny"],
        "agora_projection": {
            "row1": {
                "connectorId": "tw-twse-tpex-official-market",
                "ingestRunId": "ingest-e53a86463995",  # older run id from run 38240659167
                "sourceId": "tw-official:tw_price_daily:TWSE:2330:90e323e6754e2e9e",
                "asOf": "2026-10-08T14:30:00+08:00",
                "freshness": {
                    "sourceTimestamp": "2026-10-08T14:30:00+08:00",
                    "sourceTimeStatus": "valid",
                    "status": "stale",
                    "stale": True,
                },
            }
        },
    }
    bin_dir, state_file, events_file, output_file, port = _setup_refresh_stub_docker(tmp_path, initial_state)

    test_env = dict(os.environ)
    test_env["PATH"] = f"{bin_dir}:{os.environ['PATH']}"
    test_env["SOURCE_INGEST_API_URL"] = f"http://127.0.0.1:{port}"
    test_env["SOURCE_INGEST_BOUNDED_RUN_TIMEOUT_SECONDS"] = "30"
    test_env["PANTHEON_REMOTE_DIR"] = str(ROOT)

    proc = subprocess.run(
        ["bash", str(_deploy_script_with_readback(tmp_path)), "--refresh-only", "--force", "--output", str(output_file)],
        env=test_env,
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert proc.returncode != 0, f"Expected readback to reject stale Agora projection, got 0:\n{proc.stdout}"
    assert "Agora projection does not bind the new receipt/run/source" in proc.stderr
    assert not output_file.exists() or json.loads(output_file.read_text()).get("status") != "completed"


def test_bounded_refresh_failed_readback_restores_steady_scheduler_and_removes_bounded_containers(tmp_path: Path):
    # Regression for run 38240659167 line 1035: when readback validation failed under set -euo pipefail,
    # returning from execute_bounded_source_refresh_entrypoint destroyed function-local steady_ids,
    # causing an unbound variable crash in the outer EXIT trap and leaving steady scheduler stopped (exited 137).
    # Fix: local-scope cleanup executes restore_bounded_source_refresh before return, cleanly restarting
    # steady scheduler, removing named bounded containers, restoring egress to deny, and preserving failing exit.
    initial_state = {
        "image_id": "sha256:" + "a" * 64,
        "compose_image_id": "sha256:" + "a" * 64,
        "built_projector_image_id": "sha256:0fedd6c77271dc29bf6425461cac284df85a6454d656e363d67646ec9ee24b2e",
        "projector_image_sha": "abc123",
        "container_env": ["PANTHEON_EXTERNAL_EGRESS=deny"],
        "agora_projection": {
            "row1": {
                "connectorId": "tw-twse-tpex-official-market",
                "ingestRunId": "ingest-e53a86463995",  # mismatched run to trigger readback failure
                "sourceId": "tw-official:tw_price_daily:TWSE:2330:90e323e6754e2e9e",
            }
        },
    }
    bin_dir, state_file, events_file, output_file, port = _setup_refresh_stub_docker(tmp_path, initial_state)

    test_env = dict(os.environ)
    test_env["PATH"] = f"{bin_dir}:{os.environ['PATH']}"
    test_env["SOURCE_INGEST_API_URL"] = f"http://127.0.0.1:{port}"
    test_env["SOURCE_INGEST_BOUNDED_RUN_TIMEOUT_SECONDS"] = "30"
    test_env["PANTHEON_REMOTE_DIR"] = str(ROOT)

    proc = subprocess.run(
        ["bash", str(_deploy_script_with_readback(tmp_path)), "--refresh-only", "--force", "--output", str(output_file)],
        env=test_env,
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert proc.returncode != 0, f"Expected refresh to fail closed on readback failure, got 0:\n{proc.stdout}"
    assert "steady_ids: unbound variable" not in proc.stderr
    assert "Agora projection does not bind the new receipt/run/source" in proc.stderr
    assert not output_file.exists() or json.loads(output_file.read_text()).get("status") != "completed"

    events = [json.loads(line) for line in events_file.read_text().splitlines() if line]

    # Verify bounded containers were removed
    rm_events = [e for e in events if e.get("event") == "docker_rm"]
    assert rm_events, "Named bounded containers must be removed on failure cleanup"
    removed_names = [name for e in rm_events for name in e.get("names", [])]
    assert "pantheon-bounded-refresh-source-ingest-scheduler" in removed_names
    assert "pantheon-bounded-refresh-source-ingest-agora-projector" in removed_names

    # Verify steady scheduler was restarted
    start_events = [e for e in events if e.get("event") == "docker_start"]
    assert start_events, "Steady scheduler container must be restarted on failure cleanup"
    started_names = [name for e in start_events for name in e.get("names", [])]
    assert "cid-source-ingest-scheduler" in started_names

    # Verify external egress and controller mode were restored to deny / reconcile_only
    restore_ups = [
        e for e in events
        if e.get("event") == "compose_up"
        and "source-ingest" in e.get("services", [])
        and e["env"].get("PANTHEON_EXTERNAL_EGRESS") == "deny"
    ]
    assert restore_ups, "Runtime must be restored to egress deny on failure cleanup"
    assert restore_ups[-1]["env"].get("SOURCE_INGEST_CONTROLLER_MODE") == "reconcile_only"


def test_bounded_refresh_failed_one_shot_restores_steady_scheduler(tmp_path: Path):
    # When a bounded one-shot container fails during execution, failure cleanup
    # must restart steady scheduler and remove bounded containers without unbound variable errors.
    initial_state = {
        "image_id": "sha256:" + "a" * 64,
        "compose_image_id": "sha256:" + "a" * 64,
        "built_projector_image_id": "sha256:0fedd6c77271dc29bf6425461cac284df85a6454d656e363d67646ec9ee24b2e",
        "projector_image_sha": "abc123",
        "container_env": ["PANTHEON_EXTERNAL_EGRESS=deny"],
        "dlq_entries": [dict(CANONICAL_TW_PENDING_DLQ_ENTRIES[0])],  # forces exit code 1 for scheduler
    }
    bin_dir, state_file, events_file, output_file, port = _setup_refresh_stub_docker(tmp_path, initial_state)

    test_env = dict(os.environ)
    test_env["PATH"] = f"{bin_dir}:{os.environ['PATH']}"
    test_env["SOURCE_INGEST_API_URL"] = f"http://127.0.0.1:{port}"
    test_env["SOURCE_INGEST_BOUNDED_RUN_TIMEOUT_SECONDS"] = "30"
    test_env["PANTHEON_REMOTE_DIR"] = str(ROOT)

    proc = subprocess.run(
        ["bash", str(_deploy_script_without_readback(tmp_path)), "--refresh-only", "--force", "--output", str(output_file)],
        env=test_env,
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert proc.returncode != 0, f"Expected one-shot failure to fail closed, got 0:\n{proc.stdout}"
    assert "steady_ids: unbound variable" not in proc.stderr

    events = [json.loads(line) for line in events_file.read_text().splitlines() if line]
    start_events = [e for e in events if e.get("event") == "docker_start"]
    assert start_events, "Steady scheduler container must be restarted on one-shot failure"
    started_names = [name for e in start_events for name in e.get("names", [])]
    assert "cid-source-ingest-scheduler" in started_names


def test_bounded_refresh_launch_failure_restores_steady_scheduler_and_removes_bounded_containers(tmp_path: Path):
    # Regression for launch failure / raw errexit (exit code 42) before container starts:
    # When docker compose run fails during launch, script-scoped variables ensure
    # failure cleanup executes completely, restarts steady scheduler, removes named bounded containers,
    # and preserves the failing return code without unbound variable errors.
    initial_state = {
        "image_id": "sha256:" + "a" * 64,
        "compose_image_id": "sha256:" + "a" * 64,
        "built_projector_image_id": "sha256:0fedd6c77271dc29bf6425461cac284df85a6454d656e363d67646ec9ee24b2e",
        "projector_image_sha": "abc123",
        "container_env": ["PANTHEON_EXTERNAL_EGRESS=deny"],
        "dlq_entries": [],
    }
    bin_dir, state_file, events_file, output_file, port = _setup_refresh_stub_docker(tmp_path, initial_state)

    docker = bin_dir / "docker"
    delegate = bin_dir / "original-owned-docker-stub"
    docker.rename(delegate)
    docker.write_text(
        "#!/usr/bin/env python3\n"
        "import os,sys,json\n"
        "from pathlib import Path\n"
        "a=sys.argv[1:]\n"
        'if "run" in a and "--name" in a and "pantheon-bounded-refresh-source-ingest-scheduler" in a:\n'
        f"    p=Path({repr(str(events_file))})\n"
        '    p.open("a").write(json.dumps({"event":"QA_owned_scheduler_launch_failed","args":a})+"\\n")\n'
        "    sys.exit(42)\n"
        f"os.execv({repr(str(delegate))}, [{repr(str(delegate))}]+a)\n"
    )
    docker.chmod(0o755)

    test_env = dict(os.environ)
    test_env["PATH"] = f"{bin_dir}:{os.environ['PATH']}"
    test_env["SOURCE_INGEST_API_URL"] = f"http://127.0.0.1:{port}"
    test_env["SOURCE_INGEST_BOUNDED_RUN_TIMEOUT_SECONDS"] = "30"
    test_env["PANTHEON_REMOTE_DIR"] = str(ROOT)

    proc = subprocess.run(
        ["bash", str(_deploy_script_without_readback(tmp_path)), "--refresh-only", "--force", "--output", str(output_file)],
        env=test_env,
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert proc.returncode == 42, f"Expected launch failure return code 42, got {proc.returncode}:\n{proc.stdout}\n{proc.stderr}"
    assert "steady_ids: unbound variable" not in proc.stderr
    assert not output_file.exists() or json.loads(output_file.read_text()).get("status") != "completed"

    events = [json.loads(line) for line in events_file.read_text().splitlines() if line]

    # Verify launch failure occurred
    assert any(e.get("event") == "QA_owned_scheduler_launch_failed" for e in events)

    # Verify bounded containers were removed on cleanup
    rm_events = [e for e in events if e.get("event") == "docker_rm"]
    assert rm_events, "Named bounded containers must be removed on failure cleanup"
    removed_names = [name for e in rm_events for name in e.get("names", [])]
    assert "pantheon-bounded-refresh-source-ingest-scheduler" in removed_names
    assert "pantheon-bounded-refresh-source-ingest-agora-projector" in removed_names

    # Verify steady scheduler was restarted on cleanup
    start_events = [e for e in events if e.get("event") == "docker_start"]
    assert start_events, "Steady scheduler container must be restarted on launch failure"
    started_names = [name for e in start_events for name in e.get("names", [])]
    assert "cid-source-ingest-scheduler" in started_names

    # Verify runtime restored to egress deny
    restore_ups = [
        e for e in events
        if e.get("event") == "compose_up"
        and "source-ingest" in e.get("services", [])
        and e["env"].get("PANTHEON_EXTERNAL_EGRESS") == "deny"
    ]
    assert restore_ups, "Runtime must be restored to egress deny on launch failure cleanup"
    assert restore_ups[-1]["env"].get("SOURCE_INGEST_CONTROLLER_MODE") == "reconcile_only"


def _steady_containers_for_case(case: str) -> list[dict[str, Any]]:
    if case == "positive":
        return [
            {
                "id": "0d124a6305c2800d280fcb5751c95bfa25eb695d138d26a21bd343b6e62542c5",
                "name": "/pantheon-bounded-refresh-source-ingest-scheduler",
                "status": "exited",
            },
            {
                "id": "847ed9fb5e7ab6722f7bf65d0525a0e27dd90437deddd496c5965904f666b565",
                "name": "/pantheon-source-ingest-scheduler-1",
                "status": "running",
                "env": [
                    "PANTHEON_TENANT_ID=tenant-dev",
                    "PANTHEON_ENV=dev",
                    "GIT_SHA=abc123",
                    "SOURCE_INGEST_CONTROLLER_INTERVAL_SECONDS=1",
                ],
            },
        ]
    if case == "stopped_only":
        return [
            {
                "id": "0d124a6305c2800d280fcb5751c95bfa25eb695d138d26a21bd343b6e62542c5",
                "name": "/pantheon-bounded-refresh-source-ingest-scheduler",
                "status": "exited",
            }
        ]
    if case == "ambiguous_live":
        return [
            {
                "id": "847ed9fb5e7ab6722f7bf65d0525a0e27dd90437deddd496c5965904f666b565",
                "status": "running",
                "env": [
                    "PANTHEON_TENANT_ID=tenant-dev",
                    "PANTHEON_ENV=dev",
                    "GIT_SHA=abc123",
                ],
            },
            {
                "id": "1111111111111111111111111111111111111111111111111111111111111111",
                "status": "running",
                "env": [
                    "PANTHEON_TENANT_ID=tenant-dev",
                    "PANTHEON_ENV=dev",
                    "GIT_SHA=abc123",
                ],
            },
        ]
    raise ValueError(f"Unknown case: {case}")


@pytest.mark.parametrize(
    "case",
    ["positive", "stopped_only", "ambiguous_live"],
)
def test_bounded_refresh_steady_container_selection(tmp_path: Path, case: str):
    # Parameterized tests sharing duplicate setup across steady selection cases:
    # - positive: running steady scheduler alongside stopped bounded containers; running status filter
    #   selects only steady container, reads steady env, runs sequentially, validates real readback,
    #   restarts steady scheduler, and leaves old one-shots stopped.
    # - stopped_only: only stopped bounded containers exist; running query returns empty, deploy-time
    #   values fail closed before mutation, and old one-shots are not started.
    # - ambiguous_live: multiple running steady containers exist; running query returns multiline IDs,
    #   deploy-time lookup fails closed before mutation without first-ID fallback, and no containers mutate.
    scheduler_containers = _steady_containers_for_case(case)
    initial_state: dict[str, Any] = {
        "image_id": "sha256:" + "a" * 64,
        "compose_image_id": "sha256:" + "a" * 64,
        "built_projector_image_id": "sha256:0fedd6c77271dc29bf6425461cac284df85a6454d656e363d67646ec9ee24b2e",
        "projector_image_sha": "abc123",
        "container_env": ["PANTHEON_EXTERNAL_EGRESS=deny"],
        "compose_service_containers": {
            "source-ingest-scheduler": scheduler_containers,
            "source-ingest-agora-projector": [
                {
                    "id": "f9e6c68db2cc6202b23878fa3c6615fa873ff3c49f446bfdeb523d6c078387cd",
                    "name": "/pantheon-bounded-refresh-source-ingest-agora-projector",
                    "status": "exited",
                }
            ],
        },
    }
    if case == "positive":
        # Positive steady selection reaches real bounded execution and exercises
        # actual original receipt/run/source readback validation via _deploy_script_with_readback.
        # Testing real binding rejection where Agora projection does not bind new receipt/run/source
        # proves entry reaches source, waits Source before Agora, validates readback rejection,
        # cleanly restarts only steady scheduler, and leaves old one-shots stopped.
        initial_state["agora_projection"] = {
            "row1": {
                "connectorId": "tw-twse-tpex-official-market",
                "ingestRunId": "ingest-e53a86463995",
                "sourceId": "tw-official:tw_price_daily:TWSE:2330:90e323e6754e2e9e",
            }
        }
    bin_dir, state_file, events_file, output_file, port = _setup_refresh_stub_docker(tmp_path, initial_state)

    test_env = dict(os.environ)
    test_env["PATH"] = f"{bin_dir}:{os.environ['PATH']}"
    test_env["SOURCE_INGEST_API_URL"] = f"http://127.0.0.1:{port}"
    test_env["SOURCE_INGEST_BOUNDED_RUN_TIMEOUT_SECONDS"] = "30"
    test_env["PANTHEON_REMOTE_DIR"] = str(ROOT)

    proc = subprocess.run(
        ["bash", str(_deploy_script_with_readback(tmp_path)), "--refresh-only", "--force", "--output", str(output_file)],
        env=test_env,
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    events = [json.loads(line) for line in events_file.read_text().splitlines() if line] if events_file.exists() else []

    if case == "positive":
        assert proc.returncode != 0, f"Expected readback binding rejection to fail closed, got 0:\n{proc.stdout}"
        assert "steady_scheduler_deploy_values_missing" not in proc.stderr
        assert "Agora projection does not bind the new receipt/run/source" in proc.stderr
        assert not output_file.exists() or json.loads(output_file.read_text()).get("status") != "completed"

        # Verify stopped containers: only steady scheduler 847ed9... was stopped, NOT 0d124a...
        stop_events = [e for e in events if e.get("event") == "docker_stop"]
        assert stop_events, "Steady scheduler must be stopped before bounded run"
        stopped_ids = [cid for e in stop_events for cid in e.get("names", [])]
        assert "847ed9fb5e7ab6722f7bf65d0525a0e27dd90437deddd496c5965904f666b565" in stopped_ids
        assert "0d124a6305c2800d280fcb5751c95bfa25eb695d138d26a21bd343b6e62542c5" not in stopped_ids
        assert "f9e6c68db2cc6202b23878fa3c6615fa873ff3c49f446bfdeb523d6c078387cd" not in stopped_ids

        # Verify sequential runs: scheduler ran before projector
        runs = [e for e in events if e.get("event") == "compose_run"]
        assert len(runs) == 2
        assert "source-ingest-scheduler" in runs[0]["args"]
        assert "source-ingest-agora-projector" in runs[1]["args"]

        # Verify restarted containers: ONLY steady scheduler 847ed9... was restarted, NOT 0d124a...
        start_events = [e for e in events if e.get("event") == "docker_start"]
        assert start_events, "Steady scheduler must be restarted on completion"
        started_ids = [cid for e in start_events for cid in e.get("names", [])]
        assert "847ed9fb5e7ab6722f7bf65d0525a0e27dd90437deddd496c5965904f666b565" in started_ids
        assert "0d124a6305c2800d280fcb5751c95bfa25eb695d138d26a21bd343b6e62542c5" not in started_ids
        assert "f9e6c68db2cc6202b23878fa3c6615fa873ff3c49f446bfdeb523d6c078387cd" not in started_ids

        # Verify egress restored to deny
        restore_ups = [
            e for e in events
            if e.get("event") == "compose_up"
            and "source-ingest" in e.get("services", [])
            and e["env"].get("PANTHEON_EXTERNAL_EGRESS") == "deny"
        ]
        assert restore_ups, "Runtime must be restored to egress deny"

    elif case == "stopped_only":
        assert proc.returncode != 0, f"Expected stopped-only steady scheduler to fail closed, got 0:\n{proc.stdout}"
        assert "steady_scheduler_deploy_values_missing" in proc.stderr
        assert not output_file.exists() or json.loads(output_file.read_text()).get("status") != "completed"

        # Old stopped container must NOT be started
        start_events = [e for e in events if e.get("event") == "docker_start"]
        started_ids = [cid for e in start_events for cid in e.get("names", [])]
        assert "0d124a6305c2800d280fcb5751c95bfa25eb695d138d26a21bd343b6e62542c5" not in started_ids
        # No containers stopped
        assert not any(e.get("event") == "docker_stop" for e in events)
        # No compose run
        assert not any(e.get("event") == "compose_run" for e in events)

    elif case == "ambiguous_live":
        assert proc.returncode != 0, f"Expected ambiguous live identity to fail closed, got 0:\n{proc.stdout}"
        assert "steady_scheduler_deploy_values_missing" in proc.stderr
        assert not output_file.exists() or json.loads(output_file.read_text()).get("status") != "completed"

        # No containers stopped
        assert not any(e.get("event") == "docker_stop" for e in events)
        # No containers started
        assert not any(e.get("event") == "docker_start" for e in events)
        # No compose run
        assert not any(e.get("event") == "compose_run" for e in events)


def test_workflow_dispatches_taifex_ssf_contracts_after_twse_with_separate_connector_and_hosts():
    data = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    steps = data["jobs"]["refresh"]["steps"]
    by_id = {step.get("id"): (idx, step) for idx, step in enumerate(steps) if step.get("id")}

    assert "refresh" in by_id, "Missing TWSE refresh step id=refresh"
    assert "taifex_refresh" in by_id, "Missing TAIFEX refresh step id=taifex_refresh"
    twse_idx, twse_step = by_id["refresh"]
    taifex_idx, taifex_step = by_id["taifex_refresh"]
    assert taifex_idx < twse_idx, "TAIFEX contract refresh must run before TWSE market refresh"
    twse_if = twse_step.get("if") or ""
    # Only the post-publication FinMind polling crons may skip TWSE; never a prior-step outcome.
    assert "steps." not in twse_if and "failure()" not in twse_if, "TWSE market refresh must never be prevented by a TAIFEX failure"
    assert not twse_if or "github.event.schedule" in twse_if

    assert twse_step["env"]["SOURCE_INGEST_BOUNDED_CONNECTOR_ID"] == "tw-twse-tpex-official-market"
    assert twse_step["env"]["SOURCE_INGEST_BOUNDED_DATASET"] == "tw_price_daily"
    assert twse_step["env"]["PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS"] == "openapi.twse.com.tw,www.tpex.org.tw"

    assert taifex_step["env"]["SOURCE_INGEST_BOUNDED_CONNECTOR_ID"] == "tw-taifex-futures-options-chip"
    assert taifex_step["env"]["SOURCE_INGEST_BOUNDED_DATASET"] == "taifex_ssf_contracts"
    assert taifex_step["env"]["PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS"] == "openapi.taifex.com.tw,www.taifex.com.tw"
    assert taifex_step.get("continue-on-error") is True
    assert "--non-blocking" in taifex_step["run"]
    assert "--connector-id tw-taifex-futures-options-chip" in taifex_step["run"]
    assert "--allowed-hosts openapi.taifex.com.tw,www.taifex.com.tw" in taifex_step["run"]
    assert "--dataset taifex_ssf_contracts" in taifex_step["run"]

    stop_step = next(s for s in steps if s.get("name") == "Stop heartbeat and release lease")
    assert stop_step["env"]["REFRESH_OUTCOME"] == "${{ steps.refresh.outcome }}"
    assert stop_step["env"]["TAIFEX_REFRESH_OUTCOME"] == "${{ steps.taifex_refresh.outcome }}"
    assert '"${REFRESH_OUTCOME}" =~ ^(success|skipped)$ && "${TAIFEX_REFRESH_OUTCOME}" =~ ^(success|skipped)$' in stop_step["run"]


def test_refresh_entrypoint_taifex_connector_runs_bounded_pull_and_restores_egress_deny(tmp_path: Path):
    initial_env = [
        "PANTHEON_EXTERNAL_EGRESS=deny",
        "PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS=",
        "PORT=8097",
        "DATABASE_URL=postgresql://pantheon_app:pantheon_app@postgres:5432/pantheon",
    ]
    initial_state = {
        "image_id": "sha256:" + "a" * 64,
        "compose_image_id": "a" * 64,
        "container_env": initial_env,
    }
    bin_dir, _state_file, events_file, output_file, port = _setup_refresh_stub_docker(tmp_path, initial_state)

    test_env = dict(os.environ)
    test_env["PATH"] = f"{bin_dir}:{os.environ['PATH']}"
    test_env["SOURCE_INGEST_API_URL"] = f"http://127.0.0.1:{port}"
    test_env["SOURCE_INGEST_BOUNDED_RUN_TIMEOUT_SECONDS"] = "30"
    test_env["PANTHEON_REMOTE_DIR"] = str(ROOT)

    proc = subprocess.run(
        [
            "bash",
            str(_deploy_script_without_readback(tmp_path)),
            "--refresh-only",
            "--force",
            "--connector-id",
            "tw-taifex-futures-options-chip",
            "--allowed-hosts",
            "openapi.taifex.com.tw,www.taifex.com.tw",
            "--dataset",
            "taifex_ssf_contracts",
            "--output",
            str(output_file),
        ],
        env=test_env,
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert proc.returncode == 0, f"TAIFEX refresh failed: {proc.stderr}\n{proc.stdout}"
    assert output_file.exists()
    out_data = json.loads(output_file.read_text(encoding="utf-8"))
    assert out_data.get("status") == "completed"

    events = [json.loads(line) for line in events_file.read_text(encoding="utf-8").splitlines() if line]
    bounded_up = next(
        e
        for e in events
        if e.get("event") == "compose_up"
        and "source-ingest" in e.get("services", [])
        and e["env"].get("PANTHEON_EXTERNAL_EGRESS") == "allowlist"
    )
    assert bounded_up["env"]["PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS"] == "openapi.taifex.com.tw,www.taifex.com.tw"
    assert bounded_up["env"]["SOURCE_INGEST_BOUNDED_DATASET"] == "taifex_ssf_contracts"

    runs = [e for e in events if e.get("event") == "compose_run"]
    assert len(runs) == 2
    scheduler_run = next(r for r in runs if "source-ingest-scheduler" in r["args"])
    assert "SOURCE_INGEST_BOUNDED_DATASET=taifex_ssf_contracts" in scheduler_run["args"]

    recorded_state = json.loads(_state_file.read_text(encoding="utf-8"))
    configured_conns = recorded_state.get("configured_connectors") or []
    assert len(configured_conns) == 1
    conn_payload = configured_conns[0]["connector"]
    fetch_payload = configured_conns[0]["fetch"]
    assert conn_payload["connector_id"] == "tw-taifex-futures-options-chip"
    assert fetch_payload["adapter_config"]["dataset"] == "taifex_ssf_contracts"
    assert fetch_payload["request"]["dataset"] == "taifex_ssf_contracts"
    assert fetch_payload["adapter_config"]["contracts"] == ["2330"]

    active_schedules = recorded_state.get("active_universe_schedules") or []
    assert len(active_schedules) == 1
    assert active_schedules[0]["enqueue"] is True
    assert active_schedules[0]["rules"][0]["connector_id"] == "tw-taifex-futures-options-chip"
    assert active_schedules[0]["rules"][0]["dataset"] == "taifex_ssf_contracts"
    assert [m["symbol"] for m in active_schedules[0]["members"]] == ["2330"]

    # Verify container ID was re-resolved after recreation and desired_state.json was written to recreated cid
    assert recorded_state.get("written_desired_state_cid") == "cid-source-ingest-recreated"
    written_desired = recorded_state.get("written_desired_state") or {}
    assert any(
        r.get("dataset") == "taifex_ssf_contracts"
        and r.get("connector_candidates") == ["tw-taifex-futures-options-chip"]
        for r in (written_desired.get("personas") or [{}])[0].get("required_data_sources", [])
    )
    exec_events = [e for e in events if e.get("event") == "docker_exec"]
    assert len(exec_events) == 2
    assert all(e.get("cid") == "cid-source-ingest-recreated" for e in exec_events)

    restore_up = next(
        e
        for e in events
        if e.get("event") == "compose_up"
        and "source-ingest" in e.get("services", [])
        and e["env"].get("PANTHEON_EXTERNAL_EGRESS") == "deny"
    )
    assert restore_up["env"]["PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS"] == ""
    assert "SOURCE_INGEST_BOUNDED_DATASET" not in restore_up["env"]
    assert "SOURCE_INGEST_CONTROLLER_FORCE_CONNECTOR_IDS" not in restore_up["env"]
    assert "SOURCE_INGEST_CONTROLLER_EXCLUSIVE_CONNECTOR_IDS" not in restore_up["env"]


@pytest.mark.parametrize(
    ("failure_flag", "expected_error"),
    [
        ("fail_token_exec", "failed to read controller_token from running source-ingest container cid-source-ingest-recreated"),
        ("empty_token_exec", "empty controller_token for running source-ingest container cid-source-ingest-recreated"),
        ("fail_desired_state_exec", "failed to write bounded desired_state.json into source-ingest container cid-source-ingest-recreated"),
    ],
)
def test_refresh_entrypoint_taifex_fails_closed_on_token_or_desired_state_exec_failure(
    tmp_path: Path, failure_flag: str, expected_error: str
):
    initial_state = {
        "image_id": "sha256:" + "a" * 64,
        "compose_image_id": "a" * 64,
        "container_env": [
            "PANTHEON_EXTERNAL_EGRESS=deny",
            "PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS=",
            "PORT=8097",
        ],
        failure_flag: True,
    }
    bin_dir, _state_file, events_file, output_file, port = _setup_refresh_stub_docker(tmp_path, initial_state)

    test_env = dict(os.environ)
    test_env["PATH"] = f"{bin_dir}:{os.environ['PATH']}"
    test_env["SOURCE_INGEST_API_URL"] = f"http://127.0.0.1:{port}"
    test_env["SOURCE_INGEST_BOUNDED_RUN_TIMEOUT_SECONDS"] = "30"
    test_env["PANTHEON_REMOTE_DIR"] = str(ROOT)
    test_env.pop("SOURCE_INGEST_CONTROLLER_TOKEN", None)
    test_env.pop("SOURCE_INGEST_CONTROLLER_TOKEN_FILE", None)

    proc = subprocess.run(
        [
            "bash",
            str(_deploy_script_without_readback(tmp_path)),
            "--refresh-only",
            "--force",
            "--connector-id",
            "tw-taifex-futures-options-chip",
            "--allowed-hosts",
            "openapi.taifex.com.tw,www.taifex.com.tw",
            "--dataset",
            "taifex_ssf_contracts",
            "--output",
            str(output_file),
        ],
        env=test_env,
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert proc.returncode != 0, f"Expected failure for {failure_flag}, got 0:\n{proc.stdout}"
    assert expected_error in proc.stderr
    assert "prepared bounded source refresh dataset" not in proc.stdout
    assert not output_file.exists() or json.loads(output_file.read_text(encoding="utf-8")).get("status") != "completed"
    events = [json.loads(line) for line in events_file.read_text(encoding="utf-8").splitlines() if line]
    assert not any(e.get("event") == "compose_run" for e in events)
    assert any(
        e.get("event") == "compose_up"
        and "source-ingest" in e.get("services", [])
        and e["env"].get("PANTHEON_EXTERNAL_EGRESS") == "deny"
        for e in events
    )


def test_refresh_entrypoint_taifex_non_force_proceeds_when_twse_fresh_and_enforces_ssf_readback(tmp_path: Path):
    from services.source_ingestion.connectors.taiwan_official import governed_taiwan_calendar_evidence

    fresh_twse_snapshot = {
        "snapshot_id": "snap-2026-11-06",
        "event_time": "2026-11-06T06:30:00Z",
        "observed_at": "2026-11-06T07:05:00Z",
        "calendar_evidence": governed_taiwan_calendar_evidence(venue="TWSE", trade_date="2026-11-06"),
        "lineage": {
            "source_ids": ["tw-official:tw_price_daily:TWSE:2330:receipt-2026-11-06"],
            "connector_ids": ["tw-twse-tpex-official-market"],
        },
    }
    initial_state = {
        "image_id": "sha256:" + "a" * 64,
        "compose_image_id": "a" * 64,
        "container_env": [
            "PANTHEON_EXTERNAL_EGRESS=deny",
            "PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS=",
            "PORT=8097",
            "DATABASE_URL=postgresql://pantheon_app:pantheon_app@postgres:5432/pantheon",
        ],
        "snapshots": {"2330.TW": fresh_twse_snapshot},
        "receipts": [],
        "readback_connectors": [],
        "post_run_receipts": [{
            "connector_id": "tw-taifex-futures-options-chip",
            "status": "completed",
            "typed_failure": None,
            "source_timestamp": "2026-11-06T07:15:00Z",
            "source_timestamp_status": "valid",
            "created_at": "2030-01-01T00:00:00Z",
            "finished_at": "2030-01-01T00:00:00Z",
            "ingest_run_id": "run-taifex-ssf-1",
        }],
        "post_run_readback_connectors": [{
            "connector_id": "tw-taifex-futures-options-chip",
            "desired_state": {"dataset": "taifex_ssf_contracts"},
            "freshness": {
                "latest_receipt": {"ingest_run_id": "run-taifex-ssf-1"},
                "source_timestamp_status": "valid",
                "last_success_at": "2030-01-01T00:00:00Z",
            },
            "latest_source_record": {
                "source_id": "tw-taifex:taifex_ssf_contracts:QFF:202611:abc123",
                "status": "normalized",
                "metadata": {
                    # Verifier binds to the latest closed session at deploy start (real clock).
                    "date": _latest_closed_taipei_session(datetime.now(timezone.utc)),
                    "underlying_code": "2330",
                    "dataset": "taifex_ssf_contracts",
                    "schema_hash": "tw_taifex_ssf_contracts.v1",
                },
                "provenance": {
                    "source_ingest_run_id": "run-taifex-ssf-1",
                    "dataset": "taifex_ssf_contracts",
                    "schema_hash": "tw_taifex_ssf_contracts.v1",
                },
            },
        }],
    }
    bin_dir, state_file, events_file, output_file, port = _setup_refresh_stub_docker(tmp_path, initial_state)

    test_env = dict(os.environ)
    test_env["PATH"] = f"{bin_dir}:{os.environ['PATH']}"
    test_env["SOURCE_INGEST_API_URL"] = f"http://127.0.0.1:{port}"
    test_env["SOURCE_INGEST_BOUNDED_RUN_TIMEOUT_SECONDS"] = "30"
    test_env["PANTHEON_REMOTE_DIR"] = str(ROOT)
    test_env["PANTHEON_REFRESH_PREFLIGHT_NOW"] = "2026-11-06T08:00:00Z"

    # Run WITHOUT --force and WITH real verify_bounded_source_refresh_readback
    proc = subprocess.run(
        [
            "bash",
            str(DEPLOY_SCRIPT),
            "--refresh-only",
            "--connector-id",
            "tw-taifex-futures-options-chip",
            "--allowed-hosts",
            "openapi.taifex.com.tw,www.taifex.com.tw",
            "--dataset",
            "taifex_ssf_contracts",
            "--output",
            str(output_file),
        ],
        env=test_env,
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert proc.returncode == 0, f"Non-force TAIFEX refresh with real readback failed: {proc.stderr}\n{proc.stdout}"
    out_data = json.loads(output_file.read_text(encoding="utf-8"))
    assert out_data.get("status") == "completed"

    # Now verify that if post-run readback returns taifex_futures_chip instead of taifex_ssf_contracts, readback verification fails closed
    bad_state = json.loads(state_file.read_text(encoding="utf-8"))
    bad_state["receipts"] = []
    bad_state["readback_connectors"] = []
    bad_state["post_run_readback_connectors"] = [{
        "connector_id": "tw-taifex-futures-options-chip",
        "desired_state": {"dataset": "taifex_futures_chip"},
        "freshness": {
            "latest_receipt": {"ingest_run_id": "run-taifex-ssf-1"},
            "source_timestamp_status": "valid",
        },
        "latest_source_record": {
            "source_id": "tw-taifex:taifex_futures_chip:TX:foreign_investors:abc123",
            "status": "normalized",
            "metadata": {
                "date": "2026-11-06",
                "dataset": "taifex_futures_chip",
                "schema_hash": "tw_taifex_futures_chip.v1",
            },
            "provenance": {
                "source_ingest_run_id": "run-taifex-ssf-1",
                "dataset": "taifex_futures_chip",
                "schema_hash": "tw_taifex_futures_chip.v1",
            },
        },
    }]
    state_file.write_text(json.dumps(bad_state), encoding="utf-8")
    bad_output = tmp_path / "bad_output.json"
    proc_bad = subprocess.run(
        [
            "bash",
            str(DEPLOY_SCRIPT),
            "--refresh-only",
            "--connector-id",
            "tw-taifex-futures-options-chip",
            "--allowed-hosts",
            "openapi.taifex.com.tw,www.taifex.com.tw",
            "--dataset",
            "taifex_ssf_contracts",
            "--output",
            str(bad_output),
        ],
        env=test_env,
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert proc_bad.returncode != 0, "Expected TAIFEX refresh to fail closed when readback dataset is taifex_futures_chip"
    assert "taifex_ssf_contracts" in proc_bad.stderr



@pytest.mark.parametrize(
    "bad_hosts",
    [
        "openapi.taifex.com.tw",
        "www.taifex.com.tw",
        "openapi.twse.com.tw,www.twse.com.tw,www.tpex.org.tw,openapi.taifex.com.tw,www.taifex.com.tw",
        "*.taifex.com.tw,www.taifex.com.tw",
    ],
)
def test_refresh_entrypoint_taifex_rejects_merged_or_wrong_hosts(tmp_path: Path, bad_hosts: str):
    initial_state = {
        "image_id": "sha256:" + "a" * 64,
        "compose_image_id": "a" * 64,
        "container_env": ["PANTHEON_EXTERNAL_EGRESS=deny"],
    }
    bin_dir, _state_file, events_file, output_file, port = _setup_refresh_stub_docker(tmp_path, initial_state)

    test_env = dict(os.environ)
    test_env["PATH"] = f"{bin_dir}:{os.environ['PATH']}"
    test_env["SOURCE_INGEST_API_URL"] = f"http://127.0.0.1:{port}"
    test_env["SOURCE_INGEST_BOUNDED_RUN_TIMEOUT_SECONDS"] = "30"
    test_env["PANTHEON_REMOTE_DIR"] = str(ROOT)

    proc = subprocess.run(
        [
            "bash",
            str(_deploy_script_without_readback(tmp_path)),
            "--refresh-only",
            "--force",
            "--connector-id",
            "tw-taifex-futures-options-chip",
            "--allowed-hosts",
            bad_hosts,
            "--dataset",
            "taifex_ssf_contracts",
            "--output",
            str(output_file),
        ],
        env=test_env,
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert proc.returncode != 0, f"Expected TAIFEX refresh to reject hosts {bad_hosts}"
    events = [json.loads(line) for line in events_file.read_text(encoding="utf-8").splitlines() if line] if events_file.exists() else []
    assert not any(
        e.get("event") == "compose_up" and e.get("env", {}).get("PANTHEON_EXTERNAL_EGRESS") == "allowlist"
        for e in events
    )


def test_refresh_entrypoint_taifex_weekend_skip_records_outcome_without_container_mutation(tmp_path: Path):
    from services.source_ingestion.connectors.taiwan_official import governed_taiwan_calendar_evidence

    fresh_twse_snapshot = {
        "snapshot_id": "snap-2026-11-06",
        "event_time": "2026-11-06T06:30:00Z",
        "observed_at": "2026-11-06T07:05:00Z",
        "calendar_evidence": governed_taiwan_calendar_evidence(venue="TWSE", trade_date="2026-11-06"),
        "lineage": {
            "source_ids": ["tw-official:tw_price_daily:TWSE:2330:receipt-2026-11-06"],
            "connector_ids": ["tw-twse-tpex-official-market"],
        },
    }
    initial_state = {
        "image_id": "sha256:" + "a" * 64,
        "compose_image_id": "a" * 64,
        "container_env": ["PANTHEON_EXTERNAL_EGRESS=deny"],
        "snapshots": {"2330.TW": fresh_twse_snapshot},
        "readback_connectors": [],
    }
    bin_dir, _state_file, events_file, output_file, port = _setup_refresh_stub_docker(tmp_path, initial_state)

    test_env = dict(os.environ)
    test_env["PATH"] = f"{bin_dir}:{os.environ['PATH']}"
    test_env["SOURCE_INGEST_API_URL"] = f"http://127.0.0.1:{port}"
    test_env["SOURCE_INGEST_BOUNDED_RUN_TIMEOUT_SECONDS"] = "30"
    test_env["PANTHEON_REMOTE_DIR"] = str(ROOT)
    # Sunday 2026-11-08 22:21:20 Asia/Taipei (run 42488948670)
    test_env["PANTHEON_REFRESH_PREFLIGHT_NOW"] = "2026-11-08T14:21:20Z"

    proc = subprocess.run(
        [
            "bash",
            str(DEPLOY_SCRIPT),
            "--refresh-only",
            "--non-blocking",
            "--connector-id",
            "tw-taifex-futures-options-chip",
            "--allowed-hosts",
            "openapi.taifex.com.tw,www.taifex.com.tw",
            "--dataset",
            "taifex_ssf_contracts",
            "--output",
            str(output_file),
        ],
        env=test_env,
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert proc.returncode == 0, f"Expected weekend TAIFEX skip to exit 0, got:\n{proc.stderr}"
    assert output_file.exists()
    out_data = json.loads(output_file.read_text(encoding="utf-8"))
    assert out_data["status"] == "skipped"
    assert out_data["reason"] == "weekend"
    assert out_data["log"] == ""
    assert out_data["connector_id"] == "tw-taifex-futures-options-chip"
    assert out_data["dataset"] == "taifex_ssf_contracts"

    events = [json.loads(line) for line in events_file.read_text(encoding="utf-8").splitlines() if line] if events_file.exists() else []
    assert not any(e.get("event") in ("compose_up", "compose_run", "docker_stop") for e in events)


def test_refresh_entrypoint_taifex_non_blocking_failure_continues_and_records_redacted_log_while_twse_fails_closed(
    tmp_path: Path,
):
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJkZXYifQ.c2lnbmF0dXJlLXZhbHVl"
    initial_state = {
        "image_id": "sha256:" + "a" * 64,
        "compose_image_id": "a" * 64,
        "container_env": [
            "PANTHEON_EXTERNAL_EGRESS=deny",
            "PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS=",
            "PORT=8097",
        ],
        "dlq_entries": [dict(CANONICAL_TW_PENDING_DLQ_ENTRIES[0])],
        "container_logs": (
            "ControllerTickError: desired-state reconcile failed closed: conflicts=1 unsupported=0\n"
            f"Authorization: Bearer secret-taifex-token-999 jwt={jwt}\n"
        ),
    }
    bin_dir, _state_file, events_file, output_file, port = _setup_refresh_stub_docker(tmp_path, initial_state)

    test_env = dict(os.environ)
    test_env["PATH"] = f"{bin_dir}:{os.environ['PATH']}"
    test_env["SOURCE_INGEST_API_URL"] = f"http://127.0.0.1:{port}"
    test_env["SOURCE_INGEST_BOUNDED_RUN_TIMEOUT_SECONDS"] = "30"
    test_env["PANTHEON_REMOTE_DIR"] = str(ROOT)

    patched_script = _deploy_script_without_readback(tmp_path)

    # 1. TAIFEX with --non-blocking continues (exit 0), records status/reason/redacted log, and restores egress=deny
    proc_taifex = subprocess.run(
        [
            "bash",
            str(patched_script),
            "--refresh-only",
            "--non-blocking",
            "--connector-id",
            "tw-taifex-futures-options-chip",
            "--allowed-hosts",
            "openapi.taifex.com.tw,www.taifex.com.tw",
            "--dataset",
            "taifex_ssf_contracts",
            "--output",
            str(output_file),
        ],
        env=test_env,
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert proc_taifex.returncode == 0, f"Expected non-blocking TAIFEX failure to exit 0, got {proc_taifex.returncode}:\n{proc_taifex.stderr}"
    assert output_file.exists()
    taifex_outcome = json.loads(output_file.read_text(encoding="utf-8"))
    assert taifex_outcome["status"] == "failed"
    assert "bounded source refresh service source-ingest-scheduler exited with code 1" in taifex_outcome["reason"]
    assert "ControllerTickError: desired-state reconcile failed closed: conflicts=1 unsupported=0" in taifex_outcome["log"]
    assert "Bearer [REDACTED]" in taifex_outcome["log"]
    assert "[REDACTED_JWT]" in taifex_outcome["log"]
    assert "secret-taifex-token-999" not in taifex_outcome["log"]
    assert "eyJhbGciOiJIUzI1NiJ9" not in taifex_outcome["log"]

    events = [json.loads(line) for line in events_file.read_text(encoding="utf-8").splitlines() if line]
    bounded_up = next(
        e
        for e in events
        if e.get("event") == "compose_up"
        and "source-ingest" in e.get("services", [])
        and e["env"].get("PANTHEON_EXTERNAL_EGRESS") == "allowlist"
    )
    assert bounded_up["env"]["PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS"] == "openapi.taifex.com.tw,www.taifex.com.tw"
    restore_ups = [
        e
        for e in events
        if e.get("event") == "compose_up"
        and "source-ingest" in e.get("services", [])
        and e["env"].get("PANTHEON_EXTERNAL_EGRESS") == "deny"
    ]
    assert restore_ups, "TAIFEX failure must still restore external egress to deny"
    assert restore_ups[-1]["env"].get("SOURCE_INGEST_CONTROLLER_MODE") == "reconcile_only"
    assert restore_ups[-1]["env"].get("PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS") == ""

    # 2. TWSE refresh failure on the same state keeps today's fail-closed behaviour (returncode != 0)
    twse_output = tmp_path / "twse_output.json"
    proc_twse = subprocess.run(
        [
            "bash",
            str(patched_script),
            "--refresh-only",
            "--output",
            str(twse_output),
        ],
        env=test_env,
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert proc_twse.returncode != 0, "TWSE refresh failure must remain fail-closed"


def test_workflow_finmind_broker_refresh_is_bounded_nonblocking_and_secret_safe():
    data = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    steps = data["jobs"]["refresh"]["steps"]
    step = next(s for s in steps if s.get("id") == "finmind_refresh")
    assert step.get("continue-on-error") is True
    assert step["env"]["SOURCE_INGEST_BOUNDED_CONNECTOR_ID"] == "tw-finmind-broker-daily-report"
    assert step["env"]["PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS"] == "api.finmindtrade.com,storage.googleapis.com"
    assert step["env"]["FINMIND_API_TOKEN"] == "${{ secrets.FINMIND_API_TOKEN }}"
    assert "--allowed-hosts api.finmindtrade.com,storage.googleapis.com" in step["run"]
    assert "missing_credential" in step["run"]
    assert "${#FINMIND_API_TOKEN}" in step["run"]
    assert 'echo "${FINMIND_API_TOKEN}"' not in step["run"]
    twse_idx = next(i for i, s in enumerate(steps) if s.get("id") == "refresh")
    assert steps.index(step) < twse_idx


def test_finmind_token_flows_through_compose_and_deploy_script():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert compose.count("FINMIND_API_TOKEN: ${FINMIND_API_TOKEN:-}") == 2
    script = (ROOT / "scripts/deploy_nonprod_vm.sh").read_text(encoding="utf-8")
    assert 'FINMIND_API_TOKEN=$(shell_quote "${FINMIND_API_TOKEN:-}")' in script
    deploy_wf = (ROOT / ".github/workflows/nonprod-deploy.yml").read_text(encoding="utf-8")
    assert "FINMIND_API_TOKEN: ${{ secrets.FINMIND_API_TOKEN }}" in deploy_wf


@pytest.mark.parametrize(
    "bad_hosts",
    ["api.finmindtrade.com", "storage.googleapis.com", "api.finmindtrade.com,storage.googleapis.com,openapi.twse.com.tw"],
)
def test_refresh_entrypoint_finmind_rejects_wrong_hosts(tmp_path: Path, bad_hosts: str):
    initial_state = {
        "image_id": "sha256:" + "a" * 64,
        "compose_image_id": "a" * 64,
        "container_env": ["PANTHEON_EXTERNAL_EGRESS=deny"],
    }
    bin_dir, _s, events_file, output_file, port = _setup_refresh_stub_docker(tmp_path, initial_state)
    test_env = dict(os.environ)
    test_env["PATH"] = f"{bin_dir}:{os.environ['PATH']}"
    test_env["SOURCE_INGEST_API_URL"] = f"http://127.0.0.1:{port}"
    test_env["SOURCE_INGEST_BOUNDED_RUN_TIMEOUT_SECONDS"] = "30"
    test_env["PANTHEON_REMOTE_DIR"] = str(ROOT)
    proc = subprocess.run(
        ["bash", str(_deploy_script_without_readback(tmp_path)), "--refresh-only", "--force",
         "--connector-id", "tw-finmind-broker-daily-report", "--allowed-hosts", bad_hosts,
         "--dataset", "tw_broker_top", "--output", str(output_file)],
        env=test_env, capture_output=True, text=True, cwd=str(ROOT),
    )
    assert proc.returncode != 0
    assert "unsupported bounded source refresh connector" not in proc.stderr + proc.stdout
    events = [json.loads(l) for l in events_file.read_text().splitlines() if l] if events_file.exists() else []
    assert not any(e.get("event") == "compose_up" and e.get("env", {}).get("PANTHEON_EXTERNAL_EGRESS") == "allowlist" for e in events)


def test_finmind_token_reaches_both_remote_prefixes_and_bounded_compose_env():
    script = (ROOT / "scripts/deploy_nonprod_vm.sh").read_text(encoding="utf-8")
    refresh_part, normal_part = script.split('    command_prefix+=" bash -s"\n  else\n', 1)
    assert 'FINMIND_API_TOKEN=$(shell_quote "${FINMIND_API_TOKEN:-}")' in refresh_part
    assert 'FINMIND_API_TOKEN=$(shell_quote "${FINMIND_API_TOKEN:-}")' in normal_part.split("assert_", 1)[0][:6000]
    # The supplied secret overrides a stale/empty recorded container value for the bounded window.
    assert 'env["FINMIND_API_TOKEN"] = finmind_token' in script


def test_finmind_dataset_is_tw_broker_top_everywhere():
    script = (ROOT / "scripts/deploy_nonprod_vm.sh").read_text(encoding="utf-8")
    wf = (ROOT / ".github/workflows/dev-tw-market-refresh.yml").read_text(encoding="utf-8")
    assert "tw_broker_daily_report" not in script + wf
    assert 'dataset != "tw_broker_top"' in script
    assert "SOURCE_INGEST_BOUNDED_DATASET: tw_broker_top" in wf


def test_finmind_prepare_and_missing_secret_paths_exist():
    script = (ROOT / "scripts/deploy_nonprod_vm.sh").read_text(encoding="utf-8")
    assert "FinMindTaiwanBrokerDailyReportAdapter(connector_id=connector_id)" in script
    assert '=~ ^(tw-taifex-futures-options-chip|tw-finmind-broker-daily-report)$ ]]; then\n    extra_bounded_env_args' in script
    guard = script.index('== "tw-finmind-broker-daily-report" && -z "${FINMIND_API_TOKEN:-}"')
    assert guard < script.index("resolve_bounded_source_refresh_active_symbols\n  preflight_output=")
    assert '"skipped" "missing_credential"' in script[guard:guard + 600]


# --- COLLECTOR-STEADY-PULL-RESTORE-20261112: restore keeps the steady Collector pull ---

_COLLECTOR_TEST_HOST = "collector.example.test"


def _run_refresh_with_steady_mode(
    tmp_path: Path, steady_mode_env: list[str], *, fail_readback: bool, collector_host: str | None = _COLLECTOR_TEST_HOST
) -> tuple[subprocess.CompletedProcess[str], list[dict[str, Any]]]:
    initial_state: dict[str, Any] = {
        "image_id": "sha256:" + "a" * 64,
        "compose_image_id": "sha256:" + "a" * 64,
        "container_env": ["PANTHEON_EXTERNAL_EGRESS=deny"],
        "steady_lease_env": steady_mode_env,
    }
    if fail_readback:
        initial_state["built_projector_image_id"] = "sha256:0fedd6c77271dc29bf6425461cac284df85a6454d656e363d67646ec9ee24b2e"
        initial_state["projector_image_sha"] = "abc123"
        initial_state["agora_projection"] = {
            "row1": {
                "connectorId": "tw-twse-tpex-official-market",
                "ingestRunId": "ingest-e53a86463995",
                "sourceId": "tw-official:tw_price_daily:TWSE:2330:90e323e6754e2e9e",
            }
        }
    bin_dir, _state_file, events_file, output_file, port = _setup_refresh_stub_docker(tmp_path, initial_state)
    test_env = dict(os.environ)
    test_env["PATH"] = f"{bin_dir}:{os.environ['PATH']}"
    test_env["SOURCE_INGEST_API_URL"] = f"http://127.0.0.1:{port}"
    test_env["SOURCE_INGEST_BOUNDED_RUN_TIMEOUT_SECONDS"] = "30"
    test_env["PANTHEON_REMOTE_DIR"] = str(ROOT)
    if collector_host:
        test_env["PANTHEON_COLLECTOR_HOST"] = collector_host
    else:
        test_env.pop("PANTHEON_COLLECTOR_HOST", None)
    script = _deploy_script_with_readback(tmp_path) if fail_readback else _deploy_script_without_readback(tmp_path)
    proc = subprocess.run(
        ["bash", str(script), "--refresh-only", "--force", "--output", str(output_file)],
        env=test_env, capture_output=True, text=True, cwd=str(ROOT),
    )
    events = [json.loads(line) for line in events_file.read_text(encoding="utf-8").splitlines() if line]
    return proc, events


def _last_restore_up(events: list[dict[str, Any]], egress: str) -> dict[str, Any]:
    ups = [
        e for e in events
        if e.get("event") == "compose_up"
        and "source-ingest" in e.get("services", [])
        and e["env"].get("PANTHEON_EXTERNAL_EGRESS") == egress
    ]
    assert ups, "no restore compose up observed"
    return ups[-1]


@pytest.mark.parametrize("fail_readback", [False, True])
def test_bounded_refresh_restore_keeps_configured_steady_collector_pull(tmp_path: Path, fail_readback: bool):
    proc, events = _run_refresh_with_steady_mode(
        tmp_path, ["SOURCE_INGEST_CONTROLLER_MODE=collector_steady_pull", "SOURCE_INGEST_CONTROLLER_LEASE_SECONDS=1", "SOURCE_INGEST_CONTROLLER_INTERVAL_SECONDS=1"], fail_readback=fail_readback
    )
    assert (proc.returncode != 0) is fail_readback, f"{proc.stdout}\n{proc.stderr}"
    restore = _last_restore_up(events, "allowlist")
    assert restore["env"]["PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS"] == _COLLECTOR_TEST_HOST
    assert restore["env"]["SOURCE_INGEST_CONTROLLER_MODE"] == "collector_steady_pull"
    assert "to collector_steady_pull" in proc.stdout + proc.stderr


@pytest.mark.parametrize("fail_readback", [False, True])
def test_bounded_refresh_restore_never_invents_steady_pull(tmp_path: Path, fail_readback: bool):
    # Prior steady posture was reconcile_only: restore must not widen it.
    proc, events = _run_refresh_with_steady_mode(
        tmp_path, ["SOURCE_INGEST_CONTROLLER_INTERVAL_SECONDS=1", "SOURCE_INGEST_CONTROLLER_LEASE_SECONDS=1"], fail_readback=fail_readback
    )
    restore = _last_restore_up(events, "allowlist")
    assert restore["env"]["SOURCE_INGEST_CONTROLLER_MODE"] == "reconcile_only"


def test_bounded_refresh_restore_drops_steady_pull_without_collector_host(tmp_path: Path):
    proc, events = _run_refresh_with_steady_mode(
        tmp_path, ["SOURCE_INGEST_CONTROLLER_MODE=collector_steady_pull", "SOURCE_INGEST_CONTROLLER_LEASE_SECONDS=1", "SOURCE_INGEST_CONTROLLER_INTERVAL_SECONDS=1"], fail_readback=False, collector_host=None
    )
    restore = _last_restore_up(events, "deny")
    assert restore["env"]["SOURCE_INGEST_CONTROLLER_MODE"] == "reconcile_only"


@pytest.mark.parametrize(("sig_name", "expected_rc"), [("INT", 130), ("TERM", 143)])
def test_bounded_refresh_terminating_signal_restores_and_exits_nonzero(tmp_path: Path, sig_name: str, expected_rc: int):
    # COLLECTOR-STEADY-PULL-RESTORE-20261112 (Codex2 P1): a terminating INT/TERM must
    # restore the steady Collector pull, exit with 128+signal, and never go on to
    # launch the projector or write a completed outcome.
    initial_state = {
        "image_id": "sha256:" + "a" * 64,
        "compose_image_id": "sha256:" + "a" * 64,
        "container_env": ["PANTHEON_EXTERNAL_EGRESS=deny"],
        "steady_lease_env": [
            "SOURCE_INGEST_CONTROLLER_MODE=collector_steady_pull",
            "SOURCE_INGEST_CONTROLLER_LEASE_SECONDS=1",
            "SOURCE_INGEST_CONTROLLER_INTERVAL_SECONDS=1",
        ],
    }
    bin_dir, _state_file, events_file, output_file, port = _setup_refresh_stub_docker(tmp_path, initial_state)
    docker = bin_dir / "docker"
    delegate = bin_dir / "original-owned-docker-stub"
    docker.rename(delegate)
    docker.write_text(
        "#!/usr/bin/env python3\n"
        "import os,sys,json,signal\n"
        "from pathlib import Path\n"
        "a=sys.argv[1:]\n"
        'if "run" in a and "--name" in a and "pantheon-bounded-refresh-source-ingest-scheduler" in a:\n'
        f"    Path({repr(str(events_file))}).open('a').write(json.dumps({{'event':'QA_signal_sent'}})+'\\n')\n"
        f"    os.kill(os.getppid(), signal.SIG{sig_name})\n"
        "    sys.exit(0)\n"
        f"os.execv({repr(str(delegate))}, [{repr(str(delegate))}]+a)\n"
    )
    docker.chmod(0o755)
    test_env = dict(os.environ)
    test_env["PATH"] = f"{bin_dir}:{os.environ['PATH']}"
    test_env["SOURCE_INGEST_API_URL"] = f"http://127.0.0.1:{port}"
    test_env["SOURCE_INGEST_BOUNDED_RUN_TIMEOUT_SECONDS"] = "30"
    test_env["PANTHEON_REMOTE_DIR"] = str(ROOT)
    test_env["PANTHEON_COLLECTOR_HOST"] = _COLLECTOR_TEST_HOST
    proc = subprocess.run(
        ["bash", str(_deploy_script_without_readback(tmp_path)), "--refresh-only", "--force", "--output", str(output_file)],
        env=test_env, capture_output=True, text=True, cwd=str(ROOT),
    )
    assert proc.returncode == expected_rc, f"{proc.returncode}\n{proc.stdout}\n{proc.stderr}"
    events = [json.loads(line) for line in events_file.read_text(encoding="utf-8").splitlines() if line]
    assert any(e.get("event") == "QA_signal_sent" for e in events)
    restore = _last_restore_up(events, "allowlist")
    assert restore["env"]["SOURCE_INGEST_CONTROLLER_MODE"] == "collector_steady_pull"
    sent = next(i for i, e in enumerate(events) if e.get("event") == "QA_signal_sent")
    assert not any("projector" in json.dumps(e) and e.get("event") in {"docker_run", "run"} for e in events[sent + 1:])
    assert not output_file.exists() or json.loads(output_file.read_text()).get("status") != "completed"


@pytest.mark.parametrize(
    "now,expected",
    [
        ("2026-11-13T02:48:00Z", "2026-11-12"),  # actual 10:48 Taipei Nov13: Nov13 not yet closed
        ("2026-11-13T05:29:00Z", "2026-11-12"),  # 13:29 Taipei, still open
        ("2026-11-13T05:30:00Z", "2026-11-13"),  # 13:30 close
        ("2026-11-16T02:00:00Z", "2026-11-13"),  # Monday morning -> Friday
    ],
)
def test_taifex_bounded_prepare_targets_latest_closed_session(now: str, expected: str) -> None:
    _run_taifex_prepare(now, None, expected)


def test_taifex_bounded_prepare_keeps_session_selected_at_refresh_start_across_cutoff() -> None:
    # Started 13:29:59 Taipei (open Nov13 -> Nov12); preparation runs after 13:30.
    _run_taifex_prepare("2026-11-13T05:31:00Z", "2026-11-13T05:29:59Z", "2026-11-12")
    # Without the carried selection the later wall clock selects Nov13.
    _run_taifex_prepare("2026-11-13T05:31:00Z", None, "2026-11-13")


def _run_taifex_prepare(now: str, selected_at: str | None, expected: str) -> None:
    import http.server
    import threading

    script = (ROOT / "scripts/deploy_nonprod_vm.sh").read_text(encoding="utf-8")
    body = script.split("<<'PREPARE_DATASET_PY'\n", 1)[1].split("\nPREPARE_DATASET_PY", 1)[0]
    posted: list[dict] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            posted.append({"path": self.path, **json.loads(self.rfile.read(int(self.headers["Content-Length"])))})
            raw = b"{}"
            self.send_response(200)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        do_PUT = do_POST

        def log_message(self, *args) -> None:
            pass

    # Isolated test clock inside the child process only; production has no override.
    clock = (
        "import datetime as _dt\n_REAL = _dt.datetime\n"
        f"_FIX = _dt.datetime.fromisoformat({now!r}.replace('Z', '+00:00'))\n"
        "class _D(_dt.datetime):\n    @classmethod\n    def now(cls, tz=None):\n"
        "        return _FIX.astimezone(tz) if tz else _FIX.replace(tzinfo=None)\n"
        "_dt.datetime = _D\n"
    )
    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        proc = subprocess.run(
            ["python3", "-c", f"import sys\n{clock}exec(compile({body!r}, 'prepare', 'exec'))\n",
             "tw-taifex-futures-options-chip", "taifex_ssf_contracts", "2330.TW,0050.TW,2317.TW"],
            cwd=ROOT,
            env={**os.environ, "PYTHONPATH": str(ROOT), "SOURCE_INGEST_API_URL": f"http://127.0.0.1:{server.server_port}",
                 "SOURCE_INGEST_CONTROLLER_TOKEN": "t",
                 **({"SOURCE_INGEST_BOUNDED_SESSION_SELECTED_AT": selected_at} if selected_at else {})},
            capture_output=True, text=True, check=False,
        )
    finally:
        server.shutdown()
    assert proc.returncode == 0, proc.stderr
    schedule = next(item for item in posted if item["path"].endswith("/active-universe/schedule"))
    assert schedule["run_date"] == expected


def _latest_closed_taipei_session(now: datetime) -> str:
    from services.source_ingestion.controller_worker import _latest_eligible_session

    return _latest_eligible_session("tw-taifex-futures-options-chip", now).isoformat()


def _run_embedded_readback_verifier(tmp_path: Path, *, started: str, record_meta: dict, symbols: str = "2330.TW") -> subprocess.CompletedProcess:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    start = script.index("<<'PY'\nimport json\nimport math\n", script.index("verify_bounded_source_refresh_readback() {"))
    body = script[start + len("<<'PY'\n"):].split("\nPY\n", 1)[0]
    run = "run-ssf-1"
    receipt_at = (datetime.fromisoformat(started.replace("Z", "+00:00")) + timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    receipts = {"receipts": [{
        "connector_id": "tw-taifex-futures-options-chip", "status": "completed", "typed_failure": None,
        "source_timestamp": "2026-11-12T07:15:00Z", "source_timestamp_status": "valid",
        "created_at": receipt_at, "finished_at": receipt_at, "ingest_run_id": run,
    }]}
    readback = {"connectors": [{
        "connector_id": "tw-taifex-futures-options-chip",
        "freshness": {"latest_receipt": {"ingest_run_id": run}, "source_timestamp_status": "valid"},
        "latest_source_record": {
            "source_id": "tw-taifex:taifex_ssf_contracts:CDF:202611:abc", "status": "normalized",
            "metadata": {"dataset": "taifex_ssf_contracts", "schema_hash": "tw_taifex_ssf_contracts.v1", **record_meta},
            "provenance": {"source_ingest_run_id": run, "dataset": "taifex_ssf_contracts", "schema_hash": "tw_taifex_ssf_contracts.v1"},
        },
    }]}
    for name, payload in (("receipts", receipts), ("readback", readback), ("projection", {})):
        (tmp_path / f"{name}.json").write_text(json.dumps(payload), encoding="utf-8")
    return subprocess.run(
        ["python3", "-I", "-c", f"import sys\nsys.path.insert(0, {str(ROOT)!r})\nexec(compile({body!r}, 'verify', 'exec'))",
         "tw-taifex-futures-options-chip", started, str(tmp_path / "receipts.json"), str(tmp_path / "readback.json"),
         str(tmp_path / "projection.json"), symbols, "taifex_ssf_contracts"],
        capture_output=True, text=True, check=False,
    )


@pytest.mark.parametrize(
    "started,meta,message",
    [
        ("2026-11-13T02:48:00Z", {"date": "2026-11-12", "underlying_code": "2330"}, None),
        ("2026-11-13T02:48:00Z", {"date": "2026-11-11", "underlying_code": "2330"}, "requested session"),
        ("2026-11-13T02:48:00Z", {"date": "2026-11-13", "underlying_code": "2330"}, "requested session"),
        ("2026-11-13T02:48:00Z", {"date": "2026-11-12", "underlying_code": "9999", "contract": "ZZF"}, "outside active scope"),
        ("2026-11-13T05:30:00Z", {"date": "2026-11-13", "underlying_code": "2330"}, None),
        # Started before the 13:30 close, receipt finished after it: still the Nov12 session.
        ("2026-11-13T05:29:59Z", {"date": "2026-11-12", "underlying_code": "2330"}, None),
        ("2026-11-13T05:29:59Z", {"date": "2026-11-13", "underlying_code": "2330"}, "requested session"),
    ],
)
def test_taifex_terminal_verifier_binds_requested_session_and_scope(tmp_path: Path, started: str, meta: dict, message: str | None) -> None:
    proc = _run_embedded_readback_verifier(tmp_path, started=started, record_meta=meta)
    if message is None:
        assert proc.returncode == 0, proc.stderr
    else:
        assert proc.returncode != 0 and message in proc.stderr, proc.stderr


def test_chip_terminal_verifier_binds_dataset_schema_venue_run_and_source_session(tmp_path):
    from services.source_ingestion.connector_definitions import TW_CHIP_REFRESH_PROFILES
    body = DEPLOY_SCRIPT.read_text().split('verify_bounded_source_refresh_readback() {', 1)[1].split("<<'PY'\n", 1)[1].split('\nPY\n', 1)[0]
    for cid, profile in TW_CHIP_REFRESH_PROFILES.items():
        dataset = profile['dataset']
        prefix = 'tw-taifex' if profile['base'] == 'tw-taifex-futures-options-chip' else 'tw-official'
        record = {'source_id': f'{prefix}:{dataset}:recorded',
                  'metadata': {'dataset': dataset, 'schema_hash': profile['schema_hash'],
                               'date': '2026-11-13', 'venue': profile.get('venues', ['TAIFEX'])[0]},
                  'provenance': {'dataset': dataset, 'source_ingest_run_id': 'chip-run'}}
        receipts = {'receipts': [{'connector_id': cid, 'status': 'completed', 'typed_failure': None,
                                 'source_timestamp': '2026-11-13T10:00:00Z', 'source_timestamp_status': 'valid',
                                 'finished_at': '2026-11-13T11:01:00Z', 'ingest_run_id': 'chip-run'}]}
        (tmp_path / 'receipts.json').write_text(json.dumps(receipts))
        (tmp_path / 'projection.json').write_text('{}')
        cases = [({}, {}, True), ({'dataset': 'other'}, {}, False), ({'schema_hash': 'wrong'}, {}, False),
                 ({'date': ''}, {}, False), ({'date': '2026-11-12'}, {}, False),
                 ({}, {'source_ingest_run_id': 'old-run'}, False)]
        if 'venues' in profile:
            cases.append(({'venue': 'TAIFEX'}, {}, False))
        for meta_patch, provenance_patch, accepted in cases:
            latest = {**record, 'metadata': {**record['metadata'], **meta_patch},
                      'provenance': {**record['provenance'], **provenance_patch}}
            # A record's dataset must agree across provenance and metadata.
            if 'dataset' in meta_patch:
                latest['provenance']['dataset'] = meta_patch['dataset']
            readback = {'connectors': [{'connector_id': cid, 'freshness': {
                'latest_receipt': {'ingest_run_id': 'chip-run'}, 'source_timestamp_status': 'valid', 'status': 'fresh'},
                'latest_source_record': latest}]}
            (tmp_path / 'readback.json').write_text(json.dumps(readback))
            proc = subprocess.run([sys.executable, '-c', body, cid, '2026-11-13T11:00:00Z',
                                   str(tmp_path / 'receipts.json'), str(tmp_path / 'readback.json'),
                                   str(tmp_path / 'projection.json'), '', dataset],
                                  cwd=ROOT, text=True, capture_output=True, timeout=20)
            assert (proc.returncode == 0) is accepted, (cid, meta_patch, proc.stderr)


def test_chip_preflight_does_not_require_equity_price_snapshots():
    from services.source_ingestion.connector_definitions import TW_CHIP_REFRESH_PROFILES
    for cid, profile in TW_CHIP_REFRESH_PROFILES.items():
        result = _run_preflight_script('2026-11-13T11:00:00Z', None,
                                       profile=(cid, profile['dataset']), readback={'connectors': []})
        assert result['status'] == 'proceed', (cid, result)
        assert result['reason'] != 'snapshot_missing'
