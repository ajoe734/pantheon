from __future__ import annotations

from services.foundation import (
    ActorRef,
    ActorType,
    DeadLetterQueue,
    DeadLetterReplayProcessor,
    DeadLetterStatus,
    EnvironmentName,
    EnvironmentScope,
    EventEnvelope,
    JsonlOutboxStore,
    OutboxRecord,
    OutboxRecordStatus,
    SchemaRegistry,
    TraceContext,
)


def _actor() -> ActorRef:
    return ActorRef(
        actor_type=ActorType.SERVICE,
        actor_id="runtime-manager",
        roles=("runtime_writer",),
        workspace_id="workspace-a",
    )


def _environment() -> EnvironmentScope:
    return EnvironmentScope(EnvironmentName.PAPER, region="us", market="US", timezone="UTC")


def _registry() -> SchemaRegistry:
    registry = SchemaRegistry()
    registry.register(
        subject="runtime.binding.requested",
        version=1,
        owner_service="runtime-manager",
        schema={
            "type": "object",
            "required": ["binding_id", "plan_id", "runtime_action"],
            "properties": {
                "binding_id": {"type": "string"},
                "plan_id": {"type": "string"},
                "runtime_action": {"type": "string"},
            },
        },
    )
    return registry


def _event(registry: SchemaRegistry) -> EventEnvelope:
    trace = TraceContext.new(environment=_environment(), actor_ref=_actor(), request_id="req-runtime-1")
    schema_ref = registry.resolve(subject="runtime.binding.requested", version=1).schema_ref
    return EventEnvelope.new(
        event_type="runtime.binding.requested",
        aggregate_type="runtime_binding",
        aggregate_id="binding-1",
        sequence_no=1,
        trace=trace,
        producer_service="runtime-manager",
        schema_ref=schema_ref,
        payload={
            "binding_id": "binding-1",
            "plan_id": "plan-1",
            "runtime_action": "deploy_new_binding",
        },
    )


def test_outbox_and_schema_registry_primitives_round_trip(tmp_path) -> None:
    registry = _registry()
    event = _event(registry)
    outbox = OutboxRecord.new(owner_service="runtime-manager", event=event)

    payload = outbox.to_dict()
    assert payload["schema_version"] == "outbox_record.v1"
    assert payload["status"] == "pending"
    assert payload["event"]["schema_version"] == "event_envelope.v1"
    assert payload["event"]["trace_id"] == event.trace_id
    assert payload["event"]["schema_ref"] == "runtime.binding.requested@v1"

    validation = registry.validate(event.schema_ref, event.payload)
    assert validation.valid
    assert validation.errors == ()

    store = JsonlOutboxStore(tmp_path / "outbox.jsonl")
    store.append(outbox.mark_failed("transient writer failure", dead_lettered=True))
    reloaded = store.load()

    assert len(reloaded) == 1
    assert reloaded[0].status == OutboxRecordStatus.DEAD_LETTERED
    assert reloaded[0].event.event_id == event.event_id
    assert reloaded[0].event.trace_id == event.trace_id


def test_audited_idempotent_dlq_replay_applies_duplicate_event_once() -> None:
    registry = _registry()
    event = _event(registry)
    queue = DeadLetterQueue()
    queue.reject(event, reason="writer unavailable", tags=("writer_error",), source_ref="outbox:1")
    queue.reject(event, reason="retry exhausted", tags=("retry_exhausted",), source_ref="outbox:1")
    applied: list[str] = []

    processor = DeadLetterReplayProcessor(schema_registry=registry)
    result = processor.replay(
        queue.pending_entries(),
        actor_ref=_actor(),
        environment=_environment(),
        reason="operator-approved replay after writer recovery",
        queue=queue,
        apply_fn=lambda replayed: applied.append(replayed.event_id) or f"projection:{replayed.event_id}",
    )

    assert applied == [event.event_id]
    assert result.summary["applied"] == 1
    assert result.summary["duplicate_skipped"] == 1
    assert [item.audit_action.action_type for item in result.results] == [
        "foundation.dlq.replay.applied",
        "foundation.dlq.replay.duplicate_skipped",
    ]
    assert all(item.audit_action.trace_id == event.trace_id for item in result.results)
    assert all(item.audit_action.payload_checksum for item in result.results)

    statuses = [entry.status for entry in queue.entries()]
    assert statuses == [DeadLetterStatus.REPLAYED, DeadLetterStatus.DUPLICATE_SKIPPED]
