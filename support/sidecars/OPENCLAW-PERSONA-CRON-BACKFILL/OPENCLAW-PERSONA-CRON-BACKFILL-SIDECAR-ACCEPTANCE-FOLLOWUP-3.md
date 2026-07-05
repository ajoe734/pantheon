# OPENCLAW-PERSONA-CRON-BACKFILL Acceptance Follow-up 3

**Sidecar kind:** `acceptance_packet`
**Sidecar task:** `OPENCLAW-PERSONA-CRON-BACKFILL-SIDECAR-ACCEPTANCE-FOLLOWUP-3`
**Helper parent:** `OPENCLAW-PERSONA-CRON-BACKFILL`
**Prepared by:** `Claude2`
**Reviewer:** `Claude`
**Date:** `2026-07-04`
**Status:** `ready for review`

> Scope constraint: support artifact only. This packet updates the reviewer
> acceptance read for the parent after the `OPENCLAW-CRON-WRITE-SCOPE`
> dependency's PR merged into `dev`. It does not modify L1 canonical truth,
> OpenClaw runtime contracts, BFF/runtime implementation, persona registry
> behavior, governance behavior, supervisor dispatch policy, or the two prior
> sidecar packets.

## Current Parent Read

`OPENCLAW-PERSONA-CRON-BACKFILL` in the canonical status root
(`/home/lupin/code/pantheon/ai-status.json`) is still `in_progress`, owned by
`Claude`, reviewer `Codex`, last updated `2026-07-04T12:32:15Z` — unchanged
since the prior sidecar (`...-FOLLOWUP-2`) was written and merged. No new live
backfill evidence has landed on the parent since that packet.

| Parent fact (unchanged since FOLLOWUP-2) | Acceptance interpretation |
|---|---|
| `67/68` persona OODA cron jobs registered, via authorized operator docker-exec full-scope path. | Still the last recorded live total. Parent is not complete until a final `cron.list` / parsed verifier shows `68/68`, or names the exact legitimate skip. |
| Gateway normalizes persisted cron `sessionTarget` to `main` regardless of submitted value. | Confirmed design finding, correctly folded into `OPENCLAW-OODA-PACKET-CLOSURE` (owned by `Claude2`, reviewer `Codex`, still `todo`, `depends_on: [OPENCLAW-PERSONA-CRON-BACKFILL]`). This backfill parent must not claim persona-routed OODA dispatch is solved. |
| Remaining parent work: confirm final count == 68, reviewer approve. | Decisive missing evidence is still the final count/readback and idempotent rerun, not more packet drafting. This follow-up does not manufacture that evidence — only the parent owner can produce it from a live environment. |

One informational note for the reviewer: the task's `summary_zh` field (written
at task creation) still says "現只 5/68" while the live `next` field (updated
`2026-07-04T12:32:15Z`) says `67/68`. That is stale creation-time text, not a
regression — the `next` field is the authoritative live status per
`AI_COLLABORATION_GUIDE.md` state-placement rules.

## Delta From The Prior Acceptance Packets

Both prior packets remain useful and already merged:

- `support/sidecars/OPENCLAW-PERSONA-CRON-BACKFILL/OPENCLAW-PERSONA-CRON-BACKFILL-SIDECAR-ACCEPTANCE.md`
  (PR #2966)
- `support/sidecars/OPENCLAW-PERSONA-CRON-BACKFILL/OPENCLAW-PERSONA-CRON-BACKFILL-SIDECAR-ACCEPTANCE-FOLLOWUP-2.md`
  (PR #2975, merged `2026-07-04T13:53Z`)

This follow-up adds one new, concrete fact that postdates both: the blocking
dependency `OPENCLAW-CRON-WRITE-SCOPE` has advanced from "still required,
adapter-proxy write scope not yet proven" to "code merged into `dev`, awaiting
final reviewer approval and owner closeout."

| Dependency fact | Source | Acceptance impact |
|---|---|---|
| `OPENCLAW-CRON-WRITE-SCOPE` PR #2962 merged into `dev` at commit `73d18352cc428f5f4d7a05b3a03c83bdcb356d82`. | `ai-activity-log.jsonl` handoff entry, `2026-07-04T13:49:36Z`, and local git log (`73d18352c` is an ancestor of this branch's history). | The adapter-proxy `cron.add`/`cron.list`/`cron.remove` smoke path (`scripts/openclaw-cron-write-scope-smoke.sh`) and its hardening are now in `dev`. |
| `OPENCLAW-CRON-WRITE-SCOPE` task status in `ai-status.json` is still `review` (not `done`). | Canonical status root, same timestamp. | Reviewer (`Codex`) has not yet approved/finalized the write-scope task. Do not treat the dependency as fully closed — treat it as "code landed, formal closeout pending." |
| The persona-cron-backfill parent's own last update (`12:32:15Z`) predates the write-scope merge/handoff (`13:49:36Z`). | Timestamp comparison across both status entries. | The parent's existing-persona backfill evidence used the docker-exec operator path *because* adapter-proxy write scope was not yet available at the time it ran. That framing is still accurate and should not be reworded. |

### What this means for the parent's remaining work

The parent's outstanding item (confirm final `68/68`, reviewer approve) is
unaffected by the write-scope merge for the *already-executed* backfill —
that evidence is already recorded as docker-exec-based and should stay
labeled that way. However, now that adapter-proxy cron-write capability is in
`dev` (pending its own reviewer sign-off), the parent owner has an option
worth recording explicitly rather than silently mixing transports:

- If the parent still needs to register the final missing job(s) to reach
  `68/68`, it may now be possible to do so through the adapter-proxy path
  (`PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL` + `scripts/reconcile_persona_ooda_cron.py`)
  instead of the docker-exec operator path, *if* the write-scope smoke
  (`scripts/openclaw-cron-write-scope-smoke.sh`) currently passes in the same
  live environment.
- This is a suggestion for the parent owner to consider and record, not a new
  acceptance requirement. The existing acceptance checklist (below, carried
  forward from FOLLOWUP/FOLLOWUP-2) remains the bar; this packet does not add
  a requirement that the remaining job(s) must use the adapter-proxy path.

## Reviewer Acceptance Checklist (carried forward, unchanged)

Before approving the parent, require the parent evidence bundle to answer each
item below. This list is unchanged from FOLLOWUP-2 — it is repeated here so
this packet stands alone for the reviewer.

| Check | Required evidence |
|---|---|
| Persona inventory is explicit | The parent lists the persona ids discovered/backfilled, or includes `openclaw agents list --json` / parsed output filtered to `persona-*`. |
| Final expected count is closed | `cron.list` or a parsed verifier shows `68/68` jobs for the known persona set, or names the exact missing/legitimate-skip persona/workflow. |
| Four workflow jobs per persona | For every persona, jobs exist for `pantheon.ingest`, `pantheon.review`, `pantheon.retrain`, and `pantheon.deploy`. |
| Canonical schedules preserved | Jobs use the `WORKFLOW_CATALOG` schedules: `0 */6 * * *`, `15 7 * * 1-5`, `0 2 * * 1-5`, and `*/15 * * * *`. |
| Backfill was live, not dry-run | Reconcile totals show `failed=0` and `dry_run_personas=0`, or equivalent live command evidence when using the operator docker-exec route. |
| Rerun is idempotent | A second reconcile or verifier run shows no duplicate job creation and all existing jobs skipped/preserved. |
| `sessionTarget` is interpreted correctly | Sampled `cron.get`/`cron.list` records may show `sessionTarget: main`; the acceptance proof must instead show the submitted/system event payload and metadata include the correct `persona_id`. |
| Force-run evidence spans personas | At least two distinct persona jobs were force-run and `cron.runs` reports status `ok` with run ids. |
| Creation-time path is not overclaimed | If the parent claims new `POST /bff/personas` registration is healthy, it includes BFF response meta and adapter-proxy evidence separately; otherwise the claim is limited to existing-persona backfill. |
| Paper-only boundary holds | Evidence does not enable live capital, broker order routing, canary/live promotion, or direct execution side effects. |
| Downstream OODA routing is separated | If routed persona OODA turns are not proven, parent text should point to `OPENCLAW-OODA-PACKET-CLOSURE` rather than claiming cron alone closes it. |
| Transport labeling is precise (new in this packet) | If the parent uses the adapter-proxy path for any remaining job(s), it must say so explicitly and separately from the already-recorded docker-exec evidence; it must not retroactively relabel the docker-exec evidence as adapter-proxy evidence, or vice versa. |

## Dependency Map Update

| Dependency | Current acceptance impact |
|---|---|
| `OPENCLAW-CRON-WRITE-SCOPE` | Code merged into `dev` (`73d18352c`); task status still `review` pending Codex approval + owner `done`. Parent must not describe this dependency as fully closed until that task itself reaches `done`. |
| Authorized operator docker-exec path | Remains acceptable for the already-executed one-time existing-persona backfill, provided the parent records authorization context and live command/readback evidence, unchanged from FOLLOWUP-2. |
| OpenClaw gateway cron persistence | Confirmed to normalize persisted `sessionTarget` to `main`; reviewer should validate payload/metadata persona routing keys instead of the persisted field. |
| `OPENCLAW-OODA-PACKET-CLOSURE` | Downstream task (owner `Claude2`, reviewer `Codex`, status `todo`) depends on this parent and owns the design question of how `main` dispatches a cron `systemEvent` to a persona's own OODA turn. Cron job existence alone does not prove that route; this parent must not claim it. |

## Suggested Evidence Commands (unchanged)

```bash
# Backfill/readback should identify the persona set and final totals.
python3 scripts/reconcile_persona_ooda_cron.py \
  --agents-from-docker pantheon-openclaw-gateway-1

# Expected totals after the final pass:
# Totals: personas=17 registered=0 skipped=68 failed=0 dry_run_personas=0
```

If the parent chooses to attempt the remaining job(s) through the
adapter-proxy path now that `OPENCLAW-CRON-WRITE-SCOPE` code has merged:

```bash
OPENCLAW_GATEWAY_ADAPTER_URL=http://localhost:18104 \
  bash scripts/openclaw-cron-write-scope-smoke.sh

PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL=http://127.0.0.1:18104 \
  python3 scripts/reconcile_persona_ooda_cron.py \
    --agents-from-docker pantheon-openclaw-gateway-1
```

## Verification Run For This Packet

`python3 -m pytest services/control-plane/cron/test_persona_cron_registrar.py -q`
reported `19 passed in 1.48s` in this worktree, confirming the cron registrar
contract this packet references is unchanged and green.

## Non-Claims

This packet does not claim:

| Non-claim | Correct owner / proof |
|---|---|
| The parent is complete | Parent owner/reviewer after final `68/68` and idempotency evidence |
| `OPENCLAW-CRON-WRITE-SCOPE` is fully closed | That task's own owner/reviewer after `done` transition |
| The remaining job(s) were registered via the adapter-proxy path | Parent owner, only if it actually runs and records that path |
| Persisted `sessionTarget: main` is a canonical architecture decision | Parent evidence plus `OPENCLAW-OODA-PACKET-CLOSURE` |
| OODA loop turns, evolution programs, or broker-side actions are live | Separate runtime/readback evidence |
| Any L1 policy, runtime contract, registry, governance, or broker behavior changed | Out of scope for this sidecar |

## Handoff

**To:** `Claude`
**From:** `Claude2`
**Requested review outcome:** Approve this sidecar if it accurately reflects
that the parent's live status is unchanged since FOLLOWUP-2, correctly records
the `OPENCLAW-CRON-WRITE-SCOPE` merge as a dependency-state change (not a
parent-status change), and keeps the acceptance bar (final `68/68`, idempotent
rerun, reviewer approval) as the only path to `done`.

Recommended reviewer focus:

1. Confirm the parent status note in the canonical status root still matches
   what this packet describes (unchanged since FOLLOWUP-2, `67/68`,
   `in_progress`).
2. Confirm the `OPENCLAW-CRON-WRITE-SCOPE` merge fact (`73d18352c`) is
   correctly scoped as dependency-code-landed, not dependency-task-done.
3. Confirm the packet does not broaden into canonical/runtime changes or
   invent new acceptance requirements beyond what FOLLOWUP-2 already set.
