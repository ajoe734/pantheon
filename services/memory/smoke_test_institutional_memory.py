import json
import os
import sys
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

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
PRIMARY_ID = "mem-11111111-1111-1111-1111-111111111111"
SECONDARY_ID = "mem-22222222-2222-2222-2222-222222222222"
INVALID_ID = "mem-33333333-3333-3333-3333-333333333333"
INVALID_SCHEMA_ID = "mem-44444444-4444-4444-4444-444444444444"
OFFSET_ID = "mem-55555555-5555-5555-5555-555555555555"


def check(label: str, ok: bool, detail: str = "") -> None:
    results.append((label, ok, detail))
    status = "PASS" if ok else "FAIL"
    suffix = f" — {detail}" if detail else ""
    print(f"  [{status}] {label}{suffix}")


def make_entry(**overrides) -> InstitutionalMemoryEntry:
    payload = {
        "entry_id": PRIMARY_ID,
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
        entry_id=SECONDARY_ID,
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
    check("best hit is research finding", hits and hits[0].entry.entry_id == SECONDARY_ID)

    reused = store.mark_reused(PRIMARY_ID, count=2)
    check("mark_reused increments count", reused.reuse_count == 2, f"reuse_count={reused.reuse_count}")

    superseded = store.supersede(PRIMARY_ID, SECONDARY_ID)
    check("supersede records replacement id", superseded.superseded_by == SECONDARY_ID)
    active_ids = [entry.entry_id for entry in store.list()]
    check("superseded entry hidden from active list", active_ids == [SECONDARY_ID], f"active={active_ids}")


def smoke_s3_persistence_round_trip() -> None:
    print("\nS3: Persistence round trip")
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "institutional-memory.json"
        store = InstitutionalMemoryStore(path=path)
        store.create(make_entry())
        store.mark_reused(PRIMARY_ID, count=4)

        reloaded = InstitutionalMemoryStore(path=path)
        check("persisted file exists", path.exists())
        check(
            "reloaded entry keeps reuse_count",
            reloaded.require(PRIMARY_ID).reuse_count == 4,
            f"reuse_count={reloaded.require(PRIMARY_ID).reuse_count}",
        )


def smoke_s4_invalid_persisted_payload_is_rejected() -> None:
    print("\nS4: Invalid persisted payload rejected")
    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "institutional-memory.json"
        path.write_text(
            json.dumps(
                [
                    {
                        "entry_id": INVALID_ID,
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
        store.create(make_entry(entry_id=OFFSET_ID, written_at="2026-04-17T08:00:00+01:00"))
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
                        "entry_id": INVALID_SCHEMA_ID,
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


def smoke_s7_governed_retrieval_replay() -> None:
    print("\nS7: Governed retrieval replay")
    from services.memory import main as memory_main

    tracked_env = {
        "PANTHEON_MEMORY_STORE": os.environ.get("PANTHEON_MEMORY_STORE"),
        "PANTHEON_MEMORY_AUTHZ_MODE": os.environ.get("PANTHEON_MEMORY_AUTHZ_MODE"),
        "PANTHEON_GOVERNANCE_API_URL": os.environ.get("PANTHEON_GOVERNANCE_API_URL"),
        "PANTHEON_GOVERNANCE_AUTHZ_URL": os.environ.get("PANTHEON_GOVERNANCE_AUTHZ_URL"),
        "PANTHEON_GOVERNANCE_SERVICE_URL": os.environ.get("PANTHEON_GOVERNANCE_SERVICE_URL"),
    }
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            store_path = Path(tmpdir) / "institutional-memory.json"
            os.environ["PANTHEON_MEMORY_STORE"] = str(store_path)
            os.environ["PANTHEON_MEMORY_AUTHZ_MODE"] = "local"
            os.environ.pop("PANTHEON_GOVERNANCE_API_URL", None)
            os.environ.pop("PANTHEON_GOVERNANCE_AUTHZ_URL", None)
            os.environ.pop("PANTHEON_GOVERNANCE_SERVICE_URL", None)
            client = TestClient(memory_main.app)

            create_response = client.post("/api/memory/entries", json=make_entry(reuse_count=0).to_dict())
            check("facade writeback accepts canonical entry", create_response.status_code == 201, create_response.text)

            reloaded = InstitutionalMemoryStore(path=store_path)
            check("writeback survives store reload", reloaded.require(PRIMARY_ID).entry_id == PRIMARY_ID)

            retrieve_response = client.get(
                "/api/memory/retrieve",
                params={
                    "actor_id": "operator-1",
                    "actor_roles": "operator",
                    "session_id": "replay-smoke-001",
                    "scope": "institutional",
                    "query": "momentum regime",
                    "tags": "regime_break",
                },
            )
            check("governed retrieval succeeds", retrieve_response.status_code == 200, retrieve_response.text)
            payload = retrieve_response.json() if retrieve_response.status_code == 200 else {}
            hits = payload.get("hits") or []
            check("retrieval returns replayed entry", bool(hits) and hits[0]["entry"]["entry_id"] == PRIMARY_ID)
            replayed = InstitutionalMemoryStore(path=store_path)
            check("retrieval increments reuse_count durably", replayed.require(PRIMARY_ID).reuse_count == 1)
    finally:
        for key, value in tracked_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def main() -> int:
    print("Institutional memory smoke test")
    smoke_s1_schema_validation()
    smoke_s2_store_write_query_and_supersede()
    smoke_s3_persistence_round_trip()
    smoke_s4_invalid_persisted_payload_is_rejected()
    smoke_s5_non_utc_timestamp_is_rejected()
    smoke_s6_schema_only_persisted_violation_is_rejected()
    smoke_s7_governed_retrieval_replay()

    passed = sum(1 for _, ok, _ in results if ok)
    failed = len(results) - passed
    print(f"\nSummary: {passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
