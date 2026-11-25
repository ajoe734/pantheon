"""Release probe acceptance/probes/mgmt-registry-pages-readable against a local fixture server.

Every body below is the envelope the real route handler builds, with ids and values recorded on hosted
dev on 2026-11-25 (see each dependency task's evidence.json):
  * /bff/strategies          strategies/routes/collection.py list return (data/items, page_info{next_page_token,
                             total}, meta.state_counts / visible_state_counts / hidden_* counts) and the DTO
                             metric fields of routes/common.py project_strategy_dto (runtime truth branch).
  * /bff/management/trading-pulse  management_read_models/service.py runtime_rows (strategy_id,
                             runtime_binding_id, total_trades, telemetry_summary.metrics).
  * /bff/personas, /bff/artifacts  personas/routes/collection.py (data/items + listScope) and
                             research/routes/artifacts.py (list_scope); persona-ee7112a829d38b9135ac's
                             recorded success_rate is the filled-in 0.0, projected as null.
  * /bff/lineage             console_gap/lineage.py nodes with display_name / name_missing.
  * /bff/management/evidence management_read_models/service.py get_evidence / get_evidence_item.
  * scope=cited              management_read_models/cited_evidence.py compose_cited_evidence.
  * /api/v1/postmortems      postmortems/router.py list_postmortems; rows are postmortems/models.py
                             PostmortemResponse; the infrastructure row is the one of
                             services/postmortems/test_main_routes.py _si_infra_bff.
"""
from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
PROBE = ROOT / "probes" / "mgmt-registry-pages-readable"
_loader = SourceFileLoader("mgmt_registry_pages_probe", str(PROBE))
probe = module_from_spec(spec_from_loader(_loader.name, _loader))
_loader.exec_module(probe)

TOKEN = "viewer-token-must-not-print"
SECRET = "viewer-secret-must-not-print"
LIVE_TOKEN = "a81b4fd15944698341aa"
LIVE_STRATEGY = f"strategy-persona-{LIVE_TOKEN}"
SECOND_STRATEGY = "strategy-persona-06d4775a9b6a8f63b311"
NO_FILL_STRATEGY = "strategy-persona-039ad01f8bcd2b4aa466"
LIVE_PERSONA = "persona-ee7112a829d38b9135ac"
LIVE_TITLE = "衡岳｜0050 台股 Paper 學習 R2"
EVIDENCE_REF = "evi-607b5b55229e8f72"
EVIDENCE_TOTAL = 43409

# Hosted 2026-11-25 owner hide: 4205 real drafts visible, 3216 hidden drafts.
STATE_COUNTS = {"draft": 7421, "review": 0, "approved": 141, "paper_running": 0, "canary_running": 0,
                "live_running": 0, "deployed": 0, "paused": 0, "retired": 12}
VISIBLE = {**STATE_COUNTS, "draft": 4205}


DEFAULT_VIEW_TOTAL = 141  # approved 141 + review 0 + ... , hidden_retired_persona_count 0

# Query bounds the real route handlers enforce (FastAPI Query(ge=, le=)); out of range answers HTTP 422.
BOUNDS = {
    "/bff/artifacts": {"page_size": (1, 100)},          # research/routes/artifacts.py
    "/bff/strategies": {"page_size": (1, 200)},         # strategies/routes/collection.py
    "/bff/personas": {"page_size": (1, 200)},           # personas/routes/collection.py
    "/bff/management/evidence": {"page_size": (1, 100)},  # management_read_models/router.py
    "/api/v1/postmortems": {"page_size": (1, 200)},     # postmortems/router.py
    "/bff/lineage": {"depth": (1, 20)},                 # console_gap/lineage.py
}


def out_of_bounds(path):
    """The (name, value) of the first query parameter the real route would reject with 422, else None."""
    parsed = urllib.parse.urlparse(path)
    for name, (low, high) in BOUNDS.get(parsed.path, {}).items():
        for value in urllib.parse.parse_qs(parsed.query).get(name, []):
            try:
                if not low <= int(value) <= high:
                    return name, value
            except ValueError:
                return name, value
    return None


def strategy_page(total, visible=VISIBLE, items=()):
    return {"data": list(items), "items": list(items), "page_info": {"next_page_token": None, "total": total},
            "meta": {"total": total, "state_counts": STATE_COUNTS, "visible_state_counts": visible,
                     "hidden_retired_persona_count": 0, "hidden_test_persona_strategy_count": 1616,
                     "hidden_unbound_stub_strategy_count": 1600, "include_test_personas": False,
                     "surfaces": {"strategy_specs": {"status": "ok"}}}}


def strategy_dto(sid, pnl, trades, *, resolution, reason=None, runtime="running"):
    return {"id": sid, "name": LIVE_TITLE if sid == LIVE_STRATEGY else sid, "state": "approved",
            "personaIds": [LIVE_PERSONA] if sid == LIVE_STRATEGY else [], "capitalPoolId": f"pool-persona-paper-{LIVE_TOKEN}",
            "pnl": pnl, "pnl30d": None, "sharpe": None, "drawdown": 0.0012 if pnl is not None else None,
            "totalTrades": trades, "metricsAsOf": "2026-11-25T06:40:12Z" if pnl is not None else None,
            "metricsResolution": resolution, "metricsReason": reason, "metricsPersonaId": None,
            "runtimeStatus": runtime, "runtimeStatusReason": None, "operationalStatus": "running"}


def pulse_row(sid, binding, pnl, trades):
    return {"runtime_id": f"runtime-{binding}", "runtime_binding_id": binding, "strategy_id": sid,
            "persona_id": LIVE_PERSONA, "deployment_stage": "paper", "status": "active", "total_trades": trades,
            "telemetry_summary": {"metrics": {"pnl": pnl, "drawdown": 0.0012, "total_trades": trades}},
            "performance_context": {"source_status": "fresh", "pnl_at": "2026-11-25T06:40:12Z"}}


def persona_row(pid, scope, success_rate):
    return {"id": pid, "persona_id": pid, "name": LIVE_TITLE if pid == LIVE_PERSONA else pid,
            "lifecycleState": "paper_owner", "listScope": scope, "successRate": success_rate}


def lineage_body(named=True):
    ids = [LIVE_PERSONA, LIVE_STRATEGY, f"artifact-persona-pool-{LIVE_TOKEN}-v2", f"pool-persona-paper-{LIVE_TOKEN}",
           f"reg-strategy-spec-persona-{LIVE_TOKEN}"]
    names = ["衡岳", LIVE_TITLE, LIVE_TITLE, f"{LIVE_TITLE} (CapitalPool)", LIVE_TITLE]
    nodes = [{"id": i, "label": i, "type": "Strategy", "state": "active", "display_name": n if named else None,
              "name_missing": not named} for i, n in zip(ids, names)]
    status = "ok" if named else "degraded"
    return {"data": {"id": LIVE_STRATEGY, "nodes": nodes, "edges": [{"from": ids[0], "to": ids[1], "label": "owns"}],
                     "status": status, "source": "service_client"},
            "page_info": {"next_page_token": None, "total": len(nodes)},
            "meta": {"status": status, "surfaces": {"lineage": {"status": status},
                                                    "display_names": {"status": status}}}}


def evidence_item(ref):
    return {"id": ref, "ref_id": ref, "title": "0050.TW daily bars 2026-11-24", "display_label": "0050.TW daily bars",
            "source_class": "market_data", "source_type": "connector", "link_type": "supports",
            "credibility": {"tier": "unverified", "verified": None, "confidence": 1.0},
            "resolved_link": {"availability": "unavailable", "route_href": None}, "redacted": False}


def evidence_list(items, total, status="ok"):
    surface = {"status": status, "source": "service_client"}
    return {"data": {"id": "management-evidence", "items": items, "summary": {}, "facets": {}},
            "page_info": {"next_page_token": None if not items else "1", "total": total, "page_size": 1},
            "meta": {"surfaces": {"management_evidence": surface, "evidence_refs": surface,
                                  "knowledge_evidence": surface}, "redacted_evidence_count": 0}}


def cited_source(status="ok", reason=None):
    return {"status": status, "reason": reason, "read_count": 3, "truncated": False, "failed_count": 0,
            "citation_count": 2, "withheld_count": 0, "calls": 1, "response_bytes": 2048}


def cited_body(statuses):
    sources = {k: cited_source(statuses.get(k, "ok"), None if statuses.get(k, "ok") == "ok" else "http_403")
               for k in probe.CITED_SOURCE_KEYS}
    not_ok = [k for k, s in sources.items() if s["status"] != "ok"]
    status = "degraded" if not_ok else "ok"
    surface = {"status": status, "source": "bff_composed_owner_reads"}
    row = {"id": f"artifact-persona-pool-{LIVE_TOKEN}-v2", "ref_id": f"artifact-persona-pool-{LIVE_TOKEN}-v2",
           "ref_kind": "registry_artifact", "label": LIVE_TITLE, "name_missing": False, "citation_count": 1,
           "cited_by": [{"entity_type": "approval", "entity_ref": "ad-1", "source": "governance_approvals"}]}
    return {"data": {"id": "management-evidence-cited", "scope": "cited", "items": [row], "sources": sources,
                     "summary": {"scope": "cited", "complete": not not_ok, "incomplete": bool(not_ok),
                                 "sources_ok": 7 - len(not_ok), "sources_not_ok": not_ok}},
            "page_info": {"next_page_token": None, "total": 1, "page_size": 100},
            "meta": {"surfaces": {"management_evidence": dict(surface), "cited_evidence": surface,
                                  "cited_evidence_sources": sources}}}


def postmortem(pid, status, **incident):
    row = {"postmortem_id": pid, "title": "downstream degradation", "status": status, "created_at": "2026-11-25T05:00:00Z",
           "incident_id": "inc-1", "binding_id": "binding-1", "deployment_stage": "paper", "deployment_plan_id": "plan-1",
           "capital_pool_id": "pool-1", "persona_capital_binding_id": "pcb-1", "artifact_id": "artifact-1",
           "artifact_version": "1", "runtime_id": "runtime-1", "trace_id": "trace-1", "root_cause": "",
           "incident_cluster_id": "drift:rolling_drawdown_multiple", "incident_evidence_summary": "Drift threshold breached"}
    row.update(incident)
    return row


INFRA = dict(incident_id="inc-infra-bff", binding_id="infra-subject-tenant-dev-control-plane-bff-reconciliation-_si_drift",
             deployment_plan_id="infra-plan-control-plane-bff", persona_capital_binding_id="infra-authority-control-plane-bff",
             incident_cluster_id="infrastructure:tenant-dev:control-plane-bff:downstream_degradation",
             incident_evidence_summary="non_trading_infrastructure_incident=true downstream degradation")
MONITOR = dict(incident_id="agent-finding-1", binding_id="infra-binding-monitor-agent",
               deployment_plan_id="infra-plan-monitor-agent", persona_capital_binding_id="infra-authority-monitor-agent",
               incident_cluster_id="infra-cluster:monitor-agent:agent-finding-1", artifact_id="infra-component-agent-finding-1",
               incident_evidence_summary="component_kind=monitor-agent-finding")


def postmortem_list(rows, hidden=5355):
    return {"data": rows, "page_info": {"total": len(rows), "page_size": 200, "next_page_token": None},
            "meta": {"total": len(rows), "hidden_infrastructure_count": hidden, "staleness": None,
                     "surfaces": {"postmortems": {"status": "ok"}}}}


def passing_world():
    strategies = [strategy_dto(LIVE_STRATEGY, -9.988691250022384, 2, resolution="fresh"),
                  strategy_dto(SECOND_STRATEGY, -186.89087499999732, 2, resolution="fresh"),
                  strategy_dto(NO_FILL_STRATEGY, None, 0, resolution="missing", reason="no_fills")]
    pulse = [pulse_row(LIVE_STRATEGY, f"rb-{LIVE_TOKEN}", -9.988691250022384, 2),
             pulse_row(SECOND_STRATEGY, "rb-06d4775a9b6a8f63b311", -186.89087499999732, 2),
             pulse_row(NO_FILL_STRATEGY, "rb-039ad01f8bcd2b4aa466", None, 0)]
    lineage = {f"/bff/lineage?root_id={sid}&depth=3": lineage_body() for sid in (LIVE_STRATEGY, SECOND_STRATEGY, NO_FILL_STRATEGY)}
    return {
        "/bff/strategies?page_size=1": strategy_page(sum(v for k, v in VISIBLE.items() if k != "retired")),
        probe.DEFAULT_VIEW: strategy_page(DEFAULT_VIEW_TOTAL),
        "/bff/strategies?state=draft&page_size=1": strategy_page(VISIBLE["draft"]),
        "/bff/strategies?state=retired&page_size=1": strategy_page(VISIBLE["retired"]),
        probe.STRATEGY_ROWS: strategy_page(len(strategies), items=strategies),
        probe.PULSE: {"data": {"runtime_rows": pulse, "summary": {"runtime_count": 3}}, "meta": {}},
        probe.PERSONAS: {"data": [persona_row(LIVE_PERSONA, "current", None), persona_row("persona-research-1", "research", 0.42)],
                         "items": [persona_row(LIVE_PERSONA, "current", None), persona_row("persona-research-1", "research", 0.42)],
                         "page_info": {"total": 2, "next_page_token": None}},
        probe.ARTIFACTS: {"data": [{"id": f"artifact-persona-pool-{LIVE_TOKEN}-v2", "kind": "execution_bundle",
                                    "source_kind": "registry", "list_scope": "current"},
                                   {"id": "res-out-001", "kind": "research_output", "source_kind": "research_run_output",
                                    "list_scope": "research"}],
                          "page_info": {"total": 2, "next_page_token": None},
                          "meta": {"surfaces": {"artifact_list": {"status": "ok"}}}},
        **lineage,
        "/bff/management/evidence?page_size=1": evidence_list([evidence_item(EVIDENCE_REF)], EVIDENCE_TOTAL),
        f"/bff/management/evidence/{EVIDENCE_REF}": {"data": evidence_item(EVIDENCE_REF),
                                                     "meta": {"surfaces": {"evidence_ref_detail": {"status": "ok"}}}},
        f"/bff/management/evidence?page_size=1&page_token={EVIDENCE_TOTAL - 1}":
            evidence_list([evidence_item("evi-0000000000000001")], EVIDENCE_TOTAL),
        probe.CITED: cited_body({}),
        probe.POSTMORTEMS: postmortem_list([postmortem("pm-drift-1", "draft"), postmortem("pm-infra-pub", "published", **INFRA)]),
    }


@pytest.fixture()
def server(monkeypatch):
    state = {"routes": passing_world(), "seen": []}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, code, body):
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
            state["seen"].append(("POST", self.path))
            assert self.path == "/bff/auth/dev-login" and payload["client_secret"] == SECRET
            self._send(200, {"access_token": TOKEN})

        def do_GET(self):
            state["seen"].append(("GET", self.path))
            if self.headers.get("Authorization") != "Bearer " + TOKEN:
                return self._send(401, {"error": "unauthorized"})
            bad = out_of_bounds(self.path)
            if bad:
                return self._send(422, {"detail": [{"loc": ["query", bad[0]], "type": "value_error"}]})
            answer = state["routes"].get(self.path)
            if answer is None:
                return self._send(404, {"error": "not_in_fixture", "path": self.path})
            if isinstance(answer, int):
                return self._send(answer, {"error": {"code": "FORBIDDEN"}})
            return self._send(200, answer)

        def do_PUT(self):  # pragma: no cover - the probe must never write
            state["seen"].append(("PUT", self.path))
            self._send(405, {})

        do_PATCH = do_DELETE = do_PUT

    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    monkeypatch.setenv("ACCEPTANCE_BFF_URL", "http://127.0.0.1:%d" % httpd.server_port)
    monkeypatch.setenv("ACCEPTANCE_VIEWER_CLIENT_ID", "viewer")
    monkeypatch.setenv("ACCEPTANCE_VIEWER_CLIENT_SECRET", SECRET)
    try:
        yield state
    finally:
        httpd.shutdown()
    assert {m for m, _ in state["seen"]} <= {"GET", "POST"}
    assert [p for m, p in state["seen"] if m == "POST"] in ([], ["/bff/auth/dev-login"])


def run(capsys):
    code = probe.main()
    out = capsys.readouterr().out
    assert TOKEN not in out and SECRET not in out
    report = json.loads(out.strip().splitlines()[-1])
    return code, out, report


def statuses(report):
    return {c["check"]: c["status"] for c in report["checks"]}


def test_executable_with_owner_header():
    assert os.access(PROBE, os.X_OK)
    assert "# acceptance-owner: REGISTRY-PAGES-ACCEPTANCE-PROBE-20261125" in PROBE.read_text().splitlines()[1]
    assert set(probe.OWNERS) == {name for name, _ in probe.CHECKS}


def test_all_checks_pass_on_recorded_shapes(server, capsys):
    code, out, report = run(capsys)
    assert code == 0, out
    assert set(statuses(report).values()) == {"passed"}, statuses(report)
    assert "FAILED" not in out
    assert "INFO state=draft read took" in out and "(information only)" in out
    owners = {c["check"]: c["owner"] for c in report["checks"]}
    assert owners["postmortem_infra_hidden"] == "POSTMORTEM-SKIP-INFRA-DRAFTS-20261125"


def test_draft_duration_is_information_only(server, capsys):
    ticks = iter([0.0, 3600.0])
    report = probe.check(lambda p: _fixture_fetch(server, p), clock=lambda: next(ticks, 3600.0))
    assert report["passed"]
    assert report["checks"][0]["info"]["state_draft_seconds"] == 3600.0


def _fixture_fetch(server, path):
    answer = server["routes"].get(path)
    if isinstance(answer, int):
        raise probe.Denied(answer)
    if answer is None:
        raise probe.ReadFailed("http_404")
    return copy.deepcopy(answer)


# --- one failing case per check, each run through the fixture server -------------------------------

def _break_state_total(routes):
    routes["/bff/strategies?state=draft&page_size=1"]["page_info"]["total"] = 7421  # hidden drafts counted


def _break_visible_counts_null(routes):
    routes["/bff/strategies?page_size=1"]["meta"]["visible_state_counts"] = None


def _break_default_view_null(routes):  # hosted b3f93d448: default view answered visible_state_counts null
    routes[probe.DEFAULT_VIEW]["meta"]["visible_state_counts"] = None


def _break_default_view_differs(routes):
    routes[probe.DEFAULT_VIEW]["meta"]["visible_state_counts"] = {**VISIBLE, "draft": 7421}  # hidden drafts counted


def _break_default_view_total(routes):  # hosted shape: 14 approved, 11 hidden for retired personas, total 3
    body = strategy_page(3, visible={**VISIBLE, "approved": 14})
    body["meta"]["hidden_retired_persona_count"] = 5
    routes[probe.DEFAULT_VIEW] = body
    routes["/bff/strategies?page_size=1"] = strategy_page(
        sum(v for k, v in {**VISIBLE, "approved": 14}.items() if k != "retired"), visible={**VISIBLE, "approved": 14})
    routes["/bff/strategies?state=draft&page_size=1"] = strategy_page(VISIBLE["draft"], visible={**VISIBLE, "approved": 14})
    routes["/bff/strategies?state=retired&page_size=1"] = strategy_page(VISIBLE["retired"], visible={**VISIBLE, "approved": 14})


def _break_metric_vs_pulse(routes):
    routes[probe.PULSE]["data"]["runtime_rows"][1]["telemetry_summary"]["metrics"]["pnl"] = -150.0


def _break_fresh_null(routes):
    row = routes[probe.STRATEGY_ROWS]["items"][2]
    row.update(metricsResolution="fresh", metricsReason=None)


def _break_persona_zero(routes):
    routes[probe.PERSONAS]["data"][0]["successRate"] = 0.0
    routes[probe.PERSONAS]["items"][0]["successRate"] = 0.0


def _break_persona_scope(routes):
    del routes[probe.PERSONAS]["data"][1]["listScope"]
    del routes[probe.PERSONAS]["items"][1]["listScope"]


def _break_artifact_scope(routes):
    del routes[probe.ARTIFACTS]["data"][0]["list_scope"]


def _break_lineage(routes):
    routes[f"/bff/lineage?root_id={SECOND_STRATEGY}&depth=3"] = lineage_body(named=False)


def _break_evidence_detail(routes):
    routes[f"/bff/management/evidence/{EVIDENCE_REF}"] = evidence_list([evidence_item(EVIDENCE_REF)], 1)


def _break_deep_page(routes):
    routes[f"/bff/management/evidence?page_size=1&page_token={EVIDENCE_TOTAL - 1}"] = evidence_list([], 0)


def _break_cited(routes):
    body = cited_body({"governance_approvals": "failed"})
    body["data"]["summary"].update(sources_not_ok=[], incomplete=False, complete=True)
    body["meta"]["surfaces"]["cited_evidence"]["status"] = "ok"
    routes[probe.CITED] = body


def _break_cited_missing_source(routes):
    del routes[probe.CITED]["data"]["sources"]["research_demands"]


def _break_postmortem_infra(routes):
    routes[probe.POSTMORTEMS]["data"].append(postmortem("pm-infra-draft", "draft", **INFRA))


def _break_postmortem_monitor(routes):
    routes[probe.POSTMORTEMS]["data"].append(postmortem("pm-agent-draft", "draft", **MONITOR))


def _break_hidden_count(routes):
    routes[probe.POSTMORTEMS]["meta"]["hidden_infrastructure_count"] = None


FAILING = [
    ("strategy_state_totals", _break_state_total, "state_total_differs_from_visible_state_counts", "/bff/strategies?state=draft"),
    ("strategy_state_totals", _break_visible_counts_null, "visible_state_counts_missing", "/bff/strategies?page_size=1"),
    ("strategy_default_view_counts", _break_default_view_null, "default_view_visible_state_counts_missing", "hide_retired_personas=true"),
    ("strategy_default_view_counts", _break_default_view_differs, "default_view_counts_differ_from_unfiltered_view", "hide_retired_personas=true"),
    ("strategy_default_view_counts", _break_default_view_total, "default_view_total_differs_from_visible_state_counts", "hide_retired_personas=true"),
    ("strategy_metrics_vs_pulse", _break_metric_vs_pulse, "list_metric_differs_from_trading_pulse", "trading-pulse"),
    ("strategy_metrics_vs_pulse", _break_fresh_null, "fresh_metrics_with_null_values_without_reason", probe.STRATEGY_ROWS),
    ("persona_artifact_scope", _break_persona_zero, "persona_success_rate_zero_without_record", probe.PERSONAS),
    ("persona_artifact_scope", _break_persona_scope, "persona_list_scope_missing", probe.PERSONAS),
    ("persona_artifact_scope", _break_artifact_scope, "artifact_list_scope_missing", probe.ARTIFACTS),
    ("lineage_node_names", _break_lineage, "lineage_node_without_name", SECOND_STRATEGY),
    ("evidence_detail_paging", _break_evidence_detail, "evidence_detail_is_list_shaped", EVIDENCE_REF),
    ("evidence_detail_paging", _break_deep_page, "deep_page_empty_with_surface_ok", f"page_token={EVIDENCE_TOTAL - 1}"),
    ("cited_sources_visible", _break_cited, "cited_list_hides_failed_source", probe.CITED),
    ("cited_sources_visible", _break_cited_missing_source, "cited_source_not_reported", probe.CITED),
    ("postmortem_infra_hidden", _break_postmortem_infra, "infrastructure_draft_in_default_list", probe.POSTMORTEMS),
    ("postmortem_infra_hidden", _break_postmortem_monitor, "infrastructure_draft_in_default_list", probe.POSTMORTEMS),
    ("postmortem_infra_hidden", _break_hidden_count, "hidden_infrastructure_count_missing", probe.POSTMORTEMS),
]


@pytest.mark.parametrize("check_name,breaker,code,route_part", FAILING, ids=[f[2] for f in FAILING])
def test_each_check_fails_on_its_defect_and_names_route_and_owner(server, capsys, check_name, breaker, code, route_part):
    breaker(server["routes"])
    exit_code, out, report = run(capsys)
    assert exit_code == 1, out
    assert report["failed"] == [check_name], statuses(report)
    lines = [line for line in out.splitlines() if line.startswith("FAILED ")]
    assert lines and all(f"FAILED {check_name} owner={probe.OWNERS[check_name]} code={code}" in line for line in lines), lines
    assert route_part in lines[0] and "values=" in lines[0]


def test_default_view_null_counts_names_default_view_owner(server, capsys):
    _break_default_view_null(server["routes"])
    code, out, report = run(capsys)
    assert code == 1 and report["failed"] == ["strategy_default_view_counts"]
    assert report["checks"][0]["owner"] == "REGISTRY-STRATEGY-LIST-OWNER-HIDE-20261125"
    assert "owner=BFF-STRATEGY-DEFAULT-VIEW-COUNTS-20261125 code=default_view_visible_state_counts_missing" in out


def test_default_view_hosted_shape_passes_when_total_is_visible_minus_hidden_retired(server, capsys):
    """Hosted shape: 14 approved visible-state rows, 11 hidden for retired personas, default total 3."""
    _break_default_view_total(server["routes"])
    body = server["routes"][probe.DEFAULT_VIEW]
    body["meta"]["hidden_retired_persona_count"] = 11
    body["page_info"]["total"] = body["meta"]["total"] = 3
    code, out, report = run(capsys)
    assert code == 0, out


def test_fixture_server_enforces_real_route_bounds(server):
    """Every bound the real handlers declare answers 422 outside its range (and the probe reads only inside it)."""
    cases = ["/bff/artifacts?page_size=200", "/bff/artifacts?page_size=0", "/bff/strategies?page_size=201",
             "/bff/personas?page_size=201", "/bff/management/evidence?page_size=101", "/api/v1/postmortems?page_size=201",
             "/bff/lineage?root_id=x&depth=21", "/bff/lineage?root_id=x&depth=0", "/bff/artifacts?page_size=abc"]
    assert all(out_of_bounds(p) for p in cases)
    assert not any(out_of_bounds(p) for p in ["/bff/artifacts?page_size=100", "/bff/strategies?page_size=200",
                                              "/bff/lineage?root_id=x&depth=3", "/bff/management/evidence?page_size=100"])
    base = os.environ["ACCEPTANCE_BFF_URL"]
    import urllib.error
    import urllib.request
    request = urllib.request.Request(base + "/bff/artifacts?page_size=200", headers={"Authorization": "Bearer " + TOKEN})
    with pytest.raises(urllib.error.HTTPError) as caught:
        urllib.request.urlopen(request)
    assert caught.value.code == 422


def test_every_probe_request_is_inside_the_real_route_bounds(server, capsys):
    assert run(capsys)[0] == 0
    sent = [p for m, p in server["seen"] if m == "GET"]
    assert sent and not [p for p in sent if out_of_bounds(p)], sent
    assert not [p for p in sent if p.startswith("/bff/artifacts") and "page_size=100" not in p]


def test_artifacts_follow_next_page_token_and_fail_on_a_later_page(server, capsys):
    routes = server["routes"]
    routes[probe.ARTIFACTS]["page_info"]["next_page_token"] = "100"
    routes[probe.ARTIFACTS + "&page_token=100"] = {
        "data": [{"id": "artifact-page-2", "kind": "execution_bundle"}],  # no list_scope
        "page_info": {"total": 3, "next_page_token": None}}
    code, out, report = run(capsys)
    assert code == 1 and report["failed"] == ["persona_artifact_scope"]
    assert "code=artifact_list_scope_missing" in out and "artifact-page-2" in out
    assert ("GET", probe.ARTIFACTS + "&page_token=100") in server["seen"]


def test_artifacts_page_size_above_route_bound_would_fail_read(server, capsys):
    """Regression: page_size=200 on /bff/artifacts is HTTP 422 on the real route (persona_artifact_scope read_failed)."""
    routes = server["routes"]
    routes["/bff/artifacts?page_size=200"] = routes[probe.ARTIFACTS]
    probe_artifacts = probe.ARTIFACTS
    try:
        probe.ARTIFACTS = "/bff/artifacts?page_size=200"
        code, out, report = run(capsys)
    finally:
        probe.ARTIFACTS = probe_artifacts
    assert code == 1 and "code=read_failed route=/bff/artifacts?page_size=200" in out and "http_422" in out


def test_metric_difference_that_settles_on_reread_passes(server, capsys):
    """Values move between reads: only a difference that survives one re-read of both fails."""
    good = copy.deepcopy(server["routes"][probe.PULSE])
    _break_metric_vs_pulse(server["routes"])
    calls = []

    def fetch(path):
        calls.append(path)
        if path == probe.PULSE and calls.count(probe.PULSE) > 1:
            return copy.deepcopy(good)
        return _fixture_fetch(server, path)

    assert probe.check(fetch)["passed"]
    assert calls.count(probe.PULSE) == 2


def test_viewer_role_denial_is_denied_not_failed(server, capsys):
    for sid in (LIVE_STRATEGY, SECOND_STRATEGY, NO_FILL_STRATEGY):
        server["routes"][f"/bff/lineage?root_id={sid}&depth=3"] = 403  # lineage is operator-only
    code, out, report = run(capsys)
    assert code == 0, out
    assert statuses(report)["lineage_node_names"] == "denied"
    assert f"DENIED lineage_node_names owner={probe.OWNERS['lineage_node_names']} route=/bff/lineage?root_id=" in out
    assert "FAILED" not in out


def test_unreadable_route_fails_that_check_only(server, capsys):
    server["routes"].pop(probe.ARTIFACTS)
    code, out, report = run(capsys)
    assert code == 1 and report["failed"] == ["persona_artifact_scope"]
    assert "code=read_failed route=/bff/artifacts?page_size=100" in out and "http_404" in out


def test_no_running_strategy_is_not_exercised_not_passed(server, capsys):
    for row in server["routes"][probe.STRATEGY_ROWS]["items"]:
        row["runtimeStatus"] = "none"
    code, out, report = run(capsys)
    assert code == 0 and statuses(report)["lineage_node_names"] == "not_exercised"


def test_infrastructure_predicate_matches_owner_rule():
    assert probe._is_infrastructure_postmortem(postmortem("a", "draft", **INFRA))
    assert probe._is_infrastructure_postmortem(postmortem("b", "draft", **MONITOR))
    summary_only = dict(INFRA, incident_cluster_id="other", incident_evidence_summary="non_trading_infrastructure_incident=true")
    assert probe._is_infrastructure_postmortem(postmortem("c", "draft", **summary_only))
    assert not probe._is_infrastructure_postmortem(postmortem("d", "draft"))  # drift incident


def test_infrastructure_predicate_agrees_with_incident_owner():
    owner = pytest.importorskip("services.incident.incident")
    for fields in (INFRA, MONITOR, {}, dict(INFRA, incident_cluster_id="drift:x")):
        row = postmortem("x", "draft", **fields)
        assert probe._is_infrastructure_postmortem(row) is owner.is_infrastructure_postmortem(row), fields


def test_exit_77_names_missing_variable_without_network():
    env = {k: v for k, v in os.environ.items() if not k.startswith("ACCEPTANCE_")}
    env["ACCEPTANCE_BFF_URL"] = "http://127.0.0.1:1"
    done = subprocess.run([sys.executable, str(PROBE)], env=env, capture_output=True, text=True)
    assert done.returncode == 77 and "ACCEPTANCE_VIEWER_CLIENT_ID" in done.stdout


def test_login_failure_is_failed_and_prints_no_secret(monkeypatch, capsys):
    monkeypatch.setenv("ACCEPTANCE_BFF_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("ACCEPTANCE_VIEWER_CLIENT_ID", "viewer")
    monkeypatch.setenv("ACCEPTANCE_VIEWER_CLIENT_SECRET", SECRET)
    assert probe.main() == 1
    out = capsys.readouterr().out
    assert out.startswith("failed: login") and SECRET not in out and "127.0.0.1" not in out


def test_every_route_the_probe_reads_is_registered_in_the_bff_application(server, capsys):
    """The fixture paths are the probe's real requests, and each one is a GET route of the real BFF app."""
    os.environ.setdefault("RANKING_STORE_DSN", "postgresql://test:test@localhost:5432/test")
    os.environ.setdefault("RANKING_STORE_BOOTSTRAP", "0")
    from services.control_plane.bff import main as bff_main
    from services.control_plane.bff.test_normalized_route_uniqueness import scan_fastapi_routes

    registered = {e.raw_path for e in scan_fastapi_routes(bff_main.app) if e.method == "GET"}
    assert run(capsys)[0] == 0
    requested = {p.split("?")[0] for m, p in server["seen"] if m == "GET"}
    templates = {"/bff/management/evidence/" + EVIDENCE_REF: "/bff/management/evidence/{ref_id}"}
    assert len(requested) == 8, requested  # the default-view read is the same route as the others
    for path in requested:
        assert templates.get(path, path) in registered, path
