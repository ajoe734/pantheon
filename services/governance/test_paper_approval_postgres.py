"""Real mounted Governance + Registry HTTP owners on the dedicated test database.

Proves the dedicated ``pantheon-dev-paper-provisioner`` boundary end to end:
strict claim admission, owner-stamped durable ``authorization_scope``,
Registry candidate recheck at review/decide, receipt replay across a process
restart, and scoped consumption by the real Registry owner.

Run with GOV_APPROVAL_TEST_DSN pointing only to ``gov_approval_test``.
Signing material is generated in memory; no hosted credentials are used.
"""
from __future__ import annotations

import socket
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from services.governance.paper_approval_scope import (
    DEV_PAPER_APPROVAL_SCOPE,
    DEV_PAPER_AUTHORIZATION_SCOPE,
    DEV_PAPER_PROVISIONER_SUBJECT,
)
from services.governance.test_approval_authority_postgres import (  # noqa: F401  (owner_env fixture)
    approval_records,
    headers,
    owner_env,
    post,
    server,
    token,
)
from services.registry.paper_strategy_spec import build_strategy_spec

TENANT, PERSONA, POOL = "tenant-dev", "persona-paper-001", "pool-paper-001"
PAPER_CLAIMS = dict(sub=DEV_PAPER_PROVISIONER_SUBJECT, tenant_id=TENANT, roles=["automated_gate"],
                    scope=DEV_PAPER_APPROVAL_SCOPE)
REVIEWER_CLAIMS = dict(sub="synthetic-reviewer", tenant_id=TENANT, roles=["governance_reviewer"])


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@contextmanager
def _server(env, app, port):
    """Like the shared ``server`` helper but on a pre-chosen port (owner URLs cross-reference)."""
    with tempfile.TemporaryFile(mode="w+") as output:
        process = subprocess.Popen([sys.executable, "-m", "uvicorn", app, "--host", "127.0.0.1",
                                    "--port", str(port), "--log-level", "warning"],
                                   env=env, stdout=output, stderr=subprocess.STDOUT)
        url = f"http://127.0.0.1:{port}"
        try:
            for _ in range(150):
                if process.poll() is not None:
                    output.seek(0)
                    pytest.fail(f"{app} failed startup: " + output.read())
                try:
                    if httpx.get(url + "/health", timeout=.5).status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                time.sleep(.1)
            else:
                pytest.fail(f"{app} startup timed out")
            yield url
        finally:
            process.terminate()
            try:
                code = process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
                pytest.fail(f"{app} failed to terminate")
            output.seek(0)
            assert code in (0, -15), output.read()


def _registry_env(owner_env, *, governance_url, environment="dev"):
    secret = owner_env["PANTHEON_GOVERNANCE_JWT_SECRET"]
    schema = "registry_paper_test_" + uuid.uuid4().hex
    env = dict(owner_env)
    env.pop("PANTHEON_ENV", None)
    env.update(
        REGISTRY_STORE_BACKEND="postgres", REGISTRY_STORE_DSN=owner_env["GOVERNANCE_STORE_DSN"],
        REGISTRY_ENTRIES_TABLE=schema + ".entries", REGISTRY_RECEIPTS_TABLE=schema + ".receipts",
        PANTHEON_REGISTRY_AUTH_MODE="strict", PANTHEON_REGISTRY_JWT_SECRET=secret,
        PANTHEON_REGISTRY_JWT_ISSUER="isolated-governance-test", PANTHEON_REGISTRY_JWT_AUDIENCE="isolated-registry",
        REGISTRY_GOVERNANCE_BASE_URL=governance_url,
        REGISTRY_GOVERNANCE_SERVICE_TOKEN=token(secret, sub="registry-approval-reader", roles=["approval_reader"], tenant_id=TENANT),
    )
    if environment is not None:
        env["PANTHEON_ENV"] = environment
    return env


@pytest.fixture(scope="module")
def paper_owners(owner_env):
    secret = owner_env["PANTHEON_GOVERNANCE_JWT_SECRET"]
    governance_port, registry_port = _free_port(), _free_port()
    governance_url = f"http://127.0.0.1:{governance_port}"
    registry_url = f"http://127.0.0.1:{registry_port}"
    governance_env = dict(
        owner_env, GOVERNANCE_DEV_PAPER_APPROVAL_ENABLED="true", PANTHEON_ENV="dev",
        GOVERNANCE_REGISTRY_BASE_URL=registry_url,
        GOVERNANCE_REGISTRY_SERVICE_TOKEN=token(secret, sub="governance-registry-reader", roles=["registry-reader"],
                                                tenant=TENANT, tenant_id=TENANT, aud="isolated-registry"),
    )
    registry_env = _registry_env(owner_env, governance_url=governance_url)
    registry_token = token(secret, sub="registry-operator", roles=["operator"], tenant=TENANT, tenant_id=TENANT, aud="isolated-registry")
    with _server(governance_env, "services.governance.main:app", governance_port) as governance:
        with _server(registry_env, "services.registry.service:app", registry_port) as registry:
            yield dict(governance_url=governance, registry_url=registry, governance_env=governance_env,
                       registry_env=registry_env, registry_token=registry_token, secret=secret)


def _registry_client(owners, url=None):
    return httpx.Client(base_url=url or owners["registry_url"],
                        headers={"Authorization": "Bearer " + owners["registry_token"]}, timeout=10)


def _paper_metadata():
    return {"tenant_id": TENANT, "execution_context": "paper", "capital_scale_pct": 0,
            "persona_id": PERSONA, "capital_pool_id": POOL}


def _register_candidate(owners, *, metadata=None, spec_metadata=None, advance=True, url=None):
    spec = build_strategy_spec()
    spec["strategy_id"] = "paper-proof-" + uuid.uuid4().hex[:8]
    # Checksummed embedded binding (Root's coordinator writes the same keys).
    spec["metadata"] = _paper_metadata() if spec_metadata is None else spec_metadata
    with _registry_client(owners, url) as client:
        created = client.post("/api/registry/strategy-specs", json={
            "strategy_id": spec["strategy_id"], "version": "1.0.0", "artifact_state": "draft",
            "lineage": {"source_run_ids": ["paper-proof-run"]}, "strategy_spec": spec,
            "metadata": _paper_metadata() if metadata is None else metadata,
        })
        assert created.status_code == 200, created.text
        entry = created.json()["entry"]
        if not advance:
            return entry
        advanced = client.post(f"/api/registry/entries/{entry['registry_id']}/advance", json={
            "target_state": "candidate", "expected_artifact_state": "draft", "expected_version": entry["version"],
            "expected_updated_at": entry["updated_at"], "command_key": uuid.uuid4().hex,
        })
        assert advanced.status_code == 200, advanced.text
        return advanced.json()["entry"]


def _expires(hours=1.0):
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _proposal(entry, **overrides):
    body = dict(target_type="registry_entry", target_id=entry["registry_id"], target_version=entry["version"],
                tenant_id=TENANT, owner_user_id=DEV_PAPER_PROVISIONER_SUBJECT, expected_version=0,
                candidate_digest=entry["checksum"], persona_id=PERSONA, capital_pool_id=POOL, expires_at=_expires())
    body.update(overrides)
    return body


def _paper_post(owners, path, body, key=None, url=None, **claims):
    return post(url or owners["governance_url"], path, owners["governance_env"], body, key, **{**PAPER_CLAIMS, **claims})


def _paper_approve(owners, entry, **proposal_overrides):
    proposed = _paper_post(owners, "", _proposal(entry, **proposal_overrides))
    assert proposed.status_code == 201, proposed.text
    path = "/" + proposed.json()["decision_id"]
    reviewed = _paper_post(owners, path + "/review", dict(expected_version=1, actor_id=DEV_PAPER_PROVISIONER_SUBJECT, actor_role="automated_gate"))
    assert reviewed.status_code == 200, reviewed.text
    decided = _paper_post(owners, path + "/decide", dict(expected_version=2, actor_id=DEV_PAPER_PROVISIONER_SUBJECT, actor_role="automated_gate",
                                                         outcome="approved", rationale="dev paper candidate verified"))
    assert decided.status_code == 200, decided.text
    return proposed.json(), reviewed.json(), decided.json()


# ---------------------------------------------------------------------------
# Positive lifecycle: durable scope, restart replay, owner consumption
# ---------------------------------------------------------------------------

def test_paper_principal_lifecycle_is_scoped_durable_and_replayable(paper_owners, owner_env):
    owners = paper_owners
    entry = _register_candidate(owners)
    proposed = _paper_post(owners, "", _proposal(entry))
    assert proposed.status_code == 201, proposed.text
    decision = proposed.json()
    assert decision["authorization_scope"] == DEV_PAPER_AUTHORIZATION_SCOPE
    assert decision["owner_user_id"] == DEV_PAPER_PROVISIONER_SUBJECT and decision["tenant_id"] == TENANT
    path = "/" + decision["decision_id"]

    reviewed = _paper_post(owners, path + "/review", dict(expected_version=1, actor_id=DEV_PAPER_PROVISIONER_SUBJECT, actor_role="automated_gate"))
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["authorization_scope"] == DEV_PAPER_AUTHORIZATION_SCOPE

    decide_body = dict(expected_version=2, actor_id=DEV_PAPER_PROVISIONER_SUBJECT, actor_role="automated_gate",
                       outcome="approved", rationale="dev paper candidate verified")
    key = uuid.uuid4().hex
    decided = _paper_post(owners, path + "/decide", decide_body, key)
    assert decided.status_code == 200, decided.text
    approved = decided.json()
    assert approved["decision"] == "approved" and approved["version"] == 3
    assert approved["authorization_scope"] == DEV_PAPER_AUTHORIZATION_SCOPE
    assert approved["actor_role"] == "automated_gate" and approved["authority_status"] == "authoritative"

    # Every durable owner record (decision, receipt response, audit) carries the scope.
    decision_rows, receipt_rows, audit_rows = approval_records(owner_env, decision["decision_id"])
    assert decision_rows and receipt_rows and audit_rows
    assert all('"authorization_scope"' in row[0] and '"max_capital_scale_pct"' in row[0] for row in decision_rows)
    assert all('"authorization_scope"' in row[0] for row in receipt_rows)

    # Readers see the scope; latest-approved never surfaces a scoped approval as unrestricted authority.
    readback = httpx.get(owners["governance_url"] + "/api/governance/approvals" + path, headers=headers(owners["governance_env"], **PAPER_CLAIMS))
    assert readback.status_code == 200 and readback.json() == approved
    latest = httpx.get(owners["governance_url"] + "/api/governance/approvals/latest-approved",
                       params={"target_type": "registry_entry", "target_id": entry["registry_id"]},
                       headers=headers(owners["governance_env"], **REVIEWER_CLAIMS))
    assert latest.status_code == 200 and latest.json() is None

    # Restart: the original receipt replays byte-for-byte, scope intact.
    with _server(owners["governance_env"], "services.governance.main:app", _free_port()) as restarted:
        replay = _paper_post(owners, path + "/decide", decide_body, key, url=restarted)
        assert replay.status_code == 200 and replay.json() == approved
        divergent = _paper_post(owners, path + "/decide", dict(decide_body, rationale="changed"), key, url=restarted)
        assert divergent.status_code == 409
        current = httpx.get(restarted + "/api/governance/approvals" + path, headers=headers(owners["governance_env"], **PAPER_CLAIMS))
        assert current.json() == approved

    # The dedicated principal cannot revoke, even its own decision.
    revoked = _paper_post(owners, path + "/revoke", dict(expected_version=3, actor_id=DEV_PAPER_PROVISIONER_SUBJECT, actor_role="automated_gate"))
    assert revoked.status_code == 403, revoked.text

    # The real Registry owner consumes the scoped approval only as a dev/paper/0% candidate.
    with _registry_client(owners) as client:
        command = {"target_state": "approved", "expected_artifact_state": "candidate", "expected_version": entry["version"],
                   "expected_updated_at": entry["updated_at"], "command_key": uuid.uuid4().hex,
                   "approval_decision_id": decision["decision_id"]}
        consumed = client.post(f"/api/registry/entries/{entry['registry_id']}/advance", json=command)
        assert consumed.status_code == 200, consumed.text
        approved_entry = consumed.json()["entry"]
        assert approved_entry["artifact_state"] == "approved"
        assert approved_entry["approval_evidence"]["authorization_scope"] == DEV_PAPER_AUTHORIZATION_SCOPE
        assert approved_entry["approver"] == DEV_PAPER_PROVISIONER_SUBJECT
        assert client.get(f"/api/registry/entries/{entry['registry_id']}").json() == consumed.json()


def test_registry_outside_dev_never_admits_scoped_approval(paper_owners, owner_env):
    owners = paper_owners
    registry_env = _registry_env(owner_env, governance_url=owners["governance_url"], environment=None)
    port = _free_port()
    with _server(registry_env, "services.registry.service:app", port) as registry_url:
        # Register the candidate on the non-dev Registry, but approve it through the dev Governance owner.
        # Governance must be able to read it: point a dedicated Governance at this Registry.
        governance_env = dict(owners["governance_env"], GOVERNANCE_REGISTRY_BASE_URL=registry_url)
        entry = _register_candidate(owners, url=registry_url)
        with _server(governance_env, "services.governance.main:app", _free_port()) as governance_url:
            local = dict(owners, governance_url=governance_url, governance_env=governance_env)
            _, _, approved = _paper_approve(local, entry)
            assert approved["authorization_scope"] == DEV_PAPER_AUTHORIZATION_SCOPE
        with _registry_client(owners, registry_url) as client:
            denied = client.post(f"/api/registry/entries/{entry['registry_id']}/advance", json={
                "target_state": "approved", "expected_artifact_state": "candidate", "expected_version": entry["version"],
                "expected_updated_at": entry["updated_at"], "command_key": uuid.uuid4().hex,
                "approval_decision_id": approved["decision_id"]})
            assert denied.status_code == 400, denied.text
            assert "authorization_scope" in denied.text
            assert client.get(f"/api/registry/entries/{entry['registry_id']}").json()["entry"] == entry


# ---------------------------------------------------------------------------
# Strict principal admission
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("claims", [
    {"scope": None}, {"scope": "pantheon:prod-paper-approval"}, {"scope": "pantheon:dev-paper-approval extra"},
    {"tenant_id": "tenant-other"}, {"roles": ["governance_reviewer"]}, {"roles": ["automated_gate", "operator"]},
    {"roles": ["risk_owner"]},
])
def test_dedicated_subject_with_incorrect_claims_fails_closed_on_every_route(paper_owners, claims):
    owners = paper_owners
    entry = _register_candidate(owners)
    _, _, approved = _paper_approve(owners, entry)
    root = "/api/governance/approvals"
    denied = _paper_post(owners, "", _proposal(entry, tenant_id=claims.get("tenant_id", TENANT)), **claims)
    assert denied.status_code == 403, denied.text
    for suffix in ("", "/" + approved["decision_id"], "/latest-approved?target_type=registry_entry&target_id=" + entry["registry_id"]):
        read = httpx.get(owners["governance_url"] + root + suffix, headers=headers(owners["governance_env"], **{**PAPER_CLAIMS, **claims}))
        assert read.status_code == 403, (suffix, read.text)
    for operation in ("review", "decide", "revoke"):
        body = dict(expected_version=3, actor_id=DEV_PAPER_PROVISIONER_SUBJECT, actor_role="automated_gate")
        if operation == "decide":
            body.update(outcome="approved", rationale="forged")
        response = _paper_post(owners, "/" + approved["decision_id"] + "/" + operation, body, **claims)
        assert response.status_code == 403, (operation, response.text)


def test_missing_feature_grant_denies_dedicated_subject_but_not_generic_reviewers(paper_owners):
    owners = paper_owners
    entry = _register_candidate(owners)
    for env in (
        {k: v for k, v in owners["governance_env"].items() if k != "GOVERNANCE_DEV_PAPER_APPROVAL_ENABLED"},
        # A non-dev (but non-enforced) environment: the flag alone is not enough.
        dict(owners["governance_env"], PANTHEON_ENV="sandbox"),
    ):
        with server(env) as url:
            denied = post(url, "", env, _proposal(entry), **PAPER_CLAIMS)
            assert denied.status_code == 403, denied.text
            generic = post(url, "", env, _proposal(entry, owner_user_id="synthetic-reviewer"), **REVIEWER_CLAIMS)
            assert generic.status_code == 201, generic.text
            assert generic.json()["authorization_scope"] is None


@pytest.mark.parametrize("change,status", [
    ({"risk_level": "high"}, 403), ({"risk_level": "medium"}, 403), ({"target_type": "strategy_spec"}, 403),
    ({"owner_user_id": "synthetic-reviewer"}, 403), ({"tenant_id": "tenant-other"}, 403),
    ({"expires_at": _expires(48)}, 422), ({"expires_at": "2000-01-01T00:00:00Z"}, 422), ({"expires_at": None}, 422),
    ({"persona_id": None}, 422), ({"capital_pool_id": None}, 422), ({"candidate_digest": None}, 422),
    ({"authorization_scope": DEV_PAPER_AUTHORIZATION_SCOPE}, 422),
    ({"authorization_scope": None}, 422),
])
def test_paper_principal_cannot_widen_or_self_declare_scope(paper_owners, owner_env, change, status):
    owners = paper_owners
    entry = _register_candidate(owners)
    decision_id = "widen-" + uuid.uuid4().hex
    denied = _paper_post(owners, "", _proposal(entry, decision_id=decision_id, **change))
    assert denied.status_code == status, denied.text
    assert approval_records(owner_env, decision_id) == [[], [], []]


def test_generic_reviewer_cannot_stamp_scope_and_paper_principal_cannot_take_foreign_proposal(paper_owners):
    owners = paper_owners
    entry = _register_candidate(owners)
    generic = post(owners["governance_url"], "", owners["governance_env"], _proposal(entry, owner_user_id="synthetic-reviewer"), **REVIEWER_CLAIMS)
    assert generic.status_code == 201, generic.text
    assert generic.json()["authorization_scope"] is None
    path = "/" + generic.json()["decision_id"]
    taken = _paper_post(owners, path + "/review", dict(expected_version=1, actor_id=DEV_PAPER_PROVISIONER_SUBJECT, actor_role="automated_gate"))
    assert taken.status_code == 403, taken.text
    assert "another owner" in taken.text
    current = httpx.get(owners["governance_url"] + "/api/governance/approvals" + path, headers=headers(owners["governance_env"], **REVIEWER_CLAIMS))
    assert current.json() == generic.json()


# ---------------------------------------------------------------------------
# Actual candidate recheck at review/decide
# ---------------------------------------------------------------------------

def test_review_denies_digest_changed_candidate_without_transition(paper_owners, owner_env):
    owners = paper_owners
    entry = _register_candidate(owners)
    proposed = _paper_post(owners, "", _proposal(entry, candidate_digest="sha256:" + "b" * 64))
    assert proposed.status_code == 201, proposed.text
    path = "/" + proposed.json()["decision_id"]
    before = approval_records(owner_env, proposed.json()["decision_id"])
    denied = _paper_post(owners, path + "/review", dict(expected_version=1, actor_id=DEV_PAPER_PROVISIONER_SUBJECT, actor_role="automated_gate"))
    assert denied.status_code == 400, denied.text
    assert "checksum" in denied.text or "does not match" in denied.text
    assert approval_records(owner_env, proposed.json()["decision_id"]) == before


@pytest.mark.parametrize("metadata", [
    {}, {"execution_context": "paper"}, {"execution_context": "paper", "capital_scale_pct": 1, "persona_id": PERSONA, "capital_pool_id": POOL},
    {"execution_context": "live", "capital_scale_pct": 0, "persona_id": PERSONA, "capital_pool_id": POOL},
    {"execution_context": "paper", "capital_scale_pct": 0, "persona_id": "persona-other", "capital_pool_id": POOL},
])
def test_review_denies_malformed_or_non_paper_candidate(paper_owners, metadata):
    owners = paper_owners
    entry = _register_candidate(owners, metadata=metadata)
    proposed = _paper_post(owners, "", _proposal(entry))
    assert proposed.status_code == 201, proposed.text
    denied = _paper_post(owners, "/" + proposed.json()["decision_id"] + "/review",
                         dict(expected_version=1, actor_id=DEV_PAPER_PROVISIONER_SUBJECT, actor_role="automated_gate"))
    assert denied.status_code == 400, denied.text


def test_review_denies_candidate_that_is_still_draft(paper_owners):
    owners = paper_owners
    entry = _register_candidate(owners, advance=False)
    proposed = _paper_post(owners, "", _proposal(entry))
    assert proposed.status_code == 201, proposed.text
    denied = _paper_post(owners, "/" + proposed.json()["decision_id"] + "/review",
                         dict(expected_version=1, actor_id=DEV_PAPER_PROVISIONER_SUBJECT, actor_role="automated_gate"))
    assert denied.status_code == 400 and "artifact_state" in denied.text


@pytest.mark.parametrize("spec_metadata", [
    {**_paper_metadata(), "persona_id": "persona-other"},
    {**_paper_metadata(), "capital_pool_id": "pool-other"},
    {**_paper_metadata(), "tenant_id": "tenant-other"},
    {**_paper_metadata(), "capital_scale_pct": 1},
    {**_paper_metadata(), "execution_context": "live"},
])
def test_review_denies_embedded_spec_metadata_drift_with_consistent_digest_and_outer_metadata(paper_owners, owner_env, spec_metadata):
    owners = paper_owners
    # Registry computes the checksum from this exact spec, so the digest is
    # consistent and the outer owner metadata is the correct paper contract.
    entry = _register_candidate(owners, spec_metadata=spec_metadata)
    assert entry["metadata"]["persona_id"] == PERSONA and entry["metadata"]["capital_pool_id"] == POOL
    proposed = _paper_post(owners, "", _proposal(entry))
    assert proposed.status_code == 201, proposed.text
    before = approval_records(owner_env, proposed.json()["decision_id"])
    denied = _paper_post(owners, "/" + proposed.json()["decision_id"] + "/review",
                         dict(expected_version=1, actor_id=DEV_PAPER_PROVISIONER_SUBJECT, actor_role="automated_gate"))
    assert denied.status_code == 400, denied.text
    assert "strategy_spec.metadata" in denied.text
    assert approval_records(owner_env, proposed.json()["decision_id"]) == before


def test_expired_proposal_cannot_be_reviewed_or_decided(paper_owners):
    owners = paper_owners
    entry = _register_candidate(owners)
    proposed = _paper_post(owners, "", _proposal(entry, expires_at=_expires(2 / 3600)))
    assert proposed.status_code == 201, proposed.text
    time.sleep(3)
    denied = _paper_post(owners, "/" + proposed.json()["decision_id"] + "/review",
                         dict(expected_version=1, actor_id=DEV_PAPER_PROVISIONER_SUBJECT, actor_role="automated_gate"))
    assert denied.status_code == 422 and "future" in denied.text


def test_decide_cannot_attach_conditions_or_change_expiry(paper_owners, owner_env):
    owners = paper_owners
    entry = _register_candidate(owners)
    proposed = _paper_post(owners, "", _proposal(entry))
    path = "/" + proposed.json()["decision_id"]
    reviewed = _paper_post(owners, path + "/review", dict(expected_version=1, actor_id=DEV_PAPER_PROVISIONER_SUBJECT, actor_role="automated_gate"))
    assert reviewed.status_code == 200, reviewed.text
    before = approval_records(owner_env, proposed.json()["decision_id"])
    base = dict(expected_version=2, actor_id=DEV_PAPER_PROVISIONER_SUBJECT, actor_role="automated_gate", rationale="widen")
    for body in (dict(base, outcome="approved_with_conditions", conditions=["later"]),
                 dict(base, outcome="approved", expires_at=_expires(48)),
                 # Still inside the 24h bound, but not the proposal's instant.
                 dict(base, outcome="approved", expires_at=_expires(0.5)),
                 dict(base, outcome="approved", expires_at=_expires(23)),
                 dict(base, outcome="approved", candidate_digest="sha256:" + "c" * 64)):
        key = uuid.uuid4().hex
        for _ in range(2):
            denied = _paper_post(owners, path + "/decide", body, key)
            assert denied.status_code in (403, 422), denied.text
            # A denied decide leaves decision, receipt and audit rows untouched.
            assert approval_records(owner_env, proposed.json()["decision_id"]) == before
        readback = httpx.get(owners["governance_url"] + "/api/governance/approvals" + path, headers=headers(owners["governance_env"], **PAPER_CLAIMS))
        assert readback.json() == reviewed.json()
    restated = dict(base, outcome="approved", expires_at=proposed.json()["expires_at"].replace("Z", "+00:00"))
    accepted = _paper_post(owners, path + "/decide", restated)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["expires_at"] == proposed.json()["expires_at"].replace("Z", "+00:00")
    assert accepted.json()["authorization_scope"] == DEV_PAPER_AUTHORIZATION_SCOPE


def test_decide_rejected_outcome_keeps_scope(paper_owners):
    owners = paper_owners
    entry = _register_candidate(owners)
    proposed = _paper_post(owners, "", _proposal(entry))
    path = "/" + proposed.json()["decision_id"]
    assert _paper_post(owners, path + "/review", dict(expected_version=1, actor_id=DEV_PAPER_PROVISIONER_SUBJECT, actor_role="automated_gate")).status_code == 200
    base = dict(expected_version=2, actor_id=DEV_PAPER_PROVISIONER_SUBJECT, actor_role="automated_gate", rationale="widen")
    rejected = _paper_post(owners, path + "/decide", dict(base, outcome="rejected"))
    assert rejected.status_code == 200 and rejected.json()["decision"] == "rejected"
    assert rejected.json()["authorization_scope"] == DEV_PAPER_AUTHORIZATION_SCOPE
