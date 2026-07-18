# OPS-ACTIVITY-ROTATION-PENDING-INTENT-RECOVERY-001 — Historical pre-review evidence

Status at this snapshot: implementation + isolated validation complete; no
live mutation had yet been performed. The guarded recovery was subsequently
executed once on 2026-07-17. See
`post-execution-evidence-20260717.md` and
`post-execution-artifact-digests-20260718.json` for the post-execution record,
`post-execution-redacted-manifests-20260718.json` for the safe before/after
manifest summary, `verification-matrix-20260718.md` for row-level fault and
tamper results, and `current-closeout-stop-evidence-20260718.md` for the
current fail-closed boundary and governed status readback.
Statements below about work not yet performed describe this historical
pre-review head and must not be read as current runtime state.

## Composition

- Base: planning merge `0ec0881108c12701aeae0d5a080599f017c0ab26`
  (current `origin/dev` at composition time).
- PR #3782 exact head `577af8f9cd1cbf95256fb47f7aa2dca8f4738e6b` was merged
  into this branch unmodified, so one branch carries the single authoritative
  recovery contract (schema-v2 lineage + this pending-intent resolution).
  This task's branch does not push to or rewrite the #3782 branch.

## Fresh read-only incident inventory (hash-bound)

Captured `2026-07-17T00:22:55Z` by
`.orchestrator/activity_pending_intent_recovery.py inventory` against
`/home/lupin/code/pantheon`, read-only (`O_RDONLY|O_NOFOLLOW` regular-file
reads, lstat-only on locks, no lock opened, nothing written).

- Pinned manifest: `pinned-manifest.json`
- Pinned inventory digest:
  `af523b7528128d178b803d4e0a873924948fd80d11a695a576219f282dfe6cfe`

| Artifact | Bytes | Lines | SHA-256 (compressed / payload) |
| --- | --- | --- | --- |
| Pending v1 intent (`…intent.json`) | 564 | — | `c0ec91c4…` file bytes |
| Staged archive (`…0188.archive.gz`) | 532,930 | 1,607 payload | gz `1cba352f…` / payload `b320711e…` (3,625,811 B) |
| Staged tail (`…0188.tail`) | 1,751,624 | 1,000 | `92f21fc4…` |
| Installed content archive (`archive/logs/ai-activity-log.jsonl-b320711e….gz`) | 532,930 | 1,607 payload | identical bytes to staged archive |
| Superseding legacy archive (`archive/logs/ai-activity-log.jsonl-2026-07-16T2337Z.gz`) | 5,431,928 payload | 2,627 | gz `e19b982f…` / payload `dbe1e818…` |
| Active log at pin | 3,456,083 | 1,265 | `06b3a151…` |

Intent transaction: `activity-rotation-…c7910188`, schema v1. No lineage
file exists. No resolution file exists. 423 archive leaves inventoried with
per-file compressed SHA-256 (412 legacy_ts_std + 1 content-addressed +
10 legacy_ts_old in the legacy dir; three pre-existing `*.bak`/`*.bak2`
operator copies sit outside every reader glob and were not touched).

## Proven byte relations (all exact, none inferred from names/mtimes)

1. staged_archive_payload + staged_tail == intent source
   (`7a261c5e…`, 5,377,435 B, 2,607 lines); all three intent digests match.
2. installed content archive bytes == staged archive bytes (byte-equal).
3. superseding legacy payload == intent source + exactly 20 post-intent
   appended events (54,493 B, newline-terminated) — the intent source is an
   exact byte prefix; exactly one legacy archive satisfies this (uniqueness
   proven against all 422 legacy archives by full decompression).
4. active == last-1,000 lines of the superseding payload + post-rotation
   appends (265 at pin time; the suffix is newline-terminated JSONL and
   shares no event id with the superseding archive).
5. Logical event conservation: 2,892 distinct events across the superseding
   archive and active; missing = 0, duplicates = 0. The 1,607 + 1,000 events
   of the stranded transaction are fully contained in the superseding legacy
   archive, so completing the v1 rotation would DUPLICATE 2,607 events.

Root cause of the incident shape: the v1 intent was published by a
current-code `scripts/ai_status.py` rotation (5 MiB / keep-1000) at
23:36:44Z; before the active swap completed, an old-vintage worktree
checkout (pre-content-addressed `OPS-LOG-ROTATE-001` code) rotated the whole
grown active file into the timestamp archive `…2026-07-16T2337Z.gz` with
keep-1000. This is why an env-only guard can never cover all writers.

## Accepted recovery contract (superseded-by-legacy resolution)

Because every stranded byte is already accounted for in the legacy chain,
the safe recovery is to RESOLVE the v1 transaction as superseded, not to
complete it:

- one durable, chained, hash-bound resolution row in
  `.orchestrator/logs/activity-rotation/ai-activity-log.jsonl.resolutions.jsonl`
  registers the orphan content-addressed archive with full digests, counts,
  the superseding archive binding, the pinned inventory digest, and the
  writer-guard attestation;
- the original intent + staged files are preserved byte-verified under
  `…/activity-rotation/resolved/<transaction>/` before the pending marker is
  removed;
- the active log, installed archive, and every historical archive are never
  rewritten, truncated, renamed, recompressed, or deleted;
- readers validate resolution rows fail-closed every read and never
  enumerate a superseded archive; rotation refuses to publish onto a
  superseded path; lineage stays empty until the first real schema-v2
  rotation, whose 1,000-line boundary normalization was proven to work on
  the post-recovery layout at live scale.

Dry-run (read-only, `dry-run-report.json`): status `resolvable`, zero
mutation, appends since pin classified (7,983 B), proposed transaction shown.

## PR #3782 planner findings closed on this branch

1. Enforceable all-writer guard: `PANTHEON_ACTIVITY_ROTATION_PAUSE=1` is now
   read inside `common.rotate_activity_log_unlocked` /
   `prepare_activity_audit_unlocked`, which both `scripts/ai_status.py` and
   every supervisor/common writer funnel through — one switch covers both
   current-code mechanisms (tested). Old-vintage checkouts are covered by
   process stop + readback; the rewritten transition guard runbook
   (`../ops-activity-rotation-overlap-prevention-001/transition-guard-runbook.md`)
   no longer claims env coverage it cannot deliver.
2. Newest-row + newest-archive rollback now tested for multi-row lineage
   with BOTH `keep_lines=1000` and `keep_lines=0`
   (`test_newest_row_and_archive_rollback_fails_for_both_keep_lines`).
3. Explicit per-field active-control tamper matrix: independent mutations of
   sequence, transaction_id, archive payload/gzip digests, lineage digest,
   lineage row digest, tail digest/byte-count/line-count, schema_version,
   log_name, plus stale control (pre-existing) and retained-tail truncation
   (`test_active_control_field_level_tamper_matrix_fails_closed`,
   `test_active_control_retained_tail_truncation_fails_closed`).
4. Central-lock absence readback: every suite ran under a fuser-sampling
   monitor (`lock-isolation/*.json`) — zero candidate test PIDs ever held
   `/home/lupin/code/pantheon/.orchestrator/activity-audit.lock` or
   `task-state.lock` — plus a full `strace -f` pass over the new recovery
   suite with ZERO syscalls touching `/home/lupin/code/pantheon`.
5. BEHIND-dev composition: this branch is composed on current `origin/dev`
   (`0ec0881…`) with #3782's exact head merged in.

## Verification matrix mapping (brief → tests)

All tests live in `.orchestrator/test_activity_pending_intent_recovery.py`
(32 tests) unless noted; all use repo-external tempdir status roots.

- Exact incident fixture: `test_execute_resolves_exact_incident`,
  `test_live_scale_recovery_stream_and_next_rotation` (1,000-line overlap,
  1,550-event conservation, plus first post-recovery schema-v2 rotation with
  boundary normalization).
- Relation variants (duplicate / prefix / one-byte diff / overlap-without-
  newline / independent / two candidates):
  `test_exact_duplicate_superseding_archive_is_safe` (safe case),
  `test_superseding_archive_relationship_variants_fail_closed`.
- Append variants (0/1/many, zero overlap, partial final line, append during
  dry-run, append between pin and execute, changed inode):
  `test_execute_supports_zero_one_many_appends_and_zero_overlap`,
  `test_partial_final_active_line_fails_closed`,
  `test_dry_run_allows_append_since_pin`,
  `test_execute_stops_on_append_since_pin`,
  `test_execute_stops_on_changed_active_inode`.
- Crash/retry (SIGKILL at pin-recheck, preserve, resolution publish,
  resolution readback, intent unlink, stage unlink; restart convergence;
  already-completed; stale pin; competing process):
  `test_sigkill_at_each_publish_step_converges_on_retry`,
  `test_execute_resolves_exact_incident` (idempotent re-run + stale-pin
  fail), `test_stale_pin_against_mutated_manifest_fails`,
  `test_competing_recovery_is_serialized_by_exclusive_lock`.
- Tamper (intent fields/transaction id, staged archive/tail, installed gzip,
  active source, superseding archive, resolution row per-field, truncation,
  missing files, extra archive, unknown name, symlinks):
  `test_incident_artifact_tampers_fail_closed`,
  `test_symlinked_incident_leaves_fail_closed`,
  `ResolutionReaderContractTests` (7 tests).
- Conservation: byte + event-id accounting in every proof
  (`missing_event_count == 0`, `duplicate_event_count == 0` asserted).
- Isolation: `test_isolated_lock_paths_and_no_central_references` + the
  lock-monitor and strace evidence above.
- Reader/writer contract: `StrandedIntentFailClosedTests` (v2 code never
  silently accepts a v1 intent; guard pauses both mechanisms),
  `test_rotation_rejects_publishing_onto_superseded_archive_path`,
  `test_superseded_archive_registered_in_lineage_fails_closed`.

## Isolated validation results (final head)

Environment: `env -u ORCH_RUN_ID -u PANTHEON_WORKTREE_ROOT
-u ORCH_WORKSPACE_PATH -u ORCH_RUNNER_STATUS_PATH -u ORCH_HEARTBEAT_PATH
PANTHEON_STATUS_ROOT=/tmp/oparpir-001/...` (repo-external roots).

- `python3 -m py_compile .orchestrator/common.py
  .orchestrator/activity_pending_intent_recovery.py
  .orchestrator/test_common.py
  .orchestrator/test_activity_pending_intent_recovery.py` — pass
- `.orchestrator/test_common.py` — 64 tests OK
- `.orchestrator/test_activity_pending_intent_recovery.py` — 32 tests OK
- `scripts.test_activity_audit_logical_inventory` — 19 tests OK
- `scripts.test_ai_status` (isolated root) — 74 tests OK
- `.orchestrator/test_supervisor_watchdog.py` — 28 tests OK
- `.orchestrator/test_worker_runner_heartbeat.py` — 13 tests OK
- `.orchestrator/test_runtime_state.py` — OK
- `.orchestrator/test_supervisor.py` (isolated root) — OK
- `git diff --check origin/dev...HEAD` — clean
- Central-lock readback: all `lock-isolation/*.json` show
  `candidate_pids_holding_central_locks == []` for both central locks.

## Redaction statement

Evidence contains only paths, sizes, line counts, digests, event totals, and
truncated command identities. No tokens, no raw activity payloads, no
personal data, and no process environments are published. Raw snapshot
copies and full manifests remain in the operator-controlled work area
`/tmp/oparpir-001/` on the host.

## What was intentionally not done at this historical head

- At this pre-review snapshot there had been no merge, install, or live
  execute. Execute required
  `PANTHEON_ACTIVITY_PENDING_INTENT_RECOVERY_EXECUTE=I-UNDERSTAND-LIVE-MUTATION`,
  the exact pinned digest, a non-empty writer-guard attestation, and the
  exclusive activity lock. The later single execution and readback are
  recorded in `post-execution-evidence-20260717.md`.
- No central status file, archive byte, or active log was modified while this
  pre-review evidence was captured.

## Current closeout boundary

The historical execution evidence is complete enough to preserve the exact
recovery-window result, but the task is not ready for `done`. The current
read-only audit found an unauthenticated legacy-to-legacy disjoint transition
from `2026-07-17T0404Z.gz` to `2026-07-17T1754Z.gz`. The first schema-v2
lineage row authenticates the later `1754Z -> content archive` boundary only.
See `current-closeout-stop-evidence-20260718.md`; do not use a generic
"disjoint epoch" relaxation or another recovery execute to clear this gate.
