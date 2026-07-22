# OPENCLAW-PERSONA-CRON-BACKFILL Acceptance Packet

**Sidecar kind:** `acceptance_packet`
**Sidecar task:** `OPENCLAW-PERSONA-CRON-BACKFILL-SIDECAR-ACCEPTANCE`
**Helper parent:** `OPENCLAW-PERSONA-CRON-BACKFILL`
**Prepared by:** `Codex2`
**Reviewer:** `Claude`
**Date:** `2026-07-04`
**Status:** `review_approved; finalized for parent-owner handoff`

> Scope constraint: support artifact only. This packet defines acceptance
> checks, dependencies, and handoff notes for the parent backfill task. It does
> not modify L1 canonical truth, OpenClaw runtime contracts, core BFF/runtime
> implementation, registry/governance behavior, or supervisor cadence.

## Executive Summary

`OPENCLAW-PERSONA-CRON-BACKFILL` should be accepted only when existing
persona agents that missed creation-time OODA cron registration are reconciled
through the live OpenClaw adapter path and the result is visible in
`cron.list`.

The parent should not be reduced to "the reconcile script exists." The script
is a helper for existing personas; acceptance needs live evidence that the
OpenClaw gateway contains the expected recurring jobs and that reruns are
idempotent.

The key dependency is still the OpenClaw adapter device scope from
`OPENCLAW-CRON-WRITE-SCOPE`: adapter-proxy `cron.add` requires the
Human/Ops-approved `operator.admin` device scope. If that scope has not been
granted, `cron.add` failing with pairing/scope-upgrade errors is an expected
environment blocker, not proof that the backfill logic is complete.

## Parent Acceptance Read

The parent task should satisfy the following before being marked complete:

| Criterion | Pass condition | Required evidence |
|---|---|---|
| Existing persona set is explicit | Parent names the persona ids it backfilled, or records discovery via `openclaw agents list --json` filtered to `persona-*` ids | Command output / evidence note with persona id list |
| Backfill uses the adapter path | `scripts/reconcile_persona_ooda_cron.py` runs with `PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL` set, or the parent explicitly documents why a host-side operator helper was used | Command line and env target |
| No dry-run acceptance | Reconcile output has `dry_run_personas=0` | Script totals line |
| Four workflow jobs per persona | For each persona, the gateway has jobs for `pantheon.ingest`, `pantheon.review`, `pantheon.retrain`, and `pantheon.deploy` | `cron.list` evidence with job names/metadata |
| Expected schedules preserved | Jobs use the `WORKFLOW_CATALOG` cron expressions: `0 */6 * * *`, `15 7 * * 1-5`, `0 2 * * 1-5`, `*/15 * * * *` | `cron.list` payload or parsed verification |
| Idempotent rerun | A second reconcile produces `failed=0` and skips already-present jobs instead of duplicating them | Second command output |
| Creation-time path remains aligned | A new `POST /bff/personas` smoke still returns `cron_registration_mode=gateway_rpc` and `cron_registered_count=4` when the adapter URL is configured | BFF response meta plus `cron.list` proof |
| Paper-only boundary preserved | Persona create/backfill evidence does not enable live capital, broker order routing, canary/live promotion, or direct execution side effects | BFF meta and/or OODA packet evidence showing paper-only state |
| Downstream OODA liveness is not overclaimed | If the parent claims OODA packets/loop-runs/evolution-programs are live, it includes readback evidence from the corresponding BFF surfaces; otherwise it limits the claim to cron backfill | BFF readback or explicit non-claim |

## Dependency Map

### Blocking / Live Dependencies

| Dependency | Current role | Acceptance impact |
|---|---|---|
| Human/Ops adapter device scope grant | `scripts/openclaw-approve-adapter-cron-scope.sh` grants the adapter device the `operator.admin` scope needed for `cron.add`, `cron.remove`, `cron.run`, and related write RPCs | Required before adapter-proxy backfill can create live jobs |
| Adapter cron write smoke | `scripts/openclaw-cron-write-scope-smoke.sh` proves `POST /api/openclaw-adapter/gateway/cron` can add, list, and remove a probe job | Should pass before parent backfill is treated as runnable |
| Running OpenClaw gateway and adapter | Adapter needs gateway URL/token and gateway reachability | Without this, reconcile may fall back to dry-run or fail |
| `PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL` | Selects `AdapterCronRuntime` in `PersonaCronRegistrar` | Required for the BFF/adapter route rather than local fallback |
| `openclaw-adapter-data` volume | Persists adapter device identity | Normal adapter container recreate should not require re-pairing |
| `openclaw-data` volume | Persists gateway-side device/scope state | A volume wipe may require rerunning the explicit Human/Ops approval helper |

### Code / Contract Surfaces

| Surface | Role |
|---|---|
| `services/control-plane/cron/persona_cron_registrar.py` | Owns `AdapterCronRuntime`, job naming, idempotent `cron.list` skip logic, and per-persona registration of all `WORKFLOW_CATALOG` jobs |
| `scripts/reconcile_persona_ooda_cron.py` | Backfills missing OODA cron jobs for existing personas by calling `PersonaCronRegistrar.reconcile_personas(...)` |
| `services/control-plane/cron/workflows.py` | Defines the four canonical workflow ids and schedules |
| `services/control-plane/bff/main.py` | Persona create route calls `_try_register_persona_cron(...)` and reports `cron_registration_mode` / `cron_registered_count` in response meta |
| `support/sidecars/OPENCLAW-CRON-WRITE-SCOPE/*` | Records the adapter write-scope dependency and the distinction between adapter-proxy writes and gateway-container-local writes |
| `.orchestrator/task-briefs/openclaw_persona_ooda_loop_wiring.md` | Upstream task brief for persona create -> OODA cron registration -> OpenClaw drive expectations |

## Suggested Verification Sequence

Run these from the dev/staging VM or an equivalent live environment. The
acceptance evidence should record exact commands and representative outputs.

1. Prove adapter-proxy write scope:

   ```bash
   OPENCLAW_GATEWAY_ADAPTER_URL=http://localhost:18104 \
     bash scripts/openclaw-cron-write-scope-smoke.sh
   ```

   If this reports `pairing required` or `scope upgrade pending`, Human/Ops
   must run:

   ```bash
   bash scripts/openclaw-approve-adapter-cron-scope.sh
   ```

   Then rerun the smoke.

2. Backfill explicit existing personas:

   ```bash
   PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL=http://127.0.0.1:18104 \
     python3 scripts/reconcile_persona_ooda_cron.py \
       --persona-id persona-...
   ```

   Or, for gateway-discovered personas:

   ```bash
   PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL=http://127.0.0.1:18104 \
     python3 scripts/reconcile_persona_ooda_cron.py \
       --agents-from-docker pantheon-openclaw-gateway-1
   ```

3. Confirm the totals:

   ```text
   Totals: personas=<N> registered=<R> skipped=<S> failed=0 dry_run_personas=0
   ```

   For acceptance, `R + S` should equal `4 * N`.

4. Rerun the same command and confirm idempotency: `failed=0`, no duplicate
   jobs, and already-present jobs are skipped.

5. Validate a fresh creation-time BFF path separately:

   ```bash
   curl -sS -X POST "$BFF_URL/bff/personas" \
     -H "Authorization: Bearer <operator-admin-token>" \
     -H "Content-Type: application/json" \
     -H "Idempotency-Key: openclaw-persona-cron-backfill-$(date +%s)" \
     -d '{"name":"Persona Cron Backfill Smoke","archetype":"generalist","risk":"low"}' | jq .
   ```

   Required meta: `cron_registration_mode=gateway_rpc`,
   `cron_registered_count=4`, `live_capital_side_effects=false`.

## Reviewer Checklist

Before approving this sidecar, confirm:

1. The packet stays support-only and does not claim the parent task is done.
2. The acceptance criteria require live adapter-proxy cron write evidence, not
   gateway-container-local writes or dry-run records.
3. The dependency map correctly carries forward the Human/Ops scope grant from
   `OPENCLAW-CRON-WRITE-SCOPE`.
4. The parent acceptance separates existing-persona reconcile from new-persona
   creation-time registration.
5. The paper-only/no-live-capital boundary remains explicit.

## Review And Closeout Record

**Reviewer approval:** `Claude` approved the packet in
`ai-status.json` with the finding that the acceptance criteria and dependency
map match the cron code and BFF meta fields, preserve the
`OPENCLAW-CRON-WRITE-SCOPE` `operator.admin` dependency, and remain
support-only.

**Review verification cited by reviewer:** `python3 -m pytest
services/control-plane/cron/test_persona_cron_registrar.py -q` reported
`19 passed`.

**Publication evidence:** PR #2966 merged into `dev` at
`30ed3b7e70d9b77cf88ac33dbc1d8b43f858209c`; GitHub reported required
`Commit trailers`, `Runtime mirror guard`, and `Smoke acceptance` checks as
successful.

**Owner closeout verification:** Codex2 re-read the task brief, reviewer
approval, and this packet, then reran the focused cron registrar unit suite
before final task closure: `python3 -m pytest
services/control-plane/cron/test_persona_cron_registrar.py -q` reported
`19 passed in 1.41s`.

**Residual parent dependency:** This sidecar remains an acceptance aid only.
The parent owner still needs live adapter-proxy cron write/backfill evidence
before closing `OPENCLAW-PERSONA-CRON-BACKFILL`.

## Non-Claims

This packet does not claim:

| Non-claim | Correct owner / proof |
|---|---|
| Existing personas have already been backfilled | Parent owner must run and attach live backfill evidence |
| Adapter device scope has already been granted in the target environment | Human/Ops / live smoke evidence |
| `OPENCLAW-PERSONA-CRON-BACKFILL` is accepted or complete | Parent owner and reviewer after live evidence |
| OODA packets, loop-runs, or evolution-programs are live solely because cron jobs exist | Parent must include BFF readback evidence if making that claim |
| Any L1 policy, runtime contract, registry, governance, or broker behavior changed | Out of scope for this sidecar |

## Handoff

**To:** `Claude`
**From:** `Codex2`
**Requested review outcome:** Approve this sidecar if it accurately captures
the parent acceptance checklist and dependency map without broadening the
sidecar into canonical/runtime implementation.

State note: this generated sidecar task id was not present in `ai-status.json`
in this worktree. This packet therefore avoids manual L0 state edits and keeps
the deliverable limited to the task brief plus this support artifact.
