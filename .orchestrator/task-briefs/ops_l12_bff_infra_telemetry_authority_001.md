# Task Brief: OPS-L12-BFF-INFRA-TELEMETRY-AUTHORITY-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Add strict-auth infrastructure health telemetry authority
- Status: in_progress
- Owner: Claude
- Reviewer: Codex2
- Next: Independent `Codex2` review of PR #4211 at the fourth owner evidence cut. The three repairs asked for by the previous review round are done and are described under Owner response below. `AC6` stays `pending_reviewer` and no approval is asserted by either reviewer.

## Summary
- Telemetry owns an authoritative, strict-auth, non-trading `InfrastructureHealthEvent` contract, so control-plane health monitoring never invents a `RuntimeBinding` and never gets a shape-based shortcut around trading validation.
- Admission is durable and idempotent by stable `event_id`: a two-phase fenced reservation commits its ledger receipt only after a durable enqueue receipt, and the configured buffer must prove durability from its own `is_durable()` before the reservation is taken and again before the commit.
- Trading ingest keeps evidence contract E-1 through E-6 and its authoritative `RuntimeBinding` cross-validation unchanged, and now also refuses `infrastructure_health` outright.
- Scope is limited to `services/telemetry`. `services/incidents` stays owned by `L12-EVO-001` and `services/control-plane/bff` stays owned by `L12-BFF-001`.

## Owner response to the previous review round

1. **PR `BEHIND` dev and `Commit trailers` failing.** Run 30219467575 failed on
   `0410a89f0`, an already-merged `dev` commit from
   `OPS-L12-TELEMETRY-LINEAGE-TEST-ISOLATION-001` whose squash subject is 79
   chars. The pre-fix workflow anchored the scan range at the synthetic merge
   commit, so the PR was red on somebody else's history. Merging current `dev`
   (`6578ef968`) into the task branch cleared the range and picked up the
   `OPS-CI-PR-TRAILER-RANGE-001` repair. Merge commit `cca84df53`, no conflict,
   no task file touched. All three required checks are now green on that head:
   `Commit trailers`, `Runtime mirror guard`, `Smoke acceptance`, on both the
   `pull_request` (run 30222836610) and `push` (run 30222834932) events.

2. **Stale `Antigravity` reviewer identity.** The evidence `task.reviewer`, `AC6`
   statement and `blocking_until`, `security_and_safety.two_person_approval`
   proof, `schema_status.note`, and the evidence README now name `Codex2`. Both
   `Antigravity` `changes_requested` decisions stay in `record_log` at sequences
   2 and 4 as the historical review trail, the reassignment is recorded at
   sequence 6, and no `Antigravity` verdict is restated as a `Codex2` verdict.
   The canonical acceptance row in `ai-status.json` still reads `Antigravity`
   because it predates the reassignment; that is stated in `AC6`
   `blocking_until` rather than silently rewritten.

3. **Validation predating the durability repair.** `validated_head_sha` was
   `2f723037e`; it is now the merge head `cca84df53` with
   `validated_base_sha` `6578ef968`. Focused and full telemetry validation were
   rerun there:
   - `services/telemetry/test_infrastructure_health_ingest.py` — 44 passed.
   - `services/telemetry` — 335 passed, 1 skipped, 29 subtests passed, **no
     failures**. The missing-`PANTHEON_RUNTIME_MANAGER_URL` residual the third
     cut carried is gone, because the `dev` sync brought in the test-owned
     isolation fixture from `OPS-L12-TELEMETRY-LINEAGE-TEST-ISOLATION-001`.
   - Cross-service regression set — 11 passed and one pre-existing failure in
     `test_p0_paper_operating_loop_smoke.py` on `PermissionError: [Errno 13]
     Permission denied: '/data'` while creating
     `/data/runtime/lifecycle-outbox`. Unwritable host directory, never touched
     by telemetry admission.

   The runtime readback and the three mutation controls were taken at head
   `28b13a16d` and are carried forward verbatim, because the implementation
   bytes at `28b13a16d` and at the validated head are identical.

4. **Binding the proof to the head under review.** The commit carrying the
   manifest lands after the validated head, so its own `Branch CI Gate` run
   cannot be named inside the manifest that commit creates; that run is the
   branch-protection merge gate on PR #4211.
   `integrity.source_artifact_sha256_by_epoch.implementation_files_at_validated_head`
   records the `sha256` of each of the five implementation files at
   `cca84df53`, so `git show <final-head>:<path> | sha256sum` must reproduce
   them exactly at whatever head the reviewer reads.

## Evidence

- `docs/deployment/evidence/twelve-loop-gap/OPS-L12-BFF-INFRA-TELEMETRY-AUTHORITY-001/evidence.json`
  — schema-valid against `schemas/product-evidence.schema.json`;
  `overall_admission=pass_owner_evidence_ready`, which asserts owner proof only.
- `.../README.md` — narrative packet, including § Head binding.
- `.../current-runtime-readback.json` — bounded local nonprod readback over real
  HTTP against a real NATS JetStream file-storage work queue.
- `.../evidence.sha256` — companion digests for the manifest and the readback.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
