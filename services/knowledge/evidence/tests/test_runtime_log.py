from __future__ import annotations

import json
import os

import pytest

from services.knowledge.evidence import (
    REDACTED_VALUE,
    RuntimeEvidenceLog,
    RuntimeEvidenceVerificationError,
    compute_runtime_evidence_checksum,
)
from services.knowledge.evidence import runtime_log as runtime_log_module


def test_runtime_log_appends_verified_hash_chain_and_recursively_redacts(tmp_path) -> None:
    path = tmp_path / "runtime-evidence.jsonl"
    log = RuntimeEvidenceLog(path, owner_service="training-session")
    original_payload = {
        "job_id": "job-001",
        "decision_by": "operator-alice",
        "nested": {
            "actor_id": "operator-bob",
            "accessToken": "secret-token",
            "note": "contains private free-form content",
            "dataset_version_id": "dataset-v7",
        },
        "items": [{"credentials": {"username": "alice", "password": "hunter2"}, "metric": 1.25}],
    }

    first = log.append(
        "preview_evaluated",
        original_payload,
        recorded_at="2026-07-15T14:00:00Z",
    )
    second = log.append(
        "commit_admitted",
        {"proof_id": "proof-001", "nested": {"actor_id": "operator-charlie"}},
        recorded_at="2026-07-15T14:01:00Z",
    )

    assert first["sequence"] == 1
    assert first["previous_checksum"] is None
    assert second["sequence"] == 2
    assert second["previous_checksum"] == first["checksum"]
    assert first["checksum"] == compute_runtime_evidence_checksum(first)
    assert second["checksum"] == compute_runtime_evidence_checksum(second)
    assert first["payload"]["decision_by"] == REDACTED_VALUE
    assert first["payload"]["nested"]["actor_id"] == REDACTED_VALUE
    assert first["payload"]["nested"]["accessToken"] == REDACTED_VALUE
    assert first["payload"]["nested"]["note"] == REDACTED_VALUE
    assert first["payload"]["nested"]["dataset_version_id"] == "dataset-v7"
    assert first["payload"]["items"][0]["credentials"] == REDACTED_VALUE
    assert original_payload["decision_by"] == "operator-alice"
    assert original_payload["nested"]["actor_id"] == "operator-bob"

    records = log.read_verified()
    assert records == [first, second]
    verification = log.verify()
    assert verification.record_count == 2
    assert verification.last_sequence == 2
    assert verification.last_checksum == second["checksum"]
    assert verification.owner_service == "training-session"
    assert len(path.read_text(encoding="utf-8").splitlines()) == 2


def test_runtime_log_detects_payload_tampering(tmp_path) -> None:
    path = tmp_path / "runtime-evidence.jsonl"
    log = RuntimeEvidenceLog(path, owner_service="training-session")
    log.append("preview_evaluated", {"metric": 1.0})

    record = json.loads(path.read_text(encoding="utf-8"))
    record["payload"]["metric"] = 999.0
    path.write_text(json.dumps(record, sort_keys=True) + "\n", encoding="utf-8")

    with pytest.raises(RuntimeEvidenceVerificationError, match="checksum mismatch"):
        log.read_verified()


def test_runtime_log_detects_relinked_chain_even_with_valid_record_checksum(tmp_path) -> None:
    path = tmp_path / "runtime-evidence.jsonl"
    log = RuntimeEvidenceLog(path, owner_service="training-session")
    log.append("preview_evaluated", {"metric": 1.0})
    log.append("commit_admitted", {"proof_id": "proof-001"})

    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    records[1]["previous_checksum"] = "0" * 64
    records[1]["checksum"] = compute_runtime_evidence_checksum(records[1])
    path.write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeEvidenceVerificationError, match="previous_checksum chain mismatch"):
        log.verify()


def test_runtime_log_rejects_malformed_json(tmp_path) -> None:
    path = tmp_path / "runtime-evidence.jsonl"
    path.write_bytes(b"{not-json}\n")

    log = RuntimeEvidenceLog(path, owner_service="training-session")
    with pytest.raises(RuntimeEvidenceVerificationError, match="malformed JSON"):
        log.read_verified()


def test_runtime_log_rejects_sequence_tampering_even_with_valid_record_checksum(tmp_path) -> None:
    path = tmp_path / "runtime-evidence.jsonl"
    log = RuntimeEvidenceLog(path, owner_service="training-session")
    record = log.append("preview_evaluated", {"metric": 1.0})
    record["sequence"] = 7
    record["checksum"] = compute_runtime_evidence_checksum(record)
    path.write_text(json.dumps(record, sort_keys=True) + "\n", encoding="utf-8")

    with pytest.raises(RuntimeEvidenceVerificationError, match="sequence mismatch"):
        log.verify()


def test_runtime_log_propagates_fsync_failure(tmp_path, monkeypatch) -> None:
    path = tmp_path / "runtime-evidence.jsonl"
    log = RuntimeEvidenceLog(path, owner_service="training-session")
    real_fsync = os.fsync
    calls = 0

    def fail_first_fsync(descriptor: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("injected runtime evidence fsync failure")
        real_fsync(descriptor)

    monkeypatch.setattr(runtime_log_module.os, "fsync", fail_first_fsync)

    with pytest.raises(OSError, match="injected runtime evidence fsync failure"):
        log.append("preview_evaluated", {"metric": 1.0})

    assert calls == 1


def test_runtime_log_propagates_append_write_failure(tmp_path, monkeypatch) -> None:
    path = tmp_path / "runtime-evidence.jsonl"
    log = RuntimeEvidenceLog(path, owner_service="training-session")

    def fail_write(descriptor: int, line: bytes) -> int:
        raise OSError("injected runtime evidence write failure")

    monkeypatch.setattr(runtime_log_module.os, "write", fail_write)

    with pytest.raises(OSError, match="injected runtime evidence write failure"):
        log.append("preview_evaluated", {"metric": 1.0})
