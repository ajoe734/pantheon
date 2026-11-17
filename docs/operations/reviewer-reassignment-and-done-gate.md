# Reviewer Reassignment: Independence Exception And Done Gate

Task: SUP-REVIEWER-REASSIGNMENT-RECORD-AND-DONE-GATE-20261117 (register RC4 / WP4.3).
This is development tooling; it takes effect on the live supervisor only after a
governed runtime promotion.

## 1. Independence exception record

Family map (AGENTS.md "Review Independence"), code: `task_machine.AGENT_FAMILIES`:
Claude/Claude2 = claude, Codex/Codex2 (and `codexN_M` lanes) = codex,
Antigravity/Antigravity2 = antigravity, plus copilot and piastra. A test fails if
a configured or known agent has no family.

When a reassignment (supervisor `Orchestrator` or Human/Ops `assign`) leaves the
owner and reviewer in the same family, the audited `task_reassigned` event
carries `review_independence_exception`:

```json
{"owner_family":"claude","reviewer_family":"claude",
 "considered":[{"family":"codex","reason":"Codex2:terminal_quota:codex"}, ...]}
```

- Supervisor: reasons come from runtime delivery health
  (`assignment_terminal_unavailability`); a family with any viable agent is
  recorded as `no capacity evidence supplied`, never as exhausted.
- Human/Ops: supply `TASK_REVIEW_EXCEPTION_EVIDENCE="codex=<ref>;antigravity=<ref>"`.
- A cross-family pair records nothing. The record lives in the activity audit,
  so it stays readable after the task is archived. The event id digest does not
  cover the new field, so existing readers/validators are unaffected.
- Budget: at most 4 families x 120-char reason, under 1 KiB, once per
  reassignment; nothing recurring.

## 2. Handoff admission

`handoff` rejects a head commit whose `Reviewer:` trailer names an agent that an
audited reassignment chain already replaced with the current reviewer. The
message names the commit and the expected reviewer; amend the trailer, push, and
hand off again. Trailers naming a never-reviewer, or with no audit, are left to
the existing done-time checks.

## 3. Done gate

`done` accepts a reviewer reassignment that precedes the delivered commit only
when the task is `review_approved` and the canonical approval binding
(`review_binding.head_sha`) equals the exact delivered head, and the audited
chain runs from the trailer's reviewer to the current reviewer. A missing or
different approved head keeps the original refusal
("audited reviewer reassignment must follow the delivered commit"). A reviewer
change with no audited event, a trailer naming an agent that was never reviewer,
self-review and owner trailer mismatches are still refused. The proof records
`approved_head_binding: true`.

## 4. Recovery of the two blocked rows (after runtime promotion)

SOURCE-DAILY-BARS-VALUE-TIME-STATUS-COLUMNS-20261118 and
DEV-COMPOSE-MEMORY-LIMITS-20261117 have `review_file` = `evidence.json`, so they
use the **owner `done` path**, not `reconcile_merged_done` with
`RECONCILE_EVIDENCE_FILE` (that path accepts only a Markdown `# Task Brief:`
file; this task chose to document the path rather than widen it). Coordinator:

```bash
"$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh" show <TASK>   # confirm review_approved, binding head = merged PR head
AI_NAME=<Owner> "$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh" done <TASK> "finalize merged delivery"
```

No task or queue JSON is edited by hand. If the approval binding head is not the
merged head, `done` stays refused and the task needs a fresh review.
