"""
Unit tests for the services/feedback/ preference events store and trajectory schema.

Acceptance criteria:
  - preference events can be written and read back
  - smoke test passes
  - at least 3 unit tests
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

import pytest

from services.feedback.models import (
    ActorRole,
    ArtifactType,
    Channel,
    EditItem,
    EditOperation,
    ExecutionMode,
    ExecutionTelemetryEvent,
    FeedbackEventType,
    GovernedLinkage,
    PromotionState,
    TelemetryEventType,
    TraderFeedbackEvent,
)
from services.feedback.store import DuplicateEventError, FeedbackStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _linkage(
    strategy_id: str = "strat-001",
    registry_id: str = "reg-abc",
    promotion_state: PromotionState = PromotionState.CANDIDATE,
) -> GovernedLinkage:
    return GovernedLinkage(
        strategy_id=strategy_id,
        registry_id=registry_id,
        promotion_state=promotion_state,
    )


def _approve_event(event_id: str = "evt-approve-001") -> TraderFeedbackEvent:
    return TraderFeedbackEvent(
        event_id=event_id,
        event_type=FeedbackEventType.APPROVE,
        created_at="2026-04-17T10:00:00Z",
        actor_id="operator-1",
        actor_role=ActorRole.OPERATOR,
        channel=Channel.WEB,
        target=_linkage(),
    )


def _pnl_event(event_id: str = "evt-pnl-001") -> ExecutionTelemetryEvent:
    return ExecutionTelemetryEvent(
        event_id=event_id,
        event_type=TelemetryEventType.PNL_SNAPSHOT,
        created_at="2026-04-17T11:00:00Z",
        execution_mode=ExecutionMode.PAPER,
        target=_linkage(promotion_state=PromotionState.PAPER),
        metrics={"pnl": 42.5},
    )


# ---------------------------------------------------------------------------
# Smoke test: write and read back a preference event
# ---------------------------------------------------------------------------

def test_smoke_write_and_read_preference_event():
    store = FeedbackStore()
    event = _approve_event()
    store.write(event)
    assert len(store) == 1
    retrieved = store.get("evt-approve-001")
    assert retrieved is not None
    assert retrieved is not event  # deep copy — not the same object
    assert retrieved.event_type == FeedbackEventType.APPROVE


# ---------------------------------------------------------------------------
# Unit test 1: edit event requires non-empty edits list
# ---------------------------------------------------------------------------

def test_edit_event_requires_edits():
    with pytest.raises(ValueError, match="edits must be non-empty"):
        TraderFeedbackEvent(
            event_id="evt-edit-bad",
            event_type=FeedbackEventType.EDIT,
            created_at="2026-04-17T10:00:00Z",
            actor_id="operator-1",
            actor_role=ActorRole.OPERATOR,
            channel=Channel.CONSOLE,
            target=_linkage(),
            edits=[],
        )


def test_edit_event_with_edits_succeeds():
    ev = TraderFeedbackEvent(
        event_id="evt-edit-ok",
        event_type=FeedbackEventType.EDIT,
        created_at="2026-04-17T10:00:00Z",
        actor_id="operator-1",
        actor_role=ActorRole.OPERATOR,
        channel=Channel.API,
        target=_linkage(),
        edits=[EditItem(path="thesis.risk_limit", operation=EditOperation.REPLACE, value=0.02)],
    )
    assert ev.edits[0].operation == EditOperation.REPLACE


# ---------------------------------------------------------------------------
# Unit test 2: rationale event requires rationale text
# ---------------------------------------------------------------------------

def test_rationale_event_requires_rationale_text():
    with pytest.raises(ValueError, match="rationale must be set"):
        TraderFeedbackEvent(
            event_id="evt-rat-bad",
            event_type=FeedbackEventType.RATIONALE,
            created_at="2026-04-17T10:00:00Z",
            actor_id="operator-1",
            actor_role=ActorRole.OPERATOR,
            channel=Channel.WEB,
            target=_linkage(),
        )


# ---------------------------------------------------------------------------
# Unit test 3: duplicate event_id raises DuplicateEventError
# ---------------------------------------------------------------------------

def test_duplicate_event_id_rejected():
    store = FeedbackStore()
    store.write(_approve_event("evt-dup"))
    with pytest.raises(DuplicateEventError):
        store.write(_approve_event("evt-dup"))
    assert len(store) == 1


# ---------------------------------------------------------------------------
# Unit test 4: draft artifacts excluded from execution telemetry
# ---------------------------------------------------------------------------

def test_draft_excluded_from_telemetry():
    draft_linkage = GovernedLinkage(
        strategy_id="strat-draft",
        registry_id="reg-draft",
        promotion_state=PromotionState.DRAFT,
    )
    with pytest.raises(ValueError, match="Draft artifacts must not appear"):
        ExecutionTelemetryEvent(
            event_id="evt-telem-draft",
            event_type=TelemetryEventType.PNL_SNAPSHOT,
            created_at="2026-04-17T11:00:00Z",
            execution_mode=ExecutionMode.PAPER,
            target=draft_linkage,
            metrics={"pnl": 0.0},
        )


# ---------------------------------------------------------------------------
# Unit test 5: query by strategy_id filters correctly
# ---------------------------------------------------------------------------

def test_query_by_strategy_id():
    store = FeedbackStore()
    ev_a = TraderFeedbackEvent(
        event_id="fb-strat-a",
        event_type=FeedbackEventType.APPROVE,
        created_at="2026-04-17T10:00:00Z",
        actor_id="op-1",
        actor_role=ActorRole.OPERATOR,
        channel=Channel.WEB,
        target=GovernedLinkage(strategy_id="alpha", registry_id="r1"),
    )
    ev_b = TraderFeedbackEvent(
        event_id="fb-strat-b",
        event_type=FeedbackEventType.REJECT,
        created_at="2026-04-17T10:01:00Z",
        actor_id="op-1",
        actor_role=ActorRole.OPERATOR,
        channel=Channel.WEB,
        target=GovernedLinkage(strategy_id="beta", registry_id="r2"),
    )
    store.write(ev_a)
    store.write(ev_b)
    results = store.query(strategy_id="alpha")
    assert len(results) == 1
    assert results[0].event_id == "fb-strat-a"


# ---------------------------------------------------------------------------
# Unit test 6: query by event_type
# ---------------------------------------------------------------------------

def test_query_by_event_type():
    store = FeedbackStore()
    store.write(_approve_event("fb-q-1"))
    store.write(
        TraderFeedbackEvent(
            event_id="fb-q-2",
            event_type=FeedbackEventType.REJECT,
            created_at="2026-04-17T10:01:00Z",
            actor_id="op-1",
            actor_role=ActorRole.APPROVER,
            channel=Channel.WEB,
            target=_linkage(),
        )
    )
    results = store.query(event_type="approve")
    assert len(results) == 1
    assert results[0].event_id == "fb-q-1"


# ---------------------------------------------------------------------------
# Unit test 7: query by event_family separates feedback from telemetry
# ---------------------------------------------------------------------------

def test_query_by_event_family():
    store = FeedbackStore()
    store.write(_approve_event("fam-fb"))
    store.write(_pnl_event("fam-telem"))

    fb_results = store.query(event_family="feedback")
    telem_results = store.query(event_family="telemetry")

    assert len(fb_results) == 1
    assert isinstance(fb_results[0], TraderFeedbackEvent)
    assert len(telem_results) == 1
    assert isinstance(telem_results[0], ExecutionTelemetryEvent)


# ---------------------------------------------------------------------------
# Unit test 8: GovernedLinkage requires at least one linkage anchor
# ---------------------------------------------------------------------------

def test_governed_linkage_requires_anchor():
    with pytest.raises(ValueError, match="requires at least one of"):
        GovernedLinkage(strategy_id="strat-x")


def test_governed_linkage_accepts_registry_id():
    link = GovernedLinkage(strategy_id="strat-x", registry_id="reg-y")
    assert link.registry_id == "reg-y"


def test_governed_linkage_accepts_version_and_type():
    link = GovernedLinkage(
        strategy_id="strat-x",
        artifact_version="v1.2.3",
        artifact_type=ArtifactType.STRATEGY_SPEC,
    )
    assert link.artifact_type == ArtifactType.STRATEGY_SPEC


# ---------------------------------------------------------------------------
# Unit test 9: metrics must be non-empty
# ---------------------------------------------------------------------------

def test_telemetry_empty_metrics_rejected():
    with pytest.raises(ValueError, match="metrics must contain at least one entry"):
        ExecutionTelemetryEvent(
            event_id="evt-no-metrics",
            event_type=TelemetryEventType.DRAWDOWN_SNAPSHOT,
            created_at="2026-04-17T11:00:00Z",
            execution_mode=ExecutionMode.LIVE,
            target=_linkage(promotion_state=PromotionState.LIVE),
            metrics={},
        )


# ---------------------------------------------------------------------------
# Regression: append-only — post-write mutation must not alter stored history
# ---------------------------------------------------------------------------

def test_post_write_mutation_does_not_alter_store():
    store = FeedbackStore()
    event = _approve_event("evt-mutate")
    store.write(event)

    # Mutate the original object after write
    event.actor_id = "MUTATED"
    event.target.strategy_id = "MUTATED"

    stored = store.get("evt-mutate")
    assert stored.actor_id == "operator-1", "stored copy must be unaffected by post-write mutation"
    assert stored.target.strategy_id == "strat-001", "stored linkage must be unaffected by post-write mutation"


def test_get_returns_independent_copy():
    """Mutating the object returned by get() must not alter stored history."""
    store = FeedbackStore()
    store.write(_approve_event("evt-copy"))

    first = store.get("evt-copy")
    first.actor_id = "ALTERED"

    second = store.get("evt-copy")
    assert second.actor_id == "operator-1", "second get() must reflect original stored value"


# ---------------------------------------------------------------------------
# Regression: invalid created_at is rejected at ingest, not during query
# ---------------------------------------------------------------------------

def test_invalid_created_at_rejected_on_construction_feedback():
    with pytest.raises(ValueError, match="RFC3339"):
        TraderFeedbackEvent(
            event_id="evt-bad-ts",
            event_type=FeedbackEventType.APPROVE,
            created_at="not-a-timestamp",
            actor_id="op-1",
            actor_role=ActorRole.OPERATOR,
            channel=Channel.WEB,
            target=_linkage(),
        )


def test_invalid_created_at_rejected_on_construction_telemetry():
    with pytest.raises(ValueError, match="RFC3339"):
        ExecutionTelemetryEvent(
            event_id="evt-bad-ts-telem",
            event_type=TelemetryEventType.PNL_SNAPSHOT,
            created_at="2026/04/17",  # wrong format — not ISO-8601 with timezone
            execution_mode=ExecutionMode.PAPER,
            target=_linkage(promotion_state=PromotionState.PAPER),
            metrics={"pnl": 1.0},
        )


def test_valid_rfc3339_with_offset_accepted():
    ev = TraderFeedbackEvent(
        event_id="evt-offset-ts",
        event_type=FeedbackEventType.APPROVE,
        created_at="2026-04-17T10:00:00+09:00",
        actor_id="op-1",
        actor_role=ActorRole.OPERATOR,
        channel=Channel.WEB,
        target=_linkage(),
    )
    assert ev.event_id == "evt-offset-ts"


# ---------------------------------------------------------------------------
# Regression: raw-string enum fields are coerced at construction
# ---------------------------------------------------------------------------

def test_raw_string_event_type_coerced_to_enum():
    """Exact repro from review: raw string enum fields must be coerced, not stored as plain str."""
    ev = TraderFeedbackEvent(
        event_id="raw-str-001",
        event_type="approve",        # raw string
        actor_role="operator",       # raw string
        channel="web",               # raw string
        created_at="2026-04-17T10:00:00Z",
        actor_id="op-raw",
        target=_linkage(),
    )
    assert isinstance(ev.event_type, FeedbackEventType)
    assert isinstance(ev.actor_role, ActorRole)
    assert isinstance(ev.channel, Channel)
    assert ev.event_type == FeedbackEventType.APPROVE


def test_raw_string_feedback_event_survives_query():
    """FeedbackStore.query(event_type=...) must not crash when event was ingested via raw string."""
    store = FeedbackStore()
    ev = TraderFeedbackEvent(
        event_id="raw-query-001",
        event_type="approve",
        actor_role="operator",
        channel="web",
        created_at="2026-04-17T10:00:00Z",
        actor_id="op-raw",
        target=_linkage(),
    )
    store.write(ev)
    results = store.query(event_type="approve")
    assert len(results) == 1
    assert results[0].event_id == "raw-query-001"


def test_raw_string_telemetry_event_type_coerced():
    """TelemetryEventType and ExecutionMode fields are coerced from raw strings."""
    ev = ExecutionTelemetryEvent(
        event_id="raw-telem-001",
        event_type="pnl_snapshot",
        execution_mode="paper",
        created_at="2026-04-17T11:00:00Z",
        target=_linkage(promotion_state=PromotionState.PAPER),
        metrics={"pnl": 1.5},
    )
    assert isinstance(ev.event_type, TelemetryEventType)
    assert isinstance(ev.execution_mode, ExecutionMode)
    assert ev.event_type == TelemetryEventType.PNL_SNAPSHOT


def test_invalid_raw_string_enum_rejected():
    """Invalid raw string must raise ValueError with a helpful message."""
    with pytest.raises(ValueError, match="event_type"):
        TraderFeedbackEvent(
            event_id="raw-bad-001",
            event_type="not_a_valid_type",
            actor_role="operator",
            channel="web",
            created_at="2026-04-17T10:00:00Z",
            actor_id="op-raw",
            target=_linkage(),
        )


def test_raw_string_governed_linkage_enums_coerced():
    """GovernedLinkage artifact_type and promotion_state are coerced from raw strings."""
    link = GovernedLinkage(
        strategy_id="strat-raw",
        registry_id="reg-raw",
        artifact_type="strategy_spec",
        promotion_state="candidate",
    )
    assert isinstance(link.artifact_type, ArtifactType)
    assert isinstance(link.promotion_state, PromotionState)
    assert link.promotion_state == PromotionState.CANDIDATE
