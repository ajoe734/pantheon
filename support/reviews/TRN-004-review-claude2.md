# TRN-004 Review: trainer commit / discard / replay

Reviewer: Claude2
Date: 2026-05-16
Commit reviewed: 92784e26

## Verdict: APPROVED

## Scope Verified

TRN-004 hardens the `training-session-svc` replay decision path. The following
behaviors were reviewed against the implementation in commit 92784e26.

### Idempotency Key Support

- `_idempotency_key(primary, alias)` normalizes both `Idempotency-Key` and
  `X-Idempotency-Key` headers, preferring the primary. Both commit and discard
  endpoints accept either header and pass the normalized key to `_decide_replay()`.
- `_stable_hash()` produces a deterministic SHA-256 of the decision payload
  (session_id, state, expected_candidate_snapshot_at, actor_id, note) for conflict
  detection.

### Same-Key Replay (no duplicate event)

`_decide_replay()` checks for an existing `idempotency` entry in `replay_resolution`
before processing. If the same key and hash match, it returns the existing replay
state with `replayed: True` without appending a second `TeachingEvent`. Verified by:
- `test_replay_commit_idempotency_replays_without_duplicate_event`
- `test_replay_discard_idempotency_keeps_after_artifact_empty`

### Same-Key / Different-Payload Conflict

If the same key is present but the request hash differs, `_decide_replay()` raises
HTTP 409. Verified by the conflict branch in `test_replay_commit_idempotency_replays_without_duplicate_event`.

### Commit Lineage Refs

`_decision_lineage_refs()` stamps `lineage_ref`, `lineage_edge_id`,
`lineage_recorded_at`, `decision_record_ref`, plus `persona_policy_ref` and
`route_policy_ref` when `state == "committed"` and `persona_id` is present.
These are written into both `replay.artifacts` and the decision `TeachingEvent.artifact_refs`.
Verified by:
- `test_replay_commit_records_persona_route_policy_lineage_refs`
- `test_training_session_lifecycle_event_preview_and_replay_contract` (artifact_refs assertions)

### Discard Lineage (no persona/route mutation)

For `state == "discarded"`, `_decision_lineage_refs()` omits `persona_policy_ref`
and `route_policy_ref`. `after_artifact_ref` remains `None`. Verified by:
- `test_discard_replay_records_decision_lineage_and_idempotent_replay`
- `test_replay_discard_idempotency_keeps_after_artifact_empty`

## Test Results

All 61 tests pass:

| Suite | Count |
|---|---|
| `services/training-session/tests/test_http_service.py` | 7 passed |
| `services/training-session/tests` (full) | 17 passed |
| `services/control-plane/bff/test_training_session_service_client.py` | 3 passed |
| `services/control-plane/bff/test_tw04_teaching_replay_contract.py` | 34 passed |

Warnings in BFF suite (8 × `datetime.utcnow()` deprecation) are pre-existing and
unrelated to TRN-004.

## Minor Note

The commit body lists `Reviewer: Codex2`; the chair reassigned review to Claude2 after
Codex2 became quota-terminal. This predates the reassignment and has no impact on
correctness.

## Review Notes (ZH)

審查通過：TRN-004 commit 92784e26 正確實作 trainer replay 決策路徑強化。
`Idempotency-Key` 與 `X-Idempotency-Key` 兩種 header 均支援；同 key / 同 payload
重試回傳現有決策，不重複附加 TeachingEvent；同 key / 不同 payload 回傳 409 conflict；
commit 決策正確在 artifacts 與 event artifact_refs 兩層寫入 lineage_ref、
lineage_edge_id、persona_policy_ref、route_policy_ref；discard 決策保持
after_artifact_ref 為 None、不寫 persona/route policy 欄位；
evidence packet 在 support/evidence/TRN-004/README.md。
61 個測試全數通過，治理不變量均覆蓋。最終收尾由 owner Codex 完成。
