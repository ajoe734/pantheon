"""BFF-TRADE-JOURNEY-READABLE-20261120: readable list/detail rows, fixtures in the recorded hosted shape."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from services.control_plane.bff import trade_journeys as tj
from services.control_plane.bff.models import OperatorIdentity
from services.control_plane.bff.trade_journey_projection_store import ProjectionPage, TradeJourneyProjectionStore
from services.trade_journey.materializer import JourneyProjection

FIXTURE = json.loads(
    (Path(__file__).resolve().parents[4] / "acceptance" / "fixtures" / "hosted_trade_journeys_20261120.json").read_text()
)
NOW = datetime(2026, 11, 20, 0, 13, tzinfo=timezone.utc)
HEADERS = {"Authorization": "Bearer op-1:operator"}
PROBE_PERSONA = "persona-d64835a203ce276b8fbb"
NAMES = FIXTURE["persona_names"]


def projection(key: str, *, drop_reason: bool = False) -> JourneyProjection:
    j = FIXTURE["journeys"][key]
    timeline = [dict(e) for e in j["timeline"]]
    if drop_reason:
        for event in timeline:
            event.pop("reason_code", None)
    snapshot = {
        "journey_id": j["journey_id"], "tenant_id": j["tenant_id"], "environment": j["environment"],
        "status": j["status"], "stages": j["stages"], "identifiers": j["identifiers"], "revision": 1,
        "created_at": j["created_at"], "updated_at": j["updated_at"],
        "completeness": {"missing_stages": j["diagnostics"][0]["stages"], "complete": False},
    }
    return JourneyProjection(j["journey_id"], j["tenant_id"], j["environment"], timeline, snapshot, [], j["diagnostics"])


def personas():
    return [
        {"persona_id": pid, "name": name, **({"metadata": {"release_probe": True}} if pid == PROBE_PERSONA else {})}
        for pid, name in NAMES.items()
    ]


class Reader:
    def __init__(self, items):
        self.items, self.calls = items, []

    def page_journeys(self, **kwargs):
        self.calls.append(kwargs)
        return ProjectionPage(items=list(self.items), next_page_token=None, total=len(self.items))

    def get_journey(self, **kwargs):
        return self.items[0]

    def controller_freshness(self, **kwargs):
        return None


def client(reader, *, list_personas=None, list_incidents=None):
    def extract_identity(authorization, mfa_token=None, session_cookie=None):
        if not authorization:
            raise HTTPException(status_code=401)
        return OperatorIdentity(operator_id="op-1", roles=["operator"], claims={})

    app = FastAPI()
    app.include_router(tj.create_trade_journeys_router(
        extract_identity=extract_identity, require_read_role=lambda identity: None,
        get_projection_reader=lambda: reader, list_personas=list_personas, list_incidents=list_incidents,
    ))
    return TestClient(app, raise_server_exceptions=False)


LIST = "/bff/management/trade-journeys?tenant_id=tenant-dev&environment=paper"


def row(key, **kw):
    return tj._list_row(projection(key), now=NOW, **kw)


def test_recorded_sell_of_one_share_keeps_order_quantity():
    r = row("tj_29473e86")
    assert (r["quantity"], r["filled_quantity"], r["side"]) == (1.0, 1.0, "sell")


def test_rejected_buy_quantity_is_requested_and_nothing_filled():
    r = row("tj_79726064")
    assert (r["quantity"], r["filled_quantity"]) == (452.0, None)


def test_outcomes_of_recorded_journeys():
    assert row("tj_e32ead77")["outcome"] == {"kind": "normal", "reason_code": None, "reason_detail": None}
    hold = row("tj_efaa5c30")["outcome"]
    assert (hold["kind"], hold["reason_code"]) == ("no_trade", "hold_signal")
    variance = row("tj_29473e86")["outcome"]
    assert variance["kind"] == "variance"


def test_deferred_reconciliation_reason_is_its_own_kind():
    p = projection("tj_29473e86")
    for event in p.timeline:
        if event["stage"] == "reconciliation":
            event["reason_code"] = "runtime_unhealthy_at_reconciliation"
    assert tj._list_row(p, now=NOW)["outcome"]["kind"] == "reconciliation_deferred"


def test_reason_recorded_before_reason_fields_existed():
    # The recorded rejection only carries the expression code long_cash; the log line holds the real reason.
    r = row("tj_79726064")
    assert (r["outcome"]["kind"], r["outcome"]["reason_code"]) == ("rejected", "reason_not_recorded")
    assert "reason=insufficient_cash" in FIXTURE["paper_runtime_rejection_log"]
    p = projection("tj_79726064")
    event = next(e for e in p.timeline if e["stage"] == "order_submission")
    event.update(reason_code="insufficient_cash", reason_detail={"required": 100034.3461, "available": 100000.0})
    outcome = tj._list_row(p, now=NOW)["outcome"]
    assert outcome["reason_code"] == "insufficient_cash" and outcome["reason_detail"]["required"] == 100034.3461


def test_next_step_only_for_rejected_or_variance_and_prefers_open_incident():
    assert row("tj_e32ead77")["next_step"] is None and row("tj_efaa5c30")["next_step"] is None
    rejected = row("tj_79726064")
    assert rejected["next_step"]["href"] == f"/personas/{rejected['persona_id']}"
    binding = FIXTURE["journeys"]["tj_29473e86"]["identifiers"]["binding_id"][0]
    variance = row("tj_29473e86", open_incident_ids={binding: "inc-9"})
    assert variance["next_step"] == {"action_needed": "review_open_incident", "href": "/incidents/inc-9"}
    assert row("tj_29473e86")["next_step"]["href"].startswith("/personas/")


def test_names_come_from_one_persona_read_and_probe_flag_from_shared_rule():
    reads = []
    items = [projection(k) for k in ("tj_79726064", "tj_efaa5c30", "tj_29473e86", "tj_e32ead77")]
    reader = Reader(items)
    c = client(reader, list_personas=lambda: reads.append(1) or personas())
    body = c.get(LIST + "&include_probe=true", headers=HEADERS).json()
    assert len(reads) == 1
    by_id = {i["journey_id"][:11]: i for i in body["data"]["items"]}
    assert by_id["tj_29473e86"]["persona_name"] == NAMES["persona-2448c7053e33ea48da47"]
    assert by_id["tj_79726064"]["persona_is_probe"] is False
    assert any(i["persona_is_probe"] for i in body["data"]["items"] if i["persona_id"] == PROBE_PERSONA)


def test_unreadable_persona_owner_keeps_rows_with_null_name():
    def boom():
        raise RuntimeError("owner down")

    c = client(Reader([projection("tj_29473e86")]), list_personas=boom)
    items = c.get(LIST, headers=HEADERS).json()["data"]["items"]
    assert len(items) == 1 and items[0]["persona_name"] is None and items[0]["persona_is_probe"] is False


def test_probe_personas_excluded_by_default_and_included_on_request():
    reader = Reader([projection("tj_efaa5c30")])
    c = client(reader, list_personas=personas)
    c.get(LIST, headers=HEADERS)
    c.get(LIST + "&include_probe=true", headers=HEADERS)
    assert reader.calls[0]["filters"]["exclude_persona_ids"] == [PROBE_PERSONA]
    assert reader.calls[1]["filters"]["exclude_persona_ids"] is None


def test_unknown_attention_is_rejected_and_known_values_pass_through():
    reader = Reader([])
    c = client(reader)
    assert c.get(LIST + "&attention=bogus", headers=HEADERS).status_code == 400
    assert reader.calls == []
    for value in ("attention", "risk_reject", "broker_reject", "partial_fill", "recon_mismatch"):
        assert c.get(LIST + f"&attention={value}", headers=HEADERS).status_code == 200
        assert reader.calls[-1]["filters"]["attention"] == value


class Conn:
    def __init__(self, calls):
        self.calls, self.description = calls, []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return self

    def execute(self, sql, params):
        self.calls.append((sql, params))
        self._sql = sql

    def fetchall(self):
        if "COUNT(*)" in self._sql:
            self.description = [("total",)]
            return [(0,)]
        return []


def sql_for(**filters):
    calls = []
    TradeJourneyProjectionStore("dsn", token_secret="s" * 16, connect=lambda *_: Conn(calls)).page_journeys(
        tenant_id="t", environment="paper", filters=filters
    )
    return calls


def test_recon_mismatch_is_applied_in_sql_to_variance_rows_before_paging_and_counting():
    calls = sql_for(attention="recon_mismatch")
    page_sql, count_sql = calls[0][0], next(sql for sql, _ in calls if "COUNT(*)" in sql)
    for sql in (page_sql, count_sql):
        assert "status='completed_with_variance'" in sql
        assert "runtime_unhealthy_at_reconciliation" in sql
    assert page_sql.index("completed_with_variance") < page_sql.index("LIMIT")


def test_attention_filters_never_include_no_order_or_completed():
    for value in ("attention", "risk_reject", "broker_reject", "partial_fill"):
        sql = sql_for(attention=value)[0][0]
        assert "no_order" not in sql and "'completed'" not in sql


def test_probe_exclusion_is_in_sql_and_total_counts_the_filtered_result():
    calls = sql_for(exclude_persona_ids=[PROBE_PERSONA])
    count = next((sql, params) for sql, params in calls if "COUNT(*)" in sql)
    assert "NOT COALESCE" in count[0] and [PROBE_PERSONA] in count[1]


def test_detail_reports_upstream_lineage_unlinked_without_attention():
    p = projection("tj_29473e86")
    detail = tj._detail(p, now=NOW)
    items = [d for d in detail["diagnostics"] if d["code"] == "upstream_lineage_unlinked"]
    assert len(items) == 1 and items[0]["attention"] is False
    assert items[0]["stages"] == ["research_rationale", "strategy_candidate", "candidate_evaluation",
                                  "promotion_decision", "capital_binding", "deployment_runtime"]
    assert not any(d["code"] == "missing_stages" for d in detail["diagnostics"])
    assert detail["outcome"]["kind"] == "variance" and detail["quantity"] == 1.0


def test_detail_infers_risk_stage_for_risk_gate_rejection():
    # BFF-TRADE-JOURNEY-READABLE-RESIDUALS-20261120: a gate rejection is no longer a missing stage.
    detail = tj._detail(projection("tj_79726064"), now=NOW)
    assert [d for d in detail["diagnostics"] if d["code"] == "missing_stages"] == []
    assert detail["stages"]["risk_evaluation"]["status"] == "rejected"
