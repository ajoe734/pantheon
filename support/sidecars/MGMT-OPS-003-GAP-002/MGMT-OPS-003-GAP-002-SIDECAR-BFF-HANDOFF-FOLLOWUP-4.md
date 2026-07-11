# MGMT-OPS-003-GAP-002 Sidecar BFF / Frontend Handoff Follow-Up 4

Date: 2026-07-11
Owner: Codex
Reviewer: Antigravity
Parent task: `MGMT-OPS-003-GAP-002`
Sidecar task: `MGMT-OPS-003-GAP-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-4`
Helper kind: `bff_handoff_packet`
Scope: support-only handoff packet. This packet does not change canonical
truth, BFF or runtime implementation, reconciliation behavior, frontend code,
registry/governance behavior, deployment configuration, or live data.

## Purpose

Give the parent owner and the separate `execute-plans` frontend lane one
fail-closed integration checklist for the runtime-binding and telemetry repair.
The packet translates the observed hosted gaps into BFF query expectations,
operator-visible states, context-preserving navigation, and evidence fields.
It does not declare that the parent repair is merged, deployed, or accepted.

## Source Snapshot

| Source | Handoff use |
|---|---|
| `docs/04/pantheon_mgmt_ops_003_hosted_gap_2026-07-11/MGMT_OPS_003_HOSTED_GAP.md` | Hosted baseline: 19 pools, 6 runtimes, 2 telemetry runtimes, 14 holdings, 14 degraded holdings, 10 missing-binding holdings, and 14 incidents. |
| `docs/bff/execution-tasks/2026-07-11-mgmt-ops-003-hosted-gap/MGMT-OPS-003-GAP-002-runtime-data-quality.md` | Parent acceptance and current read-only reconciliation checkpoint. |
| `docs/bff/execution-tasks/2026-07-11-mgmt-ops-003-hosted-gap/MGMT-OPS-003-GAP-001-frontend-monitor.md` | Frontend display, filter, capital-scope, and context-preservation requirements. |
| `docs/bff/execution-tasks/2026-07-11-mgmt-ops-003-hosted-gap/REVIEWER_CHECKLIST.md` | Fail-closed hosted evidence and independent raw-source sampling gate. |
| `services/control-plane/bff/main.py` and `test_bff_pm12_portfolio_book_contract.py` | Existing Portfolio Book route family and contract-test surface; read for handoff only. |

The parent checkpoint describes a read-only reconciler that drives from runtime
bindings, proposes repairs only when authoritative sources agree, quarantines
conflicts, and keys audit entries by snapshot hash. Authenticated before/after
hosted counts and deployed-SHA proof remain explicitly outstanding.

## BFF Query Handoff

The browser must call the Pantheon BFF only. Runtime-manager, persona,
telemetry, broker, and ledger stores remain server-side sources and must not be
queried directly by `execute-plans`.

| Operator need | Existing BFF read | Required handoff behavior |
|---|---|---|
| Portfolio summary | `GET /bff/management/portfolio-book` | Preserve runtime and telemetry coverage diagnostics; a partial join must remain degraded. |
| Pool rollup | `GET /bff/management/portfolio-book/pools` | Preserve pool identity and capital scope; unknown scope must stay explicit. |
| Risk exposure | `GET /bff/management/portfolio-book/exposure` | Never convert missing runtime, persona, pool, broker, ledger, or telemetry truth into covered exposure. |
| Holdings and incidents | `GET /bff/management/portfolio-book/holdings` | Return every driving row, including quarantined and missing-binding rows, with source issues, confidence, risk state, incident, and governed links. |
| Positions | `GET /bff/management/portfolio-book/positions` | Preserve the same deployment-stage and source-confidence semantics as holdings. |

Required holdings filters are `deployment_stage`, broker identity, runtime,
source status, stale telemetry, and risk state. The frontend lane should bind
to the exact OpenAPI parameter names delivered by the parent BFF commit and
round-trip them through the URL; it must not silently rename an unsupported
client filter into a local-only filter.

## Query And Projection Gap Matrix

| Gap | BFF / parent-owner obligation | Frontend obligation | Fail-closed result while unresolved |
|---|---|---|---|
| Persona-capital binding missing | Keep the runtime-driven row and expose missing join, quarantine reason, evidence refs, and incident. | Render the row and incident; do not substitute a persona from another source. | Attribution is partial, degraded, or unavailable. |
| Runtime or deployment-plan identity conflicts | Do not emit an applicable repair unless authoritative sources agree. | Show conflict state and link to Human Review with runtime and source context. | No formal attribution or covered badge. |
| Capital pool or scope unknown | Preserve unknown identity/scope instead of defaulting to paper or live. | Render `unknown` distinctly from paper ledger, canary sleeve, and live capital pool. | Exposure remains visible but unclassified/degraded. |
| Broker or paper-ledger identity unavailable | Emit source issue and incident; do not infer from deployment stage alone. | Show missing broker/ledger identity and keep relevant filter behavior honest. | Broker/ledger filter must not falsely match an inferred value. |
| Telemetry absent for active runtime | Emit missing coverage, source status, staleness, and incident without dropping the runtime. | Render missing/stale telemetry and its review action. | Confidence cannot exceed partial/degraded. |
| Telemetry stale | Preserve observed timestamp and staleness classification. | Show stale state in text, expose filter, and avoid fresh/covered language. | Formal attribution remains forbidden. |
| Repair proposed but not applied | Distinguish proposal from authoritative write and require a fresh reconciled capture. | Do not render proposal as repaired truth. | Row remains quarantined/ineligible. |
| Repair applied but hosted evidence absent | Return current source truth, but do not let local tests stand in for deployed evidence. | Consume only the deployed BFF response in hosted verification. | Parent acceptance remains open. |

No net-new route is asserted by this sidecar. If the parent implementation
cannot project quarantine reason, audit/evidence references, or repair state
through the current route family, the parent owner must either add a governed
BFF projection in its own canonical implementation scope or record the field
as an explicit unresolved contract gap. The frontend must not reconstruct
those facts from unrelated endpoints.

## Operator Journey

1. Open `/management/portfolio-book` in strict live mode.
2. Read summary coverage and compare runtime count with telemetry-runtime
   coverage; degraded coverage is visible before inspecting rows.
3. Filter holdings by stage, broker, runtime, source status, stale telemetry,
   and risk state. Reload and confirm the URL preserves the selection.
4. Open a degraded or missing-binding holding. Keep pool, holding, persona (if
   known), runtime, deployment stage, source issues, observed time, and
   confidence visible.
5. Follow the governed Persona Fleet or Performance Attribution link without
   losing entity and source context. Attribution must remain non-formal while
   required joins are unresolved.
6. Follow the incident action to Human Review. The review context must identify
   the affected runtime/holding, quarantine or issue codes, evidence refs, and
   source timestamps; it must not imply that clicking review repairs data.
7. Return to Portfolio Book and retain the filter/context state. After a parent
   repair, refresh against a newly deployed BFF capture and verify the incident
   changes only when authoritative source truth reconciles.

## Frontend DTO And Rendering Guardrails

- Treat BFF `data_confidence`, source status/issues, risk state, incidents,
  capital scope, deployment stage, runtime/persona/pool identities, and links as
  server-owned projection facts.
- Preserve unknown and null values. Do not fill them from fixture defaults,
  labels, route state, or neighboring rows.
- Render paper, canary, live, and unknown with accessible text; color alone is
  insufficient.
- Keep every unresolved row countable and actionable. Filtering may narrow a
  view, but summary and active-filter UI must make that narrowing explicit.
- In `VITE_BFF_MODE=live` with `VITE_BFF_FALLBACK=strict`, missing required BFF
  data becomes an unavailable/degraded state, never seed or mock success.
- Do not add an `execute-plans/` mirror inside Pantheon. Frontend delivery
  belongs to `ajoe734/execute-plans` and its own task/PR.

## Evidence Handoff Contract

The parent owner should give the frontend and hosted-E2E lanes one capture set
from the same deployed BFF commit:

| Evidence field | Required content |
|---|---|
| Delivery identity | Pantheon PR/head/merge SHA, `dev` deploy run, deployed BFF SHA/ancestry, capture timestamp, BFF base URL, and auth identity shape without secret values. |
| Before/after counts | Pools, runtimes, telemetry runtimes, holdings, degraded holdings, missing bindings, incidents, broker identity coverage, ledger identity coverage, and capital-scope distribution. |
| Raw reconciliation sample | Runtime, deployment plan, persona binding, pool, broker/ledger, and telemetry source references for repaired and quarantined examples. |
| Reconciliation behavior | Snapshot hash/idempotency result, append-only audit reference, proposed/applied distinction, quarantine issue codes, and proof that a rerun did not delete unresolved rows. |
| BFF payloads | Authenticated core, pools/exposure as applicable, holdings, positions, and attribution responses with filters and response metadata. |
| UI comparison | Counts/labels mapped to the same BFF payload, desktop/mobile evidence, preserved URL/context, console exception count, failed required-request count, and fallback-data count. |

The observed baseline counts above are comparison inputs, not permanent
expected values. A lower degraded count passes only when each original row is
accounted for as repaired or still quarantined; disappearing rows are a
failure.

## Parent And Reviewer Handoff

Parent owner (`MGMT-OPS-003-GAP-002`):

1. Keep authoritative writes with the owning services; the reconciler may
   propose but must not cross-write another store.
2. Record every baseline row in the reconciliation output and classify it as
   healthy, repaired-and-reverified, or explicitly quarantined.
3. Confirm current BFF OpenAPI filter names and projection fields for the
   frontend lane; list any remaining field gap rather than implying support.
4. Merge to `dev`, deploy that merge, and capture authenticated before/after
   evidence before requesting final parent review.

Reviewer (`Antigravity`) should review this sidecar only for support accuracy:

- diff is limited to this task brief and support packet;
- no L1/canonical, BFF/runtime, frontend, registry/governance, or deployment
  implementation changed;
- route and journey guidance remains fail closed and does not claim a new
  runtime contract;
- the packet keeps unresolved rows visible and forbids inferred confidence;
- parent and frontend owners receive concrete query, context, and evidence
  expectations.

Review approval of this sidecar does not approve `MGMT-OPS-003-GAP-002`, the
frontend task, deployment, or hosted closure.

## Verification

Run for this support slice:

```bash
AI_NAME=Codex ./scripts/ai-status.sh show MGMT-OPS-003-GAP-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-4
git diff --check -- .orchestrator/task-briefs/mgmt_ops_003_gap_002_sidecar_bff_handoff_followup_4.md support/sidecars/MGMT-OPS-003-GAP-002/MGMT-OPS-003-GAP-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-4.md
```

## Handoff

Antigravity approved this support-only sidecar and returned it to Codex for
owner finalization. Approval is limited to this handoff packet and does not
approve the parent repair, frontend delivery, deployment, or hosted closure.
After this task PR merges, Codex must mark the sidecar `done`; the parent owner
decides whether and how to absorb these recommendations into the canonical
implementation.
