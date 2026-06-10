# Review: SENTINEL-RULE-COVERAGE-HEALTHREASON-001

- Reviewer: Claude2
- Owner: Claude
- Decision: **approved** (return to owner for closeout)
- Reviewed commit: 80a8af6b (PR #660 → dev)

## Scope verified

Rule-engine coverage for the 6 persona `HealthReasonCode` values (Card P2-16 /
Pack D §D-SentinelRules). `main.py` adds `_SENTINEL_HEALTH_REASON_RULES` (6 rules)
and `_health_reason_sentinel_findings`, merged into `GET /bff/v5/sentinel/findings`
as open `persona_health` findings. Matches the committed Owned/Not-changing
boundary: persona-fleet health computation, incident-derived findings, and
sentinel detail/management surfaces are untouched. Not a new endpoint.

## Correctness

- **No drift.** Rule registry keys equal exactly the reason set emitted by
  `_project_persona_fleet_health` (main.py:26207–26234):
  persona_lifecycle_not_active, no_runtime_binding, active_incident,
  drawdown_threshold, negative_pnl, runtime_status_attention. Guarded by
  `test_rule_registry_covers_every_health_reason_code`.
- **Reads live projection.** Findings derive from `_project_persona_fleet_item`,
  so the rule engine never diverges from the health clients already see. The
  13-degraded-persona acceptance test exercises the real projection end-to-end.
- **Additive merge.** Health findings appended with id-dedup against
  incident-derived records; `available` flips true only when health findings
  exist. Severity uses canonical critical/high/medium/low plus a Pack D
  info/warn/alert `severity_bucket`.
- **Filter passthrough.** kind/status/severity short-circuit correctly; `kind`
  validation now accepts `persona_health`.
- **Contract guard preserved.** test_sent001 relaxed from strict equality to
  subset-of-ids + assertion that any extra finding is `persona_health`.

## Verification reproduced

```
pytest test_sentinel_healthreason_rule_coverage.py \
  test_sent001_sentinel_findings_contract.py \
  test_bff_v5_loop_sentinel_contract.py test_read_store_loop_sentinel.py -q
=> 71 passed
```

## Non-blocking note

- The `kind` Query param description string does not list `persona_health`
  (cosmetic only; the value is accepted and validated).

## Worktree note

The inbound worktree carried an inverse-staged index (shared-index footgun
leftover from the prior worker) whose physical content was identical to HEAD.
Unstaged it; tracked content now matches HEAD with no functional change.

## Closeout reminder for owner

PR #660 is OPEN and BEHIND dev — not yet merged. `done` requires the PR merged
into dev (task-closeout-finalization §Push and Merge Policy).
