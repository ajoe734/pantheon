# MGMT-OPS-003-GAP-002 BFF Handoff Follow-up 5

| Field | Value |
|---|---|
| Parent task | `MGMT-OPS-003-GAP-002` |
| Sidecar task | `MGMT-OPS-003-GAP-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-5` |
| Owner / reviewer | `Codex` / `Antigravity` |
| Helper kind | `bff_handoff_packet` |
| Date | `2026-07-11` |
| Scope | Support-only compose readiness |

This packet is a narrow readiness update for the parent BFF and separate
`execute-plans` frontend lanes. It does not change canonical truth, contracts,
BFF/runtime code, reconciliation behavior, frontend code, registry/governance,
deployment configuration, or hosted data. The parent owner decides whether to
absorb it.

## Current Delivery Truth

No newer parent implementation or hosted-evidence commit was found after parent
anchor `6d83145c6`. The parent checkpoint remains a read-only reconciliation
proposal: it drives from runtime bindings, retains unresolved joins, proposes a
repair only when authoritative sources agree, quarantines conflicts, and writes
an append-only snapshot audit. It does not apply authoritative repairs.

The latest hosted capture therefore remains evidence of an open compose gap:

- 10 runtimes and 5 telemetry runtimes are reported at summary level;
- 18 holdings remain degraded, including 10 missing-binding holdings;
- the persona attribution bucket `unassigned` contains those 10 holdings but is
  reported as `formal`;
- hosted UI counts and some coverage labels do not match the authenticated BFF
  population.

More runtimes or telemetry rows do not prove reconciliation when the unresolved
holding population remains unchanged. Follow-up 3 records the cross-endpoint
contradiction; Follow-up 4 records the complete query, operator-journey, and
evidence handoff. This packet does not replace either one.

## BFF Compose Checklist

Before frontend composition, the parent BFF lane must supply one deployed,
authenticated snapshot in which:

1. summary, holdings, incidents, positions, and attribution refer to a stated
   population and compatible observation window;
2. every baseline unresolved row is classified as healthy,
   repaired-and-reverified, or quarantined, without disappearing;
3. holding-level missing persona/capital, runtime, broker/ledger, or telemetry
   truth propagates to aggregate confidence;
4. `unassigned` is treated as unresolved identity and cannot be formal while a
   required join is missing;
5. proposed repair, authoritative write, and fresh reconciliation are distinct
   states; a proposal alone never upgrades confidence;
6. the exact supported filter names and projection fields are identified from
   the delivered OpenAPI/BFF implementation rather than invented by this
   sidecar.

If quarantine reason, audit/evidence references, or repair state cannot be
projected through the existing Portfolio Book route family, the parent must
record that as an explicit contract gap or implement a governed projection in
its own scope. The frontend must not infer those facts from unrelated responses.

## Frontend Compose Checklist

The frontend lane should compose only after the BFF snapshot above is available:

- call Pantheon BFF routes only; never query service stores directly;
- preserve unknown/null identity and source values without fixture or
  neighboring-row defaults;
- render BFF-owned confidence, issue, incident, deployment-stage, capital-scope,
  and link facts without client-side confidence upgrades;
- keep quarantined and missing-binding rows visible under pagination and
  filtering, with the active filter reflected in the URL and count labels;
- preserve holding, runtime, pool, identity, source timestamp, and incident
  context across Portfolio Book, Persona Fleet, Performance Attribution, and
  Human Review navigation;
- in strict live mode, render missing required data as degraded or unavailable,
  never seed/mock success;
- compare desktop and mobile labels and counts against the same authenticated
  BFF capture and deployed SHA lineage.

No frontend source belongs in this Pantheon worktree. Delivery remains in
`ajoe734/execute-plans` through its own task branch and PR.

## Operator Journey Acceptance Seam

The parent and frontend owners should hand the hosted-workflow owner one
repeatable journey:

1. open Portfolio Book and observe runtime/telemetry coverage before filtering;
2. filter to missing, stale, or quarantined rows and reload with URL state
   preserved;
3. open one unresolved holding and retain all known identity/source context;
4. navigate to attribution and verify it is non-formal for that population;
5. navigate to Human Review with the same runtime, holding, issue, evidence,
   and observation context;
6. return to Portfolio Book without silently losing the selected population;
7. repeat after repair deployment and accept a changed status only when a fresh
   authoritative reconciliation accounts for the original row.

The evidence bundle must include Pantheon PR/head/merge/deployed SHA identity,
capture timestamp and sanitized auth shape, before/after population counts, raw
source samples, snapshot/idempotency/audit references, authenticated BFF
payloads, desktop/mobile captures, and zero required-request, console, lazy
chunk, or fallback-data failures.

## Handoff And Review Gate

Antigravity should review only whether this support packet accurately preserves
the existing fail-closed boundary and gives the parent/frontend lanes a usable
compose checklist. Approval of this sidecar does not approve the parent repair,
the frontend delivery, deployment, hosted evidence, or `MGMT-OPS-003-GAP-002`
acceptance.

The parent remains open until its implementation is merged to `dev`, that merge
is deployed, authenticated before/after evidence accounts for every baseline
row, attribution fails closed for unresolved joins, and an independent reviewer
samples the raw runtime/binding/telemetry sources.

## Focused Verification

```bash
AI_NAME=Codex ./scripts/ai-status.sh show MGMT-OPS-003-GAP-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-5
git diff --check -- support/sidecars/MGMT-OPS-003-GAP-002/MGMT-OPS-003-GAP-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-5.md
git log --all --since='2026-07-11 11:00:00' --oneline -- docs/bff/execution-tasks/2026-07-11-mgmt-ops-003-hosted-gap docs/deployment/evidence/mgmt-ops-003-gap support/sidecars/MGMT-OPS-003-GAP-002
```

`current-work.md` and the full `ai-activity-log.jsonl` were intentionally not
scanned; the task brief, live task entry, parent checkpoint, prior sidecar
packets, and task-scoped commit history were sufficient.
