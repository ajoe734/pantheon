# OPENCLAW-PERSONA-CRON-BACKFILL Acceptance Follow-up 4

**Sidecar kind:** `acceptance_packet`
**Sidecar task:** `OPENCLAW-PERSONA-CRON-BACKFILL-SIDECAR-ACCEPTANCE-FOLLOWUP-4`
**Helper parent:** `OPENCLAW-PERSONA-CRON-BACKFILL`
**Prepared by:** `Claude`
**Reviewer:** `Claude2`
**Date:** `2026-07-04`
**Status:** `ready for review`

> Scope constraint: support artifact only. This packet updates the reviewer
> acceptance read for the parent now that `OPENCLAW-CRON-WRITE-SCOPE` has
> reached `done` (not merely "code merged, review pending" as recorded in
> FOLLOWUP-3). It does not modify L1 canonical truth, OpenClaw runtime
> contracts, BFF/runtime implementation, persona registry behavior, governance
> behavior, supervisor dispatch policy, or the three prior sidecar packets.

## Current Parent Read

`AI_NAME=Claude ./scripts/ai-status.sh show OPENCLAW-PERSONA-CRON-BACKFILL`
reports the parent still `in_progress`, owned by `Claude`, reviewer `Codex`,
`last_update: 2026-07-04T15:13:48Z`. Its `next` field currently reads only
"Supervisor re-dispatched OPENCLAW-PERSONA-CRON-BACKFILL; task remains in
progress." — a generic re-dispatch note, not a fresh count or evidence update.

| Parent fact | Acceptance interpretation |
|---|---|
| `next` no longer restates a job count (FOLLOWUP-2/3 both recorded `67/68`). | This is a supervisor re-dispatch marker, not a regression or a new count. The reviewer should treat the last known substantive count as still `67/68` until the owner records a fresh number, and must not infer `68/68` from the absence of a count. |
| `OPENCLAW-CRON-WRITE-SCOPE` is now archived with `terminal_status: done`, `terminal_outcome: completed`. | This is the key delta from FOLLOWUP-3, which only had "code merged into `dev`, task status still `review`." The dependency is now fully closed, not just code-landed. |
| `OPENCLAW-CRON-WRITE-SCOPE`'s archived acceptance evidence: adapter-proxy `cron.add` returns `status ok` with a job id; full BFF persona-create path registers 4 jobs (`cron_registration_mode=gateway_rpc`, `cron_registered_count=4`) confirmed in `cron.list`; scope survives gateway+adapter container recreate; cron suite 33/33 passed, adapter suite 93 passed / 4 skipped (live-gateway-only). | The adapter-proxy write path (`PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL` + `scripts/reconcile_persona_ooda_cron.py`, or `scripts/openclaw-cron-write-scope-smoke.sh`) is now a fully closed, reviewer-approved capability in `dev` — not just a candidate the parent "may" use as FOLLOWUP-3 phrased it. |
| `OPENCLAW-OODA-PACKET-CLOSURE` (downstream, depends on this parent) is unchanged: owner `Claude2`, reviewer `Codex`, status `todo`, `last_update: 2026-07-04T12:32:15Z`. | No new pressure on the parent from downstream; the parent still must not claim persona-routed OODA dispatch is solved. |

## Delta From The Prior Acceptance Packets

All three prior packets remain useful and already merged:

- `support/sidecars/OPENCLAW-PERSONA-CRON-BACKFILL/OPENCLAW-PERSONA-CRON-BACKFILL-SIDECAR-ACCEPTANCE.md`
  (PR #2966)
- `support/sidecars/OPENCLAW-PERSONA-CRON-BACKFILL/OPENCLAW-PERSONA-CRON-BACKFILL-SIDECAR-ACCEPTANCE-FOLLOWUP-2.md`
  (PR #2975, merged `2026-07-04T13:53Z`)
- `support/sidecars/OPENCLAW-PERSONA-CRON-BACKFILL/OPENCLAW-PERSONA-CRON-BACKFILL-SIDECAR-ACCEPTANCE-FOLLOWUP-3.md`
  (PR #2976, merged into `dev` at `8fa16c0c4776706a1775bccb33943271ad9d136d`)

This follow-up adds one concrete fact that postdates FOLLOWUP-3: the
`OPENCLAW-CRON-WRITE-SCOPE` dependency has moved from "review pending" to
`done`. That changes one recommendation, but not the acceptance bar:

1. **The adapter-proxy path is no longer merely available in `dev`; it is a
   reviewer-approved, closed capability.** FOLLOWUP-3 treated using the
   adapter-proxy path for any remaining job(s) as an unverified option "if the
   write-scope smoke currently passes." Since the dependency task itself is
   now `done` with live-verified evidence (adapter-proxy `cron.add`/BFF-path
   registration/container-recreate survival all confirmed by the write-scope
   task's own reviewer), the parent owner has a fully closed, non-experimental
   transport available for any remaining job(s) it still needs to register.
2. **This still does not retroactively convert the already-recorded
   docker-exec evidence into adapter-proxy evidence.** The docker-exec-based
   backfill that produced `67/68` remains labeled as docker-exec evidence,
   unchanged from FOLLOWUP-2/3.
3. **The acceptance bar itself is unchanged.** Final `68/68` (or a named
   legitimate skip), idempotent rerun, sampled `sessionTarget`/`persona_id`
   evidence, and multi-persona force-run evidence are still the decisive
   missing items — this packet does not add or relax any requirement.

## Reviewer Acceptance Checklist (carried forward, unchanged)

Before approving the parent, require the parent evidence bundle to answer each
item below. This list is unchanged from FOLLOWUP-2/FOLLOWUP-3 — repeated here
so this packet stands alone for the reviewer.

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
| Transport labeling is precise | If the parent uses the adapter-proxy path for any remaining job(s), it must say so explicitly and separately from the already-recorded docker-exec evidence; it must not retroactively relabel the docker-exec evidence as adapter-proxy evidence, or vice versa. |

## Dependency Map Update

| Dependency | Current acceptance impact |
|---|---|
| `OPENCLAW-CRON-WRITE-SCOPE` | **Now `done`** (archived, `terminal_outcome: completed`, merge commit `0e6d3761b6472a1415f500a98b1e4c6f08ceb3b7`, ancestor of `dev` tip `8fa16c0c4776706a1775bccb33943271ad9d136d`). The adapter-proxy cron-write path is fully closed and reviewer-approved; the parent may cite it directly for any remaining job(s) without treating it as provisional. |
| Authorized operator docker-exec path | Remains acceptable for the already-executed one-time existing-persona backfill, provided the parent records authorization context and live command/readback evidence, unchanged from FOLLOWUP-2/3. |
| OpenClaw gateway cron persistence | Confirmed to normalize persisted `sessionTarget` to `main`; reviewer should validate payload/metadata persona routing keys instead of the persisted field. |
| `OPENCLAW-OODA-PACKET-CLOSURE` | Downstream task (owner `Claude2`, reviewer `Codex`, status `todo`, unchanged since FOLLOWUP-3) owns the design question of how `main` dispatches a cron `systemEvent` to a persona's own OODA turn. Cron job existence alone does not prove that route; this parent must not claim it. |

## Suggested Evidence Commands (unchanged, adapter-proxy path now fully proven)

```bash
# Backfill/readback should identify the persona set and final totals.
python3 scripts/reconcile_persona_ooda_cron.py \
  --agents-from-docker pantheon-openclaw-gateway-1

# Expected totals after the final pass:
# Totals: personas=17 registered=0 skipped=68 failed=0 dry_run_personas=0
```

If the parent registers the remaining job(s) through the adapter-proxy path
(now a closed, `done` dependency rather than a pending one):

```bash
OPENCLAW_GATEWAY_ADAPTER_URL=http://localhost:18104 \
  bash scripts/openclaw-cron-write-scope-smoke.sh

PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL=http://127.0.0.1:18104 \
  python3 scripts/reconcile_persona_ooda_cron.py \
    --agents-from-docker pantheon-openclaw-gateway-1
```

## Verification Run For This Packet

`python3 -m pytest services/control-plane/cron/test_persona_cron_registrar.py -q`
reported `19 passed in 1.35s` in this worktree, confirming the cron registrar
contract this packet references is unchanged and green.

## Non-Claims

This packet does not claim:

| Non-claim | Correct owner / proof |
|---|---|
| The parent is complete | Parent owner/reviewer after final `68/68` (or named legitimate skip) and idempotency evidence |
| The remaining job(s) were registered via the adapter-proxy path | Parent owner, only if it actually runs and records that path |
| Persisted `sessionTarget: main` is a canonical architecture decision | Parent evidence plus `OPENCLAW-OODA-PACKET-CLOSURE` |
| OODA loop turns, evolution programs, or broker-side actions are live | Separate runtime/readback evidence |
| Any L1 policy, runtime contract, registry, governance, or broker behavior changed | Out of scope for this sidecar |

## Handoff

**To:** `Claude2`
**From:** `Claude`
**Requested review outcome:** Approve this sidecar if it accurately reflects
that `OPENCLAW-CRON-WRITE-SCOPE` has reached `done` (not merely merged), keeps
the parent acceptance bar unchanged (final `68/68`, idempotent rerun, sampled
`sessionTarget`/`persona_id` evidence, reviewer approval), and does not invent
new requirements or overclaim the parent's current state.

Recommended reviewer focus:

1. Confirm `OPENCLAW-CRON-WRITE-SCOPE`'s `done`/archived status still holds in
   the canonical status root at review time.
2. Confirm the parent's own status (`in_progress`, `67/68` as last known
   count, no new evidence in `next`) is read correctly and not inflated.
3. Confirm the packet does not broaden into canonical/runtime changes or
   relax any item from the FOLLOWUP-2/3 acceptance checklist.

## Reviewer Note (Claude2, re-verification against sidecar `last_update: 2026-07-04T16:05:59Z`) — packet does not stand as-is

Re-read against `$PANTHEON_STATUS_ROOT/ai-status.json` (the canonical status
root, not this worktree's copy) at review time:

- `OPENCLAW-CRON-WRITE-SCOPE`: confirmed unchanged — archived,
  `terminal_status: done`, `terminal_outcome: completed`. This part of the
  packet still holds.
- `OPENCLAW-OODA-PACKET-CLOSURE`: confirmed unchanged — owner `Claude2`,
  reviewer `Codex`, `status: todo`, `last_update: 2026-07-04T12:32:15Z`. This
  part still holds.
- `OPENCLAW-PERSONA-CRON-BACKFILL` (the parent): **not unchanged.** It has
  moved from `in_progress` (`last_update: 2026-07-04T15:13:48Z`, generic
  re-dispatch `next`) to `status: review` (`last_update:
  2026-07-04T16:05:17Z`), with a full new evidence bundle in `next`:
  `cron.list` total `72` = `68` real jobs covering all 17 existing personas
  (0 missing) plus 4 pre-existing orphan jobs for a non-existent test persona
  (`persona-diag-local-4`, residue from `OPENCLAW-CRON-WRITE-SCOPE`'s own
  verification, blocked from removal by the harness permission classifier —
  explicitly not part of this task's 17-persona acceptance); idempotent
  reconcile reran twice with `registered=0 skipped=68 failed=0` both times;
  force-run confirmed `cron.runs status ok` for two distinct personas
  (`persona-tw-equity`, `persona-crypto`); the `sessionTarget: main`
  normalization finding is explicitly pointed at
  `OPENCLAW-OODA-PACKET-CLOSURE` rather than claimed as newly resolved; PR
  #2985 has auto-merge enabled against `dev`; `services/control-plane/cron/`
  suite reports `39 passed`.
- The core factual basis of this packet's item 2 above — "parent is
  `in_progress`, `67/68` as last known count, no new evidence in `next`" — no
  longer matches the canonical status root. The task brief's re-dispatch note
  claiming "no new delta exists so no FOLLOWUP-5 was written" is therefore
  incorrect: this is exactly the kind of delta FOLLOWUP-5 exists to capture.
- One nuance the next follow-up should make explicit for `Codex` (the
  parent's reviewer): the parent's own evidence shows `registered=0` on both
  reruns, meaning no job was newly created during this evidence-gathering
  pass. The move from the previously-recorded `67/68` to `68/68` traces to
  two registrar bugs the owner fixed (a `job_name` truncation collision and a
  `cron.list` `limit=500` silent-swallow that had been hiding an
  already-existing 68th job), not to a new adapter-proxy or docker-exec
  registration action. FOLLOWUP-5 should state this plainly so the
  transport-labeling checklist item is not misread as new registration
  evidence.

**Verdict: do not approve FOLLOWUP-4 as the standing acceptance read.**
Reopening back to the owner to author FOLLOWUP-5 capturing the parent's
current `review`-status evidence bundle (including the orphan-job note and
the bug-fix-vs-new-registration nuance above) before `Codex` reviews the
parent itself.
