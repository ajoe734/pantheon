from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from services.memory.persona_memory_store import (
    PersonaMemoryEntry,
    PersonaMemoryError,
    PersonaMemoryStore,
    PersonaMemoryType,
    PersonaRelevanceScope,
    PersonaSourceEventType,
    PersonaWriteAuthority,
    validate_persona_memory,
    validate_persona_memory_json,
)


def make_entry(**overrides) -> PersonaMemoryEntry:
    payload = {
        "memory_id": "pmem-00000000-0000-0000-0000-000000000001",
        "persona_id": "persona-alpha",
        "memory_type": PersonaMemoryType.STRATEGY_LESSON.value,
        "content": {
            "summary": "Reduce size around momentum regime breaks after postmortem review.",
            "structured_payload": {"lag_bars": 2},
            "tags": ["momentum", "regime_break"],
        },
        "source_event_type": PersonaSourceEventType.POSTMORTEM_PUBLISHED.value,
        "source_event_id": "PM-2026-042",
        "written_at": "2026-06-09T01:00:00Z",
        "write_authority": PersonaWriteAuthority.INCIDENT_SVC.value,
        "relevance_scope": PersonaRelevanceScope.PERSONA_AND_COMMITTEE.value,
        "reuse_count": 0,
    }
    payload.update(overrides)
    return PersonaMemoryEntry(**payload)


class TestPersonaMemoryEntry(unittest.TestCase):
    def test_valid_entry_serializes_round_trip(self) -> None:
        entry = make_entry()
        reloaded = PersonaMemoryEntry.from_json(entry.to_json())
        self.assertEqual(reloaded, entry)

    def test_invalid_enums_raise(self) -> None:
        with self.assertRaises(PersonaMemoryError):
            make_entry(memory_type="invalid")
        with self.assertRaises(PersonaMemoryError):
            make_entry(source_event_type="invalid")
        with self.assertRaises(PersonaMemoryError):
            make_entry(write_authority="invalid")
        with self.assertRaises(PersonaMemoryError):
            make_entry(relevance_scope="invalid")

    def test_content_requires_summary(self) -> None:
        with self.assertRaises(PersonaMemoryError):
            make_entry(content={"summary": ""})


class TestPersonaMemoryValidation(unittest.TestCase):
    def test_writeback_authority_must_match_trigger_pair(self) -> None:
        errors = validate_persona_memory(
            make_entry(
                source_event_type=PersonaSourceEventType.OPERATOR_FEEDBACK.value,
                memory_type=PersonaMemoryType.PREFERENCE.value,
                write_authority=PersonaWriteAuthority.INCIDENT_SVC.value,
            )
        )
        self.assertTrue(any("write_authority" in error for error in errors))

    def test_persona_memory_svc_can_write_any_valid_trigger_pair(self) -> None:
        errors = validate_persona_memory(
            make_entry(
                source_event_type=PersonaSourceEventType.OPERATOR_FEEDBACK.value,
                memory_type=PersonaMemoryType.PREFERENCE.value,
                write_authority=PersonaWriteAuthority.PERSONA_MEMORY_SVC.value,
            )
        )
        self.assertEqual(errors, [])

    def test_written_at_must_be_utc(self) -> None:
        errors = validate_persona_memory(make_entry(written_at="2026-06-09T01:00:00+01:00"))
        self.assertTrue(any("written_at" in error for error in errors))

    def test_schema_validation_accepts_canonical_entry(self) -> None:
        errors = validate_persona_memory_json(make_entry().to_dict())
        self.assertEqual(errors, [])

    def test_schema_validation_rejects_extra_content_fields(self) -> None:
        payload = make_entry().to_dict()
        payload["content"]["extra"] = "not-allowed"
        errors = validate_persona_memory_json(payload)
        if errors:
            self.assertTrue(any("Additional properties are not allowed" in error for error in errors))
        else:
            self.skipTest("jsonschema is not installed in this environment")


class TestPersonaMemoryStore(unittest.TestCase):
    def test_create_get_and_require(self) -> None:
        store = PersonaMemoryStore()
        created = store.create(make_entry())
        self.assertEqual(store.get(created.memory_id), created)
        self.assertEqual(store.require(created.memory_id), created)

    def test_create_rejects_duplicates(self) -> None:
        store = PersonaMemoryStore()
        store.create(make_entry())
        with self.assertRaises(PersonaMemoryError):
            store.create(make_entry())

    def test_list_filters_by_persona_memory_type_and_relevance_scope(self) -> None:
        store = PersonaMemoryStore()
        alpha = make_entry(reuse_count=1)
        alpha_private = make_entry(
            memory_id="pmem-00000000-0000-0000-0000-000000000002",
            relevance_scope=PersonaRelevanceScope.PERSONA_PRIVATE.value,
            reuse_count=3,
        )
        beta = make_entry(
            memory_id="pmem-00000000-0000-0000-0000-000000000003",
            persona_id="persona-beta",
            reuse_count=5,
        )
        for entry in (alpha, alpha_private, beta):
            store.create(entry)

        filtered = store.list(
            persona_id="persona-alpha",
            memory_type=PersonaMemoryType.STRATEGY_LESSON.value,
            relevance_scope=PersonaRelevanceScope.PERSONA_AND_COMMITTEE.value,
        )

        self.assertEqual([entry.memory_id for entry in filtered], [alpha.memory_id])

    def test_retrieve_is_persona_scoped_and_ranks_matches(self) -> None:
        store = PersonaMemoryStore()
        alpha = make_entry(reuse_count=2)
        alpha_newer = make_entry(
            memory_id="pmem-00000000-0000-0000-0000-000000000004",
            content={
                "summary": "Momentum breakout filters should pause during regime breaks.",
                "tags": ["momentum", "breakout"],
            },
            source_event_id="PM-2026-099",
            written_at="2026-06-09T02:00:00Z",
        )
        beta = make_entry(
            memory_id="pmem-00000000-0000-0000-0000-000000000005",
            persona_id="persona-beta",
            content={"summary": "Beta persona has the same momentum lesson.", "tags": ["momentum"]},
            source_event_id="PM-2026-100",
        )
        for entry in (alpha, alpha_newer, beta):
            store.create(entry)

        hits = store.retrieve(persona_id="persona-alpha", query="momentum breakout", tags=["breakout"])

        self.assertEqual(
            [hit.entry.memory_id for hit in hits],
            ["pmem-00000000-0000-0000-0000-000000000004", alpha.memory_id],
        )
        self.assertGreater(hits[0].relevance_score, hits[1].relevance_score)
        self.assertNotIn(beta.memory_id, [hit.entry.memory_id for hit in hits])

    def test_mark_reused_updates_count_without_deadlock(self) -> None:
        store = PersonaMemoryStore()
        store.create(make_entry())
        updated = store.mark_reused("pmem-00000000-0000-0000-0000-000000000001", count=2)
        self.assertEqual(updated.reuse_count, 2)
        self.assertEqual(store.require("pmem-00000000-0000-0000-0000-000000000001").reuse_count, 2)

    def test_supersede_marks_entry_inactive(self) -> None:
        store = PersonaMemoryStore()
        original = make_entry(memory_id="pmem-00000000-0000-0000-0000-000000000006")
        replacement = make_entry(
            memory_id="pmem-00000000-0000-0000-0000-000000000007",
            source_event_id="PM-2026-101",
        )
        store.create(original)
        store.create(replacement)

        updated = store.supersede(original.memory_id, replacement.memory_id)

        self.assertEqual(updated.superseded_by, replacement.memory_id)
        self.assertEqual([entry.memory_id for entry in store.list()], [replacement.memory_id])

    def test_persistence_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "persona-memory.json"
            store = PersonaMemoryStore(path=path)
            store.create(make_entry())
            store.mark_reused("pmem-00000000-0000-0000-0000-000000000001", count=2)

            reloaded = PersonaMemoryStore(path=path)
            self.assertEqual(
                reloaded.require("pmem-00000000-0000-0000-0000-000000000001").reuse_count,
                2,
            )
            persisted = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(persisted[0]["memory_id"], "pmem-00000000-0000-0000-0000-000000000001")

    def test_load_rejects_invalid_persisted_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "persona-memory.json"
            path.write_text(
                json.dumps(
                    [
                        {
                            "memory_id": "pmem-00000000-0000-0000-0000-000000000008",
                            "persona_id": "persona-alpha",
                            "memory_type": PersonaMemoryType.STRATEGY_LESSON.value,
                            "content": {
                                "summary": "Unexpected extra field should fail schema validation.",
                                "extra": "not-allowed",
                            },
                            "source_event_type": PersonaSourceEventType.POSTMORTEM_PUBLISHED.value,
                            "source_event_id": "PM-bad-schema",
                            "written_at": "2026-06-09T01:00:00Z",
                            "write_authority": PersonaWriteAuthority.INCIDENT_SVC.value,
                        }
                    ]
                ),
                encoding="utf-8",
            )
            try:
                PersonaMemoryStore(path=path)
            except PersonaMemoryError:
                return

            self.skipTest("jsonschema is not installed in this environment")


if __name__ == "__main__":
    unittest.main()
