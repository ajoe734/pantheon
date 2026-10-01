"""Restricted provider wiring and fail-closed reconstruction contracts."""
from copy import deepcopy

import pytest

from . import semantic_provider as provider
from .reconstruction import StrategyMap, SemanticReconstructionDraft, reconstruct_strategy_from_events
from .runner import run_reconstruction_worker
from .store import MemoryWorkshopStore
from services.research.strategy_spec.test_models import _strategy_spec_payload


def draft():
    return {
        "strategy_map": {"hypothesis": {"status": "partial", "summary": "Not specified"}},
        "explicit_facts": [], "inferences": [], "assumptions": [], "contradictions": [],
        "next_best_question": {"question_id": "q1", "text": "請先說明你的假設？",
                               "resolves": ["hypothesis"], "why_now": "尚未提出策略"},
        "strategy_spec": None,
    }


def envelope(payload):
    return {"status": "ok", "data": {"provider": "openclaw", "status": "completed",
                                       "output": {"structured_data": payload}}}


def invoke(messages=None):
    return provider.reconstruct_strategy_with_agent(
        workshop_id="ws1", sequence_no=1, events=[{"event_id": "e1", "sequence_no": 1}],
        messages_content=messages or ["沒有定義買進訊號，也尚未決定停損，不要幫我補上。"],
        tenant_id="tenant1", user_id="user1",
    )


def test_real_worker_calls_restricted_provider_and_preserves_semantic_output(monkeypatch):
    calls = []
    payload = draft()
    def request(self, method, path, **kw):
        calls.append((method, path, kw))
        return envelope(payload)
    monkeypatch.setattr(provider.OpenClawOpsClient, "_request", request)
    result = invoke()
    assert result.strategy_map.hypothesis.status == "partial"
    assert result.next_best_question.text == payload["next_best_question"]["text"]
    assert result.completeness.grade == "insufficient"
    assert result.draft_proposal is None
    method, path, kw = calls[0]
    assert method == "POST" and path.endswith("/openclaw/structured")
    assert "沒有定義買進訊號" in kw["body"]["prompt"]
    assert "tools" not in kw["body"] and "agent_id" not in kw["body"]
    assert kw["headers"]["X-Operator-Id"] == "user1"
    assert kw["timeout_seconds"] > 0
    assert result.provider_lineage["engine"] == provider.ENGINE_VERSION
    assert len(result.provider_lineage["input_sha256"]) == 64


@pytest.mark.parametrize("bad", [None, {}, {"status": "provider_error"}, envelope({}),
                                     envelope(draft() | {"completeness": {"grade": "trading_room_ready"}})])
def test_provider_malformed_or_authority_claims_fail_closed(monkeypatch, bad):
    monkeypatch.setattr(provider.OpenClawOpsClient, "_request", lambda *a, **kw: bad)
    with pytest.raises(provider.ReconstructionProviderError):
        invoke()


@pytest.mark.parametrize("citations", [None, [], [0], [2], [True], ["1"]])
def test_confirmed_fields_require_valid_message_citations(monkeypatch, citations):
    payload = draft()
    payload["strategy_map"]["hypothesis"] = {
        "status": "confirmed", "summary": "assertion", "details": {"message_numbers": citations},
    }
    monkeypatch.setattr(provider.OpenClawOpsClient, "_request", lambda *a, **kw: envelope(payload))
    with pytest.raises(provider.ReconstructionProviderError):
        invoke()


def test_provider_timeout_is_failed_card_and_retry_is_not_stale_success(monkeypatch):
    store = MemoryWorkshopStore()
    store.create_session({"workshop_id": "ws1", "tenant_id": "tenant1", "user_id": "user1"})
    store.create_event({"workshop_id": "ws1", "actor_type": "operator", "event_type": "message", "redacted_summary": "No entry defined"})
    def timeout(*a, **kw):
        raise provider.OpenClawOpsClientError("upstream secret detail", status_code=504, error_code="TIMEOUT")
    monkeypatch.setattr(provider.OpenClawOpsClient, "_request", timeout)
    kwargs = dict(store=store, canonical=None, workshop_id="ws1", tenant_id="tenant1", user_id="user1")
    with pytest.raises(provider.ReconstructionProviderError) as exc:
        run_reconstruction_worker(**kwargs)
    assert exc.value.status_code == 504
    assert "secret" not in str(exc.value)
    card = store.list_workshop_cards("ws1")[0]
    assert card["status"] == "failed" and "reconstruction" not in card["payload"]
    monkeypatch.setattr(provider.OpenClawOpsClient, "_request", lambda *a, **kw: envelope(draft()))
    assert run_reconstruction_worker(**kwargs)["job_status"] == "completed"
    monkeypatch.setattr(provider.OpenClawOpsClient, "_request", lambda *a, **kw: pytest.fail("replayed provider"))
    assert run_reconstruction_worker(**kwargs)["job_status"] == "replayed"
    with pytest.raises(ValueError, match="scope mismatch"):
        run_reconstruction_worker(**(kwargs | {"tenant_id": "other"}))


@pytest.mark.parametrize("mutation", ["invalid", "approved", "waive", "contradiction", "assumption", "uncited", "universe_mismatch", "cadence_mismatch", "valid"])
def test_only_valid_consistent_semantics_can_propose_a_draft_never_trade_ready(mutation):
    payload = draft()
    payload["strategy_map"] = {name: {"status": "confirmed", "summary": name,
                                       "details": {"message_numbers": [1]}}
                               for name in StrategyMap.model_fields}
    spec = deepcopy(_strategy_spec_payload())
    spec["lifecycle_state"] = "draft"
    spec["governance"]["approval_required"] = True
    payload["strategy_spec"] = spec
    payload["strategy_map"]["universe"]["details"].update(deepcopy(spec["market_scope"]))
    payload["strategy_map"]["exit_rules"]["details"]["rebalance_cadence"] = spec["execution_profile"].get("rebalance_cadence")
    if mutation == "uncited":
        payload["strategy_map"]["hypothesis"]["details"] = {}
    elif mutation == "universe_mismatch":
        payload["strategy_map"]["universe"]["details"]["symbols"] = ["DIFFERENT"]
    elif mutation == "cadence_mismatch":
        payload["strategy_map"]["exit_rules"]["details"]["rebalance_cadence"] = "different"
    elif mutation == "invalid":
        spec.pop("market_scope")
    elif mutation == "approved":
        spec["lifecycle_state"] = "active"
    elif mutation == "waive":
        spec["governance"]["approval_required"] = False
    elif mutation == "contradiction":
        payload["contradictions"] = ["buy and do not buy"]
    elif mutation == "assumption":
        payload["assumptions"] = ["assume 2% risk"]
    result = reconstruct_strategy_from_events(
        workshop_id="ws", sequence_no=1, events=[], messages_content=["fixture conversation"],
        semantic_draft=SemanticReconstructionDraft.model_validate(payload),
    )
    assert result.completeness.grade == ("draftable" if mutation == "valid" else "insufficient")
    assert (result.draft_proposal is not None) == (mutation == "valid")


@pytest.mark.parametrize("error,status", [(TimeoutError("private detail"), 504),
                                          (ValueError("private JSON failure"), 502),
                                          (ConnectionError("private connection failure"), 502)])
def test_unexpected_transport_errors_are_sanitized(monkeypatch, error, status):
    def fail(*a, **kw):
        raise error
    monkeypatch.setattr(provider.OpenClawOpsClient, "_request", fail)
    with pytest.raises(provider.ReconstructionProviderError) as exc:
        invoke()
    assert exc.value.status_code == status
    assert "private" not in str(exc.value)


def test_composed_schema_validates_real_specs_and_namespaces_refs(monkeypatch):
    from jsonschema import Draft202012Validator
    schema = provider.reconstruction_extraction_schema()
    Draft202012Validator.check_schema(schema)
    payload = draft() | {"strategy_spec": _strategy_spec_payload()}
    validator = Draft202012Validator(schema)
    validator.validate(payload)
    payload["strategy_spec"].pop("market_scope")
    assert list(validator.iter_errors(payload))

    # A future canonical schema may introduce local refs with names that also
    # appear in the reconstruction schema. Keep them in a separate namespace.
    monkeypatch.setattr(provider, "load_strategy_spec_schema", lambda: {
        "$id": "https://example.invalid/spec", "$schema": "http://json-schema.org/draft-07/schema#",
        "$defs": {"StrategyMap": {"type": "string", "const": "scoped"}},
        "type": "object", "required": ["value"],
        "properties": {"value": {"$ref": "#/$defs/StrategyMap"}},
    })
    validator = Draft202012Validator(provider.reconstruction_extraction_schema())
    validator.validate(draft() | {"strategy_spec": {"value": "scoped"}})
    assert list(validator.iter_errors(draft() | {"strategy_spec": {"value": "wrong"}}))


def test_content_changes_have_distinct_reconstruction_identity():
    kwargs = dict(workshop_id="ws", sequence_no=1, events=[], messages_content=["conversation"])
    first = reconstruct_strategy_from_events(**kwargs, semantic_draft=SemanticReconstructionDraft.model_validate(draft()))
    second_draft = draft() | {"inferences": ["Different interpretation"]}
    second = reconstruct_strategy_from_events(**kwargs, semantic_draft=SemanticReconstructionDraft.model_validate(second_draft))
    assert first.reconstruction_id != second.reconstruction_id


def test_input_bound_fails_before_provider_call(monkeypatch):
    monkeypatch.setattr(provider.OpenClawOpsClient, "_request", lambda *a, **kw: pytest.fail("provider called"))
    with pytest.raises(provider.ReconstructionProviderError) as exc:
        invoke(["x" * 65537])
    assert exc.value.status_code == 422
