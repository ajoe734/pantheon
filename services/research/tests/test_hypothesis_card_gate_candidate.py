"""HYPOTHESIS-CARD-GATE-CANDIDATE-20261115: stored text -> card -> rule -> spec -> admission -> gate -> candidate.

The model outputs are recorded fixtures (``hypothesis_screen_entry_recorded.json``,
hand-authored model-shaped responses, not live provider captures); no live model
call is made. The gate's backtest result is a recorded governed evaluation bound to
the compiled artifact (same technique as the template-path registration test).
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from services.research.alpha_replication import admission as adm
from services.research.alpha_replication.admission import (
    CardSpecAdmissionOwner,
    register_card_pass,
)
from services.research.alpha_replication.revalidation_worker import AlphaRevalidationWorker
from services.research.alpha_replication.test_revalidation_worker import FakeAuthority
from services.research.candidate_registration import TemplateCandidateRegistrar
from services.research.tests.test_candidate_registration import (
    BARS_FIXTURE,
    OwnerEnvironment,
    _governed_passing_evaluation_for_artifact,
)
from services.registry.test_candidate_pool import recorded_market
from services.research.tests.test_persona_demand_routes_auth import TENANT
from services.source_ingestion import distillation_controller as dc
from services.source_ingestion import semantic_extraction_client as sec
from services.source_ingestion.controller_state import ControllerState, ControllerStateStore
from services.source_ingestion.distillation_controller import (
    DistillationControllerConfig,
    run_controller_tick,
)
from services.source_ingestion.tests.test_distillation_controller import DummyLoopWriter
from services.source_ingestion.tests.test_hypothesis_extraction import (
    _prediction_recording,
    _screen_entry_recording,
    _source,
)
from services.source_ingestion.tests.test_hypothesis_compiler import _proposal

ENABLED = "PANTHEON_L2_EXTRACTION_ENABLED"


def _dataset() -> dict[str, Any]:
    return json.loads(BARS_FIXTURE.read_text(encoding="utf-8"))["dataset"]


def _distill(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, source=None, bind=True,
             predictions=False) -> list[dict]:
    """Run the controller tick on a stored text record; return registry payloads it synced."""
    recording = _prediction_recording() if predictions else _screen_entry_recording()
    # Predictions card (recorded extraction) is compiled to a plain price rule the
    # governed daily-bars dataset can execute (no screen needing a pool-market dataset).
    proposal = _proposal("tw_price_daily", "close") if predictions else recording["proposal"]

    def transport(payload: dict[str, Any]) -> dict[str, Any]:
        assert "tools" not in payload  # recorded model output only, never a live call
        if payload["prompt"].startswith("Propose a factor_rule_v1"):
            return {"output": {"structured_data": copy.deepcopy(proposal)}}
        return copy.deepcopy(recording["response"])

    real = sec.SemanticExtractionClient

    class Client(real):  # type: ignore[misc, valid-type]
        def __init__(self, **kw: Any) -> None:
            super().__init__(transport_fn=transport, **kw)

    monkeypatch.setattr(sec, "SemanticExtractionClient", Client)
    monkeypatch.setenv(ENABLED, "1")
    if bind:
        ds = _dataset()
        monkeypatch.setenv("PANTHEON_L2_CARD_DATASET_ID", ds["dataset_id"])
        monkeypatch.setenv("PANTHEON_L2_CARD_SYMBOLS", "2330.TW")
        monkeypatch.setenv("PANTHEON_L2_CARD_SOURCE_DATASET_REFS", ",".join(ds["source_dataset_refs"][:2]))
    else:
        monkeypatch.delenv("PANTHEON_L2_CARD_DATASET_ID", raising=False)
        monkeypatch.delenv("PERSONA_POOL_SOURCE_DATASET_ID", raising=False)

    record = source or _source("src-1")
    if source is None:
        record.metadata["body"] = recording["stored_text"]
    record.metadata["tenant_id"] = TENANT
    evidence = tmp_path / "evidence.jsonl"
    evidence.write_text(json.dumps({"record_type": "source_record", "payload": record.to_dict()}) + "\n")
    config = DistillationControllerConfig(
        database_url="postgresql://x", registry_url="http://reg", interval_seconds=60, max_ticks=1,
        state_path=tmp_path / "state.json", alive_path=None, job_queue_path=tmp_path / "q.jsonl",
        seed_store_path=tmp_path / "seeds.jsonl", evidence_store_path=evidence, source_dirs=[tmp_path],
    )
    state = ControllerState(controller_id="c", controller_name="n", environment="test", tenant_id=TENANT,
                            deployment={"git_sha": "s"})
    ControllerStateStore(config.state_path).save(state)
    payloads: list[dict] = []
    registry: dict[str, dict] = {}
    monkeypatch.setattr(dc, "_get_registry_entry", lambda url, rid: registry.get(rid))

    def register(url: str, payload: dict) -> dict:
        payloads.append(copy.deepcopy(payload))
        md = dict(payload.get("metadata") or {})
        md.setdefault("strategy_spec", payload["strategy_spec"])
        entry = {**payload, "artifact_type": "strategy_spec", "artifact_state": "draft",
                 "lineage": {"parent_registry_ids": None, **dict(payload.get("lineage") or {}),
                             "source_strategy_spec_id": None}, "metadata": md}
        entry.pop("strategy_spec", None)
        entry.pop("source_digest", None)
        registry[payload["registry_id"]] = {"entry": entry}
        return {"entry": entry}

    monkeypatch.setattr(dc, "_register_strategy_spec_if_absent", register)
    run_controller_tick(config=config, state=state, store=ControllerStateStore(config.state_path),
                        writer=DummyLoopWriter())
    return payloads


def test_compiled_card_reaches_registry_spec_with_artifact_and_dataset_binding(tmp_path, monkeypatch):
    (payload,) = _distill(tmp_path, monkeypatch)
    meta = payload["strategy_spec"]["metadata"]
    assert meta["card_wiring"]["status"] == "gate_ready"
    assert meta["hypothesis_card"]["claim"]
    assert "factor_rule_v1" not in meta["hypothesis_card"]
    artifact = meta["strategy_artifact"]
    assert artifact["strategy_logic"]["kind"] == "factor_rule_v1"
    assert artifact["strategy_id"] == payload["strategy_spec"]["strategy_id"]
    assert artifact["parameters"]["symbols"] == ["2330.TW"]  # governed binding owns the universe
    assert meta["backtest_dataset"] == {
        "dataset_id": _dataset()["dataset_id"],
        "source_dataset_refs": _dataset()["source_dataset_refs"][:2],
        "binding": "governed_reference",
    }
    assert {"ref": _dataset()["dataset_id"], "kind": "dataset"} in payload["strategy_spec"]["data_dependencies"]


def test_card_without_governed_dataset_stays_research_only_draft_with_reason(tmp_path, monkeypatch):
    (payload,) = _distill(tmp_path, monkeypatch, bind=False)
    meta = payload["strategy_spec"]["metadata"]
    assert meta["card_wiring"] == {"status": "research_only_draft",
                                   "reason": "governed_dataset_binding_unavailable"}
    assert "strategy_artifact" not in meta and "backtest_dataset" not in meta
    assert meta["research_only"] is True


def test_market_source_still_produces_no_draft(tmp_path, monkeypatch):
    market = _source("src-mkt", "market")
    assert _distill(tmp_path, monkeypatch, source=market) == []


def _attested_market() -> dict[str, Any]:
    """Recorded candidate-pool market with the survivorship attestation set.

    Every compiled rule carries a ``screen``, and the gate refuses a screened
    candidate unless the market attests delisted/suspended coverage. The flags are
    set here by the test to exercise the pass path; a production dataset resolver
    must return them only when the governed dataset really attests them.
    """
    market = copy.deepcopy(recorded_market())
    market["includes_delisted"] = True
    market["includes_suspended"] = True
    return market


def _entry_from_payload(payload: dict) -> dict:
    """Registry entry view of a synced draft StrategySpec."""
    md = dict(payload.get("metadata") or {})
    md["strategy_spec"] = payload["strategy_spec"]
    md["tenant_id"] = TENANT
    return {**{k: payload[k] for k in ("registry_id", "checksum", "version")}, "artifact_type": "strategy_spec",
            "artifact_state": "draft", "strategy_id": payload["strategy_spec"]["strategy_id"], "metadata": md}


class _Registrar(TemplateCandidateRegistrar):
    pass


def test_unmatched_spec_stays_draft_and_creates_no_admission_or_governance(tmp_path, monkeypatch):
    (payload,) = _distill(tmp_path, monkeypatch)
    calls: list[str] = []
    registrar = _Registrar(data_dir=tmp_path / "a", owner_call=lambda **kw: calls.append(kw["step"]),
                           proposer_id="research-proposer", gate_id="paper-gate")
    store = adm.ReplicationAdmissionStore(tmp_path / "a")
    owner = CardSpecAdmissionOwner(registrar=registrar, store=store, matcher=lambda e, d: False)
    out = owner.admit(_entry_from_payload(payload), [{"demand_id": "d1", "tenant_id": TENANT, "persona_id": "p"}])
    assert out == {"status": "draft", "reason": "no_open_demand_match", "registry_id": payload["registry_id"]}
    assert calls == [] and store.list_admissions() == []


def test_owner_requires_distinct_proposer_and_gate_principals(tmp_path):
    registrar = _Registrar(data_dir=tmp_path, owner_call=lambda **kw: None,
                           proposer_id="same", gate_id="same")
    with pytest.raises(adm.CardAdmissionError):
        CardSpecAdmissionOwner(registrar=registrar, store=adm.ReplicationAdmissionStore(tmp_path),
                               matcher=lambda e, d: True)


def test_stored_text_to_registered_candidate_end_to_end(tmp_path, monkeypatch):
    env = OwnerEnvironment(tmp_path, monkeypatch)
    dataset = _dataset()
    (tmp_path / "d").mkdir()
    (payload,) = _distill(tmp_path / "d", monkeypatch, predictions=True)
    payload["metadata"]["tenant_id"] = TENANT
    posted = env.registry_client.post("/api/registry/strategy-specs", json=payload,
                                      headers=env.registry_headers(TENANT))
    assert posted.status_code in (200, 201), posted.text
    registry_id = payload["registry_id"]

    demand = env.research.research_demand_store.open(TENANT, {
        "persona_id": "persona-alpha", "mandate": "TW equity", "missing_datasets": [],
        "tried_families": [], "short_measure": "effective_bets", "pool_version": 0})
    registrar = TemplateCandidateRegistrar(
        data_dir=env.data_dir, owner_call=env.owner_call,
        proposer_id="research-orchestrator", gate_id="automated-replication-gate",
        admission_store=env.research.alpha_replication_admission_store,
        queue_store=env.research.alpha_replication_queue)
    store = env.research.alpha_replication_admission_store
    owner = CardSpecAdmissionOwner(registrar=registrar, store=store, matcher=lambda e, d: True)

    entry = env.registry_client.get(f"/api/registry/strategy-specs/{registry_id}",
                                    headers=env.registry_headers(TENANT)).json()["entry"]
    outcome = owner.admit(entry, [demand])
    assert outcome["status"] == "admitted", outcome
    admission = store.get_admission(TENANT, registry_id)
    assert admission["requested_by"] == "research-orchestrator" and admission["approver"] != admission["requested_by"]
    assert admission["demand_ref"] == demand["demand_id"]
    decision = env.governance.decisions[admission["approval_decision_id"]]
    assert decision.to_dict()["decision"] == "approved"

    approved = env.registry_client.get(f"/api/registry/strategy-specs/{registry_id}",
                                       headers=env.registry_headers(TENANT)).json()["entry"]
    assert approved["artifact_state"] == "approved"
    spec = approved["metadata"]["strategy_spec"]
    artifact = spec["metadata"]["strategy_artifact"]

    # Gate: recorded governed passing evaluation bound to the compiled artifact; the
    # resolver returns the governed dataset version for the spec's reference.
    def recorded_protocol(**kw: Any) -> dict[str, Any]:
        """Recorded governed evaluation bound to whichever artifact the gate submits.

        The screened candidate beats the whole-boundary control (``-entry-only``)
        out of sample, so the screen-contribution comparison passes on its merits.
        """
        ev = _governed_passing_evaluation_for_artifact(kw["strategy_artifact"], dataset, run_id=kw["run_id"])
        if kw["run_id"].endswith("-entry-only"):
            ev["out_of_sample"] = {**ev["out_of_sample"],
                                   "sharpe_ratio": ev["out_of_sample"]["sharpe_ratio"] - 1.0}
        return ev

    for target in ("services.research.vectorbt.adapter.vectorbt_adapter.run_replication_backtest_protocol",
                   "services.research.replication.gate.run_replication_backtest_protocol"):
        monkeypatch.setattr(target, recorded_protocol, raising=False)
    queue = env.research.alpha_replication_queue
    queue.enqueue({"tenant_id": TENANT, "strategy_spec_id": registry_id, "strategy_id": approved["strategy_id"],
                   "spec_version": approved["version"], "artifact_state": "approved",
                   "checksum": approved["checksum"], "approval_decision_id": approved["approval_decision_id"],
                   "approver": approved["approver"], "approved_at": approved["approved_at"]},
                  enqueued_by="test")
    resolved: list[dict] = []
    worker = AlphaRevalidationWorker(
        queue, env.data_dir, authority=FakeAuthority(), registry_url="http://registry.test",
        dataset_resolver=lambda binding: resolved.append(binding) or {**dataset, "candidate_pool_market": _attested_market()})
    monkeypatch.setattr(worker, "_fetch_strategy_spec_entry", lambda _id: approved)
    seen_requests: list[Any] = []
    from services.research.replication.gate import ReplicationGate
    real_eval = ReplicationGate.evaluate_candidate
    monkeypatch.setattr(ReplicationGate, "evaluate_candidate",
                        lambda self, req: seen_requests.append(req) or real_eval(self, req))
    worker.run_once(tenant_id=TENANT)
    assert resolved and resolved[0]["dataset_id"] == dataset["dataset_id"]
    runs = worker.list_runs(tenant_id=TENANT, strategy_spec_id=registry_id)
    assert len(runs) == 1
    meta = runs[0]["metadata"]
    assert meta["replication_gate"]["results"], meta
    # Card predictions reach the gate mechanism stage (no longer silently untested).
    assert meta["mechanism_status"] is not None
    assert seen_requests and seen_requests[0].research_handoff["hypothesis_predictions"] == \
        spec["metadata"]["hypothesis_predictions"]
    assert runs[0]["status"] == "completed", [r for r in meta["replication_gate"]["results"] if r["criterion_id"] == "real_backtest_validation"]
    assert meta["replication_gate"]["admission_status"] == "admitted"

    reg = register_card_pass(registrar=registrar, entry=approved, demand=demand, run_metadata=meta)
    assert reg["status"] == "approved" and reg["demand_ref"] == demand["demand_id"]
    got = env.registry_client.get(f"/api/registry/strategy-artifacts/{artifact['artifact_id']}",
                                  headers=env.registry_headers(TENANT)).json()["entry"]
    assert got["artifact_state"] == "approved" and got["replication_success"] is True
