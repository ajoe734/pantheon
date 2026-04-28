from __future__ import annotations

import pytest

from services.foundation import (
    ActorRef,
    ActorType,
    AuditAction,
    AuthorityScope,
    CommandEnvelope,
    CommandRecoveryAction,
    EnvironmentName,
    EnvironmentScope,
    ErrorEnvelope,
    FoundationValidationError,
    IdempotencyRecord,
    IdempotencyStatus,
    PolicyDecision,
    PolicyDecisionValue,
    SecretProvider,
    SecretRef,
    SecretScopeType,
    TraceContext,
    canonical_json,
    command_recovery_entry,
    idempotency_record_from_entry,
    load_command_recovery_entries,
    sha256_checksum,
)


def _actor() -> ActorRef:
    return ActorRef(
        actor_type=ActorType.USER,
        actor_id="operator-1",
        roles=("operator",),
        workspace_id="workspace-a",
    )


def _environment() -> EnvironmentScope:
    return EnvironmentScope(EnvironmentName.PAPER, region="us", market="US", timezone="UTC")


def _authority_scope(environment: EnvironmentScope) -> AuthorityScope:
    return AuthorityScope(
        action="runtime.pause",
        target_type="runtime_binding",
        target_id="binding-1",
        environment=environment,
        capital_pool_id="pool-1",
    )


def test_trace_context_serializes_required_boundary_fields() -> None:
    actor = _actor()
    environment = _environment()
    trace = TraceContext.new(environment=environment, actor_ref=actor, request_id="req-1")
    child = trace.child(source_system="runtime-manager", request_id="req-2")

    payload = child.to_dict()

    assert payload["schema_version"] == "trace_context.v1"
    assert payload["trace_id"] == trace.trace_id
    assert payload["correlation_id"] == trace.correlation_id
    assert payload["parent_span_id"] == trace.trace_id
    assert payload["actor_ref"]["actor_id"] == "operator-1"
    assert payload["environment"]["name"] == "paper"
    assert payload["source_system"] == "runtime-manager"


def test_command_envelope_adds_trace_idempotency_and_stable_payload_shape() -> None:
    actor = _actor()
    environment = _environment()
    scope = _authority_scope(environment)

    command = CommandEnvelope.new(
        command_type="PauseRuntime",
        actor_ref=actor,
        authority_scope=scope,
        payload={"duration_seconds": 300, "reason": "operator test"},
    )

    payload = command.to_dict()
    assert payload["schema_version"] == "command_envelope.v1"
    assert payload["idempotency_key"].startswith("idmp-")
    assert payload["trace"]["idempotency_key"] == payload["idempotency_key"]
    assert payload["authority_scope"]["target_ref"] == "runtime_binding:binding-1"
    assert payload["payload"]["duration_seconds"] == 300


def test_command_envelope_rejects_trace_idempotency_mismatch() -> None:
    actor = _actor()
    environment = _environment()
    trace = TraceContext.new(environment=environment, actor_ref=actor, idempotency_key="idmp-a")

    with pytest.raises(FoundationValidationError):
        CommandEnvelope(
            command_id="cmd-1",
            command_type="PauseRuntime",
            actor_ref=actor,
            authority_scope=_authority_scope(environment),
            payload={},
            trace=trace,
            idempotency_key="idmp-b",
        )


def test_error_envelope_supports_validation_and_policy_denial_shapes() -> None:
    actor = _actor()
    environment = _environment()
    trace = TraceContext.new(environment=environment, actor_ref=actor)

    validation_error = ErrorEnvelope.validation(message="missing reason", trace=trace)
    policy_error = ErrorEnvelope.policy_denial(
        message="risk policy denied command",
        trace=trace,
        policy_decision_ref="poldec-1",
    )

    assert validation_error.to_dict()["status_code"] == 422
    assert policy_error.to_dict()["status_code"] == 403
    assert policy_error.to_dict()["details"]["policy_decision_ref"] == "poldec-1"
    assert policy_error.to_dict()["trace"]["trace_id"] == trace.trace_id


def test_idempotency_record_detects_duplicate_payloads_and_status_transitions() -> None:
    payload = {"command_type": "PauseRuntime", "target": "binding-1", "reason": "test"}
    record = IdempotencyRecord.reserve(
        idempotency_key="idmp-1",
        operation_type="command.runtime.pause",
        target_ref="runtime_binding:binding-1",
        request_payload=payload,
        trace_id="trace-1",
    )

    assert record.status == IdempotencyStatus.RESERVED
    assert record.matches_request({"reason": "test", "target": "binding-1", "command_type": "PauseRuntime"})
    assert not record.matches_request({**payload, "reason": "changed"})

    succeeded = record.with_status(IdempotencyStatus.SUCCEEDED, result_ref="audit:audit-1")
    assert succeeded.to_dict()["status"] == "succeeded"
    assert succeeded.to_dict()["result_ref"] == "audit:audit-1"
    assert IdempotencyRecord.from_dict(succeeded.to_dict()) == succeeded


def test_command_recovery_entries_quarantine_corrupt_partial_state() -> None:
    record = IdempotencyRecord.reserve(
        idempotency_key="idmp-recovery-1",
        operation_type="runtime_manager.kill_switch.dispatch",
        target_ref="CapitalPool:pool-1",
        request_payload={"reason": "operator_emergency_stop"},
        trace_id="trace-recovery-1",
    )
    raw_entries = {
        "idmp-recovery-1": command_recovery_entry(record, result={"command": {"command_id": "ks-1"}}),
        "bad-entry": {
            "idempotency_record": {
                **record.to_dict(),
                "idempotency_key": "bad-entry",
                "status": "not-a-status",
            }
        },
    }

    loaded, audits = load_command_recovery_entries(
        raw_entries,
        owner_service="runtime-manager",
        operation_type="runtime_manager.kill_switch.dispatch",
    )

    assert list(loaded) == ["idmp-recovery-1"]
    assert idempotency_record_from_entry(loaded["idmp-recovery-1"]) == record
    assert loaded["idmp-recovery-1"]["result"]["command"]["command_id"] == "ks-1"
    assert len(audits) == 1
    assert audits[0].action_type == CommandRecoveryAction.QUARANTINED
    assert audits[0].idempotency_key == "bad-entry"


def test_policy_decision_requires_reasons_for_denials() -> None:
    actor = _actor()
    environment = _environment()
    decision = PolicyDecision.make(
        policy_id="default_rbac_policy_v1",
        policy_version="1",
        decision=PolicyDecisionValue.DENY,
        actor_ref=actor,
        action="runtime.liquidate",
        target_ref="runtime_binding:binding-1",
        environment=environment,
        trace_id="trace-1",
        reasons=("risk_admin role required",),
    )

    assert decision.is_denied
    assert decision.to_dict()["reasons"] == ["risk_admin role required"]

    with pytest.raises(FoundationValidationError):
        PolicyDecision.make(
            policy_id="default_rbac_policy_v1",
            policy_version="1",
            decision=PolicyDecisionValue.DENY,
            actor_ref=actor,
            action="runtime.liquidate",
            target_ref="runtime_binding:binding-1",
            environment=environment,
            trace_id="trace-1",
        )


def test_audit_action_records_trace_refs_and_deterministic_checksum() -> None:
    actor = _actor()
    environment = _environment()
    trace = TraceContext.new(environment=environment, actor_ref=actor)
    payload = {"status_before": "running", "status_after": "paused"}

    audit = AuditAction.record(
        actor_ref=actor,
        action_type="runtime.pause",
        target_ref="runtime_binding:binding-1",
        environment=environment,
        reason="operator requested pause",
        trace=trace,
        payload=payload,
        before_state_ref="runtime:running",
        after_state_ref="runtime:paused",
    )

    serialized = audit.to_dict()
    assert serialized["trace_id"] == trace.trace_id
    assert serialized["correlation_id"] == trace.correlation_id
    assert serialized["payload_checksum"] == sha256_checksum(payload)
    assert serialized["before_state_ref"] == "runtime:running"


def test_secret_ref_is_metadata_only_and_blocks_frontend_consumers() -> None:
    environment = _environment()
    secret = SecretRef(
        secret_ref="secret://ibkr/paper/account",
        provider=SecretProvider.CLOUD_SECRET_MANAGER,
        scope_type=SecretScopeType.BROKER_ACCOUNT,
        scope_id="paper-account-1",
        environment=environment,
        allowed_consumers=("lean-platform-runtime",),
        metadata={"rotation_days": 90},
    )

    payload = secret.to_dict()
    assert payload["secret_ref"] == "secret://ibkr/paper/account"
    assert "value" not in canonical_json(payload)
    assert "raw_secret" not in canonical_json(payload)

    with pytest.raises(FoundationValidationError):
        SecretRef(
            secret_ref="secret://bad",
            provider=SecretProvider.ENV,
            scope_type=SecretScopeType.SERVICE,
            scope_id="svc",
            environment=environment,
            allowed_consumers=("frontend",),
        )

    with pytest.raises(FoundationValidationError):
        SecretRef(
            secret_ref="secret://bad-metadata",
            provider=SecretProvider.ENV,
            scope_type=SecretScopeType.SERVICE,
            scope_id="svc",
            environment=environment,
            metadata={"raw_secret": "should-not-cross-boundary"},
        )
