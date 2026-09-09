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
