# MGMT-OPS-003-GAP-002 BFF Handoff Follow-up 3

| Field | Value |
|---|---|
| Parent task | `MGMT-OPS-003-GAP-002` |
| Sidecar task | `MGMT-OPS-003-GAP-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-3` |
| Owner / reviewer | `Codex` / `Antigravity` |
| Helper kind | `bff_handoff_packet` |
| Date | `2026-07-11` |
| Delivery layer | support only |

This packet translates the latest authenticated hosted capture into a narrow
BFF/frontend compose handoff. It changes no canonical truth, BFF/runtime
implementation, schema, registry, governance, or frontend source. The parent
owner decides whether and how to absorb it.

## Hosted Delta

Parent branch anchor `6d83145c6` adds evidence under
`docs/deployment/evidence/mgmt-ops-003-gap/gap-002/20260711T114815Z/`.
That evidence is not yet merged into `dev`; as branch-scoped review material it
establishes deployment ancestry and HTTP 200 responses, but its cross-endpoint
truth is not safe to present as closure:

| Surface | Captured result | Required interpretation |
|---|---|---|
| Portfolio summary | 10 runtimes, 5 telemetry runtimes, `missing_binding_count: 0`, 5 degraded sources | Summary-level join health cannot erase holding-level identity gaps. |
| Holdings | 18 degraded holdings, 10 missing-binding holdings, 18 incidents | All unresolved rows remain visible and quarantined. |
| Performance attribution | `unassigned` has 10 holdings and `data_confidence: formal` | This conflicts with the holding-level missing-binding truth and must fail closed. |
| Hosted frontend | Telemetry Runtime 0; some `0/0` runtime rows shown as covered | UI counts and confidence do not match the authenticated BFF capture. |

The earlier baseline was 6 runtimes, 2 telemetry runtimes, 14 holdings, 10
missing-binding holdings, and 14 degraded holdings. The new capture has more
runtimes and telemetry coverage, but the unresolved missing-binding population
did not shrink. Increased totals are not reconciliation proof.

## BFF Compose Decision Required

The parent implementation should establish one fail-closed rule across the
Portfolio Book and Performance Attribution read models:

1. Holding-level missing identity or quarantine evidence must propagate into
   every aggregate containing that holding.
2. An aggregate containing unresolved required joins cannot be `formal`, even
   when runtime telemetry exists or summary-level joins report healthy.
3. Summary counters must name their population and join boundary. A zero for
   pool/runtime joins must not be rendered as zero holding identity gaps.
4. `unassigned` is missing attribution identity, not a valid persona identity.
   It must remain partial, degraded, or unavailable while the required persona
   or capital binding is absent.
5. Parent verification must compare the same captured row population across
   summary, holdings, incidents, and attribution, rather than approving each
   endpoint independently.

This packet does not prescribe a new field or route. The parent should first
reuse existing `source_status`, `source_issues`, incidents, and
`data_confidence`; any contract extension requires its own bounded review.

## Frontend Handoff

- Label counters by population, for example runtime-binding gaps versus
  holding identity gaps; do not collapse both into “missing bindings.”
- Never render `formal` for an aggregate if any included holding has a required
  missing binding, quarantine disposition, or unavailable telemetry join.
- Render `unassigned` as unresolved identity with an incident path, not as a
  successful persona bucket.
- Use BFF-provided truth only; do not repair the mismatch with client-side
  arithmetic or by hiding unresolved holdings.
- Keep the 18 degraded holdings and incidents reachable under filtering and
  pagination, and preserve holding/runtime/binding/incident context in Human
  Review drilldowns.
- After the composing frontend deploy, repeat desktop and mobile captures and
  compare displayed runtime, telemetry, missing-binding, degraded, incident,
  and confidence values with the same authenticated API snapshot.

## Parent And Reviewer Gate

The parent remains `REQUEST_CHANGES` until all of the following are true:

- the 10 missing-binding holdings have repaired, quarantined, or unchanged
  dispositions without disappearing;
- the `unassigned` attribution bucket is no longer formal while those required
  joins remain unresolved;
- summary and holdings counters explicitly describe different populations or
  reconcile to one consistent definition;
- hosted frontend counts and coverage labels match authenticated BFF truth;
- a repeated hosted capture records deployed backend and frontend SHAs,
  desktop/mobile evidence, zero required-request failures, and independent raw
  source sampling.

Antigravity should review this as a support-only handoff and confirm that it
identifies the cross-endpoint confidence contradiction without claiming a new
canonical contract or completed repair.

## Compose Handoff

The parent owner should compose this packet with the parent branch evidence at
`6d83145c6`, not copy its observations into a new sidecar-owned contract. The
minimum parent implementation/review seam is:

- BFF/runtime owner: make aggregate confidence inherit unresolved holding
  identity and telemetry truth, while retaining every affected row;
- frontend owner: render the resulting BFF confidence, population labels, and
  incident links without client-side reinterpretation;
- parent reviewer: compare summary, holdings, incidents, and attribution from
  one authenticated capture and one deployed SHA lineage;
- hosted-workflow owner: repeat the cross-page desktop/mobile journey only
  after the parent repair and frontend compose commits are deployed.

This sidecar does not authorize a repair mutation, data rewrite, schema field,
frontend implementation, deployment, or acceptance verdict. Those decisions
remain with the owning parent and downstream tasks.

## Focused Verification

```bash
git diff --check -- support/sidecars/MGMT-OPS-003-GAP-002/MGMT-OPS-003-GAP-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-3.md
git show 6d83145c6:docs/deployment/evidence/mgmt-ops-003-gap/gap-002/20260711T114815Z/hosted-summary.json | jq '{summary: .summary | {runtime_count, telemetry_runtime_count, missing_binding_count, degraded_source_count}}'
git show 6d83145c6:docs/deployment/evidence/mgmt-ops-003-gap/gap-002/20260711T114815Z/performance-attribution.json | jq '[.data.items[] | select(.dimension == "persona" and .dimension_key == "unassigned") | {holding_count, data_confidence, source_status}]'
```

`current-work.md` and the full `ai-activity-log.jsonl` were intentionally not
scanned; task-scoped context, prior sidecar packets, parent commits, and hosted
evidence were sufficient.
