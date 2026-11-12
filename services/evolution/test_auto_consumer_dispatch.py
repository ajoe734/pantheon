"""Consumer -> real Evolution API -> real dispatch worker -> research downstream.

No approval snapshot is injected.  A stateful Governance stand-in enforces what
the real owner enforces here (distinct proposer/decider principals, exact
version, deciding only an existing request); the dispatch worker's own
``require_execution_approval`` then reads the approval that the consumer's
ports actually created and decided.  Only the research downstream is scripted.
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import urllib.error
from pathlib import Path

import pytest

_tmp = tempfile.mkdtemp(prefix="evo_auto_dispatch_")
os.environ["EVOLUTION_DATA_DIR"] = _tmp
os.environ["INCIDENT_DATA_DIR"] = _tmp
_CP_GOV = Path(__file__).resolve().parent.parent / "control-plane" / "governance"
if str(_CP_GOV) not in sys.path:
    sys.path.insert(0, str(_CP_GOV))

from fastapi.testclient import TestClient  # noqa: E402

import services.evolution.auto_consumer_runtime as rt  # noqa: E402
from services.evolution import dispatch_outbox, dispatch_worker  # noqa: E402
from services.evolution import main as evo_main  # noqa: E402
from services.evolution.dispatch_outbox import (  # noqa: E402
    CompensationLedger, EvolutionDispatchOutbox, build_dispatch_outbox_store,
)
from services.evolution.dispatch_receipts import OUTCOME_RETRYABLE, DispatchReceipt  # noqa: E402
from services.evolution.testing_receipts import ALL_PLANES, install_scripted_adapter  # noqa: E402
from services.governance.approval_authority import ApprovalEvidence, ApprovalInvalid  # noqa: E402
from services.governance.test_approval_authority import approval_snapshot  # noqa: E402

client = TestClient(evo_main.app)
TENANT = "default"
PROPOSER_TOKEN, GATE_TOKEN = "tok-evo-proposer", "tok-gate"
PRINCIPALS = {PROPOSER_TOKEN: "evolution-execute-proposer", GATE_TOKEN: "gate"}
SPEC_ID, ARTIFACT_ID = "spec-d1", "art-d1"
ENV = {
    "EVOLUTION_AUTO_CONSUMER_ENABLED": "true", "EVOLUTION_API_URL": "http://evo",
    "EVOLUTION_SCHEDULER_TENANT_ID": TENANT,
    "AUTO_CONSUMER_PERSONA_URL": "http://per", "AUTO_CONSUMER_GOVERNANCE_URL": "http://gov",
    "AUTO_CONSUMER_CAPITAL_URL": "http://cap", "AUTO_CONSUMER_REGISTRY_URL": "http://reg",
    "AUTO_CONSUMER_PROPOSER_TOKEN": "tp", "AUTO_CONSUMER_PROPOSER_ID": "proposer",
    "AUTO_CONSUMER_GATE_TOKEN": GATE_TOKEN, "AUTO_CONSUMER_GATE_ID": "gate",
    "AUTO_CONSUMER_PERSONA_TOKEN": "tper", "AUTO_CONSUMER_CAPITAL_TOKEN": "tc", "AUTO_CONSUMER_REGISTRY_TOKEN": "tr",
}


class Governance:
    """Stateful approvals owner: principal from bearer token, never from the body."""

    def __init__(self):
        self.approvals: dict[str, dict] = {}
        self.down = False
        self.log: list[tuple[str, str]] = []

    def handle(self, method, path, token, body):
        if self.down:
            return 503, {"detail": "down"}
        actor = PRINCIPALS.get(token)
        if actor is None:
            return 401, {"detail": "unknown principal"}
        self.log.append((method, path))
        if path == "/api/governance/approvals" and method == "POST":
            if body["decision_id"] in self.approvals:
                return 409, {"detail": "exists"}
            if body["owner_user_id"] != actor:
                return 403, {"detail": "owner must be the verified principal"}
            self.approvals[body["decision_id"]] = {**body, "decision_state": "proposed", "version": 1}
            return 201, self.approvals[body["decision_id"]]
        approval_id = path.split("/")[4]
        record = self.approvals.get(approval_id)
        if record is None:
            return 404, {"detail": "nf"}
        if method == "GET":
            return 200, record
        op = path.rsplit("/", 1)[1]
        if body["expected_version"] != record["version"] or actor == record["owner_user_id"]:
            return 403, {"detail": "stale version or decider is the proposer"}
        if op == "review":
            record.update(decision_state="under_review", version=2)
        else:
            if body.get("expires_at") != record["expires_at"]:
                return 422, {"detail": "vote expiry must equal request expiry"}
            record.update(decision_state="decided", decision=body["outcome"], version=3,
                          decided_by=actor, decided_role=body["actor_role"])
        return 200, record

    def evidence(self, approval_id):
        record = self.approvals.get(approval_id)
        if record is None or record["decision_state"] != "decided":
            raise ApprovalInvalid("Governance denied exact decision read")
        return ApprovalEvidence.model_validate(approval_snapshot(
            decision_id=approval_id, tenant_id=record["tenant_id"], target_type=record["target_type"],
            target_id=record["target_id"], target_version=record["target_version"], risk_level=record["risk_level"],
            decision=record["decision"], actor_id=record["decided_by"], actor_role=record["decided_role"],
            owner_user_id=record["owner_user_id"], expires_at=record["expires_at"],
            metadata={"subject": record["subject"],
                      "approvals": [{"actor_id": record["decided_by"], "actor_role": record["decided_role"]}]},
        ))


class Reader:
    def __init__(self, gov):
        self.gov = gov

    def get(self, approval_id):
        return self.gov.evidence(approval_id)


class FlakyAdapter:
    """Research downstream: retryable on the first submit(s), then accepts."""

    kind = "test_downstream_record"
    plane = "research"

    def __init__(self, fail_first=0):
        self.fail_first, self.submissions = fail_first, []

    def submit(self, intent):
        self.submissions.append(dict(intent))
        if len(self.submissions) <= self.fail_first:
            return DispatchReceipt(outcome=OUTCOME_RETRYABLE, downstream_kind=self.kind, detail="busy")
        from services.evolution.dispatch_receipts import OUTCOME_PENDING
        return DispatchReceipt(outcome=OUTCOME_PENDING, downstream_kind=self.kind,
                               downstream_ref_id=f"run-{intent['decision_id']}", detail="accepted")

    def read_receipt(self, ref):
        from services.evolution.dispatch_receipts import OUTCOME_PENDING
        return DispatchReceipt(outcome=OUTCOME_PENDING, downstream_kind=self.kind, downstream_ref_id=ref)


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(evo_main, "store", evo_main.EvolutionDecisionStore(storage_path=str(tmp_path / "d.json")))
    outbox = EvolutionDispatchOutbox(build_dispatch_outbox_store(data_dir=tmp_path))
    monkeypatch.setattr(evo_main, "dispatch_outbox", outbox)
    monkeypatch.setattr(evo_main, "compensation_ledger", CompensationLedger(data_dir=tmp_path))
    gov = Governance()
    monkeypatch.setattr(dispatch_outbox, "configured_approval_reader", lambda domain: Reader(gov))

    # The Evolution owner's own filing code runs for real; only its urllib socket is redirected.
    class Resp(io.BytesIO):
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def urlopen(request, timeout=None):
        path = request.full_url[len("http://gov"):]
        body = json.loads(request.data) if request.data else None
        token = request.headers["Authorization"].split()[1]
        status, payload = gov.handle(request.get_method(), path, token, body)
        if status >= 400:
            raise urllib.error.HTTPError(request.full_url, status, "err", {}, io.BytesIO(json.dumps(payload).encode()))
        return Resp(json.dumps(payload).encode())

    monkeypatch.setattr(dispatch_outbox.urllib.request, "urlopen", urlopen)
    monkeypatch.setenv("EVOLUTION_GOVERNANCE_BASE_URL", "http://gov")
    monkeypatch.setenv("EVOLUTION_EXECUTE_PROPOSER_TOKEN", PROPOSER_TOKEN)
    monkeypatch.setattr(dispatch_outbox, "_default_proposer_token", lambda: PROPOSER_TOKEN)

    def transport(method, url, headers, body):
        if url.startswith("http://gov"):
            return gov.handle(method, url[len("http://gov"):], headers["Authorization"].split()[1], body)
        if url.startswith("http://evo"):
            r = client.request(method, url[len("http://evo"):], json=body,
                               headers={k: v for k, v in headers.items() if k.lower() == "x-tenant-id"})
            return r.status_code, (r.json() if r.content else {})
        if url == "http://cap/api/bindings":
            return 200, [{"binding_id": "pcb-persona-paper-1", "tenant_id": TENANT, "persona_id": "p1", "status": "active",
                          "metadata": {"registry_id": ARTIFACT_ID, "source_strategy_artifact_ref": SPEC_ID, "tenant_id": TENANT,
                                       "discovery_allocator": {"marker": True}}}]
        if url == f"http://reg/api/registry/strategy-artifacts/{ARTIFACT_ID}":
            return 200, {"entry": {"registry_id": ARTIFACT_ID, "owner_tenant": TENANT, "metadata": {"strategy_artifact": {
                "strategy_logic": {"kind": "factor_rule_v1"}, "lineage": {"source_strategy_spec_id": SPEC_ID}}}}}
        if url == "http://per/api/personas/p1":
            return 200, {"persona_id": "p1", "tenant_id": TENANT, "lifecycle_state": "paper_owner"}
        raise AssertionError(url)

    original = dict(evo_main.receipt_registry)
    adapter = FlakyAdapter()
    install_scripted_adapter(evo_main.receipt_registry, *ALL_PLANES)
    for plane in ALL_PLANES:
        evo_main.receipt_registry[plane] = adapter

    def http_get(url, timeout_seconds, *, tenant_id=None, auth_token=None):
        r = client.get(url[url.index("/api/"):], headers={"X-Tenant-Id": tenant_id} if tenant_id else {})
        if r.status_code >= 400:
            raise urllib.error.HTTPError(url, r.status_code, str(r.status_code), None, None)
        return r.json()

    def http_post(url, payload, timeout_seconds, *, tenant_id=None, auth_token=None):
        r = client.post(url[url.index("/api/"):], json=payload, headers={"X-Tenant-Id": tenant_id} if tenant_id else {})
        if r.status_code >= 400:
            raise urllib.error.HTTPError(url, r.status_code, str(r.status_code), None, None)
        return r.json()

    monkeypatch.setattr(dispatch_worker, "_http_get", http_get)
    monkeypatch.setattr(dispatch_worker, "_http_post", http_post)
    yield {"gov": gov, "outbox": outbox, "adapter": adapter, "transport": transport, "tmp": tmp_path,
           "compensations": evo_main.compensation_ledger}
    evo_main.receipt_registry.clear()
    evo_main.receipt_registry.update(original)


def _propose(decision_id="evo-ad-1", action="retrain"):
    r = client.post("/api/evolution/proposals", json={
        "decision_id": decision_id, "tenant_id": TENANT, "target_type": "candidate_artifact", "target_id": ARTIFACT_ID,
        "target_version": "1.0.0", "action_type": action, "rationale": "Paper drawdown breached baseline.",
        "created_by_role": "evolution_controller", "created_by_id": "evolution-controller-01",
        "linked_incident_id": f"inc-{decision_id}", "target_stage": "paper"})
    assert r.status_code == 201, r.text
    return r.json()


def _env(world):
    return dict(ENV, AUTO_CONSUMER_EVIDENCE_PATH=str(world["tmp"] / "ev.jsonl"),
                AUTO_CONSUMER_DECAY_LEDGER_PATH=str(world["tmp"] / "w.jsonl"))


def _poll(world):
    return dispatch_worker.run_poll(api_url="http://evolution.test", outbox=world["outbox"],
                                    registry=evo_main.receipt_registry, compensations=world["compensations"],
                                    timeout_seconds=5.0)


@pytest.mark.parametrize("action", ["retrain", "mutate", "revalidate"])
def test_auto_approved_research_work_is_dispatched_with_real_governance_authority(world, action):
    _propose(action=action)
    consumer = rt.build_consumer(_env(world), world["transport"])
    report = consumer.tick()
    assert report.approved == ["evo-ad-1"] and not report.errors, report
    decision = client.get("/api/evolution/proposals/evo-ad-1").json()
    approval_id = decision["approval_decision_id"]
    assert approval_id.startswith("apv-evoexec-evo-ad-1-") and approval_id in world["gov"].approvals
    approval = world["gov"].approvals[approval_id]
    assert approval["decision_state"] == "decided" and approval["owner_user_id"] != approval["decided_by"]
    assert approval["decided_role"] == "automated_gate" and approval["target_type"] == "evolution_execute"
    out = _poll(world)
    assert len(world["adapter"].submissions) == 1, out
    assert world["adapter"].submissions[0]["decision_id"] == "evo-ad-1"


def test_retrying_downstream_and_governance_outage_still_reach_research_once_approved(world):
    _propose()
    world["gov"].down = True
    env = _env(world)
    first = rt.build_consumer(env, world["transport"]).tick()
    assert first.errors and client.get("/api/evolution/proposals/evo-ad-1").json()["decision_state"] == "proposed"
    assert _poll(world)["claimed"] == 0 and not world["adapter"].submissions  # nothing approved, nothing dispatched
    world["gov"].down = False
    # Resume from a fresh consumer (new process): binding is persisted on the decision/Governance, not in memory.
    second = rt.build_consumer(env, world["transport"]).tick()
    assert second.approved == ["evo-ad-1"] and not second.errors
    world["adapter"].fail_first = 2
    from datetime import datetime, timedelta, timezone

    base = datetime.now(timezone.utc)
    for hour in range(1, 5):  # each tick is far enough out that any backoff is due
        dispatch_worker.run_poll(api_url="http://evolution.test", outbox=world["outbox"],
                                 registry=evo_main.receipt_registry, compensations=world["compensations"],
                                 timeout_seconds=5.0, now=base + timedelta(hours=hour))
    # two retryable failures, then accepted; every attempt re-read the real Governance approval
    assert len(world["adapter"].submissions) >= 3  # in-flight runs re-attach on later ticks
    assert {x["approval_decision_id"] for x in world["adapter"].submissions} == {
        client.get("/api/evolution/proposals/evo-ad-1").json()["approval_decision_id"]}


def test_decision_bound_to_a_synthetic_id_is_never_dispatched(world):
    _propose()
    for step, extra in (("review", {}), ("approve", {})):
        r = client.post(f"/api/evolution/proposals/evo-ad-1/{step}", json={
            "actor_role": "automated_gate", "actor_id": "x", "tenant_id": TENANT,
            "approval_decision_id": "auto-gate-evo-ad-1", "note": "synthetic"})
        assert r.status_code == 200, r.text
    out = _poll(world)
    assert not world["adapter"].submissions and out.get("executed", 0) == 0
