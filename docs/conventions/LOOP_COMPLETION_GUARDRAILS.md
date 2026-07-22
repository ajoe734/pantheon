# Loop Completion Guardrails

Status: canonical
Last updated: 2026-06-27
Tier: L0 Collaboration & State (operating rule for workers and supervisor)
Scope: loop-autopilot task closure — what counts as valid evidence, what is rejected

## Purpose

Workers and auto-dispatchers must not claim a loop task is complete when the
only evidence is a route stub, a seed/fixture file, or a UI panel copy.  This
document defines the enforcement rules, the evidence requirements, and how the
guardrail is wired into the status system.

## What Is a Loop-Autopilot Task

A task is subject to these guardrails when it has **any** of:

- a non-empty `loop_ids` field in `ai-status.json`
- `non_goals` containing one of the canonical loop-autopilot phrases:
  - `"No panel-only closure"`
  - `"No seed fixture as live proof"`
  - `"No approval gate bypass"`

## Prohibited Closure Patterns

| Pattern | Why it is rejected |
|---|---|
| Route-only | An API route may be registered without a controller.  The loop is not alive. |
| Seed / fixture | Fixture data proves the schema, not that the real loop runs. |
| Panel copy | A UI panel screenshot or copy proves rendering, not controller liveness. |
| Config-only | Adding a config key without a running worker does not close the loop. |

## Required Evidence for a Valid Done Claim

Before a loop-autopilot task may move to `done` the following must be true:

1. **A `review_file` must be present.**  The reviewer sets this field by running
   the `approve` command with `REVIEW_FILE=<evidence-path>`.  The file should
   be a real evidence artifact such as a smoke test log, a replay record, or a
   linked acceptance note — not a panel screenshot or a fixture listing.

2. **Review notes must not contain fixture/seed-as-proof language.**  Phrases
   such as `"fixture only"`, `"seed only"`, `"panel only"`, `"panel copy"`, or
   `"route only"` (case-insensitive) in `review_notes_zh` will cause the done
   transition to be rejected.

3. **`proof_required` tasks must have a linked evidence file.**  When the task
   lists `proof_required` items (unit tests, contract tests, smoke tests, replay
   evidence), the reviewer must reference an evidence artifact via `REVIEW_FILE`
   before the owner can close.

## Enforcement Points

### `ai-status.sh done` / `python3 scripts/ai_status.py done`

`command_done` in `scripts/ai_status.py` calls `validate_loop_completion_claim`
immediately after checking `review_approved` status.  If any evidence gap is
detected the command exits non-zero with a descriptive message before touching
the task record.

This enforcement is automatic.  Workers do not need to call anything extra.

### Standalone Pre-Check Script

Run `scripts/loop_done_guardrail.py` at any point to audit loop tasks without
making any state changes:

```bash
# Check all loop-autopilot tasks
python3 scripts/loop_done_guardrail.py

# Check one task
python3 scripts/loop_done_guardrail.py --task-id LOOP-AUTO-002
```

Exit codes: `0` = all tasks pass, `1` = one or more gaps found, `2` = error.

The supervisor should run this script as part of the dispatch readiness check
before handing a loop task back to the owner for finalization.

### Reviewer Responsibility

The reviewer is the last gate before a task is eligible for `done`.  When
reviewing a loop-autopilot task the reviewer must:

1. Verify that real controller liveness evidence exists (not just a route or
   fixture).
2. Set `REVIEW_FILE=<path-to-evidence>` when running the `approve` command.
3. Not approve a task whose only artifact is a panel screenshot, a seed file,
   or a stub route registration.

```bash
REVIEW_FILE=docs/deployment/evidence/loop-auto-002-smoke.md \
REVIEW_NOTES_ZH="Controller liveness confirmed via smoke run" \
AI_NAME=Codex ./scripts/ai-status.sh approve LOOP-AUTO-002 "Evidence verified"
```

### Owner Responsibility

After receiving `review_approved`, the owner verifies:

1. The `review_file` field is set in the task record.
2. The proof listed in `proof_required` is reachable from the linked evidence.
3. Runs `python3 scripts/loop_done_guardrail.py --task-id <ID>` as a final
   pre-check before calling `done`.

## Evidence Quality Levels

| Evidence type | Counts toward closure? |
|---|---|
| Unit test passing | Partial — proves code path, not controller liveness |
| Contract test passing | Partial — proves schema, not that the loop runs live |
| Local service smoke (`python3 scripts/probe_loop_liveness.py`) | Yes |
| Restart / replay log | Yes |
| Seed fixture | No |
| Panel screenshot | No |
| Route registration | No |

At minimum one "Yes" evidence type must be present in the `review_file` artifact
for the done claim to be accepted.

## Related Documents

- `DELIVERY_CLOSURE_AND_LOOP_STATES.md` — delivery-loop closure semantics
- `EXECUTION_PROOF_AND_MATURITY_LEVELS.md` — evidence ladder and maturity levels
- `.orchestrator/skills/task-closeout-finalization.md` — task closeout checklist
- `scripts/loop_done_guardrail.py` — standalone audit tool
- `scripts/probe_loop_liveness.py` — live liveness probe for BFF surfaces
