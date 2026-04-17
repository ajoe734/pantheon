import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

try:
    import jsonschema

    HAS_JSONSCHEMA = True
except ImportError:
    HAS_JSONSCHEMA = False

from services.memory.institutional_memory_store import (
    InstitutionalMemoryEntry,
    InstitutionalMemoryError,
    InstitutionalMemoryStore,
    KnowledgeType,
    Scope,
    SourceEventType,
    WriteAuthority,
)

SCHEMA_PATH = Path(__file__).parent / "institutional_memory_entry.schema.json"
results: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    results.append((label, ok, detail))
    status = "PASS" if ok else "FAIL"
    suffix = f" — {detail}" if detail else ""
    print(f"  [{status}] {label}{suffix}")


def make_entry(**overrides) -> InstitutionalMemoryEntry:
    payload = {
        "entry_id": "inst-smoke-001",
        "knowledge_type": KnowledgeType.INCIDENT_LESSON.value,
        "content": {
            "headline": "Momentum buffers should widen near regime transitions.",
            "body": "Postmortem evidence shows recurring lag around abrupt regime breaks.",
            "tags": ["momentum", "regime_break"],
        },
        "source_event_type": SourceEventType.POSTMORTEM_PUBLISHED.value,
        "source_event_id": "PM-smoke-001",
        "written_at": "2026-04-17T08:00:00Z",
        "write_authority": WriteAuthority.INCIDENT_SVC.value,
        "scope": Scope.STRATEGY_FAMILY.value,
        "scope_filter": "momentum",
        "contributing_persona_ids": ["LP-001"],
    }
    payload.update(overrides)
    return InstitutionalMemoryEntry(**payload)


def smoke_s1_schema_validation() -> None:
    print("\nS1: Schema validation")
    if not HAS_JSONSCHEMA:
        check("jsonschema available", False, "install jsonschema to enable schema validation")
        return

    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    entry = make_entry().to_dict()

    try:
        jsonschema.validate(instance=entry, schema=schema)
        check("canonical entry passes schema", True)
    except jsonschema.ValidationError as exc:
        check("canonical entry passes schema", False, exc.message)

    invalid = dict(entry)
    invalid["scope"] = "bad_scope"
    try:
        jsonschema.validate(instance=invalid, schema=schema)
        check("invalid scope fails schema", False, "expected failure did not occur")
    except jsonschema.ValidationError:
        check("invalid scope fails schema", True)


def smoke_s2_store_write_query_and_supersede() -> None:
    print("\nS2: Store write, query, supersede")
    store = InstitutionalMemoryStore()
    primary = make_entry()
    secondary = make_entry(
        entry_id="inst-smoke-002",
        knowledge_type=KnowledgeType.RESEARCH_FINDING.value,
        source_event_type=SourceEventType.RESEARCH_TASK_COMPLETED.value,
        source_event_id="RS-smoke-002",
        write_authority=WriteAuthority.RESEARCH_SVC.value,
        content={
            "headline": "Breakout filter reduces momentum lag false positives.",
            "body": "Research indicates joint breakout and momentum filters reduce bad entries.",
            "tags": ["momentum", "breakout"],
        },
        reuse_count=2,
    )
    store.create(primary)
    store.create(secondary)

    hits = store.retrieve(query="momentum breakout", tags=["breakout"], limit=2)
    check("query returns both entries", len(hits) == 2, f"got {len(hits)} hits")
    check("best hit is research finding", hits and hits[0].entry.entry_id == "inst-smoke-002")

    reused = store.mark_reused("inst-smoke-001", count=2)
    check("mark_reused increments count", reused.reuse_count == 2, f"reuse_count={reused.reuse_count}")

    superseded = store.supersede("inst-smoke-001", "inst-smoke-002")
    check("supersede records replacement id", superseded.superseded_by == "inst-smoke-002")
    active_ids = [entry.entry_id for entry in store.list()]
    check("superseded entry hidden from active list", active_ids == ["inst-smoke-002"], f"active={active_ids}")


def smoke_s3_persistence_round_trip() -> None:
    print("\nS3: Persistence round trip")
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "institutional-memory.json"
        store = InstitutionalMemoryStore(path=path)
        store.create(make_entry())
        store.mark_reused("inst-smoke-001", count=4)

        reloaded = InstitutionalMemoryStore(path=path)
        check("persisted file exists", path.exists())
        check(
            "reloaded entry keeps reuse_count",
            reloaded.require("inst-smoke-001").reuse_count == 4,
            f"reuse_count={reloaded.require('inst-smoke-001').reuse_count}",
        )


def smoke_s4_invalid_persisted_payload_is_rejected() -> None:
    print("\nS4: Invalid persisted payload rejected")
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "institutional-memory.json"
        path.write_text(
            json.dumps(
                [
                    {
                        "entry_id": "inst-invalid-001",
                        "knowledge_type": KnowledgeType.INCIDENT_LESSON.value,
                        "content": {"headline": "Bad timestamp", "body": "No timezone suffix"},
                        "source_event_type": SourceEventType.POSTMORTEM_PUBLISHED.value,
                        "source_event_id": "PM-invalid-001",
                        "written_at": "2026-04-17T08:00:00",
                        "write_authority": WriteAuthority.INCIDENT_SVC.value,
                        "scope": Scope.SYSTEM_WIDE.value,
                    }
                ]
            ),
            encoding="utf-8",
        )
        try:
            InstitutionalMemoryStore(path=path)
            check("invalid persisted entry raises", False, "expected load failure did not occur")
        except InstitutionalMemoryError:
            check("invalid persisted entry raises", True)


def smoke_s5_non_utc_timestamp_is_rejected() -> None:
    print("\nS5: Non-UTC timestamp rejected")
    store = InstitutionalMemoryStore()
    try:
        store.create(make_entry(entry_id="inst-offset-001", written_at="2026-04-17T08:00:00+01:00"))
        check("non-UTC timestamp rejected", False, "expected create failure did not occur")
    except InstitutionalMemoryError:
        check("non-UTC timestamp rejected", True)


def smoke_s6_schema_only_persisted_violation_is_rejected() -> None:
    print("\nS6: Schema-only persisted violation rejected")
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "institutional-memory.json"
        path.write_text(
            json.dumps(
                [
                    {
                        "entry_id": "inst-invalid-schema-001",
                        "knowledge_type": KnowledgeType.INCIDENT_LESSON.value,
                        "content": {
                            "headline": "Unexpected extra key",
                            "body": "Persisted payload should be rejected during load.",
                            "extra": "not-allowed",
                        },
                        "source_event_type": SourceEventType.POSTMORTEM_PUBLISHED.value,
                        "source_event_id": "PM-invalid-schema-001",
                        "written_at": "2026-04-17T08:00:00Z",
                        "write_authority": WriteAuthority.INCIDENT_SVC.value,
                        "scope": Scope.SYSTEM_WIDE.value,
                    }
                ]
            ),
            encoding="utf-8",
        )
        try:
            InstitutionalMemoryStore(path=path)
            if HAS_JSONSCHEMA:
                check("schema-only persisted violation rejected", False, "expected load failure did not occur")
            else:
                check("schema-only persisted violation rejected", False, "install jsonschema to enable schema validation")
        except InstitutionalMemoryError:
            check("schema-only persisted violation rejected", True)


def main() -> int:
    print("Institutional memory smoke test")
    smoke_s1_schema_validation()
    smoke_s2_store_write_query_and_supersede()
    smoke_s3_persistence_round_trip()
    smoke_s4_invalid_persisted_payload_is_rejected()
    smoke_s5_non_utc_timestamp_is_rejected()
    smoke_s6_schema_only_persisted_violation_is_rejected()

    passed = sum(1 for _, ok, _ in results if ok)
    failed = len(results) - passed
    print(f"\nSummary: {passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
