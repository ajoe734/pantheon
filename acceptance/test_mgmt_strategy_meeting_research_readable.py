"""Release probe acceptance/probes/mgmt-strategy-meeting-research-readable against recorded hosted shapes."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROBE = ROOT / "probes" / "mgmt-strategy-meeting-research-readable"
FX = json.loads((ROOT / "fixtures" / "hosted_mgmt_strategy_meeting_research_20261120.json").read_text())

_loader = SourceFileLoader("mgmt_strategy_meeting_research_probe", str(PROBE))
probe = module_from_spec(spec_from_loader(_loader.name, _loader))
_loader.exec_module(probe)


def with_demands(body, status="ok", reason=None):
    """Overlay the research_demands surface (added 2026-11-25) on a recorded 2026-11-20 research page."""
    body = json.loads(json.dumps(body))
    body.setdefault("meta", {}).setdefault("surfaces", {})["research_demands"] = {"status": status, "reason": reason}
    return body


def fetcher(strategies, meetings, research):
    def fetch(path):
        if path.startswith("/bff/strategies"):
            return FX[strategies]
        if path.startswith("/bff/meetings"):
            return FX[meetings]
        if path.startswith("/bff/research-tasks"):
            return with_demands(FX[research]) if research.endswith("after_fix") else FX[research]
        if path.startswith("/bff/management/persona-fleet"):
            return {"data": {"items": []}}
        raise AssertionError(path)
    return fetch


def test_executable_with_owner_header():
    assert os.access(PROBE, os.X_OK)
    assert "# acceptance-owner:" in "\n".join(PROBE.read_text().splitlines()[:20])


def test_passes_on_fixed_hosted_shapes():
    report = probe.check(fetcher("strategies_after_fix", "meetings_after_fix", "research_after_fix"))
    assert report["passed"], report
    assert report["strategy_rows"] == 3


def test_fails_on_recorded_pre_fix_hosted_shapes():
    report = probe.check(fetcher("strategies_before_fix", "meetings_before_fix", "research_before_fix"))
    assert not report["passed"]
    assert {f.split(":")[0] for f in report["failed"]} == {
        "stub_or_probe_strategies_listed", "meta_hidden_unbound_stub_strategy_count_missing",
        "meta_hidden_test_persona_strategy_count_missing", "dead_letter_meetings_listed",
        "non_real_research_tasks_listed", "meta_hidden_non_real_count_missing",
        "research_demands_unavailable"}
    strategy_failure = next(f for f in report["failed"] if f.startswith("stub_or_probe"))
    assert "strat-tw-official" in strategy_failure and "dev-product-20260913" in strategy_failure


def test_live_probe_named_release_row_is_flagged_even_when_running():
    # The test-scoped deploy probe persona must never be listed, whatever its status.
    body = json.loads(json.dumps(FX["strategies_after_fix"]))
    body["items"].append(
        {"id": "strategy-persona-59b8a11bc277c249c89d", "name": "Pantheon Dev Paper Release 43688794313 Attempt 1",
         "operationalStatus": "running", "personaIds": ["persona-d64835a203ce276b8fbb"], "state": "approved"}
    )
    def fetch(path):
        return body if path.startswith("/bff/strategies") else fetcher("x", "meetings_after_fix", "research_after_fix")(path)
    report = probe.check(fetch)
    assert any("59b8a11bc277c249c89d" in f for f in report["failed"])


def test_exit_77_names_missing_variable_without_network():
    env = {k: v for k, v in os.environ.items() if not k.startswith("ACCEPTANCE_")}
    env["ACCEPTANCE_BFF_URL"] = "http://127.0.0.1:1"
    done = subprocess.run([sys.executable, str(PROBE)], env=env, capture_output=True, text=True)
    assert done.returncode == 77 and "ACCEPTANCE_VIEWER_CLIENT_ID" in done.stdout
    env["ACCEPTANCE_VIEWER_CLIENT_ID"] = "viewer"
    done = subprocess.run([sys.executable, str(PROBE)], env=env, capture_output=True, text=True)
    assert done.returncode == 77 and "ACCEPTANCE_VIEWER_CLIENT_SECRET" in done.stdout


# ---- BFF-RESEARCH-DEMAND-READ-20261125 ----
TEST_PERSONA = "persona-strat-hosted-all12-h12-claude-20261027-4923-src-106b4057d07a"
REAL_PERSONA = "persona-ee7112a829d38b9135ac"


def research_page(rows, demands="ok"):
    return with_demands({"items": rows, "meta": {"hidden_non_real_count": 3}}, demands,
                        None if demands == "ok" else "owner_read_failed:RuntimeError")


def run_check(research_body, test_personas=(TEST_PERSONA,)):
    def fetch(path):
        if path.startswith("/bff/research-tasks"):
            return research_body
        if path.startswith("/bff/management/persona-fleet"):
            return {"data": {"items": [{"persona_id": p, "data_scope": "test"} for p in test_personas]}}
        return fetcher("strategies_after_fix", "meetings_after_fix", "research_after_fix")(path)
    return probe.check(fetch)


def test_fails_when_research_demands_unavailable():
    report = run_check(research_page([{"task_id": "rt-1", "is_non_real": False}], demands="unavailable"))
    assert report["failed"] == ["research_demands_unavailable:owner_read_failed:RuntimeError"]


def test_fails_when_research_demands_surface_missing():
    body = {"items": [], "meta": {"hidden_non_real_count": 0}}
    assert run_check(body)["failed"] == ["research_demands_unavailable:missing"]


def test_fails_when_listed_task_resolves_to_test_scope_persona():
    rows = [{"task_id": "rt-real", "is_non_real": False, "persona_ids": [REAL_PERSONA]},
            {"task_id": "rt-test", "is_non_real": False, "persona_ids": [TEST_PERSONA]},
            {"task_id": "rt-mixed", "is_non_real": False, "persona_ids": [TEST_PERSONA, REAL_PERSONA]}]
    report = run_check(research_page(rows))
    assert report["failed"] == ["test_scope_persona_research_tasks_listed:rt-test"]
    assert run_check(research_page(rows[:1]))["passed"]


def test_probe_main_against_local_fixture_server(monkeypatch, capsys):
    pages = {"/bff/research-tasks": research_page([{"task_id": "rt-test", "is_non_real": False,
                                                    "persona_ids": [TEST_PERSONA]}], demands="unavailable")}
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, body):
            data = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            seen.append(("POST", self.path))
            self._send({"access_token": "viewer-token"})

        def do_GET(self):
            seen.append(("GET", self.path))
            assert self.headers["Authorization"] == "Bearer viewer-token"
            route = self.path.split("?")[0]
            if route in pages:
                return self._send(pages[route])
            if route == "/bff/management/persona-fleet":
                return self._send({"data": {"items": [{"persona_id": TEST_PERSONA}]}})
            return self._send(fetcher("strategies_after_fix", "meetings_after_fix", "research_after_fix")(self.path))

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        monkeypatch.setenv("ACCEPTANCE_BFF_URL", "http://127.0.0.1:%d" % server.server_port)
        monkeypatch.setenv("ACCEPTANCE_VIEWER_CLIENT_ID", "viewer")
        monkeypatch.setenv("ACCEPTANCE_VIEWER_CLIENT_SECRET", "secret")
        assert probe.main() == 1
        report = json.loads(capsys.readouterr().out)
        assert {f.split(":")[0] for f in report["failed"]} == {
            "research_demands_unavailable", "test_scope_persona_research_tasks_listed"}
        pages["/bff/research-tasks"] = research_page([{"task_id": "rt-real", "is_non_real": False,
                                                        "persona_ids": [REAL_PERSONA]}])
        assert probe.main() == 0
    finally:
        server.shutdown()
    assert {m for m, _ in seen} == {"GET", "POST"} and not [p for m, p in seen if m not in ("GET", "POST")]
