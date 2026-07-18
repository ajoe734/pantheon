# P0 regression receipt — 2026-07-18

Task: `OPS-ACTIVITY-AUDIT-LEGACY-OVERLAP-RECOVERY-001`

Corrective implementation commit:
`afb3b67d10ec55a2989bd54b3bc59f22e55b67f7`.

Validation completed at `2026-07-18T15:15:45Z`. All test status roots were
isolated under `/tmp`; the central status root was used only by the explicit
read-only probe below. No governed status write was issued.

## Exact central fail-closed probe

The anchored inventory was run with:

```text
python3 scripts/activity_audit_logical_inventory.py \
  --status-root "$PANTHEON_STATUS_ROOT" \
  --evidence-dir <empty repo-external temp directory>
```

Result: expected exit `1` with structured invariant
`activity_unregistered_disjoint_edge`. The exact rejected edge was
`ai-activity-log.jsonl-2026-07-17T0404Z.gz ->
ai-activity-log.jsonl-2026-07-17T1754Z.gz`, source index `424`, activated by
continuity anchor
`archive/logs/ai-activity-log.jsonl-2026-07-16T1450Z.gz`. The diagnostic
evidence SHA-256 was
`dc726f4e5bd122224e642d080c7c537745e1ba4fae085437ec07847ee2857abb`.

The repo-external evidence directory remained empty and was removed with
`rmdir`. Before/after gzip SHA-256 values were identical:

| Source | Before and after SHA-256 |
| :--- | :--- |
| `1450Z` | `f4c09816106783bde90463df6a1a8b227384cf088e1ab05d299c3caa1398e9cc` |
| `0404Z` | `9aad2a2e5eb40b8233aaf91f02a429142084eef09c33cf36f8fa9076a1c3e65b` |
| `1754Z` | `5c9a4f97af7e69beb3dd6b547452fad3f56f9e57442d61c49e94d4552c7d6bd2` |

## Corrective behavior proved

- The production anchor registry is exact across relative path, basename,
  source class, gzip digest/bytes, payload digest/bytes, and line count.
- The exact `1450Z -> 0404Z [byte-identical fold] -> 1754Z [unregistered
  gap]` fixture raises before any logical row or callback is exposed.
- Same-name anchor content tampering raises structured
  `activity_content_identity` and exposes no rows or callbacks.
- Once anchored, a chain that terminates before the active log raises
  `activity_continuity_not_active`.
- Only validated schema-v2 boundary, content-chain, and active-head edges are
  replayed through `on_disjoint`; an inserted legacy edge is rejected.
- The inventory walks only byte-identical folds and reader-emitted authorized
  disjoint edges. It no longer infers a successor from filename ordering.
- A first schema-v2 writer transition with legacy history but no byte-proven
  boundary fails before publishing an archive or lineage row. Existing
  superseded-archive conflict preflight remains intact.
- A failed inventory leaves pre-existing `manifest.json`, `summary.json`, and
  `evidence.md` bytes unchanged.

## Test matrix

| Suite | Result |
| :--- | :--- |
| P0 reader focused regressions | `6 passed` across exact registry, unregistered gap, tamper, missing active, lineage positive, and inserted edge |
| P0 inventory focused regressions | `2 passed` |
| `.orchestrator/test_common.py` | `97 passed, 52 subtests passed` |
| `scripts.test_activity_audit_logical_inventory` | `27 passed, 1 skipped` |
| `.orchestrator/test_activity_pending_intent_recovery.py` | `42 passed, 51 subtests passed` |
| `scripts/test_ai_status.py` | `83 passed, 23 subtests passed` |
| runtime-state / supervisor / watchdog / worker-runner orchestrator suites | `392 passed, 51 subtests passed` |
| Python compile and `git diff --check` | passed |
| P0 verdict and installed summary JSON parsing | passed |

The `ai-status` suite was run with both current and legacy command-runtime env
bindings removed, so its local subprocess fixtures could exercise the local
wrapper while `PANTHEON_STATUS_ROOT` remained an isolated temp directory.

## Unrelated baseline failures

An additional six-file script-control group reported `26 passed, 2 failed`.
Both failures were reproduced unchanged at pre-P0 commit `21eda5d46` in a
clean detached worktree:

- `SupervisorQuotaGuardrailTests::test_dispatch_ready_tasks_skips_paused_provider`
  expects no delivery, while current dev behavior auto-reassigns the paused
  Qwen task to Codex.
- `test_health_passes_when_supervisor_lock_and_heartbeat_are_fresh` expects a
  healthy result under the older runtime-health lock fixture.

Neither failing file nor its product implementation is in this task diff.
They are recorded as existing dev-baseline debt, not treated as P0 acceptance
evidence and not swept into this corrective PR.

## Residual boundary

The registry is intentionally incident-scoped. It does not declare all
historical legacy disjoint epochs continuous. The reader continues to reject
the current `0404Z -> 1754Z` edge until immutable hash-bound lineage authority
exists. The source plan currently validates content archives twice per full
logical read; that bounded I/O duplication is a follow-up performance concern,
not an authority bypass.
