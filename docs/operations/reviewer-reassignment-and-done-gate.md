# Reviewer Reassignment: Independence Exception And Done Gate

Task: SUP-REVIEWER-REASSIGNMENT-RECORD-AND-DONE-GATE-20261117 (register RC4 / WP4.3).
This is development tooling; it takes effect on the live supervisor only after a
governed runtime promotion.

## 1. Independence exception record

Family map (AGENTS.md "Review Independence"), code: `AGENT_FAMILIES` in `scripts/ai_status.py` and `.orchestrator/supervisor.py` (a test asserts the two are identical):
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
  recorded as `available:<agent>`, never as exhausted. The supervisor reads
  runtime state through the lock-free snapshot because the caller already holds
  the task_state lock (taking runtime_admission there violates the lock order).
  If evidence cannot be computed each family records
  `evidence_unavailable:<ErrorClass>` and the supervisor log names the error.
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

## 5. Owner `LLM-Agent` trailer admission

Handoff now also checks the **owner** trailer
(`reject_owner_trailer_mismatch_at_handoff`, next to the Reviewer check). It
reads only the commit messages of the delivered range (frozen
`base_sha..head_sha`, no merges; the head alone if the binding has no base) and
considers commits whose `Task-ID` trailer is this task. A commit whose
`LLM-Agent` trailer is not the task owner is refused unless an audited
`task_reassigned` owner chain links that agent to the current owner (the same
chain walk as the done-time rule). The refusal names the commit, the trailer
found and the expected owner. Because the check runs on the head the review will
bind, an amend is re-checked at the next handoff. A trailer that is simply
absent is left to the existing required-trailer checks; done-time refusals are
unchanged, and an already-merged row with the mismatch is still refused at done.

**Commit time.** The worker launch (`worker_runner.build_worker_child_env`) sets
`PANTHEON_WORKER_AGENT` to the canonical agent identity, so the identity is
available to the commit path. Enforcing it inside `worker_commit.py` is outside
this task's artifact contract, so today only the handoff check is feasible: it
runs on the exact head the review will bind and refuses the mismatch before
review, which is the backstop for both `worker_commit.py` and raw `git commit`.

**Activation.** Development tooling: takes effect on the live supervisor only
after a runtime promotion.

### Recovery of merged rows with an owner trailer mismatch

Blocked today: TW-TAIFEX-TX-DAILY-CSV-RESPONSE-20261118 (PR 7554; author
Claude2, trailer `LLM-Agent: Claude`, owner never reassigned) and
COLLECTOR-LISTING-MODULE-SHIPPED-20261117 (merged commit ff5784500; recovery via
OPS-MERGED-RECOVERY-COLLECTOR-LISTING-MODULE-20261118). Earlier case:
FE-MANAGEMENT-ATTRIBUTION-VIEWS-20261116. Do not finalize them by hand; do not
edit task JSON.

1. A small recovery task writes a reviewed record under
   `.orchestrator/review-evidence/<TASK>.md` (example: PR 7560 for
   SOURCE-DAILY-BARS-VALUE-TIME-STATUS-COLUMNS-20261118). Everything
   `validate_merged_done_evidence` requires must be in it:
   - `# Task Brief: <TASK>`
   - `- Status: review_approved`
   - `- Owner: <Owner>` (the canonical owner; if it differs from the task row
     it needs an audited reassignment)
   - `- Reviewer: <Reviewer>` (independent of the owner)
   - the repository slug `ajoe734/pantheon` and the full 40-hex delivery commit
     SHA (both must appear verbatim)
   - the merged PR, the trailer found (`LLM-Agent: <wrong>`), the expected
     owner, and the recorded review approval.
   Merge the record to the target ref.
2. The coordinator (Human/Ops or the task's current reviewer) runs the
   reconcile command with the full environment:

   ```bash
   RECONCILE_EVIDENCE_FILE=.orchestrator/review-evidence/<TASK>.md \
   RECONCILE_EVIDENCE_COMMIT=<merge commit of the record> \
   RECONCILE_DELIVERY_REPOSITORY=ajoe734/pantheon \
   RECONCILE_DELIVERY_ROOT=<local checkout of the delivery repository> \
   RECONCILE_DELIVERY_COMMIT=<full 40-hex merged delivery commit> \
   AI_NAME=<Human/Ops or current reviewer> \
   "$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh" reconcile_merged_done <TASK> "<message>"
   ```

## Reviewer selection order and the pushed-head incumbent rule

Source: SUP-RECOVERY-CROSS-FAMILY-REVIEWER-20261120 (problem register RC4 WP4.3).
Live only after the supervisor runtime promotion.

`plan_task_assignment_pair` (ordinary dispatch, availability reassignment and
lost-lease recovery, owner and reviewer role) orders reviewer candidates as:

1. every candidate of a **different model family** than the candidate owner
   (`AGENT_FAMILIES`), in the configured order, that can take the task;
2. only when none of those can, a candidate of the owner's **own family**.

A same-family pick records `review_independence_exception` with one entry per
other family and a concrete, capped reason (`<agent>:<terminal reason>`,
`<agent>:cannot_take_task`, `available:<agent>` when an agent was able but the
pair was pinned, `no_agent_configured`, or `evidence_unavailable:<ErrorClass>`
when the evidence read failed). The supervisor never writes
`no capacity evidence supplied`. An explicit manual (human) reviewer gate is unchanged.

**Incumbent rule.** For lost-lease recovery with reason `worker_process_missing`,
if the task branch already has commits ahead of the base whose `LLM-Agent` and
`Reviewer` trailers (and `Task-ID`) name the current owner/reviewer pair, the
supervisor keeps that pair whenever the incumbent of the lost role can still
take the task, because any other pair would be refused by handoff trailer
admission. It moves to a fallback owner only when the incumbent owner cannot
take the task. `review_approved` closeout recovery still reuses only the
current pair, and trailer admission in `scripts/ai_status.py` is unchanged.
