#!/usr/bin/env python3
"""Planning-contract regression checks for immutable audit archive repair."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "docs/bff/execution-tasks/2026-07-15-immutable-audit-archive-repair/fixtures/archive-audit-repair-bootstrap-task.v1.json"


def canonical_digest(task: dict[str, object]) -> str:
    projection = {
        key: value
        for key, value in task.items()
        if key not in {"owner", "reviewer", "status", "next"}
    }
    raw = json.dumps(projection, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(raw).hexdigest()


def main() -> int:
    fixture = json.loads(FIXTURE.read_text())
    task = fixture["task"]
    assert task["id"] == "LOOP-PROD-AUDIT-ARCHIVE-REPAIR-001"
    assert task["wave"] == -2
    assert task["depends_on"] == []
    assert fixture["task_contract_sha256"] == canonical_digest(task)
    text = json.dumps(task, sort_keys=True)
    for needle in (
        "47d562e67b6f7f91fe5ea03ad08b36d473470d29e808a3981d44283e37623e24",
        "b16fd8057507ca8e76b3e40f07535e067f9fe5991dbe311b5e2e8ca43955fc07",
        "content-addressed",
        "fails closed",
        "LOOP-PROD-RUNTIME-BOOT-001",
    ):
        assert needle in text, needle
    forbidden = {"ai-status.json", "ai-activity-log.jsonl"}
    assert forbidden <= set(" ".join(task["non_goals"]).replace("`", "").split())
    print("archive audit repair packet: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
