"""BFF-MGMT-DISPLAY-NAMES-20261120: management rows carry BFF-resolved persona/strategy names."""
from __future__ import annotations

import pytest

from services.control_plane.bff.core import display_names
from services.control_plane.bff.core.display_names import (
    DisplayNameResolver,
    annotate_subject_row,
    configure_display_name_source,
)
from services.control_plane.bff.core.owner_reads import request_read_scope
from services.control_plane.bff.governance.human_inbox import _human_inbox_incident_item
from services.control_plane.bff.incidents.service import (
    _project_bff_incident_case,
    _project_incident_detail_incident,
    _project_incident_home_item,
)
from services.control_plane.bff.meetings.router import _annotate_meeting_envelope
from services.control_plane.bff.research.routes.artifacts import _annotate_artifact_payload


class CountingStore:
    def __init__(self, *, fail_strategies: bool = False):
        self.calls = {"personas": 0, "strategies": 0, "bindings": 0}
        self.fail_strategies = fail_strategies

    def list_personas(self):
        self.calls["personas"] += 1
        return [
            {"persona_id": "persona-a", "display_name": "Alpha Trader"},
            {"persona_id": "persona-b", "metadata": {"name": "Beta Chair"}},
        ]

    def get_strategy_labels(self):
        self.calls["strategies"] += 1
        if self.fail_strategies:
            raise RuntimeError("owner down")
        return {"strategy-1": {"title": "Momentum One", "name": "Momentum One"}}

    def list_runtime_bindings(self):
        self.calls["bindings"] += 1
        return [{"binding_id": "rb-1", "persona_id": "persona-a", "strategy_id": "strategy-1"}]


@pytest.fixture
def store():
    s = CountingStore()
    configure_display_name_source(s)
    yield s
    configure_display_name_source(None)


def test_resolver_names_fallback_and_flag(store):
    names = DisplayNameResolver()
    assert names.persona("persona-a") == ("Alpha Trader", False)
    assert names.persona("persona-b") == ("Beta Chair", False)
    assert names.persona("persona-zzz") == ("persona-zzz", True)
    assert names.strategy("strategy-1") == ("Momentum One", False)
    row = names.annotate({"persona_id": "persona-x", "strategy_id": "strategy-1", "name": "kept"})
    assert row["persona_display_name"] == "persona-x"
    assert row["persona_name_missing"] is True
    assert row["strategy_display_name"] == "Momentum One"
    assert "strategy_name_missing" not in row
    assert row["name"] == "kept"


def test_each_directory_is_read_once_per_request(store):
    with request_read_scope():
        for _ in range(5):  # several resolvers in one request share the owner reads
            names = DisplayNameResolver()
            for i in range(20):
                names.annotate({"persona_id": "persona-a", "strategy_id": "strategy-1"})
    assert store.calls == {"personas": 1, "strategies": 1, "bindings": 0}
    names = DisplayNameResolver()  # without a request scope the resolver itself still reads once
    for _ in range(10):
        names.annotate({"persona_id": "persona-a", "strategy_id": "strategy-1"})
    assert store.calls["personas"] == 2 and store.calls["strategies"] == 2


def test_owner_failure_falls_back_to_id():
    failing = CountingStore(fail_strategies=True)
    configure_display_name_source(failing)
    try:
        row = DisplayNameResolver().annotate({"persona_id": "persona-a", "strategy_id": "strategy-1"})
    finally:
        configure_display_name_source(None)
    assert row["persona_display_name"] == "Alpha Trader"
    assert row["strategy_display_name"] == "strategy-1"
    assert row["strategy_name_missing"] is True


def test_runtime_and_deployment_rows_get_names(store):
    names = DisplayNameResolver()
    runtime = names.annotate({"binding_id": "rb-1", "persona_id": "persona-a", "strategy_id": "strategy-1"})
    assert runtime["persona_display_name"] == "Alpha Trader"
    assert runtime["strategy_display_name"] == "Momentum One"
    assert runtime["binding_id"] == "rb-1"


def test_artifact_rows_name_registry_strategy(store):
    payload = {
        "items": [{"id": "reg-1", "name": "strategy-1", "source_kind": "registry"}],
        "data": [{"id": "reg-1", "name": "strategy-1", "source_kind": "registry"}],
    }
    out = _annotate_artifact_payload(payload)
    for row in out["items"] + out["data"]:
        assert row["name"] == "strategy-1"
        assert row["strategy_display_name"] == "Momentum One"
    detail = _annotate_artifact_payload({"data": {"id": "reg-1", "name": "strategy-9", "source_kind": "registry"}})
    assert detail["data"]["strategy_display_name"] == "strategy-9"
    assert detail["data"]["strategy_name_missing"] is True


def test_meeting_list_and_detail_carry_persona_names(store):
    listing = {
        "status": "ok",
        "items": [
            {
                "meeting_id": "m1",
                "chair_persona_id": "persona-b",
                "participants": [{"persona_id": "persona-a"}],
            }
        ],
    }
    listing["data"] = {"items": listing["items"]}
    _annotate_meeting_envelope(listing)
    row = listing["data"]["items"][0]
    assert row["chair_persona_display_name"] == "Beta Chair"
    assert row["participants"][0]["persona_display_name"] == "Alpha Trader"

    participants = [{"persona_id": "persona-a"}]
    detail = {"status": "ok", "chair_persona_id": "persona-b", "participants": participants}
    detail["data"] = {"status": "ok", "chair_persona_id": "persona-b", "participants": participants}
    _annotate_meeting_envelope(detail)
    assert detail["chair_persona_display_name"] == "Beta Chair"
    assert detail["data"]["chair_persona_display_name"] == "Beta Chair"
    assert participants[0]["persona_display_name"] == "Alpha Trader"


def test_incident_and_inbox_titles_are_presented_without_rewriting_stored_title(store):
    record = {
        "incident_id": "inc-1",
        "status": "open",
        "title": "Drawdown breach on rb-1",
        "binding_id": "rb-1",
    }
    case = _project_bff_incident_case(dict(record))
    assert case["title"] == "Drawdown breach on rb-1"
    assert case["title_display"] == "Drawdown breach on Alpha Trader / Momentum One"
    assert case["persona_display_name"] == "Alpha Trader"
    assert case["strategy_display_name"] == "Momentum One"

    home = _project_incident_home_item(dict(record))
    assert home["title"] == "Drawdown breach on rb-1"
    assert home["title_display"].endswith("Alpha Trader / Momentum One")
    detail = _project_incident_detail_incident(dict(record))
    assert detail["strategy_display_name"] == "Momentum One"

    item = _human_inbox_incident_item(dict(record))
    assert item["title"] == "Drawdown breach on rb-1"
    annotate_subject_row(DisplayNameResolver(), item)
    assert item["title_display"] == "Drawdown breach on Alpha Trader / Momentum One"
    assert item["persona_display_name"] == "Alpha Trader"
