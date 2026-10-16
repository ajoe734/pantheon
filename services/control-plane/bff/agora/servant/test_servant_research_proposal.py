"""Servant research proposals: data-only draft -> 'draft' plan, nothing dispatched until approval."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from services.control_plane.bff.agora.research.router import create_research_router
from services.control_plane.bff.agora.research.routes.common import ResearchPlanCreateRequest
from services.control_plane.bff.agora.research.store import MemoryResearchPlanStore
from services.control_plane.bff.agora.servant import research_proposal
from services.control_plane.bff.agora.strategy_workshop.operations import WorkshopCanonicalOperations
from services.control_plane.bff.agora.strategy_workshop.store import MemoryWorkshopStore
from services.control_plane.bff.personas.service import _bff_error, _require_operator_role, _require_read_role
from services.research.constants import ALLOWLISTED_STAGE_BACKENDS

_adapter_dir = str(Path(__file__).resolve().parents[4] / "openclaw-gateway-adapter")
if _adapter_dir not in sys.path:
    sys.path.insert(0, _adapter_dir)
from assistant_openclaw_provider import _validate_extraction_arguments, OpenClawProviderError

_WORKSHOP = "ws-servant-proposal"
_URL = f"/bff/agora/workshops/{_WORKSHOP}/research-plans/servant-proposal"
_VALID = {
    "spec_version": "1.0",
    "strategy_id": "strategy-servant",
    "strategy_spec_registry_id": "registry-servant-v1",
    "stages": [{"stage_type": "prototype_backtest"}],
}
_COUNTEREXAMPLE_EXTRA_FIELDS = {
    "spec_version": "1.0",
    "strategy_id": "strategy-servant",
    "strategy_spec_registry_id": "registry-servant-v1",
    "stages": [
        {
            "stage_type": "prototype_backtest",
            "model": {},
            "universe": ["2330.TW"],
            "quantity1": 1,
        }
    ],
}
_VALID_COMPLETE = {
    "spec_version": "1.0",
    "strategy_id": "strategy-servant",
    "strategy_spec_registry_id": "registry-servant-v1",
    "stages": [
        {
            "stage_id": "stage-backtest-1",
            "stage_type": "prototype_backtest",
            "status": "pending",
            "dependencies": ["stage-prep-0"],
            "required_capability": "backtest",
            "routing": {
                "preferred_backend": "vectorbt",
                "fallback_policy": "none",
                "backend_mode": "real",
                "routing_reason": "servant-recommendation",
            },
            "input_refs": ["input-dsv-1"],
            "output_refs": ["output-dsv-1"],
            "parameters": {"lookback_bars": 20, "threshold": 0.5},
            "blocking_reasons": ["awaiting_data"],
            "dataset": {"dataset_snapshot_id": "snap-001"},
        }
    ],
    "budget": {
        "compute_tier": "standard",
        "max_runtime_seconds": 1800,
        "max_parallel_stages": 2,
        "external_data_spend_allowed": False,
    },
    "execution_constraints": {
        "max_runtime_hours": 0.5,
        "compute_tier": "standard",
        "environments": ["paper"],
    },
    "dataset": {"dataset_snapshot_id": "snap-001"},
}
_VALID_NULLABLE = {
    "spec_version": "1.0",
    "strategy_id": "strategy-servant",
    "strategy_spec_registry_id": "registry-servant-v1",
    "stages": [
        {
            "stage_type": "prototype_backtest",
            "stage_id": None,
            "routing": None,
            "input_refs": None,
            "output_refs": None,
            "parameters": None,
            "blocking_reasons": None,
            "dataset": None,
        }
    ],
    "budget": None,
    "execution_constraints": None,
    "dataset": None,
}


class _FakeClient:
    draft: Any = _VALID
    calls: list[dict] = []
    plain_invokes = 0

    def _assistant_timeout_seconds(self):
        return 5.0

    def _request(self, method, path, **kwargs):
        type(self).calls.append({"method": method, "path": path, **kwargs})
        return {"status": "ok", "data": {"output": {"structured_data": type(self).draft}}}

    def invoke_assistant_provider(self, **kwargs):  # pragma: no cover - must never be used
        type(self).plain_invokes += 1
        raise AssertionError("servant proposal must use the structured, data-only path")


@pytest.fixture()
def fake(monkeypatch):
    _FakeClient.calls = []
    _FakeClient.plain_invokes = 0
    _FakeClient.draft = dict(_VALID)
    monkeypatch.setattr(research_proposal, "OpenClawOpsClient", _FakeClient)
    return _FakeClient


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    # Runs are projected from the research owner (376ad416d); a plan that was never
    # dispatched has no owner runs, so the owner is configured and lists none.
    monkeypatch.setenv("PANTHEON_RESEARCH_ORCHESTRATOR_API_URL", "http://research-owner.test")
    monkeypatch.setattr(WorkshopCanonicalOperations, "list_research_runs", lambda _self, **_kwargs: [])
    workshops = MemoryWorkshopStore()
    workshops.create_session({"workshop_id": _WORKSHOP, "tenant_id": "tenant-a", "user_id": "owner"})
    identity = SimpleNamespace(operator_id="owner", roles=["operator"], claims={"sub": "owner", "tenant_id": "tenant-a"})
    router = create_research_router(
        extract_identity=lambda _: identity,
        require_read_role=_require_read_role,
        require_write_role=_require_operator_role,
        bff_error=_bff_error,
        utc_now=lambda: "2026-09-30T00:00:00Z",
        research_plan_store=MemoryResearchPlanStore(),
        workshop_store=workshops,
    )
    monkeypatch.setattr(router.service, "_publish_research_event", lambda *args: None)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _headers(key=None, if_match=None):
    headers = {"X-Tenant-Id": "tenant-a"}
    if key:
        headers["Idempotency-Key"] = key
    if if_match:
        headers["If-Match"] = if_match
    return headers


def _plans(client) -> list:
    return client.get(f"/bff/agora/workshops/{_WORKSHOP}/research-plans", headers=_headers()).json()["items"]


def test_schema_offers_only_the_data_contract():
    schema = research_proposal.research_plan_extraction_schema()
    assert "tools" not in schema
    assert schema["properties"]["stages"]["items"]["properties"]["stage_type"]["enum"]


def test_valid_draft_creates_draft_plan_that_cannot_dispatch(client, fake):
    response = client.post(_URL, headers=_headers("servant-prop-ok"), json={"prompt": "test momentum"})
    assert response.status_code == 201, response.text
    created = response.json()
    plan_id = created["data"]["plan_id"]
    assert created["data"]["status"] == "draft"
    assert len(fake.calls) == 1 and fake.plain_invokes == 0
    call = fake.calls[0]
    assert call["path"].endswith("/providers/openclaw/structured")
    assert set(call["body"]) == {"mode", "prompt", "extraction_schema"}

    dispatch = client.post(
        f"/bff/agora/research-plans/{plan_id}/runs",
        headers=_headers("servant-prop-dispatch", created["meta"]["etag"]),
    )
    assert dispatch.status_code == 409, dispatch.text
    runs = client.get(f"/bff/agora/research-plans/{plan_id}/runs", headers=_headers())
    assert runs.json()["items"] == []

    approve = client.post(
        f"/bff/agora/research-plans/{plan_id}/approve",
        headers=_headers("servant-prop-approve", created["meta"]["etag"]),
    )
    assert approve.status_code == 200, approve.text


@pytest.mark.parametrize(
    "draft",
    [
        {**_VALID, "stages": []},
        {**_VALID, "spec_version": "2.0"},
        {**_VALID, "stages": [{"stage_type": "place_live_order"}]},
        {**_VALID, "unexpected": True},
        {"spec_version": "1.0"},
        "not-an-object",
        _COUNTEREXAMPLE_EXTRA_FIELDS,
    ],
)
def test_invalid_draft_creates_no_plan(client, fake, draft):
    fake.draft = draft
    response = client.post(_URL, headers=_headers("servant-prop-bad"), json={"prompt": "bad"})
    assert response.status_code in {422, 502}, response.text
    assert _plans(client) == []


def test_schema_derived_from_canonical_dto_and_forbids_stage_extras():
    schema = research_proposal.research_plan_extraction_schema()
    assert "$defs" not in schema
    assert "$ref" not in str(schema)
    assert "anyOf" not in str(schema)
    assert "default" not in schema.get("properties", {}).get("budget", {})
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert schema["properties"]["spec_version"]["enum"] == ["1.0"]

    stage_items = schema["properties"]["stages"]["items"]
    assert stage_items["type"] == "object"
    assert stage_items["additionalProperties"] is False
    assert stage_items["properties"]["stage_type"]["enum"] == sorted(ALLOWLISTED_STAGE_BACKENDS)

    # Optional canonical DTO properties are present and nullable
    assert schema["properties"]["budget"]["type"] == ["object", "null"]
    assert schema["properties"]["budget"]["additionalProperties"] is False
    assert schema["properties"]["execution_constraints"]["type"] == ["object", "null"]
    assert schema["properties"]["execution_constraints"]["additionalProperties"] is False
    assert schema["properties"]["dataset"]["type"] == ["object", "null"]

    routing_prop = stage_items["properties"]["routing"]
    assert routing_prop["type"] == ["object", "null"]
    assert routing_prop["additionalProperties"] is False


def test_three_extra_field_counterexample_rejected_by_adapter_and_dto_and_creates_no_plan(client, fake):
    schema = research_proposal.research_plan_extraction_schema()

    # Adapter rejects extra stage properties because stage items forbids extras
    with pytest.raises(OpenClawProviderError) as adapter_exc:
        _validate_extraction_arguments(_COUNTEREXAMPLE_EXTRA_FIELDS, schema)
    err_msg = str(adapter_exc.value)
    assert "stages[0].model" in err_msg
    assert "additionalProperties is false" in err_msg

    # Canonical DTO rejects extra stage properties
    with pytest.raises(ValidationError) as dto_exc:
        ResearchPlanCreateRequest.model_validate(_COUNTEREXAMPLE_EXTRA_FIELDS)
    dto_err_str = str(dto_exc.value)
    assert "stages.0.model" in dto_err_str
    assert "stages.0.universe" in dto_err_str
    assert "stages.0.quantity1" in dto_err_str

    # Endpoint rejects counterexample and creates zero plans
    fake.draft = _COUNTEREXAMPLE_EXTRA_FIELDS
    resp = client.post(_URL, headers=_headers("servant-counter-bad"), json={"prompt": "counterexample"})
    assert resp.status_code in {422, 502}
    assert _plans(client) == []


def test_valid_complete_draft_accepted_by_adapter_and_dto_and_stored_as_draft(client, fake):
    schema = research_proposal.research_plan_extraction_schema()

    # Adapter accepts complete valid draft
    _validate_extraction_arguments(_VALID_COMPLETE, schema)

    # Canonical DTO accepts complete valid draft
    dto_plan = ResearchPlanCreateRequest.model_validate(_VALID_COMPLETE)
    assert dto_plan.budget.max_runtime_seconds == 1800
    assert dto_plan.execution_constraints.max_runtime_hours == 0.5

    # Endpoint stores plan as 'draft' with zero runs and cannot dispatch before approval
    fake.draft = _VALID_COMPLETE
    resp = client.post(_URL, headers=_headers("servant-complete-ok"), json={"prompt": "full proposal"})
    assert resp.status_code == 201, resp.text
    created = resp.json()
    assert created["data"]["status"] == "draft"
    plan_id = created["data"]["plan_id"]

    # Draft cannot dispatch
    dispatch = client.post(
        f"/bff/agora/research-plans/{plan_id}/runs",
        headers=_headers("servant-complete-dispatch", created["meta"]["etag"]),
    )
    assert dispatch.status_code == 409
    assert client.get(f"/bff/agora/research-plans/{plan_id}/runs", headers=_headers()).json()["items"] == []


def test_valid_draft_with_nullable_fields_accepted_by_adapter_and_dto(client, fake):
    schema = research_proposal.research_plan_extraction_schema()

    # Adapter accepts nullable fields set to None
    _validate_extraction_arguments(_VALID_NULLABLE, schema)

    # Canonical DTO accepts nullable fields set to None
    dto_plan = ResearchPlanCreateRequest.model_validate(_VALID_NULLABLE)
    assert dto_plan.budget is None
    assert dto_plan.execution_constraints is None

    # Endpoint stores plan
    fake.draft = _VALID_NULLABLE
    resp = client.post(_URL, headers=_headers("servant-nullable-ok"), json={"prompt": "nullable proposal"})
    assert resp.status_code == 201, resp.text
    assert resp.json()["data"]["status"] == "draft"


def test_schema_projection_fails_closed_on_unknown_cyclic_refs_and_general_unions():
    # Unknown reference
    with pytest.raises(ValueError, match="Unknown reference"):
        research_proposal._project_canonical_schema({"$defs": {}, "properties": {"x": {"$ref": "#/$defs/Missing"}}})

    # External reference
    with pytest.raises(ValueError, match="Unknown or non-local reference"):
        research_proposal._project_canonical_schema({"$defs": {}, "properties": {"x": {"$ref": "https://example.com/schema"}}})

    # Cyclic reference
    cyclic = {
        "$defs": {
            "A": {"properties": {"b": {"$ref": "#/$defs/B"}}},
            "B": {"properties": {"a": {"$ref": "#/$defs/A"}}},
        },
        "properties": {"x": {"$ref": "#/$defs/A"}},
    }
    with pytest.raises(ValueError, match="Cyclic reference detected"):
        research_proposal._project_canonical_schema(cyclic)

    # Non-nullable general union
    union_schema = {"properties": {"x": {"anyOf": [{"type": "string"}, {"type": "integer"}]}}}
    with pytest.raises(ValueError, match="Unsupported general union"):
        research_proposal._project_canonical_schema(union_schema)

    # Multi-type nullable general union
    multi_union = {"properties": {"x": {"anyOf": [{"type": "string"}, {"type": "integer"}, {"type": "null"}]}}}
    with pytest.raises(ValueError, match="Unsupported general union"):
        research_proposal._project_canonical_schema(multi_union)

    # draft_research_plan fails closed (ServantDraftError) if schema projection fails
    with pytest.raises(research_proposal.ServantDraftError) as exc_info:
        research_proposal.draft_research_plan("test", operator_id="op", canonical_schema=union_schema)
    assert exc_info.value.status_code == 422


def test_schema_projection_retains_unsupported_constraints_for_fail_closed_validation():
    schema_with_multiple_of = {
        "type": "object",
        "properties": {
            "num": {
                "type": "integer",
                "multipleOf": 5,
            }
        },
    }
    proj = research_proposal._project_canonical_schema(schema_with_multiple_of)
    assert proj["properties"]["num"]["multipleOf"] == 5

    with pytest.raises(OpenClawProviderError) as exc_info:
        _validate_extraction_arguments({"num": 10}, proj)
    assert "multipleOf" in str(exc_info.value)

