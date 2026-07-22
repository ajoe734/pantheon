# OCLAW-PMEM-002 BFF / Frontend Handoff Follow-up 5

Status: support-only post-merge gap packet; not canonical truth or runtime proof
Parent task: `OCLAW-PMEM-002`
Sidecar task: `OCLAW-PMEM-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-5`
Owner: Codex2
Reviewer: Codex

## Purpose And Boundary

This packet audits the parent implementation now present on `origin/dev` and
identifies the remaining BFF/frontend handoff needed to satisfy the parent
acceptance honestly. It does not modify the reconciler, BFF, frontend, Persona
Registry, Memory Plane, governance, or canonical contracts. The parent owner may
adopt, amend, or reject it.

The audited parent implementation is commit `4ebd260a5` (`OCLAW-PMEM-002:
reconcile OpenClaw persona agents from runtime profile`), which is contained in
`origin/dev` in this task workspace.

## What The Parent Implementation Now Delivers

| Layer | Verified behavior | Honest claim |
|---|---|---|
| Runtime profile | `desired_agent_spec()` resolves model, workspace, SOUL, and `sync_generation` through `build_persona_runtime_profile()` | Desired agent configuration is consistently derived |
| New agent reconcile | Missing agents are created with workspace and model; SOUL is written | A batch sync attempt can create an agent |
| Existing agent reconcile | Identity and SOUL are updated; matching models proceed | Existing identity/SOUL can converge when model already matches |
| Model drift | A mismatch returns `model_drift_update_unavailable` with current/desired model and repair action | Drift is detected and blocked precisely; it is not repaired |
| Memory hook | An optional materializer runs after create/update and reports `memory_materialized` | Materialization can be invoked; freshness is not proven by agent readiness |
| BFF mutation handoff | Persona create/update persists `metadata.openclaw_agent_reconcile` as `pending` or `blocked` | Desired reconcile intent is recorded |
| Script parity | Deploy script imports the shared SOUL renderer when available | Renderer drift is reduced |

These are useful implementation facts, but none by itself proves a reachable
`openclaw/{persona_id}` agent for the current requested generation.

## Remaining Acceptance Gap

The current `SyncReport` is process-local and aggregate. It lists ids in
`created`, `updated`, `unchanged`, `memory_materialized`, and failure dictionaries,
but does not persist an attempt identity, acknowledgement, requested generation,
observed agent snapshot, probe result, or completion evidence. The BFF request
metadata likewise has no durable consumer readback.

Consequently the current surfaces cannot answer these operator questions:

1. Was this specific create/update request consumed?
2. Which attempt and generation are currently authoritative?
3. Did OpenClaw report the expected identity, workspace, and model after the
   mutation?
4. Did a request through `model=openclaw/{persona_id}` succeed?
5. Did a late older attempt lose to a newer generation?
6. Is memory unavailable, stale, or merely empty?

The parent acceptance phrase “reachable agent or a failed reconcile reason” is
therefore not yet queryable through a governed BFF surface. A `pending` metadata
record is command intent, and membership in a one-shot `SyncReport.created` or
`updated` list is mutation output; neither is observed readiness.

## Smallest Parent-Owned Closure Slice

The next parent slice should persist one persona-scoped reconcile result keyed
by `persona_id + sync_generation` and expose it through a governed BFF query.
Route naming remains the parent's choice; a dedicated
`GET /bff/personas/{persona_id}/openclaw-reconcile` remains the least ambiguous
option.

Minimum persisted fields:

```text
attempt_id, persona_id, requested_generation, lifecycle
requested_at, acknowledged_at, completed_at
desired_model, desired_workspace
observed_model, observed_workspace, observed_generation, observed_at
reachable, probe_evidence_refs[]
reason_code, retryable, repair_action
last_success_generation, last_success_observed_at
```

Minimum ordering rules:

- persist the attempt before acknowledging the handoff;
- enter `reconciling` only after consumer acknowledgement;
- read the agent back and complete a sanitized reachability probe before
  `ready`;
- compare generations before committing completion, so generation N cannot
  replace N+1;
- retain an older success as historical context only;
- return `unavailable` when the result store cannot be read, without falling
  back to desired metadata; and
- convert raw CLI/stdout/stderr and exceptions into stable redacted reason
  codes before BFF projection.

The current `model_drift_update_unavailable` result should remain `blocked`
until the parent either adds a supported model-update operation or explicitly
implements governed recreate semantics. The frontend must not present its
repair action as an automatic retry.

## BFF Handoff Delta

The BFF projection should keep four truth rows independent:

| Truth row | Source owner | Fail-closed UI behavior |
|---|---|---|
| Desired runtime profile | Persona/runtime-profile owner | Label as desired; never enable conversation |
| Observed agent reconcile | Durable reconcile result owner | Enable conversation only for current-generation `ready` plus successful probe |
| Provider readiness | Provider readiness owner | Show provider repair separately; never substitute for agent probe |
| Canonical memory/materialization | Memory Plane and bridge owners | Distinguish empty, stale, degraded, and unavailable independently |

The query needs current desired and observed values, lifecycle, drift keys,
attempt timestamps, generation, redacted reason, retryability, repair action,
evidence refs, `snapshot_at`, and a truth level. Unknown lifecycle values and
read failures must map to unavailable/degraded behavior.

Focused BFF tests should prove:

- `pending` persona metadata cannot project as observed `ready`;
- current-generation probe evidence is required for `ready`;
- desired and observed model/workspace remain visible during drift;
- late completion cannot regress the authoritative generation;
- blocked model drift returns stable safe guidance;
- unavailable result storage does not become an empty-success response; and
- response serialization excludes provider secrets, raw CLI output, and
  exception text.

## `execute-plans` Handoff Delta

The frontend repository remains `ajoe734/execute-plans`; no frontend source is
part of this packet. Its implementation should:

- present persona mutation success separately from agent reconcile state;
- consume the governed BFF projection rather than persona metadata or OpenClaw
  directly;
- disable conversation for queued, reconciling, drifted, blocked, failed,
  unavailable, and unknown states;
- enable conversation only for current-generation observed `ready`;
- retain the prior success with a “previous generation” label while a newer
  attempt runs or fails;
- discard stale poll/event responses using generation and snapshot time;
- expose Retry only when `retryable=true`, with an idempotency key; and
- render provider, agent, and memory problems as distinct operator actions.

## Parent Composition Gate

Before the parent claims full acceptance, reviewer evidence should include:

- [ ] durable attempt and result readback for one create or update;
- [ ] consumer acknowledgement and current-generation ordering proof;
- [ ] observed agent identity/model/workspace/generation after reconcile;
- [ ] a sanitized successful response through
      `model=openclaw/{persona_id}`;
- [ ] model-drift blocked or repaired behavior with a safe action;
- [ ] duplicate delivery and late-generation tests;
- [ ] BFF unavailable/unknown/secret-redaction contract tests; and
- [ ] frontend evidence that all non-ready states keep conversation disabled.

Until those checks pass, the parent can accurately claim desired-state
resolution, agent create/update attempts, shared SOUL rendering, drift
detection, and an optional memory hook. It should record durable consumption,
observed readiness, governed queryability, and hosted reachability as residual
gaps.

## Ownership And Handoff

- `OCLAW-PMEM-002` owns adoption, durable lifecycle, ordering, probe, result
  taxonomy, and hosted evidence.
- The BFF owner owns the governed fail-closed projection.
- The `execute-plans` owner owns operator rendering and stale-response defense.
- Memory/provider owners retain their separate truth surfaces.
- Reviewer `Codex` should reject any composition that promotes desired intent,
  batch membership, or provider readiness into observed agent readiness.

No canonical truth or primary runtime/registry/governance implementation is
changed by this follow-up.
