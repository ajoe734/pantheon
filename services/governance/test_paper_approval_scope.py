"""Focused unit proofs for the owner-side dev paper approval boundary.

Covers the dedicated principal grant, the strict authorization_scope value,
the usage-context contract, immutable Registry candidate verification, the
exact Registry HTTP reader, and durable ApprovalDecision serialization.
"""
from __future__ import annotations

import copy
import json
import sys
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.governance import paper_approval_scope as scope_module
from services.governance.approval_authority import ApprovalEvidence, ApprovalInvalid
from services.governance.paper_approval_scope import (
    DEV_PAPER_APPROVAL_SCOPE,
    DEV_PAPER_AUTHORIZATION_SCOPE,
    DEV_PAPER_PROVISIONER_SUBJECT,
    ApprovalUsageContext,
    AuthorizationScopeError,
    DevPaperGrant,
    PaperApprovalDenied,
    PaperCandidateExpectation,
    PaperCandidateInvalid,
    PaperCandidateUnavailable,
    PaperRegistryReader,
    UsageContextViolation,
    authorization_scope_errors,
    enforce_authorization_scope,
    normalize_authorization_scope,
    paper_candidate_usage_context,
    require_dedicated_subject_scope,
    require_paper_owned_decision,
    resolve_dev_paper_grant,
    validate_paper_decide_body,
    validate_paper_expiry,
    validate_paper_proposal,
    verify_paper_candidate_for_decision,
    verify_paper_registry_candidate,
)
from services.governance.test_approval_authority import approval_snapshot
from services.registry.paper_strategy_spec import build_strategy_spec
from services.registry.strategy_artifact import (
    BUILTIN_STRATEGY_ARTIFACT_PATHS,
    load_strategy_artifact_registration,
    strategy_artifact_checksum,
)

_CP_GOV = Path(__file__).resolve().parent.parent / "control-plane" / "governance"
if str(_CP_GOV) not in sys.path:
    sys.path.insert(0, str(_CP_GOV))
from approval_decision import (  # noqa: E402
    ApprovalDecision, ApprovalDecisionStore, DecisionOutcome, validate_decision_json,
)

NOW = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)
ENABLED_ENV = {"GOVERNANCE_DEV_PAPER_APPROVAL_ENABLED": "true", "PANTHEON_ENV": "dev"}
TENANT, PERSONA, POOL = "tenant-dev", "persona-paper-001", "pool-paper-001"


def _ctx(**overrides):
    values = dict(
        actor_id=DEV_PAPER_PROVISIONER_SUBJECT,
        roles=frozenset({"automated_gate"}),
        token_kind="jwt",
        claims={
            "sub": DEV_PAPER_PROVISIONER_SUBJECT, "tenant_id": TENANT,
            "roles": ["automated_gate"], "scope": DEV_PAPER_APPROVAL_SCOPE,
        },
    )
    claims = dict(values["claims"], **overrides.pop("claims", {}))
    values.update(overrides)
    values["claims"] = claims
    return SimpleNamespace(**values)


# ---------------------------------------------------------------------------
# Principal grant
# ---------------------------------------------------------------------------

def test_exact_dedicated_principal_is_granted():
    grant = resolve_dev_paper_grant(_ctx(), env=ENABLED_ENV)
    assert grant == DevPaperGrant(subject=DEV_PAPER_PROVISIONER_SUBJECT, tenant_id=TENANT)
    assert grant.authorization_scope == DEV_PAPER_AUTHORIZATION_SCOPE


def test_other_subjects_fall_through_to_generic_handling():
    other = _ctx(actor_id="synthetic-reviewer", claims={"sub": "synthetic-reviewer", "scope": "junk"})
    assert resolve_dev_paper_grant(other, env=ENABLED_ENV) is None
    assert resolve_dev_paper_grant(other, env={}) is None


@pytest.mark.parametrize("env", [{}, {"GOVERNANCE_DEV_PAPER_APPROVAL_ENABLED": "true"},
                                 {"PANTHEON_ENV": "dev"}, {**ENABLED_ENV, "PANTHEON_ENV": "prod"},
                                 {**ENABLED_ENV, "GOVERNANCE_DEV_PAPER_APPROVAL_ENABLED": "1"}])
def test_missing_grant_configuration_fails_closed(env):
    with pytest.raises(PaperApprovalDenied):
        resolve_dev_paper_grant(_ctx(), env=env)


@pytest.mark.parametrize("change", [
    {"claims": {"tenant_id": "tenant-other"}},
    {"claims": {"scope": None}},
    {"claims": {"scope": "pantheon:dev-paper-approval extra"}},
    {"claims": {"scope": "pantheon:prod-paper-approval"}},
    {"roles": frozenset({"governance_reviewer"})},
    {"roles": frozenset({"automated_gate", "operator"})},
    {"roles": frozenset()},
    {"token_kind": "structured"},
    {"actor_id": "someone-else"},
    {"claims": {"sub": "someone-else"}},
])
def test_dedicated_subject_with_incorrect_claims_never_falls_back(change):
    with pytest.raises(PaperApprovalDenied):
        resolve_dev_paper_grant(_ctx(**change), env=ENABLED_ENV)


# ---------------------------------------------------------------------------
# authorization_scope + usage context
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value", [
    None, "dev", [], {}, {"environment": "dev"},
    {**DEV_PAPER_AUTHORIZATION_SCOPE, "extra": 1},
    {**DEV_PAPER_AUTHORIZATION_SCOPE, "environment": "Dev"},
    {**DEV_PAPER_AUTHORIZATION_SCOPE, "environment": ""},
    {**DEV_PAPER_AUTHORIZATION_SCOPE, "allowed_target_stages": []},
    {**DEV_PAPER_AUTHORIZATION_SCOPE, "allowed_target_stages": ["prod"]},
    {**DEV_PAPER_AUTHORIZATION_SCOPE, "allowed_target_stages": ["paper", "paper"]},
    {**DEV_PAPER_AUTHORIZATION_SCOPE, "allowed_target_stages": "paper"},
    {**DEV_PAPER_AUTHORIZATION_SCOPE, "max_capital_scale_pct": True},
    {**DEV_PAPER_AUTHORIZATION_SCOPE, "max_capital_scale_pct": "0"},
    {**DEV_PAPER_AUTHORIZATION_SCOPE, "max_capital_scale_pct": -1},
    {**DEV_PAPER_AUTHORIZATION_SCOPE, "max_capital_scale_pct": 101},
    {**DEV_PAPER_AUTHORIZATION_SCOPE, "max_capital_scale_pct": float("nan")},
])
def test_malformed_scope_is_rejected(value):
    with pytest.raises(AuthorizationScopeError):
        normalize_authorization_scope(value)


def test_scope_normalization_is_canonical_copy():
    normalized = normalize_authorization_scope(DEV_PAPER_AUTHORIZATION_SCOPE)
    assert normalized == DEV_PAPER_AUTHORIZATION_SCOPE
    assert normalized is not DEV_PAPER_AUTHORIZATION_SCOPE
    normalized["allowed_target_stages"].append("live")
    assert DEV_PAPER_AUTHORIZATION_SCOPE["allowed_target_stages"] == ["paper"]


def test_scoped_approval_without_usage_context_fails_closed():
    with pytest.raises(UsageContextViolation, match="explicit usage context"):
        enforce_authorization_scope(DEV_PAPER_AUTHORIZATION_SCOPE, None)


@pytest.mark.parametrize("context", [
    ApprovalUsageContext("dev", "canary", 0),
    ApprovalUsageContext("dev", "live", 0),
    ApprovalUsageContext("dev", "paper", 0.5),
    ApprovalUsageContext("dev", "paper", 100),
    ApprovalUsageContext("prod", "paper", 0),
    ApprovalUsageContext("", "paper", 0),
    ApprovalUsageContext("dev", "", 0),
    ApprovalUsageContext("dev", "paper", -1),
    ApprovalUsageContext("dev", "paper", float("inf")),
    ApprovalUsageContext("dev", "paper", True),
    {"environment": "dev", "target_stage": "paper"},
    {"environment": "dev", "target_stage": "paper", "capital_scale_pct": 0, "x": 1},
    "dev/paper/0",
])
def test_scope_rejects_out_of_bound_usage(context):
    with pytest.raises(UsageContextViolation):
        enforce_authorization_scope(DEV_PAPER_AUTHORIZATION_SCOPE, context)


def test_scope_admits_dev_paper_zero_capital_only():
    assert enforce_authorization_scope(DEV_PAPER_AUTHORIZATION_SCOPE, ApprovalUsageContext("dev", "paper", 0)) == DEV_PAPER_AUTHORIZATION_SCOPE
    assert enforce_authorization_scope(
        DEV_PAPER_AUTHORIZATION_SCOPE, {"environment": "dev", "target_stage": "paper", "capital_scale_pct": 0.0}
    ) == DEV_PAPER_AUTHORIZATION_SCOPE
    # Unscoped approvals keep their existing meaning regardless of context.
    assert enforce_authorization_scope(None, None) is None
    assert enforce_authorization_scope(None, ApprovalUsageContext("prod", "live", 100)) is None


# ---------------------------------------------------------------------------
# Proposal / transition admission
# ---------------------------------------------------------------------------

def _proposal(**overrides):
    body = dict(target_type="registry_entry", target_id="reg-spec-001", target_version="1.0.0",
                risk_level="low", tenant_id=TENANT, owner_user_id=DEV_PAPER_PROVISIONER_SUBJECT,
                persona_id=PERSONA, capital_pool_id=POOL, candidate_digest="sha256:" + "a" * 64,
                expires_at=(NOW + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"))
    body.update(overrides)
    return body


GRANT = DevPaperGrant(subject=DEV_PAPER_PROVISIONER_SUBJECT, tenant_id=TENANT)


def test_valid_paper_proposal_is_admitted():
    validate_paper_proposal(_proposal(), grant=GRANT, now=NOW)


@pytest.mark.parametrize("change,status", [
    ({"target_type": "strategy_spec"}, 403), ({"risk_level": "medium"}, 403), ({"risk_level": "high"}, 403),
    ({"tenant_id": "tenant-other"}, 403), ({"owner_user_id": "synthetic-reviewer"}, 403),
    ({"persona_id": None}, 422), ({"capital_pool_id": ""}, 422), ({"candidate_digest": " "}, 422),
    ({"expires_at": None}, 422), ({"expires_at": "2026-09-08T11:00:00Z"}, 422),
    ({"expires_at": "2026-09-09T12:00:01Z"}, 422), ({"expires_at": "2099-01-01T00:00:00Z"}, 422),
    ({"expires_at": "2026-09-08T13:00:00"}, 422), ({"expires_at": "soon"}, 422),
])
def test_out_of_scope_or_unbounded_proposal_is_denied(change, status):
    with pytest.raises(PaperApprovalDenied) as info:
        validate_paper_proposal(_proposal(**change), grant=GRANT, now=NOW)
    assert info.value.status_code == status


def test_expiry_bound_is_exactly_24h_after_created_at():
    created = "2026-09-08T12:00:00Z"
    assert validate_paper_expiry("2026-09-09T12:00:00Z", created_at=created, now=NOW)
    with pytest.raises(PaperApprovalDenied):
        validate_paper_expiry("2026-09-09T12:00:01Z", created_at=created, now=NOW)
    with pytest.raises(PaperApprovalDenied):
        validate_paper_expiry("2026-09-09T12:00:00Z", created_at=None, now=NOW)


def _decision(**overrides):
    values = dict(_proposal(), created_at="2026-09-08T12:00:00Z",
                  authorization_scope=copy.deepcopy(DEV_PAPER_AUTHORIZATION_SCOPE))
    values.update(overrides)
    return values


def test_owned_scoped_decision_is_admitted():
    require_paper_owned_decision(_decision(), grant=GRANT, now=NOW)


@pytest.mark.parametrize("change", [
    {"owner_user_id": "synthetic-reviewer"}, {"tenant_id": "tenant-other"},
    {"target_type": "strategy_spec"}, {"risk_level": "high"},
    {"authorization_scope": None},  # scope erasure
    {"authorization_scope": {**DEV_PAPER_AUTHORIZATION_SCOPE, "allowed_target_stages": ["live"]}},
    {"authorization_scope": {**DEV_PAPER_AUTHORIZATION_SCOPE, "max_capital_scale_pct": 5}},
    {"authorization_scope": {**DEV_PAPER_AUTHORIZATION_SCOPE, "environment": "prod"}},
    {"authorization_scope": {"environment": "dev"}},
    {"expires_at": "2026-09-08T11:59:59Z"}, {"expires_at": "2026-09-10T00:00:00Z"},
])
def test_foreign_unscoped_or_expired_decision_is_denied(change):
    with pytest.raises(PaperApprovalDenied):
        require_paper_owned_decision(_decision(**change), grant=GRANT, now=NOW)


@pytest.mark.parametrize("body", [
    {"outcome": "approved_with_conditions", "conditions": ["c"]},
    {"outcome": "approved", "conditions": ["c"]},
    {"outcome": "approved", "candidate_digest": "sha256:" + "b" * 64},
    {"outcome": "approved", "expires_at": "2026-09-10T00:00:00Z"},
    {"outcome": "approved", "expires_at": "2026-09-08T11:00:00Z"},
])
def test_decide_body_cannot_widen_the_proposal(body):
    with pytest.raises(PaperApprovalDenied):
        validate_paper_decide_body(body, decision=_decision(), now=NOW)


def test_decide_body_may_only_restate_the_proposal():
    validate_paper_decide_body({"outcome": "approved"}, decision=_decision(), now=NOW)
    validate_paper_decide_body({"outcome": "rejected", "candidate_digest": "sha256:" + "a" * 64,
                                "expires_at": "2026-09-08T13:00:00Z"}, decision=_decision(), now=NOW)
    # Semantic RFC3339 equality: the same instant in another spelling restates the proposal.
    validate_paper_decide_body({"outcome": "approved", "expires_at": "2026-09-08T13:00:00+00:00"},
                               decision=_decision(), now=NOW)
    validate_paper_decide_body({"outcome": "approved", "expires_at": "2026-09-08T21:00:00+08:00"},
                               decision=_decision(), now=NOW)


@pytest.mark.parametrize("expires_at", [
    "2026-09-08T12:30:00Z",   # earlier, still future and inside 24h
    "2026-09-08T13:00:01Z",   # one second later, inside 24h
    "2026-09-09T11:59:59Z",   # last instant inside the 24h bound
    "2026-09-08T14:00:00+00:30",  # different, still-future offset instant
])
def test_decide_body_expires_at_must_equal_the_proposal_not_merely_stay_bounded(expires_at):
    with pytest.raises(PaperApprovalDenied, match="must equal the proposal expires_at"):
        validate_paper_decide_body({"outcome": "approved", "expires_at": expires_at}, decision=_decision(), now=NOW)


# ---------------------------------------------------------------------------
# Immutable Registry candidate verification
# ---------------------------------------------------------------------------

def _embedded_metadata(**overrides):
    values = {"tenant_id": TENANT, "persona_id": PERSONA, "capital_pool_id": POOL,
              "execution_context": "paper", "capital_scale_pct": 0}
    values.update(overrides)
    return values


def _spec_entry(*, registry_id="reg-spec-001", strategy_id=None, version="1.0.0", state="candidate"):
    spec = build_strategy_spec()
    if strategy_id:
        spec["strategy_id"] = strategy_id
    spec["metadata"] = _embedded_metadata()
    return {
        "registry_id": registry_id, "artifact_type": "strategy_spec", "strategy_id": spec["strategy_id"],
        "version": version, "artifact_state": state, "owner_tenant": TENANT,
        "checksum": scope_module._canonical_sha256(spec),
        "lineage": {"source_run_ids": ["paper-proof-run"]},
        "evaluation_summary": {"sharpe": 99, "mutable": "ignored"},
        "metadata": {"execution_context": "paper", "capital_scale_pct": 0, "persona_id": PERSONA,
                     "capital_pool_id": POOL, "strategy_spec": spec},
    }


def _rehash_spec(entry):
    """Recompute the checksum so only content, never a digest mismatch, can deny."""
    entry["checksum"] = scope_module._canonical_sha256(entry["metadata"]["strategy_spec"])
    return entry


def _bundle_entry(spec_entry):
    artifact = copy.deepcopy(load_strategy_artifact_registration(BUILTIN_STRATEGY_ARTIFACT_PATHS[0])["strategy_artifact"])
    artifact.update(artifact_id="reg-bundle-001", version=spec_entry["version"], strategy_id=spec_entry["strategy_id"])
    artifact["binding_intent"]["persona_id"] = PERSONA
    artifact["lineage"]["source_strategy_spec_id"] = spec_entry["registry_id"]
    return {
        "registry_id": "reg-bundle-001", "artifact_type": "execution_bundle", "strategy_id": artifact["strategy_id"],
        "version": artifact["version"], "artifact_state": "candidate", "owner_tenant": TENANT,
        "checksum": strategy_artifact_checksum(artifact),
        "lineage": {"source_run_ids": ["EVOLOOP-003"], "source_strategy_spec_id": spec_entry["registry_id"]},
        "metadata": {"execution_context": "paper", "capital_scale_pct": 0, "persona_id": PERSONA,
                     "capital_pool_id": POOL, "strategy_artifact": artifact},
    }


def _expectation(entry):
    return PaperCandidateExpectation(tenant_id=TENANT, persona_id=PERSONA, capital_pool_id=POOL,
                                     target_id=entry["registry_id"], target_version=entry["version"],
                                     candidate_digest=entry["checksum"])


def _verify(entry, views=None):
    views = dict(views or {})
    return verify_paper_registry_candidate({"entry": entry}, expectation=_expectation(entry),
                                           read_entry_view=lambda rid: views[rid])


def test_paper_spec_candidate_is_admitted_from_immutable_content_only():
    report = _verify(_spec_entry())
    assert report["kind"] == "strategy_spec" and report["capital_scale_pct"] == 0
    assert report["checksum"] == _spec_entry()["checksum"]


def _tamper(entry, path, value):
    node = entry
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    return entry


@pytest.mark.parametrize("path,value", [
    (("checksum",), "sha256:" + "b" * 64), (("artifact_state",), "approved"), (("artifact_state",), "draft"),
    (("owner_tenant",), "tenant-other"), (("version",), "1.0.1"), (("artifact_type",), "model_artifact"),
    (("metadata", "execution_context"), "live"), (("metadata", "execution_context"), None),
    (("metadata", "capital_scale_pct"), 1), (("metadata", "capital_scale_pct"), False),
    (("metadata", "capital_scale_pct"), "0"), (("metadata", "persona_id"), "persona-other"),
    (("metadata", "capital_pool_id"), "pool-other"), (("metadata", "strategy_spec"), None),
    (("metadata", "strategy_spec", "execution_profile", "execution_mode_hint"), "canary"),
    (("metadata", "strategy_spec", "title"), ""), (("metadata", "strategy_spec", "strategy_id"), "other"),
    (("metadata", "strategy_spec", "objective"), "changed after checksum"),
    (("metadata",), None),
])
def test_malformed_or_changed_spec_candidate_is_denied(path, value):
    entry = _tamper(_spec_entry(), path, value)
    if path == ("version",):
        # Version drift is caught against the approval target, not silently re-bound.
        pass
    with pytest.raises(PaperCandidateInvalid):
        verify_paper_registry_candidate({"entry": entry}, expectation=_expectation(_spec_entry()),
                                        read_entry_view=lambda rid: pytest.fail("no lineage read"))


def test_evaluation_summary_is_never_authority():
    entry = _spec_entry()
    entry["evaluation_summary"] = {"approved": True, "capital_scale_pct": 100, "execution_context": "live"}
    assert _verify(entry)["kind"] == "strategy_spec"
    entry["evaluation_summary"] = None
    assert _verify(entry)["kind"] == "strategy_spec"


@pytest.mark.parametrize("change", [
    {"persona_id": "persona-other"}, {"capital_pool_id": "pool-other"}, {"tenant_id": "tenant-other"},
    {"execution_context": "live"}, {"execution_context": None}, {"capital_scale_pct": 1},
    {"capital_scale_pct": False}, {"capital_scale_pct": "0"}, {"tenant_id": None},
])
def test_checksummed_spec_metadata_is_bound_even_when_outer_metadata_and_digest_agree(change):
    entry = _spec_entry()
    entry["metadata"]["strategy_spec"]["metadata"] = _embedded_metadata(**change)
    _rehash_spec(entry)
    # Outer owner metadata is untouched and the digest is consistent with the
    # content: the denial can only come from the embedded binding.
    assert entry["metadata"]["persona_id"] == PERSONA and entry["metadata"]["capital_pool_id"] == POOL
    with pytest.raises(PaperCandidateInvalid, match="strategy_spec.metadata"):
        _verify(entry)


def test_spec_without_embedded_metadata_is_denied_even_with_valid_digest():
    entry = _spec_entry()
    del entry["metadata"]["strategy_spec"]["metadata"]
    _rehash_spec(entry)
    with pytest.raises(PaperCandidateInvalid, match="strategy_spec.metadata is required"):
        _verify(entry)


def test_outer_metadata_alone_cannot_admit_when_embedded_disagrees_and_vice_versa():
    embedded_bad = _spec_entry()
    embedded_bad["metadata"]["strategy_spec"]["metadata"]["persona_id"] = "persona-other"
    _rehash_spec(embedded_bad)
    with pytest.raises(PaperCandidateInvalid):
        _verify(embedded_bad)
    outer_bad = _spec_entry()
    outer_bad["metadata"]["persona_id"] = "persona-other"
    with pytest.raises(PaperCandidateInvalid, match="candidate metadata.persona_id"):
        _verify(outer_bad)


def test_paper_bundle_candidate_follows_exact_lineage_to_approved_paper_spec():
    spec = _spec_entry(strategy_id="tw_session_momentum", state="approved")
    bundle = _bundle_entry(spec)
    report = _verify(bundle, {spec["registry_id"]: {"entry": spec}})
    assert report["kind"] == "execution_bundle"
    assert report["source_strategy_spec_id"] == spec["registry_id"]
    assert report["source_strategy_spec_checksum"] == spec["checksum"]


@pytest.mark.parametrize("mutate", [
    lambda b, s: _tamper(b, ("metadata", "strategy_artifact", "algorithm_ref", "signal_interface"), "services.execution.lean_runtime.live_signal_producer:Strategy"),
    lambda b, s: _tamper(b, ("metadata", "strategy_artifact", "binding_intent", "persona_id"), "persona-other"),
    lambda b, s: _tamper(b, ("metadata", "strategy_artifact", "parameters", "lookback_days"), 13),
    lambda b, s: b["metadata"]["strategy_artifact"]["lineage"].pop("source_strategy_spec_id"),
    lambda b, s: _tamper(b, ("lineage", "source_strategy_spec_id"), "reg-spec-other"),
    lambda b, s: _tamper(b, ("metadata", "strategy_artifact"), None),
    lambda b, s: _tamper(s, ("artifact_state",), "candidate"),
    lambda b, s: _tamper(s, ("artifact_type",), "model_artifact"),
    lambda b, s: _tamper(s, ("owner_tenant",), "tenant-other"),
    lambda b, s: _tamper(s, ("version",), "2.0.0"),
    lambda b, s: _tamper(s, ("strategy_id",), "other"),
    lambda b, s: _tamper(s, ("metadata", "capital_scale_pct"), 5),
    lambda b, s: _tamper(s, ("metadata", "execution_context"), "canary"),
    lambda b, s: _tamper(s, ("metadata", "strategy_spec", "objective"), "drift"),
    lambda b, s: _tamper(s, ("registry_id",), "reg-spec-renamed"),
])
def test_bundle_with_wrong_interface_lineage_or_spec_is_denied(mutate):
    spec = _spec_entry(strategy_id="tw_session_momentum", state="approved")
    bundle = _bundle_entry(spec)
    expectation = _expectation(bundle)
    mutate(bundle, spec)
    with pytest.raises(PaperCandidateInvalid):
        verify_paper_registry_candidate({"entry": bundle}, expectation=expectation,
                                        read_entry_view=lambda rid: {"entry": spec})


@pytest.mark.parametrize("where,key,value", [
    ("embedded", "persona_id", "persona-other"), ("embedded", "capital_pool_id", "pool-other"),
    ("embedded", "tenant_id", "tenant-other"), ("embedded", "capital_scale_pct", 1),
    ("embedded", "execution_context", "canary"),
    ("outer", "persona_id", "persona-other"), ("outer", "capital_pool_id", "pool-other"),
])
def test_bundle_parent_spec_must_bind_persona_and_pool_in_both_embedded_and_outer_metadata(where, key, value):
    spec = _spec_entry(strategy_id="tw_session_momentum", state="approved")
    if where == "embedded":
        spec["metadata"]["strategy_spec"]["metadata"][key] = value
        _rehash_spec(spec)  # digest stays consistent; outer metadata untouched
        assert spec["metadata"]["persona_id"] == PERSONA and spec["metadata"]["capital_pool_id"] == POOL
    else:
        spec["metadata"][key] = value  # embedded (checksummed) metadata stays correct
    bundle = _bundle_entry(spec)
    with pytest.raises(PaperCandidateInvalid, match="source strategy spec"):
        verify_paper_registry_candidate({"entry": bundle}, expectation=_expectation(bundle),
                                        read_entry_view=lambda rid: {"entry": spec})


def test_registry_consumer_context_requires_validated_paper_candidate(monkeypatch):
    spec = _spec_entry()
    evidence = {"persona_id": PERSONA, "capital_pool_id": POOL}
    context = paper_candidate_usage_context(spec, evidence=evidence, read_entry=lambda rid: None, environment="dev")
    assert context == ApprovalUsageContext("dev", "paper", 0)
    with pytest.raises(PaperCandidateInvalid):
        paper_candidate_usage_context(_tamper(_spec_entry(), ("metadata", "capital_scale_pct"), 1),
                                      evidence=evidence, read_entry=lambda rid: None, environment="dev")
    with pytest.raises(PaperCandidateInvalid):
        paper_candidate_usage_context(spec, evidence={"persona_id": "persona-other", "capital_pool_id": POOL},
                                      read_entry=lambda rid: None, environment="dev")
    approved_spec = _spec_entry(strategy_id="tw_session_momentum", state="approved")
    bundle = _bundle_entry(approved_spec)
    assert paper_candidate_usage_context(bundle, evidence=evidence, read_entry={approved_spec["registry_id"]: approved_spec}.get,
                                         environment="dev") == ApprovalUsageContext("dev", "paper", 0)
    with pytest.raises(PaperCandidateInvalid):
        paper_candidate_usage_context(bundle, evidence=evidence, read_entry=lambda rid: None, environment="dev")


# ---------------------------------------------------------------------------
# Exact Registry HTTP reader
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("status,content_type,body,expected", [
    (401, "application/json", b"{}", PaperCandidateInvalid), (403, "application/json", b"{}", PaperCandidateInvalid),
    (404, "application/json", b"{}", PaperCandidateInvalid), (503, "application/json", b"{}", PaperCandidateUnavailable),
    (200, "text/html", b"<html>login</html>", PaperCandidateUnavailable),
    (200, "application/json", b"{", PaperCandidateUnavailable), (200, "application/json", b"[]", PaperCandidateInvalid),
    (200, "application/json", b"{}", PaperCandidateInvalid),
    (200, "application/json", json.dumps({"entry": {"registry_id": "wrong"}}).encode(), PaperCandidateInvalid),
    (302, "application/json", b"{}", PaperCandidateUnavailable),
])
def test_exact_registry_reader_fails_closed_without_following_redirects(status, content_type, body, expected):
    observed = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            observed.append(self.path)
            assert self.headers["Authorization"] == "Bearer isolated-registry-token"
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            if status == 302:
                self.send_header("Location", "/credential-leak-target")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        reader = PaperRegistryReader(base_url=f"http://127.0.0.1:{server.server_port}", service_token="isolated-registry-token")
        with pytest.raises(expected) as info:
            reader.get_entry_view("reg-spec-001")
        assert "login" not in str(info.value) and "credential" not in str(info.value)
        assert observed == ["/api/registry/entries/reg-spec-001"]
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_registry_reader_serves_verify_for_decision():
    spec = _spec_entry()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"entry": spec, "deployment_stage": "none"}).encode())

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        reader = PaperRegistryReader(base_url=f"http://127.0.0.1:{server.server_port}", service_token="t")
        decision = _decision(target_id=spec["registry_id"], candidate_digest=spec["checksum"])
        assert verify_paper_candidate_for_decision(decision, reader=reader)["kind"] == "strategy_spec"
        with pytest.raises(PaperCandidateInvalid):
            verify_paper_candidate_for_decision(dict(decision, candidate_digest="sha256:" + "b" * 64), reader=reader)
        with pytest.raises(PaperCandidateInvalid):
            verify_paper_candidate_for_decision(dict(decision, persona_id=None), reader=reader)
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


@pytest.mark.parametrize("url,token,timeout", [
    ("file:///tmp/registry", "t", 5), ("http://[broken", "t", 5), ("http://user:pw@registry", "t", 5),
    ("http://registry?x=1", "t", 5), ("http://registry", "", 5), ("http://registry", "t", 0),
    ("http://registry", "t", float("inf")),
])
def test_invalid_registry_reader_configuration_is_unavailable(url, token, timeout):
    with pytest.raises(PaperCandidateUnavailable):
        PaperRegistryReader(base_url=url, service_token=token, timeout_seconds=timeout)


def test_registry_reader_connection_refused_is_unavailable():
    import socket
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with pytest.raises(PaperCandidateUnavailable):
        PaperRegistryReader(base_url=f"http://127.0.0.1:{port}", service_token="t", timeout_seconds=0.2).get_entry_view("x")


# ---------------------------------------------------------------------------
# Durable decision serialization and evidence
# ---------------------------------------------------------------------------

def test_scoped_decision_round_trips_through_every_transition_and_reload(tmp_path):
    decision = ApprovalDecision.create_proposed(
        decision_id="apv-paper-001", target_type="registry_entry", target_id="reg-spec-001",
        target_version="1.0.0", tenant_id=TENANT, owner_user_id=DEV_PAPER_PROVISIONER_SUBJECT,
        persona_id=PERSONA, capital_pool_id=POOL, candidate_digest="sha256:" + "a" * 64,
        expires_at="2099-01-01T00:00:00Z", authorization_scope=DEV_PAPER_AUTHORIZATION_SCOPE,
    )
    assert decision.validate() == []
    assert decision.to_dict()["authorization_scope"] == DEV_PAPER_AUTHORIZATION_SCOPE
    decision.accept_review("automated_gate", DEV_PAPER_PROVISIONER_SUBJECT)
    decision.decide(DecisionOutcome.APPROVED, "paper proof", actor_role="automated_gate",
                    actor_id=DEV_PAPER_PROVISIONER_SUBJECT)
    assert decision.authorization_scope == DEV_PAPER_AUTHORIZATION_SCOPE
    reloaded = ApprovalDecision.from_dict(json.loads(decision.to_json()))
    assert reloaded.authorization_scope == DEV_PAPER_AUTHORIZATION_SCOPE and reloaded.validate() == []
    store = ApprovalDecisionStore(str(tmp_path / "decisions.json"))
    store.put(decision)
    assert ApprovalDecisionStore(str(tmp_path / "decisions.json")).get("apv-paper-001").authorization_scope == DEV_PAPER_AUTHORIZATION_SCOPE
    assert validate_decision_json(decision.to_dict()) == []


def test_unscoped_decisions_serialize_without_scope():
    decision = ApprovalDecision.create_proposed("apv-legacy", "registry_entry", "reg", "1")
    assert decision.to_dict()["authorization_scope"] is None
    legacy = ApprovalDecision.from_dict({k: v for k, v in decision.to_dict().items() if k != "authorization_scope"})
    assert legacy.authorization_scope is None and legacy.validate() == []


@pytest.mark.parametrize("scope", [
    None,
    {"environment": "dev"},
    {**DEV_PAPER_AUTHORIZATION_SCOPE, "allowed_target_stages": ["paper", "canary"]},
    {**DEV_PAPER_AUTHORIZATION_SCOPE, "max_capital_scale_pct": 5},
    {**DEV_PAPER_AUTHORIZATION_SCOPE, "environment": "prod"},
])
@pytest.mark.parametrize("binding", ["actor", "owner", "both"])
def test_dedicated_subject_never_holds_unscoped_or_broadened_decision(scope, binding):
    actor = DEV_PAPER_PROVISIONER_SUBJECT if binding in ("actor", "both") else "synthetic-reviewer"
    owner = DEV_PAPER_PROVISIONER_SUBJECT if binding in ("owner", "both") else "synthetic-reviewer"
    assert authorization_scope_errors(actor_id=actor, owner_user_id=owner, authorization_scope=scope)
    with pytest.raises(AuthorizationScopeError):
        require_dedicated_subject_scope(actor_id=actor, owner_user_id=owner, authorization_scope=scope)
    payload = dict(ApprovalDecision.create_proposed("apv-x", "registry_entry", "reg", "1", tenant_id=TENANT,
                                                    owner_user_id=owner).to_dict(), authorization_scope=scope,
                   actor_id=actor if binding != "owner" else None)
    if binding == "owner":
        payload["actor_id"] = None
    loaded = ApprovalDecision.from_dict(payload)
    assert any("authorization_scope" in error for error in loaded.validate())
    assert any("authorization_scope" in error for error in validate_decision_json(payload))
    # Common evidence predicate: a stale/schema-lost response is not legacy authority.
    evidence = ApprovalEvidence.model_validate(approval_snapshot(
        actor_id=actor, owner_user_id=owner, actor_role="automated_gate", authorization_scope=scope))
    with pytest.raises(ApprovalInvalid, match="authorization_scope"):
        evidence.require_valid(usage_context=ApprovalUsageContext("dev", "paper", 0))
    with pytest.raises(ApprovalInvalid):
        evidence.require_valid()


def test_dedicated_subject_with_exact_scope_and_generic_subjects_are_admitted():
    assert authorization_scope_errors(actor_id=DEV_PAPER_PROVISIONER_SUBJECT, owner_user_id=DEV_PAPER_PROVISIONER_SUBJECT,
                                      authorization_scope=DEV_PAPER_AUTHORIZATION_SCOPE) == []
    assert authorization_scope_errors(actor_id="synthetic-reviewer", owner_user_id="synthetic-reviewer",
                                      authorization_scope=None) == []
    assert authorization_scope_errors(actor_id=None, owner_user_id="pantheon-system", authorization_scope=None) == []
    scoped = ApprovalEvidence.model_validate(approval_snapshot(
        actor_id=DEV_PAPER_PROVISIONER_SUBJECT, owner_user_id=DEV_PAPER_PROVISIONER_SUBJECT,
        actor_role="automated_gate", authorization_scope=DEV_PAPER_AUTHORIZATION_SCOPE))
    assert scoped.require_valid(usage_context=ApprovalUsageContext("dev", "paper", 0)) is scoped
    generic = ApprovalEvidence.model_validate(approval_snapshot(owner_user_id="synthetic-reviewer"))
    assert generic.require_valid() is generic


def test_malformed_scope_never_becomes_unscoped():
    with pytest.raises(AuthorizationScopeError):
        ApprovalDecision.create_proposed("apv-bad", "registry_entry", "reg", "1", authorization_scope={"environment": "dev"})
    loaded = ApprovalDecision.from_dict({**ApprovalDecision.create_proposed("apv-bad", "registry_entry", "reg", "1").to_dict(),
                                         "authorization_scope": {"environment": "dev"}})
    assert any("authorization_scope" in error for error in loaded.validate())
    assert any("authorization_scope" in error for error in validate_decision_json(loaded.to_dict()))


def test_evidence_carries_scope_and_common_validity_enforces_it():
    scoped = ApprovalEvidence.model_validate(approval_snapshot(authorization_scope=DEV_PAPER_AUTHORIZATION_SCOPE))
    assert scoped.model_dump()["authorization_scope"] == DEV_PAPER_AUTHORIZATION_SCOPE
    with pytest.raises(ApprovalInvalid, match="usage context"):
        scoped.require_valid()
    assert scoped.require_valid(usage_context=ApprovalUsageContext("dev", "paper", 0)) is scoped
    for context in (ApprovalUsageContext("dev", "canary", 0), ApprovalUsageContext("dev", "live", 0),
                    ApprovalUsageContext("dev", "paper", 1), ApprovalUsageContext("prod", "paper", 0)):
        with pytest.raises(ApprovalInvalid, match="authorization_scope"):
            scoped.require_valid(usage_context=context)
    malformed = ApprovalEvidence.model_validate(approval_snapshot(authorization_scope={"environment": "dev"}))
    with pytest.raises(ApprovalInvalid):
        malformed.require_valid(usage_context=ApprovalUsageContext("dev", "paper", 0))
    unscoped = ApprovalEvidence.model_validate(approval_snapshot())
    assert unscoped.require_valid() is unscoped
    assert unscoped.require_valid(usage_context=ApprovalUsageContext("prod", "live", 100)) is unscoped


# ---------------------------------------------------------------------------
# Paper-ladder persona lifecycle widening (operator decision 2026-11-15)
# ---------------------------------------------------------------------------
PROPOSER = "persona-enrollment-proposer"
LIFECYCLE_GRANT = scope_module.PersonaLifecycleGrant(proposer_subject=PROPOSER, grant_id="dev-paper-grant:test")
WIDE_GRANT = DevPaperGrant(subject=DEV_PAPER_PROVISIONER_SUBJECT, tenant_id=TENANT, persona_lifecycle=LIFECYCLE_GRANT)
BINDINGS = {"capital_binding_id": "bind-1", "capital_pool_id": POOL, "runtime_binding_id": "rt-1",
            "deployment_plan_id": "plan-1"}


class _Bindings:
    def __init__(self, result=None, error=None):
        self.result, self.error, self.calls = result or dict(BINDINGS), error, []

    def verify(self, *, tenant_id, persona_id):
        self.calls.append((tenant_id, persona_id))
        if self.error:
            raise self.error
        return dict(self.result)


def _lifecycle(**overrides):
    values = dict(
        tenant_id=TENANT, owner_user_id=PROPOSER, target_type="persona_lifecycle_transition", target_id=PERSONA,
        persona_id=PERSONA, risk_level="low", created_at="2026-09-08T12:00:00Z",
        expires_at="2026-09-09T11:00:00Z", authorization_scope=None,
        metadata={"subject": {"persona_id": PERSONA, "from_state": "research_only", "to_state": "consultable"}},
    )
    values.update(overrides)
    return values


def _require(decision, *, grant=WIDE_GRANT, bindings=None, env=None):
    return scope_module.require_paper_lifecycle_decision(
        decision, grant=grant, now=NOW, binding_verifier=bindings or _Bindings(), env=env or ENABLED_ENV)


def test_baseline_grant_denies_proposer_owned_lifecycle_decision():
    """Reproduction (criterion 1): today's gate check refuses the enrolment decision.

    paper_approval_scope.require_paper_owned_decision requires owner == grant
    subject and the registry_entry target only.
    """
    with pytest.raises(PaperApprovalDenied, match="another owner's decision"):
        require_paper_owned_decision(_lifecycle(), grant=GRANT, now=NOW)
    with pytest.raises(PaperApprovalDenied, match="registry_entry"):
        require_paper_owned_decision(_lifecycle(owner_user_id=DEV_PAPER_PROVISIONER_SUBJECT,
                                                authorization_scope=copy.deepcopy(DEV_PAPER_AUTHORIZATION_SCOPE)),
                                     grant=GRANT, now=NOW)


@pytest.mark.parametrize("source,target", [("research_only", "consultable"), ("consultable", "paper_owner")])
def test_widened_grant_admits_paper_ladder_steps_with_evidence(source, target):
    bindings = _Bindings()
    evidence = _require(_lifecycle(metadata={"subject": {"persona_id": PERSONA, "from_state": source,
                                                         "to_state": target}}), bindings=bindings)
    assert evidence == {"grant_id": "dev-paper-grant:test", "persona_id": PERSONA, "from_state": source,
                        "to_state": target, **BINDINGS}
    assert bindings.calls == [(TENANT, PERSONA)]


@pytest.mark.parametrize("source,target", [
    ("draft", "research_only"), ("paper_owner", "live_owner"), ("research_only", "frozen"),
    ("consultable", "frozen"), ("paper_owner", "frozen"), ("live_owner", "retired"), ("frozen", "retired"),
    ("frozen", "research_only"), ("research_only", "paper_owner"), ("consultable", "consultable"), ("", ""),
])
def test_every_other_lifecycle_transition_stays_denied(source, target):
    with pytest.raises(PaperApprovalDenied, match="covers only"):
        _require(_lifecycle(metadata={"subject": {"persona_id": PERSONA, "from_state": source, "to_state": target}}))


@pytest.mark.parametrize("change", [
    {"owner_user_id": "someone-else"}, {"owner_user_id": DEV_PAPER_PROVISIONER_SUBJECT},
    {"tenant_id": "tenant-other"}, {"risk_level": "medium"}, {"risk_level": "high"},
    {"target_id": "persona-other"}, {"persona_id": "persona-other"}, {"metadata": None},
    {"expires_at": "2026-09-08T11:59:59Z"}, {"expires_at": "2026-09-10T00:00:00Z"},
])
def test_lifecycle_identity_and_expiry_changes_are_denied(change):
    with pytest.raises(PaperApprovalDenied):
        _require(_lifecycle(**change))


def test_grant_without_widening_behaves_as_today():
    with pytest.raises(PaperApprovalDenied, match="does not authorise persona lifecycle"):
        _require(_lifecycle(), grant=GRANT)


def test_non_paper_environment_is_denied():
    with pytest.raises(PaperApprovalDenied, match="paper environment"):
        _require(_lifecycle(), env={"PANTHEON_ENV": "prod"})


@pytest.mark.parametrize("error", [PaperApprovalDenied("no active paper capital binding"),
                                   PaperCandidateUnavailable("Capital read unavailable")])
def test_missing_or_unverifiable_bindings_fail_closed(error):
    with pytest.raises(type(error)):
        _require(_lifecycle(), bindings=_Bindings(error=error))


def _capital_row(**overrides):
    row = dict(binding_id="bind-1", persona_id=PERSONA, capital_pool_id=POOL, status="active", role="paper_owner",
               allowed_deployment_scope="paper", tenant_id=TENANT)
    row.update(overrides)
    return row


def _runtime_row(**overrides):
    row = dict(binding_id="rt-1", state="active", deployment_mode="paper", plan_id="plan-1", capital_pool_id=POOL,
               persona_capital_binding_id="bind-1", metadata={"persona_id": PERSONA, "tenant_id": TENANT})
    row.update(overrides)
    return row


def _verifier(capital, runtime):
    seen = []

    def read_capital(query):
        seen.append(query)
        return capital

    def read_runtime(query):
        seen.append(query)
        return {"bindings": runtime}

    return scope_module.PersonaPaperBindingVerifier(read_capital=read_capital, read_runtime=read_runtime), seen


def test_binding_verifier_reads_both_owners_and_returns_ids():
    verifier, seen = _verifier([_capital_row()], [_runtime_row()])
    assert verifier.verify(tenant_id=TENANT, persona_id=PERSONA) == BINDINGS
    assert seen[0].startswith("/api/bindings?persona_id=") and "pool_id=" in seen[1]


@pytest.mark.parametrize("capital,runtime,text", [
    ([], [_runtime_row()], "no single active paper capital"),
    ([_capital_row(status="suspended")], [_runtime_row()], "no single active paper capital"),
    ([_capital_row(role="canary_owner")], [_runtime_row()], "no single active paper capital"),
    ([_capital_row(allowed_deployment_scope="live")], [_runtime_row()], "no single active paper capital"),
    ([_capital_row(persona_id="other")], [_runtime_row()], "no single active paper capital"),
    ([_capital_row(tenant_id="tenant-other")], [_runtime_row()], "no single active paper capital"),
    ([_capital_row(), _capital_row(binding_id="bind-2")], [_runtime_row()], "more than one"),
    ("garbage", [_runtime_row()], "no single active paper capital"),
    ([_capital_row()], [], "no single active paper runtime"),
    ([_capital_row()], [_runtime_row(state="stopped")], "no single active paper runtime"),
    ([_capital_row()], [_runtime_row(deployment_mode="live")], "no single active paper runtime"),
    ([_capital_row()], [_runtime_row(persona_capital_binding_id="bind-9")], "no single active paper runtime"),
    ([_capital_row()], [_runtime_row(metadata={"persona_id": "other", "tenant_id": TENANT})], "no single active paper runtime"),
    ([_capital_row()], [_runtime_row(), _runtime_row(binding_id="rt-2")], "no single active paper runtime"),
])
def test_binding_verifier_denies_missing_or_inconsistent_owner_bindings(capital, runtime, text):
    verifier, _ = _verifier(capital, runtime)
    with pytest.raises(PaperApprovalDenied, match=text):
        verifier.verify(tenant_id=TENANT, persona_id=PERSONA)


def _grant_env(tmp_path, content, mode=0o600):
    path = tmp_path / "grant"
    path.write_text(content)
    path.chmod(mode)
    return {**ENABLED_ENV, "GOVERNANCE_DEV_PAPER_GRANT_FILE": str(path)}


def test_widened_grant_content_is_explicit_and_plain_enabled_is_unchanged(tmp_path):
    wide = _grant_env(tmp_path, "enabled;persona_lifecycle=paper_ladder;proposer=" + PROPOSER)
    grant = resolve_dev_paper_grant(_ctx(), env=wide)
    assert grant.persona_lifecycle.proposer_subject == PROPOSER
    assert grant.persona_lifecycle.grant_id.startswith("dev-paper-grant:persona-lifecycle-paper-ladder:")
    assert resolve_dev_paper_grant(_ctx(), env=_grant_env(tmp_path, "enabled")).persona_lifecycle is None
    assert resolve_dev_paper_grant(_ctx(), env=ENABLED_ENV).persona_lifecycle is None  # unmounted


@pytest.mark.parametrize("content", [
    "enabled;persona_lifecycle=paper_ladder;proposer=", "enabled;persona_lifecycle=live;proposer=x",
    "enabled;persona_lifecycle=paper_ladder;proposer=" + DEV_PAPER_PROVISIONER_SUBJECT,
    "enabled;persona_lifecycle=paper_ladder;proposer=a/b", "disabled", "enabled;other",
])
def test_malformed_widening_never_enables_or_widens(tmp_path, content):
    with pytest.raises(PaperApprovalDenied):
        resolve_dev_paper_grant(_ctx(), env=_grant_env(tmp_path, content))


def test_unsafe_grant_file_is_not_a_widening(tmp_path):
    env = _grant_env(tmp_path, "enabled;persona_lifecycle=paper_ladder;proposer=" + PROPOSER, mode=0o644)
    with pytest.raises(PaperApprovalDenied):
        resolve_dev_paper_grant(_ctx(), env=env)


# ---------------------------------------------------------------------------
# Research card admission widening (CARD-ADMISSION-PRODUCTION-WIRING-20261115, design section 6A)
# ---------------------------------------------------------------------------
CARD_PROPOSER = "research-card-proposer"
CARD_GRANT = DevPaperGrant(
    subject=DEV_PAPER_PROVISIONER_SUBJECT, tenant_id=TENANT,
    card_admission=scope_module.CardAdmissionGrant(proposer_subject=CARD_PROPOSER, grant_id="dev-paper-grant:card-test"))
CARD_CONTENT = "enabled;card_admission=paper_research;card_proposer=" + CARD_PROPOSER


def _card(**overrides):
    values = dict(
        tenant_id=TENANT, owner_user_id=CARD_PROPOSER, target_type="registry_entry", target_id="reg-spec-1",
        target_version="1.0.0", persona_id=PERSONA, proposal_id="demand-1", candidate_digest="d" * 64,
        risk_level="low", created_at="2026-09-08T12:00:00Z", expires_at="2026-09-09T11:00:00Z",
        authorization_scope=None)
    values.update(overrides)
    return values


def test_baseline_and_lifecycle_only_grants_deny_a_research_owned_card_decision():
    """Reproduction (criterion 3): without the widening the gate refuses the research proposer's decision."""
    for grant in (GRANT, WIDE_GRANT):
        with pytest.raises(PaperApprovalDenied, match="another owner's decision"):
            require_paper_owned_decision(_card(), grant=grant, now=NOW)


def test_card_grant_admits_the_research_proposer_decision_with_evidence(monkeypatch):
    monkeypatch.setenv("PANTHEON_ENV", "dev")
    require_paper_owned_decision(_card(), grant=CARD_GRANT, now=NOW)
    evidence = scope_module.require_card_admission_decision(_card(), grant=CARD_GRANT, now=NOW, env=ENABLED_ENV)
    assert evidence == {"grant_id": "dev-paper-grant:card-test", "demand_id": "demand-1",
                        "persona_id": PERSONA, "registry_id": "reg-spec-1"}


@pytest.mark.parametrize("change", [
    {"owner_user_id": "someone-else"}, {"owner_user_id": DEV_PAPER_PROVISIONER_SUBJECT},
    {"tenant_id": "tenant-other"}, {"risk_level": "medium"}, {"target_type": "persona_lifecycle_transition"},
    {"proposal_id": ""}, {"proposal_id": None}, {"persona_id": ""}, {"candidate_digest": None},
    {"expires_at": "2026-09-08T11:59:59Z"}, {"expires_at": "2026-09-12T00:00:00Z"},
])
def test_card_decision_identity_and_expiry_changes_are_denied(change, monkeypatch):
    monkeypatch.setenv("PANTHEON_ENV", "dev")
    with pytest.raises(PaperApprovalDenied):
        require_paper_owned_decision(_card(**change), grant=CARD_GRANT, now=NOW)


def test_card_expiry_allows_the_72h_the_research_owner_proposes_and_the_baseline_stays_24h(monkeypatch):
    monkeypatch.setenv("PANTHEON_ENV", "dev")
    require_paper_owned_decision(_card(expires_at="2026-09-11T12:00:00Z"), grant=CARD_GRANT, now=NOW)  # created + 72h
    with pytest.raises(PaperApprovalDenied, match="at most 24h"):
        require_paper_owned_decision(_card(owner_user_id=DEV_PAPER_PROVISIONER_SUBJECT,
                                           expires_at="2026-09-11T12:00:00Z", target_type="registry_entry",
                                           authorization_scope=copy.deepcopy(DEV_PAPER_AUTHORIZATION_SCOPE)),
                                     grant=CARD_GRANT, now=NOW)


def test_card_grant_is_paper_environment_only():
    with pytest.raises(PaperApprovalDenied, match="paper environment"):
        scope_module.require_card_admission_decision(_card(), grant=CARD_GRANT, now=NOW, env={"PANTHEON_ENV": "prod"})


def test_card_grant_does_not_widen_the_gate_own_decisions_or_lifecycle():
    with pytest.raises(PaperApprovalDenied, match="registry_entry"):
        require_paper_owned_decision(_lifecycle(owner_user_id=DEV_PAPER_PROVISIONER_SUBJECT,
                                                authorization_scope=copy.deepcopy(DEV_PAPER_AUTHORIZATION_SCOPE)),
                                     grant=CARD_GRANT, now=NOW)
    with pytest.raises(PaperApprovalDenied, match="does not authorise persona lifecycle"):
        _require(_lifecycle(), grant=CARD_GRANT)


def test_card_grant_content_is_explicit_and_combines_with_lifecycle(tmp_path):
    grant = resolve_dev_paper_grant(_ctx(), env=_grant_env(tmp_path, CARD_CONTENT))
    assert grant.card_admission.proposer_subject == CARD_PROPOSER and grant.persona_lifecycle is None
    assert grant.card_admission.grant_id.startswith("dev-paper-grant:card-admission-paper-research:")
    both = resolve_dev_paper_grant(_ctx(), env=_grant_env(
        tmp_path, "enabled;persona_lifecycle=paper_ladder;proposer=" + PROPOSER
        + ";card_admission=paper_research;card_proposer=" + CARD_PROPOSER))
    assert both.persona_lifecycle.proposer_subject == PROPOSER and both.card_admission.proposer_subject == CARD_PROPOSER
    lifecycle_only = resolve_dev_paper_grant(_ctx(), env=_grant_env(tmp_path, "enabled;persona_lifecycle=paper_ladder;proposer=" + PROPOSER))
    assert lifecycle_only.card_admission is None
    assert both.persona_lifecycle.grant_id == lifecycle_only.persona_lifecycle.grant_id
    assert resolve_dev_paper_grant(_ctx(), env=_grant_env(tmp_path, "enabled")).card_admission is None


@pytest.mark.parametrize("content", [
    "enabled;card_admission=paper_research;card_proposer=", "enabled;card_admission=live;card_proposer=x",
    "enabled;card_admission=paper_research", "enabled;card_proposer=x",
    "enabled;card_admission=paper_research;card_proposer=" + DEV_PAPER_PROVISIONER_SUBJECT,
    "enabled;card_admission=paper_research;card_proposer=a/b",
    "enabled;card_admission=paper_research;card_proposer=x;extra=1",
    "enabled;card_admission=paper_research;card_proposer=x;card_proposer=y",
])
def test_malformed_card_widening_never_enables_or_widens(tmp_path, content):
    with pytest.raises(PaperApprovalDenied):
        resolve_dev_paper_grant(_ctx(), env=_grant_env(tmp_path, content))


def test_card_candidate_verification_requires_a_gate_ready_research_spec():
    decision = _card()
    with pytest.raises(PaperCandidateInvalid, match="does not match the approval target"):
        scope_module.verify_card_admission_candidate({"entry": {"registry_id": "other"}}, decision)
    with pytest.raises(PaperCandidateInvalid, match="malformed"):
        scope_module.verify_card_admission_candidate({}, decision)
    entry = {"registry_id": "reg-spec-1", "version": "1.0.0", "owner_tenant": TENANT, "checksum": "d" * 64,
             "artifact_state": "candidate", "artifact_type": "other_type", "metadata": {}}
    with pytest.raises(PaperCandidateInvalid, match="strategy_spec entry"):
        scope_module.verify_card_admission_candidate({"entry": entry}, decision)
    with pytest.raises(PaperCandidateInvalid, match="strategy_spec is required"):
        scope_module.verify_card_admission_candidate({"entry": {**entry, "artifact_type": "strategy_spec"}}, decision)


@pytest.mark.parametrize("change,text", [
    ({"artifact_state": "approved"}, "artifact_state does not match"), ({"metadata": {}}, "strategy_artifact is required"),
    ({"metadata": {"strategy_artifact": {}}}, "validation failed"),
])
def test_card_bundle_verification_fails_closed(change, text):
    entry = {"registry_id": "bundle-1", "version": "1.0.0", "owner_tenant": TENANT, "checksum": "d" * 64,
             "artifact_state": "candidate", "artifact_type": "execution_bundle", "metadata": {}, **change}
    decision = _card(target_id="bundle-1")
    with pytest.raises(PaperCandidateInvalid, match=text):
        scope_module.verify_card_admission_candidate({"entry": entry}, decision)


def test_candidate_verification_routes_only_non_gate_owners_to_the_card_check():
    class Reader:
        def get_entry_view(self, registry_id):
            return {"entry": {"registry_id": "other"}}
    with pytest.raises(PaperCandidateInvalid, match="does not match the approval target"):
        verify_paper_candidate_for_decision(_card(), reader=Reader())


# ---------------------------------------------------------------------------
# CARD-ADMISSION-PRODUCTION-WIRING-20261115: Governance stamps the scope for the card admission target
# ---------------------------------------------------------------------------

def _governance_world(tmp_path, monkeypatch, grant_content):
    """The real Governance application with the real issued principals and a mounted grant file."""
    from fastapi.testclient import TestClient

    from scripts import issue_dev_paper_principals as issuer
    from services.governance import main as gov_main
    from services.governance.test_approval_authority_postgres import OperatorMemoryStore

    secret, issuing = "s" * 40, {}
    issuing = {"PANTHEON_ENV": "dev", "PANTHEON_DEV_BFF_TENANT_ID": TENANT,
               "PANTHEON_DEV_PAPER_PRINCIPALS_AUTHORIZED": "true", "PANTHEON_DEV_BFF_JWT_SECRET": secret,
               "PANTHEON_DEV_BFF_JWT_ISSUER": "iss-test", "PANTHEON_DEV_BFF_JWT_AUDIENCE": "aud-test"}
    issued = issuer.issue_environment(issuing)
    grant_file = tmp_path / "grant"
    grant_file.write_text(grant_content)
    grant_file.chmod(0o600)
    for name, value in {"JWT_SECRET": secret, "JWT_ISSUER": "iss-test", "JWT_AUDIENCE": "aud-test"}.items():
        monkeypatch.setenv(f"PANTHEON_GOVERNANCE_{name}", value)
    monkeypatch.setenv("GOVERNANCE_DEV_PAPER_GRANT_FILE", str(grant_file))
    monkeypatch.setenv("GOVERNANCE_DEV_PAPER_APPROVAL_ENABLED", "true")
    monkeypatch.setenv("PANTHEON_ENV", "dev")
    monkeypatch.setattr(gov_main, "store", OperatorMemoryStore())
    monkeypatch.setattr(gov_main, "verify_paper_candidate_for_decision", lambda *a, **k: None)
    monkeypatch.setattr(gov_main, "_paper_registry_reader", lambda: None)
    return TestClient(gov_main.app), gov_main.store, issued


def _propose_and_review(client, issued, decision_id, **overrides):
    expires = (datetime.now(timezone.utc) + timedelta(hours=48)).isoformat()
    body = {"decision_id": decision_id, "expected_version": 0, "tenant_id": TENANT,
            "target_type": "registry_entry", "target_id": "reg-1", "target_version": "1", "risk_level": "low",
            "persona_id": PERSONA, "owner_user_id": "pantheon-dev-research-card-proposer",
            "proposal_id": "rd-1", "proposal_revision": 1, "proposal_content_digest": "d1",
            "validation_result_digest": "d1", "candidate_digest": "d1", "expires_at": expires}
    body.update(overrides)
    proposer = {"Authorization": "Bearer " + issued["RESEARCH_GOVERNANCE_PROPOSER_TOKEN"],
                "Idempotency-Key": "p-" + decision_id}
    gate = {"Authorization": "Bearer " + issued["RESEARCH_GOVERNANCE_GATE_TOKEN"],
            "Idempotency-Key": "r-" + decision_id}
    created = client.post("/api/governance/approvals", json=body, headers=proposer)
    if created.status_code != 201:
        return created, None
    reviewed = client.post(f"/api/governance/approvals/{decision_id}/review", headers=gate, json={
        "actor_role": "automated_gate", "actor_id": "pantheon-dev-paper-provisioner", "expected_version": 1})
    return created, reviewed


def test_widened_grant_stamps_scope_on_the_card_admission_decision(tmp_path, monkeypatch):
    client, store, issued = _governance_world(
        tmp_path, monkeypatch,
        "enabled;card_admission=paper_research;card_proposer=pantheon-dev-research-card-proposer")
    _, reviewed = _propose_and_review(client, issued, "card-1")
    assert reviewed.status_code == 200, reviewed.text
    assert store.get("card-1").to_dict()["authorization_scope"] == DEV_PAPER_AUTHORIZATION_SCOPE


@pytest.mark.parametrize("grant_content,overrides", [
    # a grant without the widening behaves as today: the gate may not act on another owner's decision
    ("enabled", {}),
    # a decision of any other target type is not admitted by the widening
    ("enabled;card_admission=paper_research;card_proposer=pantheon-dev-research-card-proposer",
     {"target_type": "persona_lifecycle_transition", "risk_level": "high"}),
])
def test_no_widening_or_other_target_type_is_never_stamped(tmp_path, monkeypatch, grant_content, overrides):
    client, store, issued = _governance_world(tmp_path, monkeypatch, grant_content)
    created, reviewed = _propose_and_review(client, issued, "card-2", **overrides)
    if reviewed is not None:
        assert reviewed.status_code in (400, 403, 422, 503), reviewed.text
        stored = store.get("card-2")
        assert stored is None or stored.to_dict()["authorization_scope"] is None
    else:
        assert created.status_code in (400, 403, 422), created.text


# ---------------------------------------------------------------------------
# persona pool envelope (persona_portfolio_v1) approval by member verification
# ---------------------------------------------------------------------------

def _pool_world(*, pool_version=2):
    from services.registry.portfolio_envelope import build_portfolio_envelope

    spec = _spec_entry(strategy_id="tw_session_momentum", state="approved")
    member = _bundle_entry(spec)
    member["artifact_state"] = "approved"
    child = member["metadata"]["strategy_artifact"]
    digest = "sha256:" + "c" * 64
    envelope = build_portfolio_envelope(
        artifact_id="artifact-persona-pool-x-v2", strategy_id=child["strategy_id"], version=f"1.{pool_version}.0",
        members=[{"member_id": child["artifact_id"], "artifact": child, "risk_weight": 1.0, "virtual_only": False}],
        pool_snapshot={"pool_version": pool_version, "pool_digest": digest},
        parent_envelope_id=child["artifact_id"],
    )
    entry = {
        "registry_id": envelope["artifact_id"], "artifact_type": "execution_bundle",
        "strategy_id": envelope["strategy_id"], "version": envelope["version"],
        "artifact_state": "candidate", "owner_tenant": TENANT,
        "checksum": strategy_artifact_checksum(envelope),
        "lineage": copy.deepcopy(envelope["lineage"]),
        "metadata": {"execution_context": "paper", "capital_scale_pct": 0.0, "persona_id": PERSONA,
                     "capital_pool_id": POOL, "tenant_id": TENANT, "pool_version": pool_version,
                     "pool_digest": digest, "previous_envelope_id": child["artifact_id"],
                     "strategy_artifact": envelope},
    }
    return entry, member, spec


def _pool_verify(entry, member, spec):
    views = {member["registry_id"]: {"entry": member}, spec["registry_id"]: {"entry": spec}}
    return _verify(entry, views)


def test_pool_envelope_is_admitted_by_verifying_its_members_without_a_source_spec():
    entry, member, spec = _pool_world()
    report = _pool_verify(entry, member, spec)
    assert report["kind"] == "execution_bundle" and "source_strategy_spec_id" not in report
    assert report["pool_members"][0]["source_strategy_spec_id"] == spec["registry_id"]
    assert report["pool_members"][0]["checksum"] == member["checksum"]
    assert report["capital_scale_pct"] == 0 and report["execution_context"] == "paper"


@pytest.mark.parametrize("mutate,text", [
    (lambda e, m, s: _tamper(m, ("artifact_state",), "candidate"), "approved execution_bundle"),
    (lambda e, m, s: _tamper(m, ("owner_tenant",), "tenant-other"), "different tenant"),
    (lambda e, m, s: _tamper(m, ("version",), "9.9.9"), "version does not match"),
    (lambda e, m, s: _tamper(m, ("checksum",), "sha256:" + "d" * 64), "checksum does not match"),
    (lambda e, m, s: _tamper(m, ("metadata", "persona_id"), "persona-other"), "persona"),
    (lambda e, m, s: _tamper(m, ("metadata", "capital_scale_pct"), 5), "capital_scale_pct"),
    (lambda e, m, s: _tamper(s, ("artifact_state",), "candidate"), "approved strategy_spec"),
    (lambda e, m, s: _tamper(e, ("metadata", "pool_digest"), "sha256:" + "e" * 64), "digest"),
    (lambda e, m, s: _tamper(e, ("metadata", "pool_version"), 7), "digest"),
    (lambda e, m, s: e["metadata"].pop("pool_digest"), "pool_version and pool_digest"),
    (lambda e, m, s: _tamper(e, ("metadata", "capital_scale_pct"), 1), "capital_scale_pct"),
    (lambda e, m, s: _tamper(e, ("metadata", "tenant_id"), "tenant-other"), "tenant"),
])
def test_pool_envelope_refuses_unsound_members_or_binding(mutate, text):
    entry, member, spec = _pool_world()
    mutate(entry, member, spec)
    with pytest.raises(PaperCandidateInvalid, match=text):
        _pool_verify(entry, member, spec)


def test_pool_envelope_with_unreadable_member_is_refused():
    entry, member, spec = _pool_world()
    with pytest.raises(PaperCandidateInvalid, match="could not be read exactly"):
        _verify(entry, {member["registry_id"]: {"entry": {"registry_id": "other"}}})


def test_empty_pool_envelope_must_continue_the_approved_previous_envelope():
    from services.registry.portfolio_envelope import build_portfolio_envelope

    entry, member, spec = _pool_world()
    template = entry["metadata"]["strategy_artifact"]
    flat = build_portfolio_envelope(
        artifact_id="artifact-persona-pool-x-v3", strategy_id=template["strategy_id"], version="1.3.0",
        members=[], empty_template=template, parent_envelope_id=entry["registry_id"],
        pool_snapshot={"pool_version": 3, "pool_digest": "sha256:" + "f" * 64},
    )
    flat_entry = copy.deepcopy(entry)
    flat_entry.update(registry_id=flat["artifact_id"], version="1.3.0", checksum=strategy_artifact_checksum(flat),
                      lineage=copy.deepcopy(flat["lineage"]))
    flat_entry["metadata"].update(strategy_artifact=flat, pool_version=3, pool_digest="sha256:" + "f" * 64,
                                  previous_envelope_id=entry["registry_id"])
    previous = copy.deepcopy(entry)
    previous["artifact_state"] = "approved"
    assert _verify(flat_entry, {previous["registry_id"]: {"entry": previous}})["pool_members"] == []
    previous["artifact_state"] = "candidate"
    with pytest.raises(PaperCandidateInvalid, match="approved execution_bundle"):
        _verify(flat_entry, {previous["registry_id"]: {"entry": previous}})


# ---------------------------------------------------------------------------
# Paper pool risk-policy widening (operator decision P-4)
# ---------------------------------------------------------------------------

RISK_CLAUSE = ";risk_policy=paper_pool;risk_proposer=" + PROPOSER
_LOW_POLICY = {"risk_policy_id": "rp-pool", "version": "2.0", "gross_limit": 1.0, "net_limit": 1.0,
               "max_leverage": 1.0, "max_single_name_weight": 0.20, "min_cash_reserve_pct": 0.10}


def _risk_decision(policy=None, **overrides):
    policy = _LOW_POLICY if policy is None else policy
    values = dict(
        target_type="capital_pool_activation", target_id=POOL, capital_pool_id=POOL, tenant_id=TENANT,
        owner_user_id=PROPOSER, risk_level="low", created_at="2026-09-08T12:00:00Z",
        expires_at=(NOW + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        metadata={"subject": {"pool_id": POOL, "risk_direction": "increase", "requested_risk": "low",
                              "risk_officer_draft": {"policy": policy, "provenance": {"source": "risk_officer_draft"}}}})
    values.update(overrides)
    return values


def test_risk_policy_clause_is_explicit_and_half_pairs_widen_nothing(tmp_path):
    grant = resolve_dev_paper_grant(_ctx(), env=_grant_env(tmp_path, "enabled" + RISK_CLAUSE))
    assert grant.risk_policy.proposer_subject == PROPOSER
    assert grant.risk_policy.grant_id.startswith("dev-paper-grant:risk-policy-paper-pool:")
    assert grant.persona_lifecycle is None and grant.card_admission is None
    assert resolve_dev_paper_grant(_ctx(), env=_grant_env(tmp_path, "enabled")).risk_policy is None
    for bad in ("enabled;risk_policy=paper_pool", "enabled;risk_proposer=" + PROPOSER,
                "enabled;risk_policy=live;risk_proposer=" + PROPOSER):
        with pytest.raises(PaperApprovalDenied):  # malformed content widens nothing and enables nothing
            resolve_dev_paper_grant(_ctx(), env=_grant_env(tmp_path, bad))


def test_risk_policy_in_bounds_draft_verifies_and_identity_is_admitted(tmp_path):
    grant = resolve_dev_paper_grant(_ctx(), env=_grant_env(tmp_path, "enabled" + RISK_CLAUSE))
    decision = _risk_decision()
    assert scope_module.risk_policy_candidate_failure(decision) is None
    evidence = scope_module.require_paper_risk_policy_decision(decision, grant=grant, now=NOW, env=ENABLED_ENV)
    assert evidence["grant_id"] == grant.risk_policy.grant_id and evidence["pool_id"] == POOL


@pytest.mark.parametrize("patch,named", [
    ({"max_single_name_weight": 0.5}, "max_single_name_weight"),
    ({"min_cash_reserve_pct": 0.05}, "min_cash_reserve_pct"),
    ({"gross_limit": 2.5}, "gross_limit"),
    ({"max_leverage": 2.5}, "max_leverage"),
    ({"max_leverage": None}, "max_leverage"),
])
def test_risk_policy_out_of_bounds_or_incomplete_names_the_failing_field(patch, named):
    policy = {**_LOW_POLICY, **patch}
    policy = {k: v for k, v in policy.items() if v is not None}
    assert scope_module.risk_policy_candidate_failure(_risk_decision(policy)).startswith(named)


def test_risk_policy_at_the_bound_passes_and_failure_names_value_and_bound():
    at = {**_LOW_POLICY, "gross_limit": 2.0, "max_leverage": 2.0}
    assert scope_module.risk_policy_candidate_failure(_risk_decision(at)) is None
    msg = scope_module.risk_policy_candidate_failure(_risk_decision({**at, "gross_limit": 2.5}))
    assert "gross_limit" in msg and "2.5" in msg and "2.0" in msg


def test_governance_default_bounds_mirror_the_capital_defaults():
    from services.capital.risk_policy import DEFAULT_PROFILE_OUTER_BOUNDS
    assert {k: dict(v) for k, v in DEFAULT_PROFILE_OUTER_BOUNDS.items()} == scope_module.RISK_POLICY_DEFAULT_BOUNDS


def test_risk_policy_without_draft_provenance_or_policy_is_refused():
    no_policy = _risk_decision()
    no_policy["metadata"]["subject"].pop("risk_officer_draft")
    assert "risk_officer_draft.policy" in scope_module.risk_policy_candidate_failure(no_policy)
    no_prov = _risk_decision()
    no_prov["metadata"]["subject"]["risk_officer_draft"].pop("provenance")
    assert "provenance" in scope_module.risk_policy_candidate_failure(no_prov)


@pytest.mark.parametrize("change", [
    {"owner_user_id": "someone-else"}, {"owner_user_id": DEV_PAPER_PROVISIONER_SUBJECT},
    {"tenant_id": "tenant-other"}, {"risk_level": "medium"}, {"target_id": "other-pool"},
    {"expires_at": "2026-09-10T00:00:00Z"},
])
def test_risk_policy_identity_denials(tmp_path, change):
    grant = resolve_dev_paper_grant(_ctx(), env=_grant_env(tmp_path, "enabled" + RISK_CLAUSE))
    with pytest.raises(PaperApprovalDenied):
        scope_module.require_paper_risk_policy_decision(_risk_decision(**change), grant=grant, now=NOW, env=ENABLED_ENV)


def test_risk_policy_widening_is_dev_only_and_absent_clause_denies(tmp_path):
    grant = resolve_dev_paper_grant(_ctx(), env=_grant_env(tmp_path, "enabled" + RISK_CLAUSE))
    with pytest.raises(PaperApprovalDenied):
        scope_module.require_paper_risk_policy_decision(_risk_decision(), grant=grant, now=NOW,
                                                        env={**ENABLED_ENV, "PANTHEON_ENV": "prod"})
    with pytest.raises(PaperApprovalDenied):
        scope_module.require_paper_risk_policy_decision(_risk_decision(), grant=GRANT, now=NOW, env=ENABLED_ENV)


# ---------------------------------------------------------------------------
# RC2: the exact coordinator proposal body (nested subject) against the real Governance application
# ---------------------------------------------------------------------------

def _coordinator_risk_proposal(decision_id, policy):
    """Shape of ``proposal_body`` built by PersonaProvisioningCoordinator (nested subject values)."""
    expires = (datetime.now(timezone.utc) + timedelta(hours=23)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "expected_version": 0, "decision_id": decision_id, "target_type": "capital_pool_activation",
        "target_id": POOL, "target_version": "d" * 64, "risk_level": "low", "capital_pool_id": POOL,
        "persona_id": PERSONA, "tenant_id": TENANT, "owner_user_id": "pantheon-dev-persona-enrollment-proposer",
        "expires_at": expires, "candidate_digest": "d" * 64,
        "subject": {
            "pool_id": POOL, "risk_direction": "increase",
            "risk_comparison": {"direction": "increase", "fields": {"gross_limit": {"from": 0.5, "to": 1.0}}},
            "requested_risk": "low", "risk_policy_id": "rp-pool", "risk_policy_version": "2.0",
            "risk_policy_digest": "d" * 64, "absence_source": "none",
            "risk_officer_draft": {"provenance": {"source": "risk_officer_draft"}, "policy": dict(policy)}},
    }


@pytest.mark.parametrize("policy,outcome", [
    (_LOW_POLICY, "approved"), ({**_LOW_POLICY, "max_single_name_weight": 0.5}, "rejected")])
def test_real_governance_accepts_nested_risk_proposal_and_gate_decides(tmp_path, monkeypatch, policy, outcome):
    from services.governance import paper_approval_scope as scope

    client, store, issued = _governance_world(
        tmp_path, monkeypatch, "enabled;risk_policy=paper_pool;risk_proposer=pantheon-dev-persona-enrollment-proposer")
    did = "apv-risk-" + outcome
    body = _coordinator_risk_proposal(did, policy)
    proposer = {"Authorization": "Bearer " + issued["PANTHEON_PERSONA_ENROLLMENT_PROPOSER_TOKEN"],
                "Idempotency-Key": "p-" + did}
    created = client.post("/api/governance/approvals", json=body, headers=proposer)
    assert created.status_code == 201, created.text
    gate = {"Authorization": "Bearer " + issued["RESEARCH_GOVERNANCE_GATE_TOKEN"]}
    reviewed = client.post(f"/api/governance/approvals/{did}/review", headers={**gate, "Idempotency-Key": "r-" + did},
                           json={"actor_role": "automated_gate", "actor_id": "pantheon-dev-paper-provisioner",
                                 "expected_version": 1})
    assert reviewed.status_code == 200, reviewed.text
    stored = client.get(f"/api/governance/approvals/{did}", headers=gate).json()
    assert stored["metadata"]["subject"]["risk_officer_draft"]["policy"]["gross_limit"] == 1.0
    assert stored["metadata"]["subject"]["risk_comparison"]["direction"] == "increase"
    failure = scope.risk_policy_candidate_failure(store.get(did).to_dict()) if hasattr(store.get(did), "to_dict") else None
    assert (failure is None) == (outcome == "approved")
    if outcome == "rejected":
        assert "max_single_name_weight" in failure


def test_real_governance_legacy_v1_proposal_stays_readable_and_gate_closure_is_stated(tmp_path, monkeypatch):
    """Stored v1.0 proposals (risk_level medium, string-only subject, no draft) remain readable; the gate's
    closure of them on the real app is recorded here (see contract.md for the operator action when refused)."""
    client, store, issued = _governance_world(
        tmp_path, monkeypatch, "enabled;risk_policy=paper_pool;risk_proposer=pantheon-dev-persona-enrollment-proposer")
    did = "apv-risk-v1"
    body = _coordinator_risk_proposal(did, _LOW_POLICY)
    body["risk_level"] = "medium"
    body["subject"] = {"pool_id": POOL, "risk_direction": "increase", "requested_risk": "medium",
                       "risk_policy_id": "rp-pool", "risk_policy_version": "1.0"}
    proposer = {"Authorization": "Bearer " + issued["PANTHEON_PERSONA_ENROLLMENT_PROPOSER_TOKEN"],
                "Idempotency-Key": "p-" + did}
    assert client.post("/api/governance/approvals", json=body, headers=proposer).status_code == 201
    gate = {"Authorization": "Bearer " + issued["RESEARCH_GOVERNANCE_GATE_TOKEN"]}
    assert client.get(f"/api/governance/approvals/{did}", headers=gate).status_code == 200
    reviewed = client.post(f"/api/governance/approvals/{did}/review", headers={**gate, "Idempotency-Key": "r-v1"},
                           json={"actor_role": "automated_gate", "actor_id": "pantheon-dev-paper-provisioner",
                                 "expected_version": 1})
    # The paper gate is confined to low-risk decisions: it cannot close a medium v1.0 proposal.
    assert reviewed.status_code == 403 and "low-risk" in reviewed.text
    assert client.get(f"/api/governance/approvals/{did}", headers=gate).json()["decision_state"] == "proposed"
