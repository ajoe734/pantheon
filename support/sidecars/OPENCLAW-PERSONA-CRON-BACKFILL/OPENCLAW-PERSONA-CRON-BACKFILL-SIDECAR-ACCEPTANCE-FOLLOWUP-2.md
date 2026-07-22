# OPENCLAW-PERSONA-CRON-BACKFILL Acceptance Follow-up 2

**Sidecar kind:** `acceptance_packet`
**Sidecar task:** `OPENCLAW-PERSONA-CRON-BACKFILL-SIDECAR-ACCEPTANCE-FOLLOWUP-2`
**Helper parent:** `OPENCLAW-PERSONA-CRON-BACKFILL`
**Prepared by:** `Codex`
**Reviewer:** `Claude`
**Date:** `2026-07-04`
**Status:** `ready for review`

> Scope constraint: support artifact only. This packet updates the reviewer
> acceptance read for the parent after the parent reported live backfill
> progress. It does not modify L1 canonical truth, OpenClaw runtime contracts,
> BFF/runtime implementation, persona registry behavior, governance behavior,
> supervisor dispatch policy, or the prior sidecar packet.

## Current Parent Read

`AI_NAME=Codex ./scripts/ai-status.sh show
OPENCLAW-PERSONA-CRON-BACKFILL` reported the parent task active and
`in_progress` at `2026-07-04`, owned by `Claude` with reviewer `Codex`.

The parent's latest status says:

| Parent fact | Acceptance interpretation |
|---|---|
| Live backfill was executed by an authorized operator using the docker-exec full-scope path. | This can satisfy existing-persona backfill evidence if the parent records the exact operator path, command, and live outputs. It does not prove the adapter-proxy write-scope path. |
| `67/68` persona OODA cron jobs were registered when the note was written. | Parent is not complete until final `cron.list` / parsed evidence confirms `68/68`, or explicitly names a legitimate skip. |
| Force-run was reconfirmed with `cron.runs` status `ok` on multiple personas. | Reviewer should require job ids, persona ids, and run ids/statuses for at least two distinct personas. |
| OpenClaw gateway normalized persisted cron `sessionTarget` to `main` even when the submitted value was a persona id. | Acceptance should not require persisted `sessionTarget == persona_id`. The current correct reviewer check is that the cron payload/metadata still carries `persona_id` and that downstream persona routing is separately tracked by the OODA packet closure work. |
| Remaining parent work says "confirm final job count == 68 after backfill fully drains; reviewer approve." | The decisive missing evidence is final count/readback plus idempotent rerun, not more support-only packet drafting. |

## Delta From The Prior Acceptance Packet

The prior packet
`support/sidecars/OPENCLAW-PERSONA-CRON-BACKFILL/OPENCLAW-PERSONA-CRON-BACKFILL-SIDECAR-ACCEPTANCE.md`
remains useful and already merged through PR #2969. This follow-up narrows two
points for review:

1. **Transport evidence should be labeled precisely.** The parent can use the
   authorized host/operator docker-exec path for the one-time existing-persona
   backfill, but that evidence must not be reworded as proof that
   `PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL` / adapter-proxy writes are healthy.
   Adapter-proxy write scope remains owned by `OPENCLAW-CRON-WRITE-SCOPE`.
2. **`sessionTarget` acceptance must follow observed gateway behavior.** Code
   submits each persona id by default, but the live gateway reportedly stores
   `main`. Review should treat `main` as the observed gateway persistence
   behavior and require `persona_id` in the `systemEvent` payload and metadata
   instead of failing solely because `cron.get` returns `sessionTarget: main`.

## Reviewer Acceptance Checklist

Before approving the parent, require the parent evidence bundle to answer each
item below.

| Check | Required evidence |
|---|---|
| Persona inventory is explicit | The parent lists the persona ids discovered/backfilled, or includes `openclaw agents list --json` / parsed output filtered to `persona-*`. |
| Final expected count is closed | `cron.list` or a parsed verifier shows `68/68` jobs for the known persona set, or names the exact missing/legitimate-skip persona/workflow. |
| Four workflow jobs per persona | For every persona, jobs exist for `pantheon.ingest`, `pantheon.review`, `pantheon.retrain`, and `pantheon.deploy`. |
| Canonical schedules preserved | Jobs use the `WORKFLOW_CATALOG` schedules: `0 */6 * * *`, `15 7 * * 1-5`, `0 2 * * 1-5`, and `*/15 * * * *`. |
| Backfill was live, not dry-run | Reconcile totals show `failed=0` and `dry_run_personas=0`, or equivalent live command evidence when using the operator docker-exec route. |
| Rerun is idempotent | A second reconcile or verifier run shows no duplicate job creation and all existing jobs skipped/preserved. |
| `sessionTarget` is interpreted correctly | Sampled `cron.get` / `cron.list` records may show `sessionTarget: main`; the acceptance proof must instead show the submitted/system event payload and metadata include the correct `persona_id`. |
| Force-run evidence spans personas | At least two distinct persona jobs were force-run and `cron.runs` reports status `ok` with run ids. |
| Creation-time path is not overclaimed | If the parent claims new `POST /bff/personas` registration is healthy, it includes BFF response meta and adapter-proxy evidence separately; otherwise the claim is limited to existing-persona backfill. |
| Paper-only boundary holds | Evidence does not enable live capital, broker order routing, canary/live promotion, or direct execution side effects. |
| Downstream OODA routing is separated | If routed persona OODA turns are not proven, parent text should point to the downstream OODA packet closure/design item rather than claiming cron alone closes it. |

## Suggested Evidence Commands

These are reviewer-oriented examples. The parent owner should record the exact
commands and outputs from the live environment they actually used.

```bash
# Backfill/readback should identify the persona set and final totals.
python3 scripts/reconcile_persona_ooda_cron.py \
  --agents-from-docker pantheon-openclaw-gateway-1

# Expected totals after the final pass:
# Totals: personas=17 registered=0 skipped=68 failed=0 dry_run_personas=0
```

For `sessionTarget`, the reviewer should prefer a sampled readback note like:

```text
cron.add submitted sessionTarget=persona-crypto.
cron.get returned sessionTarget=main.
payload.text parsed as kind=pantheon.workflow.dispatch, persona_id=persona-crypto.
metadata.persona_id=persona-crypto.
```

That is a pass for this parent if the parent limits its claim to cron wakeup
and leaves main-agent-to-persona OODA dispatch to the downstream closure task.

## Dependency Map Update

| Dependency | Current acceptance impact |
|---|---|
| `OPENCLAW-CRON-WRITE-SCOPE` | Still required before adapter-proxy cron writes can be treated as proven. Parent evidence from docker-exec full-scope must not close this dependency by implication. |
| Authorized operator docker-exec path | Acceptable for the one-time existing-persona backfill only when the parent records authorization context and live command/readback evidence. |
| OpenClaw gateway cron persistence | Now known, per parent status, to normalize persisted `sessionTarget` to `main`; reviewer should validate payload/metadata persona routing keys. |
| OODA packet closure / persona dispatch design | Owns the remaining question of how `main` dispatches a cron `systemEvent` to the persona's own OODA turn. Cron job existence alone does not prove that route. |

## Non-Claims

This packet does not claim:

| Non-claim | Correct owner / proof |
|---|---|
| The parent is complete | Parent owner/reviewer after final `68/68` and idempotency evidence |
| Adapter-proxy `cron.add` is proven in the target environment | `OPENCLAW-CRON-WRITE-SCOPE` and live adapter smoke evidence |
| Persisted `sessionTarget: main` is a canonical architecture decision | Parent evidence plus downstream OODA closure/design work |
| OODA loop turns, evolution programs, or broker-side actions are live | Separate runtime/readback evidence |
| Any L1 policy, runtime contract, registry, governance, or broker behavior changed | Out of scope for this sidecar |

## Handoff

**To:** `Claude`
**From:** `Codex`
**Requested review outcome:** Approve this sidecar if it accurately updates
the parent acceptance checklist after the live backfill note, preserves the
support-only boundary, and prevents the reviewer from rejecting valid live
evidence solely because OpenClaw persists cron `sessionTarget` as `main`.

Recommended reviewer focus:

1. Confirm the parent status note still matches the current status root.
2. Confirm the packet does not broaden into canonical/runtime changes.
3. Confirm the parent cannot close until the final `68/68` count, idempotent
   rerun, and sampled payload/metadata persona evidence are attached.
