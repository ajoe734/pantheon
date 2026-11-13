"""Real authenticated demand route -> shared composer -> owner HTTP -> influence.

Owner responses are controlled fixtures. This proves source integration and
failure handling; it makes no hosted, retrieval-quality or trading claim.
"""
from copy import deepcopy
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from jsonschema import validate

from services.knowledge import case_file as cf
from services.knowledge.test_case_file_integration import _Fixture
from services.research.research_demand import ResearchDemandStore
from services.research.retrieval_influence import (
    ResearchRetrievalInfluenceError,
    demand_case_file_influence, reset_influence_store,
)
from services.research.tests.test_research_demand import BODY, T0
from services.research.tests.test_persona_demand_routes_auth import (
    BASE, SECRET, TENANT, ISSUER, AUDIENCE, _cycle_headers, _headers,
)
from services.research.tests.test_research_orchestrator_http_service import _load_service_module

LESSON = "lessons:memory_institutional:prior-failure"


@pytest.fixture
def demand_route(tmp_path, monkeypatch):
    routes = {
        "/api/registry/strategies/a/entries": [],
        "/api/registry/strategies/b/entries": [],
        "/api/personas/p1/strategy-pool/versions": {"versions": [], "refusals": []},
        "/api/personas/p1/strategy-pool": {"pool_version": 2, "members": []},
        "/api/memory/retrieve": {"retrieval_mode": "hybrid", "hits": [{
            "type": "institutional", "relevance_score": .9,
            "entry": {"entry_id": "prior-failure", "knowledge_type": "research_finding",
                      "content": {"headline": "Family b previously failed without tw-margin data",
                                  "tags": ["family:b", "dataset:tw-margin", "negative_memory"]}},
        }]},
        "/api/search/v2/query": {"retrieval_mode": "lexical_only", "results": [{
            "result_id": "search-1", "relevance_score": .7, "citations": ["source-1"],
            "evidence_bundle_id": "bundle-1", "matched_items": [{
                "object_id": "finding-1", "excerpt": "Missing tw-margin observations invalidated the prior trial"}]}]},
    }
    server = _Fixture(routes)
    for owner in ("REGISTRY", "PERSONA", "MEMORY", "SEARCH"):
        monkeypatch.setenv(f"PANTHEON_{owner}_API_URL", server.endpoint.base_url)
    monkeypatch.setenv("RESEARCH_CASE_FILE_ACTOR_ID", "research-service")
    monkeypatch.setenv("RESEARCH_CASE_FILE_ACTOR_ROLES", "researcher")
    for key, value in {"AUTH_MODE": "strict", "JWT_SECRET": SECRET, "JWT_ISSUER": ISSUER,
                       "JWT_AUDIENCE": AUDIENCE, "MFA_REQUIRED": "false"}.items():
        monkeypatch.setenv("PANTHEON_RESEARCH_" + key, value)
    reset_influence_store()
    module = _load_service_module(data_dir=str(tmp_path))
    # Keep deterministic demand time; retain the production store and index wiring.
    module.research_demand_store._now = lambda: T0
    client = TestClient(module.app)
    try:
        yield module, client, server
    finally:
        server.shutdown()
        server.server_close()
        reset_influence_store()


def post(client, body=None):
    response = client.post(BASE, headers=_cycle_headers(), json=BODY if body is None else body)
    assert response.status_code == 201, response.text
    return response.json()


def test_next_demand_cites_prior_failure_through_real_route(demand_route):
    module, client, owners = demand_route
    first = post(client)
    next_demand = post(client, {**BODY, "short_measure": "effective_bets"})
    assert first["demand_id"] != next_demand["demand_id"]
    case = next_demand["case_file"]
    validate(case, json.loads(Path(cf.__file__).with_name("case_file.schema.json").read_text()))
    assert next_demand["case_file_id"] == case["case_file_id"]
    assert next_demand["case_file_digest"] == case["digest"]
    assert case["subject"]["strategy_families"] == ["a", "b"]
    assert case["subject"]["missing_datasets"] == ["tw-margin"]
    assert case["execution_may_read"] is False
    influence = next_demand["retrieval_influence"]
    assert LESSON in influence["metadata"]["cited_item_ids"]
    assert "similar_history:memory_negative_memory:prior-failure" in influence["metadata"]["cited_item_ids"]
    assert influence["selected_memory_refs"] == ["prior-failure"]
    assert influence["selected_evidence_refs"] == ["finding-1"]
    assert influence["influence_state"] == "influence_unknown" and influence["influence_weight"] is None
    assert module.research_demand_store.influence_store.get_record(influence["retrieval_id"]).to_dict() == influence
    # Both demand context and index survive reconstruction from disk.
    reloaded = ResearchDemandStore(module.research_demand_store.path)
    saved = next(r for r in reloaded.list(TENANT) if r["demand_id"] == next_demand["demand_id"])
    assert saved["case_file"] == case
    assert reloaded.influence_store.get_record(influence["retrieval_id"]).to_dict() == influence
    assert all(call[2] == TENANT for call in owners.calls)
    paths = [urlsplit(c[1]).path for c in owners.calls]
    assert "/api/registry/strategies/a/entries" in paths and "/api/registry/strategies/b/entries" in paths
    for call in owners.calls:
        if call[1].startswith("/api/memory/retrieve"):
            query = parse_qs(urlsplit(call[1]).query)
            assert set(query["tags"]) >= {"family:a", "family:b", "dataset:tw-margin"}
            assert "tw-margin" in query["query"][0]
            assert query["actor_id"] == ["research-service"]
        if call[1].startswith("/api/search/v2/query"):
            assert "tw-margin" in call[3]["query"]


def test_duplicate_demand_does_not_retrieve_or_overwrite_context(demand_route):
    module, client, owners = demand_route
    first = post(client)
    count = len(owners.calls)
    owners.routes["/api/memory/retrieve"]["hits"] = []
    again = post(client)
    assert not again["created"] and len(owners.calls) == count
    assert again["case_file"] == first["case_file"]
    assert again["retrieval_influence"] == first["retrieval_influence"]
    assert len(module.research_demand_store.influence_store.list_records()) == 1


@pytest.mark.parametrize("field", ["tenant_id", "persona_id"])
def test_influence_refuses_foreign_subject(demand_route, field):
    _, client, _ = demand_route
    demand = post(client)
    with pytest.raises(ResearchRetrievalInfluenceError, match="subject_mismatch"):
        demand_case_file_influence({**demand, field: "foreign"}, demand["case_file"])


@pytest.mark.parametrize("ids", [["invented"], [LESSON, LESSON]])
def test_influence_refuses_unknown_or_duplicate_citations(demand_route, ids):
    _, client, _ = demand_route
    demand = post(client)
    with pytest.raises(ResearchRetrievalInfluenceError, match="citation"):
        demand_case_file_influence(demand, demand["case_file"], cited_item_ids=ids)
    subset = demand_case_file_influence(demand, demand["case_file"], cited_item_ids=[LESSON])
    assert subset.metadata["cited_item_ids"] == [LESSON]
    assert subset.selected_evidence_refs == []


def test_influence_refuses_tampered_case_file(demand_route):
    _, client, _ = demand_route
    demand = post(client)
    case = deepcopy(demand["case_file"])
    case["groups"]["lessons"]["items"][0]["excerpt"] = "forged finding"
    with pytest.raises(ResearchRetrievalInfluenceError, match="digest_mismatch"):
        demand_case_file_influence(demand, case)


@pytest.mark.parametrize("missing", ["caller", "families"])
def test_missing_context_keeps_demand_and_records_unavailable(demand_route, monkeypatch, missing):
    module, client, owners = demand_route
    body = dict(BODY)
    if missing == "caller":
        monkeypatch.delenv("RESEARCH_CASE_FILE_ACTOR_ROLES")
        reason = "case_file_caller_context_missing"
    else:
        body["tried_families"] = []
        reason = "case_file_strategy_families_missing"
    demand = post(client, body)
    assert demand["created"] and demand["status"] == "open"
    assert demand["case_file"] is demand["case_file_id"] is demand["case_file_digest"] is None
    assert demand["case_file_unavailable_reason"] == reason
    assert demand["retrieval_influence"]["metadata"]["unavailable_reason"] == reason
    assert demand["retrieval_influence"]["selected_memory_refs"] == []
    assert demand["retrieval_influence"]["metadata"]["cited_item_ids"] == []
    assert module.research_demand_store.list(TENANT)[0]["case_file_unavailable_reason"] == reason
    assert not owners.calls


def test_denied_memory_records_failure_and_never_invents_a_citation(demand_route, monkeypatch):
    _, client, _ = demand_route
    original = cf.http_json
    def deny_memory(endpoint, path, **kwargs):
        if path == "/api/memory/retrieve":
            raise cf.OwnerUnavailable("http_403:authz_denied")
        return original(endpoint, path, **kwargs)
    monkeypatch.setattr(cf, "http_json", deny_memory)
    demand = post(client)
    group = demand["case_file"]["groups"]["lessons"]
    assert group["status"] == "unavailable" and "authz_denied" in group["reason"]
    assert demand["retrieval_influence"]["selected_memory_refs"] == []
    assert demand["retrieval_influence"]["metadata"]["unavailable_sources"]["lessons"] == group["unavailable"]


def test_malformed_owner_response_records_unavailability(demand_route):
    _, client, owners = demand_route
    owners.routes["/api/memory/retrieve"] = {"hits": [None]}
    demand = post(client)
    assert demand["case_file"] is None
    assert demand["case_file_unavailable_reason"].startswith("case_file_composition_failed:")


def test_index_failure_keeps_canonical_influence_in_demand(demand_route, monkeypatch):
    module, client, _ = demand_route
    def unavailable(_):
        raise OSError("fixture disk failure")
    monkeypatch.setattr(module.research_demand_store.influence_store, "create_record", unavailable)
    demand = post(client)
    assert demand["retrieval_influence_unavailable_reason"] == "influence_store_unavailable:OSError"
    assert LESSON in module.research_demand_store.list(TENANT)[0]["retrieval_influence"]["metadata"]["cited_item_ids"]


def test_service_cannot_be_spoofed_to_read_private_or_foreign_memory(demand_route):
    _, client, owners = demand_route
    for persona in ("p1", "other-persona"):
        owners.routes["/api/memory/retrieve"]["hits"].append({
            "type": "persona", "relevance_score": 1,
            "entry": {"memory_id": "secret-" + persona, "persona_id": persona,
                      "relevance_scope": "persona_private", "content": {
                          "summary": "secret", "tags": ["family:a", "negative_memory"]}}})
    demand = post(client, {**BODY, "actor_id": "p1", "actor_roles": ["persona_session"],
                           "case_file": {"item_id": "forged"}, "cited_item_ids": ["forged"]})
    assert demand["case_file"]["caller"] == {"actor_id": "research-service", "is_subject_persona": False}
    assert all("secret" not in ref for ref in demand["retrieval_influence"]["selected_memory_refs"])
    assert all("secret" not in ref for ref in demand["retrieval_influence"]["metadata"]["cited_item_ids"])
    assert "forged" not in demand["retrieval_influence"]["metadata"]["cited_item_ids"]


def test_unauthorized_request_does_not_retrieve(demand_route):
    _, client, owners = demand_route
    assert client.post(BASE, headers=_headers(), json=BODY).status_code == 403
    assert not owners.calls
