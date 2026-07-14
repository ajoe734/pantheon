from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import pytest

from services.source_ingestion import requirement_state
from services.source_ingestion.requirement_state import (
    CHECKSUM_ALGORITHM,
    SCHEMA_VERSION,
    RequirementSnapshotStore,
    RequirementStateError,
)


DESIRED_SHA = "a" * 64
NEXT_DESIRED_SHA = "b" * 64


def _canonical_json(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _state(*, sequence: int = 1, **overrides: object) -> dict:
    value = {
        "schema_version": SCHEMA_VERSION,
        "sequence": sequence,
        "desired_state_sha256": DESIRED_SHA,
        "bindings": {"req-prices": "connector-market"},
        "binding_count": 1,
        "persona_count": 1,
        "authority": "persona-registry",
        "authoritative": True,
    }
    value.update(overrides)
    return value


def _envelope(state: dict, **overrides: object) -> dict:
    value = {
        "state": state,
        "checksum_algorithm": CHECKSUM_ALGORITHM,
        "checksum": sha256(_canonical_json(state).encode("utf-8")).hexdigest(),
    }
    value.update(overrides)
    return value


def _write_envelopes(path: Path, *envelopes: dict, separator: str = "\n") -> None:
    path.write_text(separator.join(_canonical_json(item) for item in envelopes) + "\n", encoding="utf-8")


def _nonblank_lines(path: Path) -> list[str]:
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_append_writes_sanitized_checksummed_envelope_and_exposes_latest(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "requirements.jsonl"
    store = RequirementSnapshotStore(path)

    snapshot = store.append(
        DESIRED_SHA.upper(),
        {"req-news": "connector-news", "req-prices": "connector-market"},
        3,
        "persona-registry",
    )

    assert snapshot.sequence == 1
    assert snapshot.sequence_no == 1
    assert snapshot.desired_state_sha256 == DESIRED_SHA
    assert dict(snapshot.bindings) == {
        "req-news": "connector-news",
        "req-prices": "connector-market",
    }
    assert snapshot.binding_count == 2
    assert snapshot.persona_count == 3
    assert store.latest == snapshot

    envelope = json.loads(path.read_text(encoding="utf-8"))
    state = envelope["state"]
    assert envelope["checksum_algorithm"] == "sha256"
    assert envelope["checksum"] == sha256(_canonical_json(state).encode("utf-8")).hexdigest()
    assert state == snapshot.to_dict()
    assert set(state) == {
        "schema_version",
        "sequence",
        "desired_state_sha256",
        "bindings",
        "binding_count",
        "persona_count",
        "authority",
        "authoritative",
    }
    serialized = path.read_text(encoding="utf-8")
    assert "persona_payload" not in serialized
    assert "secret" not in serialized


def test_reload_round_trip_validates_log_and_returns_latest(tmp_path: Path) -> None:
    path = tmp_path / "requirements.jsonl"
    first_store = RequirementSnapshotStore(path)
    first_store.append(DESIRED_SHA, {"req-prices": "connector-market"}, 1, "persona-registry")
    expected = first_store.append(
        NEXT_DESIRED_SHA,
        {"req-news": "connector-news"},
        2,
        "persona-registry",
        authoritative=False,
    )

    reloaded = RequirementSnapshotStore(path)

    assert reloaded.latest == expected
    assert reloaded.latest is not None
    assert reloaded.latest.authoritative is False
    assert reloaded.reload() == expected


def test_exact_duplicate_is_idempotent_and_does_not_append(tmp_path: Path) -> None:
    path = tmp_path / "requirements.jsonl"
    store = RequirementSnapshotStore(path)
    first = store.append(
        DESIRED_SHA,
        {"req-prices": "connector-market", "req-news": "connector-news"},
        2,
        "persona-registry",
    )
    original_bytes = path.read_bytes()

    duplicate = store.append(
        DESIRED_SHA,
        {"req-news": "connector-news", "req-prices": "connector-market"},
        2,
        "persona-registry",
    )

    assert duplicate.sequence == first.sequence == 1
    assert duplicate.to_dict() == first.to_dict()
    assert path.read_bytes() == original_bytes
    assert len(_nonblank_lines(path)) == 1


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ({"desired_state_sha256": NEXT_DESIRED_SHA}, NEXT_DESIRED_SHA),
        ({"bindings": {"req-news": "connector-news"}}, DESIRED_SHA),
        ({"persona_count": 2}, DESIRED_SHA),
        ({"authority": "persona-registry-v2"}, DESIRED_SHA),
        ({"authoritative": False}, DESIRED_SHA),
    ],
)
def test_any_snapshot_content_change_appends_next_sequence(
    tmp_path: Path,
    change: dict[str, object],
    expected: str,
) -> None:
    path = tmp_path / "requirements.jsonl"
    store = RequirementSnapshotStore(path)
    store.append(DESIRED_SHA, {"req-prices": "connector-market"}, 1, "persona-registry")
    values = {
        "desired_state_sha256": DESIRED_SHA,
        "bindings": {"req-prices": "connector-market"},
        "persona_count": 1,
        "authority": "persona-registry",
        "authoritative": True,
    }
    values.update(change)

    changed = store.append(**values)

    assert changed.sequence == 2
    assert changed.desired_state_sha256 == expected
    assert len(_nonblank_lines(path)) == 2


def test_empty_authoritative_bindings_record_removal_and_are_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "requirements.jsonl"
    store = RequirementSnapshotStore(path)
    store.append(DESIRED_SHA, {"req-prices": "connector-market"}, 1, "persona-registry")

    removal = store.append(NEXT_DESIRED_SHA, {}, 0, "persona-registry", authoritative=True)
    duplicate = store.append(NEXT_DESIRED_SHA, {}, 0, "persona-registry", authoritative=True)

    assert removal.sequence == 2
    assert dict(removal.bindings) == {}
    assert removal.binding_count == 0
    assert removal.persona_count == 0
    assert removal.authoritative is True
    assert duplicate.to_dict() == removal.to_dict()
    assert len(_nonblank_lines(path)) == 2
    assert RequirementSnapshotStore(path).latest == removal


def test_append_flushes_file_with_fsync(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[int] = []
    monkeypatch.setattr(requirement_state.os, "fsync", lambda descriptor: calls.append(descriptor))

    RequirementSnapshotStore(tmp_path / "requirements.jsonl").append(
        DESIRED_SHA,
        {},
        0,
        "persona-registry",
    )

    # The new file and its directory are both synced.
    assert len(calls) == 2


def test_blank_lines_are_ignored_but_every_nonblank_record_is_validated(tmp_path: Path) -> None:
    path = tmp_path / "requirements.jsonl"
    first = _envelope(_state(sequence=1))
    second_state = _state(
        sequence=2,
        desired_state_sha256=NEXT_DESIRED_SHA,
        bindings={},
        binding_count=0,
        persona_count=0,
    )
    second = _envelope(second_state)
    path.write_text(
        "\n  \n" + _canonical_json(first) + "\n\t\n" + _canonical_json(second) + "\n",
        encoding="utf-8",
    )

    latest = RequirementSnapshotStore(path).latest

    assert latest is not None
    assert latest.sequence == 2
    assert latest.desired_state_sha256 == NEXT_DESIRED_SHA


@pytest.mark.parametrize(
    "invalid_line",
    [
        "{not-json}",
        '[]',
        '{"state":{},"state":{}}',
    ],
)
def test_reload_fails_closed_on_malformed_json_or_envelope(tmp_path: Path, invalid_line: str) -> None:
    path = tmp_path / "requirements.jsonl"
    path.write_text(invalid_line + "\n", encoding="utf-8")

    with pytest.raises(RequirementStateError, match="malformed|envelope"):
        RequirementSnapshotStore(path)


def test_reload_validates_corrupt_middle_line_even_when_latest_line_is_valid(tmp_path: Path) -> None:
    path = tmp_path / "requirements.jsonl"
    first = _canonical_json(_envelope(_state(sequence=1)))
    third = _canonical_json(_envelope(_state(sequence=3, desired_state_sha256=NEXT_DESIRED_SHA)))
    path.write_text(first + "\n{broken\n" + third + "\n", encoding="utf-8")

    with pytest.raises(RequirementStateError, match=r":2:"):
        RequirementSnapshotStore(path)


def test_reload_fails_closed_on_checksum_mismatch(tmp_path: Path) -> None:
    path = tmp_path / "requirements.jsonl"
    envelope = _envelope(_state())
    envelope["checksum"] = "0" * 64
    _write_envelopes(path, envelope)

    with pytest.raises(RequirementStateError, match="checksum mismatch"):
        RequirementSnapshotStore(path)


@pytest.mark.parametrize(
    "envelope_change",
    [
        {"checksum_algorithm": "md5"},
        {"checksum": "not-a-digest"},
        {"unexpected": "field"},
    ],
)
def test_reload_fails_closed_on_envelope_schema_errors(
    tmp_path: Path,
    envelope_change: dict[str, object],
) -> None:
    path = tmp_path / "requirements.jsonl"
    envelope = _envelope(_state())
    envelope.update(envelope_change)
    _write_envelopes(path, envelope)

    with pytest.raises(RequirementStateError, match="checksum|envelope"):
        RequirementSnapshotStore(path)


@pytest.mark.parametrize(
    "state_change",
    [
        {"schema_version": "source_ingest_requirement_snapshot.v0"},
        {"sequence": True},
        {"desired_state_sha256": DESIRED_SHA.upper()},
        {"bindings": ["connector-market"]},
        {"binding_count": 2},
        {"persona_count": -1},
        {"authority": ""},
        {"authoritative": 1},
        {"raw_personas": [{"persona_id": "must-not-be-stored"}]},
    ],
)
def test_reload_fails_closed_on_state_schema_errors(tmp_path: Path, state_change: dict[str, object]) -> None:
    path = tmp_path / "requirements.jsonl"
    state = _state()
    state.update(state_change)
    _write_envelopes(path, _envelope(state))

    with pytest.raises(RequirementStateError, match="invalid"):
        RequirementSnapshotStore(path)


@pytest.mark.parametrize("later_sequence", [1, 2])
def test_reload_fails_closed_on_sequence_regression(tmp_path: Path, later_sequence: int) -> None:
    path = tmp_path / "requirements.jsonl"
    _write_envelopes(
        path,
        _envelope(_state(sequence=3)),
        _envelope(_state(sequence=later_sequence, desired_state_sha256=NEXT_DESIRED_SHA)),
    )

    with pytest.raises(RequirementStateError, match="sequence regression"):
        RequirementSnapshotStore(path)


def test_append_revalidates_existing_log_and_refuses_to_extend_corruption(tmp_path: Path) -> None:
    path = tmp_path / "requirements.jsonl"
    store = RequirementSnapshotStore(path)
    store.append(DESIRED_SHA, {}, 0, "persona-registry")
    with path.open("a", encoding="utf-8") as handle:
        handle.write("{corrupt}\n")
    bytes_before = path.read_bytes()

    with pytest.raises(RequirementStateError, match="malformed"):
        store.append(NEXT_DESIRED_SHA, {}, 0, "persona-registry")

    assert store.latest is None
    assert path.read_bytes() == bytes_before


def test_separate_store_instances_allocate_from_current_file_sequence(tmp_path: Path) -> None:
    path = tmp_path / "requirements.jsonl"
    first_writer = RequirementSnapshotStore(path)
    second_writer = RequirementSnapshotStore(path)
    first_writer.append(DESIRED_SHA, {}, 0, "persona-registry")

    second = second_writer.append(NEXT_DESIRED_SHA, {}, 0, "persona-registry")

    assert second.sequence == 2
    assert RequirementSnapshotStore(path).latest == second


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"desired_state_sha256": "short"}, "SHA-256"),
        ({"bindings": {"": "connector"}}, "requirement id"),
        ({"bindings": {"req": ""}}, "connector id"),
        ({"bindings": []}, "bindings"),
        ({"persona_count": True}, "persona_count"),
        ({"persona_count": -1}, "persona_count"),
        ({"authority": ""}, "authority"),
        ({"authoritative": 1}, "authoritative"),
    ],
)
def test_append_rejects_invalid_input_without_writing(
    tmp_path: Path,
    kwargs: dict[str, object],
    message: str,
) -> None:
    path = tmp_path / "requirements.jsonl"
    values = {
        "desired_state_sha256": DESIRED_SHA,
        "bindings": {},
        "persona_count": 0,
        "authority": "persona-registry",
        "authoritative": True,
    }
    values.update(kwargs)

    with pytest.raises(RequirementStateError, match=message):
        RequirementSnapshotStore(path).append(**values)

    assert not path.exists()
