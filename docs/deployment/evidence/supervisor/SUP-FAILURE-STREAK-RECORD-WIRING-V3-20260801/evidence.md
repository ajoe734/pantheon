# SUP-FAILURE-STREAK-RECORD-WIRING-V3-20260801 — review-pending evidence

Task: Wire immutable failure generations from real supervisor records

Owner: Codex · Reviewer: Codex2 · Status: **review_pending**

PR: #4445 into `dev`

## Scope

This task owns the V3 failure-streak record and immutable generation contract,
its fail-closed decoders, canonical task identity propagation, and the six
existing production `record_task_failure_streak` call sites. It does not change
retry thresholds or eligibility, provider readiness, classification, progress
extraction, configuration, product tasks, or live services.

## Delivered contract

- Each aggregate and generation carries `schema_version: 3` and an exact field
  set. Unknown fields and legacy aliases are rejected.
- `owner_at_failure` and `reviewer_at_failure` come independently from the
  canonical task captured in the request snapshot; worker/request/task ids must
  agree and owner cannot equal reviewer.
- Each generation binds the logical provider, worker run, failure kind,
  independent reason class, canonical reason, non-empty raw evidence reference,
  and a rejected-head baseline of either 40 lowercase hex characters or
  `ABSENT`.
- `generation_id` is `sha256:` plus the digest of canonical immutable evidence.
  It excludes `count` and `recorded_at`, so replay and clear/recreate remain
  stable.
- Deduplication searches every generation in the streak. Replaying old evidence
  does not increment count or rewrite the aggregate; different kind, reason, or
  head values remain distinct even when `raw_ref` is the same.

## Verification

- Focused V3/call-site suite: 13 passed, 464 deselected, 62 subtests passed.
- Full supervisor regression: 477 passed, 66 subtests passed.
- Python compile check: passed.
- `git diff --check`: passed.
- Static production call-site proof: exactly six calls, each carrying
  `failure_kind`, `reason_class`, `raw_ref`, and `rejected_head`.
- Rejected PR #4442 heads `e89d09ea`, `67e8ce11`, `1041551b`, `85b4d860`,
  `5b2c309e`, and `69e69ffa`, plus rejected PR #4445 head `52cd7902`, are
  all absent from the candidate ancestry (full SHAs are recorded in
  `evidence.json`).

## Review boundary

No independent review is claimed here. `reviewed_head` remains null until
Codex2 reviews the final pushed head. Approval and `done` remain out of scope for
this owner handoff.
