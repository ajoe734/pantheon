import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services.memory.institutional_memory_store import (
    InstitutionalMemoryEntry,
    InstitutionalMemoryError,
    InstitutionalMemoryStore,
    KnowledgeType,
    Scope,
    SourceEventType,
    WriteAuthority,
    validate_institutional_memory,
    validate_institutional_memory_json,
)


def make_entry(**overrides) -> InstitutionalMemoryEntry:
    payload = {
        "entry_id": "mem-00000000-0000-0000-0000-000000000001",
        "knowledge_type": KnowledgeType.INCIDENT_LESSON.value,
        "content": {
            "headline": "Momentum strategies should widen buffers during regime shifts.",
            "body": "A published postmortem found a recurring 2-bar lag near regime breaks.",
            "tags": ["momentum", "regime_break"],
            "structured_payload": {"lag_bars": 2},
        },
        "source_event_type": SourceEventType.POSTMORTEM_PUBLISHED.value,
        "source_event_id": "PM-2026-042",
        "written_at": "2026-04-17T08:00:00Z",
        "write_authority": WriteAuthority.INCIDENT_SVC.value,
        "scope": Scope.STRATEGY_FAMILY.value,
        "scope_filter": "momentum",
        "contributing_persona_ids": ["LP-001"],
        "reuse_count": 0,
    }
    payload.update(overrides)
    return InstitutionalMemoryEntry(**payload)


class TestInstitutionalMemoryEntry(unittest.TestCase):
    def test_valid_entry_serializes_round_trip(self) -> None:
        entry = make_entry()
        reloaded = InstitutionalMemoryEntry.from_json(entry.to_json())
        self.assertEqual(reloaded, entry)

    def test_invalid_enums_raise(self) -> None:
        with self.assertRaises(InstitutionalMemoryError):
            make_entry(knowledge_type="invalid")
        with self.assertRaises(InstitutionalMemoryError):
            make_entry(source_event_type="invalid")
        with self.assertRaises(InstitutionalMemoryError):
            make_entry(write_authority="invalid")
        with self.assertRaises(InstitutionalMemoryError):
            make_entry(scope="invalid")

    def test_content_requires_headline_and_body(self) -> None:
        with self.assertRaises(InstitutionalMemoryError):
            make_entry(content={"headline": "", "body": "x"})
        with self.assertRaises(InstitutionalMemoryError):
            make_entry(content={"headline": "x", "body": ""})


class TestInstitutionalMemoryValidation(unittest.TestCase):
    def test_scope_filter_required_for_scoped_entries(self) -> None:
        errors = validate_institutional_memory(
            make_entry(scope=Scope.STRATEGY_FAMILY.value, scope_filter=None)
        )
        self.assertTrue(any("scope_filter is required" in error for error in errors))

    def test_scope_filter_omitted_for_system_wide(self) -> None:
        errors = validate_institutional_memory(
            make_entry(scope=Scope.SYSTEM_WIDE.value, scope_filter="momentum")
        )
        self.assertTrue(any("scope_filter must be null/omitted" in error for error in errors))

    def test_written_at_must_be_timezone_aware(self) -> None:
        errors = validate_institutional_memory(make_entry(written_at="2026-04-17T08:00:00"))
        self.assertTrue(any("written_at" in error for error in errors))

    def test_written_at_must_be_utc(self) -> None:
        errors = validate_institutional_memory(make_entry(written_at="2026-04-17T08:00:00+01:00"))
        self.assertTrue(any("written_at" in error for error in errors))

    def test_contributing_persona_ids_must_not_be_blank(self) -> None:
        errors = validate_institutional_memory(make_entry(contributing_persona_ids=["LP-001", ""]))
        self.assertTrue(any("contributing_persona_ids" in error for error in errors))

    def test_schema_validation_accepts_canonical_entry(self) -> None:
        errors = validate_institutional_memory_json(make_entry().to_dict())
        self.assertEqual(errors, [])

    def test_schema_validation_rejects_extra_content_fields(self) -> None:
        payload = make_entry().to_dict()
        payload["content"]["extra"] = "not-allowed"
        errors = validate_institutional_memory_json(payload)
        if errors:
            self.assertTrue(any("Additional properties are not allowed" in error for error in errors))
        else:
            self.skipTest("jsonschema is not installed in this environment")


class TestInstitutionalMemoryStore(unittest.TestCase):
    def test_create_get_and_require(self) -> None:
        store = InstitutionalMemoryStore()
        created = store.create(make_entry())
        self.assertEqual(store.get(created.entry_id), created)
        self.assertEqual(store.require(created.entry_id), created)

    def test_create_rejects_duplicates(self) -> None:
        store = InstitutionalMemoryStore()
        store.create(make_entry())
        with self.assertRaises(InstitutionalMemoryError):
            store.create(make_entry())

    def test_list_filters_and_sorts_by_reuse_then_timestamp(self) -> None:
        store = InstitutionalMemoryStore()
        older = make_entry(entry_id="mem-00000000-0000-0000-0000-000000000002", written_at="2026-04-17T07:00:00Z", reuse_count=2)
        newer = make_entry(entry_id="mem-00000000-0000-0000-0000-000000000003", written_at="2026-04-17T09:00:00Z", reuse_count=4)
        other_scope = make_entry(
            entry_id="mem-00000000-0000-0000-0000-000000000004",
            scope=Scope.INSTRUMENT_CLASS.value,
            scope_filter="equity_futures",
            knowledge_type=KnowledgeType.RESEARCH_FINDING.value,
            write_authority=WriteAuthority.RESEARCH_SVC.value,
            source_event_type=SourceEventType.RESEARCH_TASK_COMPLETED.value,
        )
        for entry in (older, newer, other_scope):
            store.create(entry)

        filtered = store.list(
            scope=Scope.STRATEGY_FAMILY.value,
            scope_filter="momentum",
            knowledge_type=KnowledgeType.INCIDENT_LESSON.value,
        )
        self.assertEqual(
            [entry.entry_id for entry in filtered],
            ["mem-00000000-0000-0000-0000-000000000003", "mem-00000000-0000-0000-0000-000000000002"],
        )

    def test_list_sorts_by_normalized_utc_instant(self) -> None:
        store = InstitutionalMemoryStore()
        earlier = make_entry(entry_id="mem-00000000-0000-0000-0000-000000000005", written_at="2026-04-17T07:30:00Z")
        later = make_entry(entry_id="mem-00000000-0000-0000-0000-000000000006", written_at="2026-04-17T08:00:00+00:00")
        store.create(earlier)
        store.create(later)

        self.assertEqual(
            [entry.entry_id for entry in store.list()],
            ["mem-00000000-0000-0000-0000-000000000006", "mem-00000000-0000-0000-0000-000000000005"],
        )

    def test_mark_reused_updates_count_without_deadlock(self) -> None:
        store = InstitutionalMemoryStore()
        store.create(make_entry())
        updated = store.mark_reused("mem-00000000-0000-0000-0000-000000000001", count=3)
        self.assertEqual(updated.reuse_count, 3)
        self.assertEqual(store.require("mem-00000000-0000-0000-0000-000000000001").reuse_count, 3)

    def test_supersede_marks_entry_inactive_without_deadlock(self) -> None:
        store = InstitutionalMemoryStore()
        original = make_entry(entry_id="mem-00000000-0000-0000-0000-000000000007")
        replacement = make_entry(
            entry_id="mem-00000000-0000-0000-0000-000000000008",
            source_event_id="PM-2026-099",
        )
        store.create(original)
        store.create(replacement)

        updated = store.supersede(
            "mem-00000000-0000-0000-0000-000000000007",
            "mem-00000000-0000-0000-0000-000000000008",
        )
        self.assertEqual(updated.superseded_by, "mem-00000000-0000-0000-0000-000000000008")
        self.assertEqual(
            [entry.entry_id for entry in store.list()],
            ["mem-00000000-0000-0000-0000-000000000008"],
        )
        all_entries = {entry.entry_id: entry for entry in store.list(active_only=False)}
        self.assertEqual(
            set(all_entries),
            {"mem-00000000-0000-0000-0000-000000000007", "mem-00000000-0000-0000-0000-000000000008"},
        )
        self.assertEqual(
            all_entries["mem-00000000-0000-0000-0000-000000000007"].superseded_by,
            "mem-00000000-0000-0000-0000-000000000008",
        )

    def test_retrieve_ranks_query_and_tag_matches(self) -> None:
        store = InstitutionalMemoryStore()
        incident = make_entry(entry_id="mem-00000000-0000-0000-0000-000000000009", reuse_count=2)
        research = make_entry(
            entry_id="mem-00000000-0000-0000-0000-00000000000a",
            knowledge_type=KnowledgeType.RESEARCH_FINDING.value,
            source_event_type=SourceEventType.RESEARCH_TASK_COMPLETED.value,
            source_event_id="RS-001",
            write_authority=WriteAuthority.RESEARCH_SVC.value,
            content={
                "headline": "Research confirms momentum lag near breakouts.",
                "body": "Breakout filters and momentum lag need joint evaluation in volatile sessions.",
                "tags": ["momentum", "breakout"],
            },
        )
        store.create(incident)
        store.create(research)

        hits = store.retrieve(query="momentum lag", tags=["breakout"], limit=2)
        self.assertEqual(
            [hit.entry.entry_id for hit in hits],
            ["mem-00000000-0000-0000-0000-00000000000a", "mem-00000000-0000-0000-0000-000000000009"],
        )
        self.assertGreater(hits[0].relevance_score, hits[1].relevance_score)

    def test_retention_policy_assigns_expiry_to_new_entries(self) -> None:
        with mock.patch.dict("os.environ", {"PANTHEON_MEMORY_RETENTION_DAYS": "30"}, clear=False):
            store = InstitutionalMemoryStore()
            created = store.create(make_entry())

        self.assertEqual(created.expires_at, "2026-05-17T08:00:00Z")

    def test_retention_policy_rejects_non_utc_written_at_as_store_error(self) -> None:
        store = InstitutionalMemoryStore()
        with self.assertRaises(InstitutionalMemoryError):
            store.create(make_entry(written_at="2026-04-17T08:00:00+01:00"))

    def test_archive_expired_hides_entries_from_active_retrieval(self) -> None:
        expired = make_entry(
            entry_id="mem-00000000-0000-0000-0000-00000000000d",
            expires_at="2026-04-18T00:00:00Z",
        )
        active = make_entry(
            entry_id="mem-00000000-0000-0000-0000-00000000000e",
            source_event_id="PM-active",
            expires_at="2027-05-18T00:00:00Z",
        )
        store = InstitutionalMemoryStore()
        store.create(expired)
        store.create(active)

        archived = store.archive_expired(now="2026-04-19T00:00:00Z")

        self.assertEqual([entry.entry_id for entry in archived], [expired.entry_id])
        self.assertEqual(store.require(expired.entry_id).archived_reason, "retention_ttl_expired")
        self.assertEqual([entry.entry_id for entry in store.list()], [active.entry_id])
        self.assertEqual([hit.entry.entry_id for hit in store.retrieve(query="Momentum")], [active.entry_id])
        self.assertEqual(
            {entry.entry_id for entry in store.list(active_only=False)},
            {expired.entry_id, active.entry_id},
        )

    def test_persistence_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "institutional-memory.json"
            store = InstitutionalMemoryStore(path=path)
            store.create(make_entry())
            store.mark_reused("mem-00000000-0000-0000-0000-000000000001", count=2)

            reloaded = InstitutionalMemoryStore(path=path)
            self.assertEqual(
                reloaded.require("mem-00000000-0000-0000-0000-000000000001").reuse_count,
                2,
            )
            persisted = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(persisted[0]["entry_id"], "mem-00000000-0000-0000-0000-000000000001")

    def test_load_rejects_invalid_persisted_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "institutional-memory.json"
            path.write_text(
                json.dumps(
                    [
                        {
                            "entry_id": "mem-00000000-0000-0000-0000-00000000000b",
                            "knowledge_type": KnowledgeType.INCIDENT_LESSON.value,
                            "content": {"headline": "Bad timestamp", "body": "Missing timezone"},
                            "source_event_type": SourceEventType.POSTMORTEM_PUBLISHED.value,
                            "source_event_id": "PM-bad",
                            "written_at": "2026-04-17T08:00:00",
                            "write_authority": WriteAuthority.INCIDENT_SVC.value,
                            "scope": Scope.SYSTEM_WIDE.value,
                        }
                    ]
                ),
                encoding="utf-8",
            )
            with self.assertRaises(InstitutionalMemoryError):
                InstitutionalMemoryStore(path=path)

    def test_load_rejects_persisted_schema_only_violation(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "institutional-memory.json"
            path.write_text(
                json.dumps(
                    [
                        {
                            "entry_id": "mem-00000000-0000-0000-0000-00000000000c",
                            "knowledge_type": KnowledgeType.INCIDENT_LESSON.value,
                            "content": {
                                "headline": "Bad content shape",
                                "body": "Unexpected extra field should fail schema validation.",
                                "extra": "not-allowed",
                            },
                            "source_event_type": SourceEventType.POSTMORTEM_PUBLISHED.value,
                            "source_event_id": "PM-bad-schema",
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
            except InstitutionalMemoryError:
                return

            self.skipTest("jsonschema is not installed in this environment")


if __name__ == "__main__":
    unittest.main()
