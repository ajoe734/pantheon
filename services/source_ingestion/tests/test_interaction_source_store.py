"""Tests for InteractionSourceRecord contract and JSONL dev store."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from services.source_ingestion.interaction_source_store import (
    InteractionRedactionStatus,
    InteractionSourceRecord,
    InteractionSourceRecordError,
    InteractionSourceRecordStore,
    InteractionSourceSurface,
    InteractionVisibility,
)

try:
    import jsonschema
except ImportError:  # pragma: no cover - optional in local lean test images.
    jsonschema = None


SCHEMA_PATH = (
    Path(__file__).resolve().parents[3]
    / "docs"
    / "contracts"
    / "interaction_source_record.schema.json"
)


def _schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _record(**overrides: Any) -> InteractionSourceRecord:
    raw_ref = overrides.pop(
        "raw_ref",
        "evidence://trainer/session-ids-001/commit-0001",
    )
    payload: dict[str, Any] = {
        "interaction_id": "interaction-ids-001",
        "source_surface": InteractionSourceSurface.TRAINER.value,
        "actor_type": "user",
        "persona_refs": ["persona-alpha"],
        "session_id": "session-ids-001",
        "raw_ref": raw_ref,
        "summary": "User committed a governed strategy hypothesis for later classification.",
        "evidence_refs": [
            {
                "ref": raw_ref,
                "kind": "raw_interaction",
                "content_hash": "sha256:abc123",
            }
        ],
        "visibility": InteractionVisibility.PERSONA.value,
        "redaction_status": InteractionRedactionStatus.PENDING.value,
        "created_at": "2026-06-12T00:00:00Z",
        "updated_at": "2026-06-12T00:00:00Z",
        "metadata": {
            "surface_name": "trainer_commit",
            "research_only": True,
            "execution_route": "none",
        },
    }
    payload.update(overrides)
    return InteractionSourceRecord(**payload)


def test_schema_declares_required_interaction_source_contract() -> None:
    schema = _schema()

    if jsonschema is not None:
        jsonschema.Draft7Validator.check_schema(schema)

    assert schema["title"] == "Pantheon InteractionSourceRecord"
    assert schema["$id"] == "https://pantheon/interaction-source-record/v1"
    required = set(schema["required"])
    for field in (
        "interaction_id",
        "source_surface",
        "actor_type",
        "persona_refs",
        "session_id",
        "raw_ref",
        "summary",
        "evidence_refs",
        "visibility",
        "redaction_status",
    ):
        assert field in required
    assert set(schema["properties"]["visibility"]["enum"]) == {"private", "persona", "desk", "shared"}
    assert set(schema["properties"]["redaction_status"]["enum"]) == {"pending", "passed", "failed"}
    assert "raw_text" not in schema["properties"]
    assert "transcript" not in schema["properties"]
    assert "content" not in schema["properties"]


def test_schema_validates_sample_and_rejects_missing_safety_fields() -> None:
    if jsonschema is None:
        pytest.skip("jsonschema not installed")

    schema = _schema()
    payload = _record().to_dict()
    jsonschema.Draft7Validator(schema).validate(payload)

    missing_visibility = dict(payload)
    missing_visibility.pop("visibility")
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft7Validator(schema).validate(missing_visibility)

    missing_redaction_status = dict(payload)
    missing_redaction_status.pop("redaction_status")
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft7Validator(schema).validate(missing_redaction_status)


def test_schema_rejects_inline_raw_fields() -> None:
    if jsonschema is None:
        pytest.skip("jsonschema not installed")

    schema = _schema()
    payload = _record().to_dict()
    payload["raw_text"] = "full raw prompt must stay in evidence storage"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft7Validator(schema).validate(payload)

    nested = _record().to_dict()
    nested["metadata"]["raw_prompt"] = "full raw prompt must stay in evidence storage"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft7Validator(schema).validate(nested)


@pytest.mark.parametrize("surface", [surface.value for surface in InteractionSourceSurface])
def test_record_can_be_created_from_each_supported_surface(tmp_path: Path, surface: str) -> None:
    raw_ref = f"evidence://{surface}/session-ids-001/commit-0001"
    store = InteractionSourceRecordStore(path=tmp_path / "interaction_records.jsonl")
    record = _record(
        interaction_id=f"interaction-{surface}",
        source_surface=surface,
        raw_ref=raw_ref,
        evidence_refs=[{"ref": raw_ref, "kind": "raw_interaction"}],
    )

    store.add(record)

    saved = store.get(record.interaction_id)
    assert saved is not None
    assert saved.interaction_id == record.interaction_id
    assert saved.source_surface.value == surface
    assert saved.raw_ref == raw_ref
    assert saved.visibility == InteractionVisibility.PERSONA
    assert saved.redaction_status == InteractionRedactionStatus.PENDING


def test_store_upserts_and_filters_by_visibility_and_redaction_status(tmp_path: Path) -> None:
    store = InteractionSourceRecordStore(path=tmp_path / "interaction_records.jsonl")
    original = _record(summary="Original governed summary.")
    updated = _record(
        summary="Updated governed summary.",
        visibility="desk",
        redaction_status="passed",
    )

    store.save(original)
    store.save(updated)

    assert store.count() == 1
    saved = store.get("interaction-ids-001")
    assert saved is not None
    assert saved.summary == "Updated governed summary."
    assert [record.interaction_id for record in store.list_by_visibility("desk")] == ["interaction-ids-001"]
    assert [record.interaction_id for record in store.list_by_redaction_status("passed")] == [
        "interaction-ids-001"
    ]


def test_model_rejects_inline_raw_metadata_and_payload_keys() -> None:
    with pytest.raises(InteractionSourceRecordError, match="raw_prompt"):
        _record(metadata={"raw_prompt": "raw user prompt must not be inlined"})

    payload = _record().to_dict()
    payload["transcript"] = "raw chat transcript must stay in evidence storage"
    with pytest.raises(InteractionSourceRecordError, match="transcript"):
        InteractionSourceRecord.from_dict(payload)

    nested_payload = _record().to_dict()
    nested_payload["metadata"] = {"safe": {"messages": ["raw chat messages"]}}
    with pytest.raises(InteractionSourceRecordError, match="messages"):
        InteractionSourceRecord.from_dict(nested_payload)


def test_model_requires_raw_ref_to_point_at_evidence_refs() -> None:
    with pytest.raises(InteractionSourceRecordError, match="raw_ref must be present"):
        _record(evidence_refs=[{"ref": "evidence://other/ref", "kind": "raw_interaction"}])


def test_model_rejects_invalid_visibility_redaction_status_and_evidence_kind() -> None:
    with pytest.raises(InteractionSourceRecordError, match="visibility must be one of"):
        _record(visibility="public")

    with pytest.raises(InteractionSourceRecordError, match="redaction_status must be one of"):
        _record(redaction_status="unknown")

    with pytest.raises(InteractionSourceRecordError, match="evidence_refs\\[0\\].kind"):
        _record(evidence_refs=[{"ref": "evidence://trainer/session-ids-001/commit-0001", "kind": "raw_text"}])
