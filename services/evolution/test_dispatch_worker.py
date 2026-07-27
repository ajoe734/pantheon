"""
Tests for the evolution dispatch worker — durable outbox delivery (L12-EVO-001).

This suite replaces the LOOP-AUTO-EVO-004 poll-and-execute tests.  That worker
read the approved-decision list and treated the ``/execute`` response's
``submitted`` status as proof of execution; both the worker and that contract
are gone.  What is verified here is the contract that replaced it:

- every supported approved action writes a durable dispatch outbox record, and
  a duplicate trigger reuses it instead of dispatching twice;
- a crash between approval and activation is recovered by reconcile, so an
  approved decision is never left with no dispatch on record;
- a decision reaches ``executed`` only on a terminal receipt the service reads
  back itself, and an in-flight downstream keeps it approved;
- a terminal downstream failure records a durable compensation and dead-letters
  rather than reporting success;
- DLQ replay is refused inside its cooldown, and the cooldown is derived from
  durable state so a restart or a duplicate replay trigger cannot shorten it.

Run:
    python3 -m pytest services/evolution/test_dispatch_worker.py -v
"""
from __future__ import annotations

import os
import sys
import tempfile
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

# ---- Isolate storage BEFORE importing main ----
_tmp = tempfile.mkdtemp(prefix="evo_dispatch_test_")
os.environ["EVOLUTION_DATA_DIR"] = _tmp
os.environ["INCIDENT_DATA_DIR"] = _tmp

# ---- Make platform objects importable ----
_CP_GOV = Path(__file__).resolve().parent.parent / "control-plane" / "governance"
if str(_CP_GOV) not in sys.path:
    sys.path.insert(0, str(_CP_GOV))

from fastapi.testclient import TestClient  # noqa: E402

from services.evolution import dispatch_worker  # noqa: E402
from services.evolution import main as evo_main  # noqa: E402
from services.evolution.dispatch_outbox import (  # noqa: E402
    CompensationLedger,
    EvolutionDispatchError,
    EvolutionDispatchOutbox,
    build_dispatch_outbox_store,
    dispatch_identity,
)
from services.evolution.dispatch_receipts import (  # noqa: E402
    OUTCOME_FAILED,
    OUTCOME_RETRYABLE,
    DispatchReceipt,
    build_adapter_registry,
)
from services.evolution.testing_receipts import (  # noqa: E402
    ALL_PLANES,
    RECEIPT_KIND,
    install_scripted_adapter,
)

client = TestClient(evo_main.app)

RESEARCH_TENANT = "tenant-research-alpha"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """Point the service's decision, outbox, and compensation stores at tmp_path.

    Rebuilding the module-level stores (rather than only clearing files) keeps
    each test's durability assertions honest: the objects under test read the
    same files a restarted process would.
    """
    from evolution_decision import EvolutionDecisionStore  # type: ignore

    monkeypatch.setattr(
        evo_main,
        "store",
        EvolutionDecisionStore(storage_path=str(tmp_path / "decisions.json")),
    )
    outbox = EvolutionDispatchOutbox(build_dispatch_outbox_store(data_dir=tmp_path))
    compensations = CompensationLedger(data_dir=tmp_path)
    monkeypatch.setattr(evo_main, "dispatch_outbox", outbox)
    monkeypatch.setattr(evo_main, "compensation_ledger", compensations)
    yield {"outbox": outbox, "compensations": compensations, "data_dir": tmp_path}


@pytest.fixture(autouse=True)
def scripted_downstream():
    """Install a downstream the test controls, across every plane."""
    original = dict(evo_main.receipt_registry)
    adapter = install_scripted_adapter(evo_main.receipt_registry, *ALL_PLANES)
    try:
        yield adapter
    finally:
        evo_main.receipt_registry.clear()
        evo_main.receipt_registry.update(original)


@pytest.fixture(autouse=True)
def worker_http(monkeypatch):
    """Route the worker's urllib calls into the in-process service.

    The worker really does speak the service's HTTP contract — status codes,
    404s, and 4xx/5xx classification all flow through the real routes — without
    needing a live socket.
    """

    def _headers(tenant_id: str | None, auth_token: str | None) -> dict[str, str]:
        headers: dict[str, str] = {}
        if tenant_id:
            headers["X-Tenant-Id"] = tenant_id
        if auth_token:
            headers["Authorization"] = f"Bearer {auth_token}"
        return headers

    def _get(
        url: str,
        timeout_seconds: float,
        *,
        tenant_id: str | None = None,
        auth_token: str | None = None,
    ):
        response = client.get(_path(url), headers=_headers(tenant_id, auth_token))
        if response.status_code >= 400:
            raise urllib.error.HTTPError(
                url, response.status_code, str(response.status_code), None, None
            )
        return response.json()

    def _post(
        url: str,
        payload: dict,
        timeout_seconds: float,
        *,
        tenant_id: str | None = None,
        auth_token: str | None = None,
    ):
        response = client.post(
            _path(url),
            json=payload,
            headers=_headers(tenant_id, auth_token),
        )
        if response.status_code >= 400:
            raise urllib.error.HTTPError(
                url, response.status_code, str(response.status_code), None, None
            )
        return response.json()

    monkeypatch.setattr(dispatch_worker, "_http_get", _get)
    monkeypatch.setattr(dispatch_worker, "_http_post", _post)


def _path(url: str) -> str:
    marker = "/api/"
    return url[url.index(marker):] if marker in url else url


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_COUNTER = {"n": 0}


def _new_decision_id(prefix: str = "evo-dispatch") -> str:
    _COUNTER["n"] += 1
    return f"{prefix}-{_COUNTER['n']:04d}"


def approve_research_decision(
    *,
    decision_id: str | None = None,
    tenant_id: str = RESEARCH_TENANT,
    target_id: str | None = None,
) -> dict:
    """Propose, review, and approve a low-risk research-plane decision."""
    decision_id = decision_id or _new_decision_id()
    target_id = target_id or f"artifact-{decision_id}"
    created = client.post(
        "/api/evolution/proposals",
        json={
            "decision_id": decision_id,
            "tenant_id": tenant_id,
            "target_type": "candidate_artifact",
            "target_id": target_id,
            "target_version": "1.0.0",
            "action_type": "retrain",
            "rationale": "Rolling drawdown breached the approved expected baseline.",
            "created_by_id": "evolution-controller-01",
            "linked_incident_id": f"inc-{decision_id}",
        },
    )
    assert created.status_code == 201, created.text

    reviewed = client.post(
        f"/api/evolution/proposals/{decision_id}/review",
        json={
            "actor_role": "reviewer_on_duty",
            "actor_id": "reviewer-01",
            "approval_decision_id": f"approval-{decision_id}",
            "tenant_id": tenant_id,
        },
    )
    assert reviewed.status_code == 200, reviewed.text

    approved = client.post(
        f"/api/evolution/proposals/{decision_id}/approve",
        json={
            "actor_role": "reviewer_on_duty",
            "actor_id": "approver-01",
            "tenant_id": tenant_id,
        },
    )
    assert approved.status_code == 200, approved.text
    return approved.json()


def run_tick(state, *, now=None):
    return dispatch_worker.run_poll(
        api_url="http://evolution.test",
        outbox=state["outbox"],
        registry=evo_main.receipt_registry,
        compensations=state["compensations"],
        timeout_seconds=5.0,
        now=now,
    )


def decision_state(decision_id: str) -> str:
    response = client.get(f"/api/evolution/proposals/{decision_id}")
    assert response.status_code == 200, response.text
    return response.json()["decision_state"]


# ---------------------------------------------------------------------------
# Acceptance: every supported approved action writes a durable dispatch outbox
# ---------------------------------------------------------------------------

def test_approving_a_decision_writes_a_durable_dispatch_record(isolated_state):
    decision = approve_research_decision()
    decision_id = decision["decision_id"]

    outbox_id, _, _ = dispatch_identity(RESEARCH_TENANT, decision_id)
    record = isolated_state["outbox"].get_by_id(outbox_id)

    assert record is not None, "approving must leave a durable dispatch intent"
    assert record.delivery_ready is True, "the intent must be deliverable once approval committed"
    assert record.event.payload["decision_id"] == decision_id
    assert record.event.payload["tenant_id"] == RESEARCH_TENANT
    assert record.event.payload["execution_plane"] == "research"


def test_duplicate_dispatch_trigger_reuses_one_durable_intent(isolated_state):
    """A retried trigger must not dispatch the approved action twice."""
    decision_id = approve_research_decision()["decision_id"]

    first = client.post(
        f"/api/evolution/proposals/{decision_id}/dispatch?tenant_id={RESEARCH_TENANT}"
    )
    second = client.post(
        f"/api/evolution/proposals/{decision_id}/dispatch?tenant_id={RESEARCH_TENANT}"
    )
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["outbox_id"] == second.json()["outbox_id"]

    records = isolated_state["outbox"].list_all(tenant_id=RESEARCH_TENANT)
    assert len(records) == 1, f"expected exactly one durable intent, got {len(records)}"


def test_two_tenants_on_one_target_get_separate_dispatch_records(isolated_state):
    """Deterministic ids are tenant-scoped, so tenants cannot collide."""
    shared_target = "artifact-shared-001"
    first = approve_research_decision(tenant_id="tenant-a", target_id=shared_target)
    second = approve_research_decision(tenant_id="tenant-b", target_id=shared_target)

    a_id, _, _ = dispatch_identity("tenant-a", first["decision_id"])
    b_id, _, _ = dispatch_identity("tenant-b", second["decision_id"])
    assert a_id != b_id
    assert isolated_state["outbox"].get_by_id(a_id) is not None
    assert isolated_state["outbox"].get_by_id(b_id) is not None


def test_token_auth_scopes_reads_and_rejects_body_tenant_spoofing(
    isolated_state,
    monkeypatch,
):
    monkeypatch.setenv("EVOLUTION_AUTH_MODE", "token")
    monkeypatch.setenv("EVOLUTION_AUTH_TOKEN", "evolution-test-token")
    monkeypatch.setenv("EVOLUTION_AUTH_ALLOWED_TENANTS", "tenant-a,tenant-b")
    tenant_a_headers = {
        "Authorization": "Bearer evolution-test-token",
        "X-Tenant-Id": "tenant-a",
    }
    tenant_b_headers = {
        "Authorization": "Bearer evolution-test-token",
        "X-Tenant-Id": "tenant-b",
    }

    decision_id = _new_decision_id("evo-auth")
    created = client.post(
        "/api/evolution/proposals",
        headers=tenant_a_headers,
        json={
            "decision_id": decision_id,
            "tenant_id": "tenant-a",
            "target_type": "candidate_artifact",
            "target_id": f"artifact-{decision_id}",
            "target_version": "1.0.0",
            "action_type": "retrain",
            "rationale": "Tenant-authenticated research retrain proposal.",
            "created_by_id": "evolution-controller-01",
            "linked_incident_id": f"inc-{decision_id}",
        },
    )
    assert created.status_code == 201, created.text

    missing_auth = client.get(f"/api/evolution/proposals/{decision_id}")
    assert missing_auth.status_code == 401
    foreign_read = client.get(
        f"/api/evolution/proposals/{decision_id}",
        headers=tenant_b_headers,
    )
    assert foreign_read.status_code == 404
    own_read = client.get(
        f"/api/evolution/proposals/{decision_id}",
        headers=tenant_a_headers,
    )
    assert own_read.status_code == 200

    spoofed_review = client.post(
        f"/api/evolution/proposals/{decision_id}/review",
        headers=tenant_a_headers,
        json={
            "actor_role": "reviewer_on_duty",
            "actor_id": "reviewer-01",
            "approval_decision_id": f"approval-{decision_id}",
            "tenant_id": "tenant-b",
        },
    )
    assert spoofed_review.status_code == 403
    unchanged = client.get(
        f"/api/evolution/proposals/{decision_id}",
        headers=tenant_a_headers,
    )
    assert unchanged.status_code == 200
    assert unchanged.json()["decision_state"] == "proposed"


# ---------------------------------------------------------------------------
# Proof: crash-after-approval dispatch recovery
# ---------------------------------------------------------------------------

def test_reconcile_activates_an_intent_stranded_by_a_crash(isolated_state, scripted_downstream):
    """An approval that committed before activation must still be dispatched.

    Simulates the crash window directly: the intent is prepared and the approval
    commits, but the activation never runs.  Without reconcile the decision
    would sit approved forever behind an inert dispatch record.
    """
    decision_id = approve_research_decision()["decision_id"]
    outbox_id, _, _ = dispatch_identity(RESEARCH_TENANT, decision_id)
    outbox = isolated_state["outbox"]

    # Rewind the record to the pre-activation state a crash would have left.
    activated = outbox.get_by_id(outbox_id)
    stranded = type(activated)(
        record=activated.record,
        delivery_ready=False,
        transition=activated.transition,
    )
    outbox.store.put(stranded)
    assert outbox.get_by_id(outbox_id).delivery_ready is False
    assert outbox.claim_due(worker_id="probe") == [], "an inert intent must not be deliverable"

    scripted_downstream.set_succeeded(decision_id)
    result = run_tick(isolated_state)

    assert result["reconciled"] == 1, result
    assert result["executed"] == 1, result
    assert decision_state(decision_id) == "executed"


def test_reconcile_does_not_activate_when_the_decision_is_not_approved(isolated_state):
    """Reconcile must fail closed rather than activate an unproven approval."""
    decision_id = _new_decision_id("evo-unapproved")
    created = client.post(
        "/api/evolution/proposals",
        json={
            "decision_id": decision_id,
            "tenant_id": RESEARCH_TENANT,
            "target_type": "candidate_artifact",
            "target_id": f"artifact-{decision_id}",
            "target_version": "1.0.0",
            "action_type": "retrain",
            "rationale": "Proposed only; never reviewed or approved.",
            "created_by_id": "evolution-controller-01",
            "linked_incident_id": f"inc-{decision_id}",
        },
    )
    assert created.status_code == 201, created.text

    intent = evo_main._dispatch_intent_for(evo_main.store.get(decision_id))
    isolated_state["outbox"].prepare(intent)

    result = run_tick(isolated_state)
    assert result["reconciled"] == 0, result
    assert result["executed"] == 0, result
    assert decision_state(decision_id) == "proposed"


# ---------------------------------------------------------------------------
# Proof: real downstream terminal receipt
# ---------------------------------------------------------------------------

def test_decision_executes_only_on_a_terminal_downstream_receipt(
    isolated_state, scripted_downstream
):
    decision_id = approve_research_decision()["decision_id"]

    # The downstream is still running: the decision must stay approved and the
    # dispatch must not be counted as a failed delivery attempt.
    pending_result = run_tick(isolated_state)
    assert pending_result["pending"] == 1, pending_result
    assert pending_result["executed"] == 0
    assert decision_state(decision_id) == "approved"

    outbox_id, _, _ = dispatch_identity(RESEARCH_TENANT, decision_id)
    assert isolated_state["outbox"].get_by_id(outbox_id).delivery_attempts == 0, (
        "an in-flight downstream must not consume the retry budget"
    )

    # The downstream reaches a terminal success; now the decision converges.
    reference = scripted_downstream.set_succeeded(decision_id)
    result = run_tick(isolated_state, now=datetime.now(timezone.utc) + timedelta(minutes=5))
    assert result["executed"] == 1, result

    executed = client.get(f"/api/evolution/proposals/{decision_id}").json()
    assert executed["decision_state"] == "executed"
    assert executed["execution_result"]["status"] == "succeeded"
    assert executed["execution_result"]["execution_ref_id"] == reference
    assert isolated_state["outbox"].get_by_id(outbox_id).status.value == "published"


def test_execute_is_refused_without_a_receipt(isolated_state):
    """The synthetic-executed path must be closed at the HTTP boundary too."""
    decision_id = approve_research_decision()["decision_id"]
    response = client.post(
        f"/api/evolution/proposals/{decision_id}/execute",
        json={
            "actor_role": "evolution_controller",
            "actor_id": "evo-ctrl",
            "tenant_id": RESEARCH_TENANT,
        },
    )
    assert response.status_code == 422, response.text
    assert "execution_receipt is required" in response.json()["detail"]
    assert decision_state(decision_id) == "approved"


def test_execute_is_refused_when_the_downstream_is_not_terminal(
    isolated_state, scripted_downstream
):
    """A caller cannot execute by naming a downstream that has not finished."""
    decision_id = approve_research_decision()["decision_id"]
    response = client.post(
        f"/api/evolution/proposals/{decision_id}/execute",
        json={
            "actor_role": "evolution_controller",
            "actor_id": "evo-ctrl",
            "tenant_id": RESEARCH_TENANT,
            "execution_receipt": {
                "downstream_kind": RECEIPT_KIND,
                "downstream_ref_id": scripted_downstream.reference_for(decision_id),
            },
        },
    )
    assert response.status_code == 422, response.text
    assert "is not terminal" in response.json()["detail"]
    assert decision_state(decision_id) == "approved"


def test_execute_is_refused_for_a_mismatched_receipt_source(
    isolated_state, scripted_downstream
):
    decision_id = approve_research_decision()["decision_id"]
    scripted_downstream.set_succeeded(decision_id)
    response = client.post(
        f"/api/evolution/proposals/{decision_id}/execute",
        json={
            "actor_role": "evolution_controller",
            "actor_id": "evo-ctrl",
            "tenant_id": RESEARCH_TENANT,
            "execution_receipt": {
                "downstream_kind": "some_other_system",
                "downstream_ref_id": scripted_downstream.reference_for(decision_id),
            },
        },
    )
    assert response.status_code == 422, response.text
    assert "does not match" in response.json()["detail"]
    assert decision_state(decision_id) == "approved"


def test_unsupported_plane_is_dead_lettered_with_a_reason_not_executed(isolated_state):
    """A plane with no real receipt source must never be stubbed executed."""
    # Restore the production registry so the governance plane is genuinely
    # unsupported rather than scripted.
    evo_main.receipt_registry.update(
        build_adapter_registry(research_api_url="http://research.test")
    )
    decision_id = _new_decision_id("evo-freeze")
    created = client.post(
        "/api/evolution/proposals",
        json={
            "decision_id": decision_id,
            "tenant_id": RESEARCH_TENANT,
            "target_type": "candidate_artifact",
            "target_id": f"artifact-{decision_id}",
            "target_version": "1.0.0",
            "action_type": "freeze",
            "target_stage": "paper",
            "rationale": "Severity-2 incident recurrence on this artifact.",
            "created_by_id": "evolution-controller-01",
            "linked_incident_id": f"inc-{decision_id}",
        },
    )
    assert created.status_code == 201, created.text
    reviewed = client.post(
        f"/api/evolution/proposals/{decision_id}/review",
        json={
            "actor_role": "risk_owner",
            "actor_id": "risk-01",
            "approval_decision_id": f"approval-{decision_id}",
            "tenant_id": RESEARCH_TENANT,
        },
    )
    assert reviewed.status_code == 200, reviewed.text
    approved = client.post(
        f"/api/evolution/proposals/{decision_id}/approve",
        json={
            "actor_role": "risk_owner",
            "actor_id": "risk-01",
            "tenant_id": RESEARCH_TENANT,
        },
    )
    assert approved.status_code == 200, approved.text

    result = run_tick(isolated_state)
    assert result["unsupported"] == 1, result
    assert result["executed"] == 0
    assert decision_state(decision_id) == "approved"

    outbox_id, _, _ = dispatch_identity(RESEARCH_TENANT, decision_id)
    record = isolated_state["outbox"].get_by_id(outbox_id)
    assert record.status.value == "dead_lettered"
    assert "authoritative governance owner" in (record.last_error or "")


# ---------------------------------------------------------------------------
# Proof: compensation readback
# ---------------------------------------------------------------------------

def test_terminal_downstream_failure_records_a_durable_compensation(
    isolated_state, scripted_downstream
):
    decision_id = approve_research_decision()["decision_id"]
    reference = scripted_downstream.set_receipt(
        decision_id,
        DispatchReceipt(
            outcome=OUTCOME_FAILED,
            downstream_kind=RECEIPT_KIND,
            downstream_ref_id=scripted_downstream.reference_for(decision_id),
            downstream_status="failed",
            detail="research run failed during evaluation",
        ),
    )

    result = run_tick(isolated_state)
    assert result["compensated"] == 1, result
    assert result["executed"] == 0
    assert decision_state(decision_id) == "approved", (
        "a failed downstream must not leave the decision recorded as executed"
    )

    listed = client.get(f"/api/evolution/compensations?tenant_id={RESEARCH_TENANT}")
    assert listed.status_code == 200, listed.text
    entries = listed.json()["compensations"]
    assert len(entries) == 1, entries
    assert entries[0]["decision_id"] == decision_id
    assert entries[0]["downstream_ref_id"] == reference
    assert entries[0]["resolved"] is False
    assert "research run failed" in entries[0]["reason"]

    # Read back through a ledger instance that re-reads the durable file — what
    # a restarted operator console would see.
    reread = CompensationLedger(data_dir=isolated_state["data_dir"])
    assert reread.get(RESEARCH_TENANT, decision_id) is not None, (
        "the compensation obligation must survive a process restart"
    )


def test_compensation_is_recorded_once_for_a_duplicated_failure(
    isolated_state, scripted_downstream
):
    decision_id = approve_research_decision()["decision_id"]
    scripted_downstream.set_receipt(
        decision_id,
        DispatchReceipt(
            outcome=OUTCOME_FAILED,
            downstream_kind=RECEIPT_KIND,
            downstream_ref_id=scripted_downstream.reference_for(decision_id),
            downstream_status="failed",
            detail="first failure",
        ),
    )
    run_tick(isolated_state)

    ledger = isolated_state["compensations"]
    first = ledger.get(RESEARCH_TENANT, decision_id)
    ledger.record(
        tenant_id=RESEARCH_TENANT,
        decision_id=decision_id,
        outbox_id="ignored",
        reason="a later duplicate trigger",
    )
    assert ledger.get(RESEARCH_TENANT, decision_id) == first, (
        "a duplicate trigger must not overwrite the original failure record"
    )
    assert len(ledger.list_all(tenant_id=RESEARCH_TENANT)) == 1


def test_compensation_can_be_resolved_with_an_actor_and_note(
    isolated_state, scripted_downstream
):
    decision_id = approve_research_decision()["decision_id"]
    scripted_downstream.set_receipt(
        decision_id,
        DispatchReceipt(
            outcome=OUTCOME_FAILED,
            downstream_kind=RECEIPT_KIND,
            downstream_ref_id=scripted_downstream.reference_for(decision_id),
            downstream_status="failed",
            detail="research run failed",
        ),
    )
    run_tick(isolated_state)

    resolved = client.post(
        f"/api/evolution/compensations/{decision_id}/resolve",
        json={
            "actor_id": "operator-01",
            "note": "Research run artifacts discarded; artifact left on prior version.",
            "tenant_id": RESEARCH_TENANT,
        },
    )
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["resolved"] is True
    assert resolved.json()["resolved_by"] == "operator-01"


# ---------------------------------------------------------------------------
# Acceptance: retry, DLQ replay cooldown, restart, duplicate triggers
# ---------------------------------------------------------------------------

def test_retryable_downstream_failures_back_off_then_dead_letter(
    isolated_state, scripted_downstream
):
    decision_id = approve_research_decision()["decision_id"]
    scripted_downstream.set_receipt(
        decision_id,
        DispatchReceipt(
            outcome=OUTCOME_RETRYABLE,
            downstream_kind=RECEIPT_KIND,
            downstream_ref_id=scripted_downstream.reference_for(decision_id),
            detail="research orchestrator temporarily unavailable",
        ),
    )
    outbox = isolated_state["outbox"]
    outbox_id, _, _ = dispatch_identity(RESEARCH_TENANT, decision_id)

    moment = datetime.now(timezone.utc)
    for _ in range(outbox.max_attempts):
        result = run_tick(isolated_state, now=moment)
        assert result["executed"] == 0
        # Step past the exponential backoff so the next attempt is due.
        moment += timedelta(hours=6)

    record = outbox.get_by_id(outbox_id)
    assert record.status.value == "dead_lettered", record.to_dict()
    assert record.delivery_attempts == outbox.max_attempts
    assert decision_state(decision_id) == "approved"


def test_dlq_replay_is_refused_inside_its_cooldown(isolated_state, scripted_downstream):
    _, outbox_id = _dead_letter_one(isolated_state, scripted_downstream)

    response = client.post(
        f"/api/evolution/dispatch-outbox/{outbox_id}/replay",
        json={
            "actor_id": "operator-01",
            "note": "Retry after the orchestrator incident.",
            "tenant_id": RESEARCH_TENANT,
        },
    )
    assert response.status_code == 409, response.text
    assert "replay cooldown" in response.json()["detail"]
    assert isolated_state["outbox"].get_by_id(outbox_id).status.value == "dead_lettered"


def test_dlq_replay_cooldown_survives_a_restart(isolated_state, scripted_downstream):
    """The cooldown comes from durable state, not an in-memory timer."""
    _, outbox_id = _dead_letter_one(isolated_state, scripted_downstream)

    restarted = EvolutionDispatchOutbox(
        build_dispatch_outbox_store(data_dir=isolated_state["data_dir"])
    )
    with pytest.raises(EvolutionDispatchError) as excinfo:
        restarted.replay(outbox_id, actor="operator-01", note="Retry after restart.")
    assert "replay cooldown" in str(excinfo.value)


def test_dlq_replay_succeeds_after_the_cooldown_elapses(
    isolated_state, scripted_downstream
):
    decision_id, outbox_id = _dead_letter_one(isolated_state, scripted_downstream)
    outbox = isolated_state["outbox"]

    after_cooldown = datetime.now(timezone.utc) + timedelta(
        seconds=outbox.replay_cooldown_seconds + 60
    )
    replayed = outbox.replay(
        outbox_id, actor="operator-01", note="Retry after the incident.", now=after_cooldown
    )
    assert replayed.status.value == "pending"
    assert replayed.redrive_count == 1
    assert replayed.delivery_attempts == 0

    # A duplicate replay trigger immediately afterwards must not double-redrive:
    # the record is no longer dead-lettered.
    with pytest.raises(EvolutionDispatchError) as excinfo:
        outbox.replay(
            outbox_id, actor="operator-01", note="Duplicate trigger.", now=after_cooldown
        )
    assert "only dead-lettered dispatches may be replayed" in str(excinfo.value)

    # And the replayed dispatch really converges once the downstream succeeds.
    scripted_downstream.set_succeeded(decision_id)
    result = run_tick(isolated_state, now=after_cooldown)
    assert result["executed"] == 1, result
    assert decision_state(decision_id) == "executed"


def test_replay_is_refused_for_an_actor_from_another_tenant(
    isolated_state, scripted_downstream
):
    _, outbox_id = _dead_letter_one(isolated_state, scripted_downstream)
    response = client.post(
        f"/api/evolution/dispatch-outbox/{outbox_id}/replay",
        json={
            "actor_id": "operator-99",
            "note": "Cross-tenant replay attempt.",
            "tenant_id": "tenant-someone-else",
        },
    )
    assert response.status_code == 403, response.text


def _dead_letter_one(isolated_state, scripted_downstream) -> tuple[str, str]:
    """Drive one dispatch to the DLQ and return ``(decision_id, outbox_id)``."""
    decision_id = approve_research_decision()["decision_id"]
    scripted_downstream.set_receipt(
        decision_id,
        DispatchReceipt(
            outcome=OUTCOME_FAILED,
            downstream_kind=RECEIPT_KIND,
            downstream_ref_id=scripted_downstream.reference_for(decision_id),
            downstream_status="failed",
            detail="research run failed",
        ),
    )
    run_tick(isolated_state)
    outbox_id, _, _ = dispatch_identity(RESEARCH_TENANT, decision_id)
    assert isolated_state["outbox"].get_by_id(outbox_id).status.value == "dead_lettered"
    scripted_downstream.statuses.clear()
    return decision_id, outbox_id


# ---------------------------------------------------------------------------
# Worker health contract
# ---------------------------------------------------------------------------

def test_healthcheck_requires_a_configured_health_file(monkeypatch):
    monkeypatch.delenv("EVOLUTION_DISPATCH_HEALTH_FILE", raising=False)
    assert dispatch_worker.healthcheck() == 1


def test_healthcheck_rejects_health_state_with_no_completed_tick(tmp_path, monkeypatch):
    health_file = tmp_path / "health.json"
    health_file.write_text('{"status": "ok", "ticks": 0}', encoding="utf-8")
    monkeypatch.setenv("EVOLUTION_DISPATCH_HEALTH_FILE", str(health_file))
    assert dispatch_worker.healthcheck() == 1
