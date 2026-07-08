# MGMT-OPS-001 BFF And Frontend Handoff Packet

| Field | Value |
|---|---|
| Parent task | `MGMT-OPS-001` |
| Parent title | Operations read model and source confidence contract |
| Parent owner / reviewer | `Codex2` / `Codex` at closeout |
| Sidecar task | `MGMT-OPS-001-SIDECAR-BFF-HANDOFF` |
| Sidecar owner / reviewer | `Codex2` / `Codex` |
| Helper kind | `bff_handoff_packet` |
| Generated | `2026-07-07` |
| Closeout update | `2026-07-08`; PR #3051 merged at `2026-07-07T15:54:41Z`; parent PR #3050 merged at `2026-07-08T04:16:27Z` |
| Mutates canonical | `false` |

This is a support artifact only. It does not define canonical truth, update L1
contracts, edit BFF/runtime code, edit frontend code, change registries, or
approve the parent implementation. The parent owner decides whether to absorb,
revise, or ignore this packet.

---

## 1. Sources Read

| Source | Relevant finding |
|---|---|
| `AI_COLLABORATION_GUIDE.md` | L0 status coordinates ownership; support packets do not override L1/L2 product truth. |
| `.orchestrator/task-briefs/mgmt_ops_001_sidecar_bff_handoff.md` | Sidecar scope is BFF query gap, operator journey, and frontend handoff material only. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show MGMT-OPS-001-SIDECAR-BFF-HANDOFF` | Canonical status root has this sidecar `review_approved`, owner `Codex2`, reviewer `Codex`, artifact path this file, and review approval noting PR #3051 is merged. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show MGMT-OPS-001` / `gh pr view 3050` | Parent status root remains `in_progress`, but GitHub PR #3050 is merged to `dev` at merge commit `cea8d1f94`. Parent owner still owns its own closeout. |
| `origin/task/MGMT-OPS-001@9e6850539`, then PR #3050 | Parent candidate implements `operations_read_model.py`, `GET /bff/management/operations-read-model/{persona_id}`, evidence doc, and focused tests. The original packet inspected the parent branch without merging it; this closeout branch later absorbed it only through `origin/dev` after PR #3050 merged. |
| `docs/04/pantheon_management_console_operations_workflow_2026-07-07/MANAGEMENT_CONSOLE_OPERATIONS_WORKFLOW_PLAN.md` | Source plan requires one shared data-confidence vocabulary across Persona Fleet, Portfolio Book, Performance Attribution, Persona League, Quarterly Ranking, and Human Review. |
| `docs/bff/execution-tasks/2026-07-07-management-console-operations-workflow/INDEX.md` | Execution packet sequences `MGMT-OPS-001` before Wave 1 frontend/page integration tasks. |
| `docs/frontend/execute-plans-dev-hosting.md` | Active frontend repo is `ajoe734/execute-plans`; dev delivery must use the Pantheon-owned FE/BFF host, not Lovable. |
| `execute-plans/src/lib/bff-v1/paths.ts` | Current frontend path registry has management routes for persona fleet, performance attribution, persona league, quarterly ranking, human inbox, and capital, but no operations-read-model helper yet. |
| `execute-plans/src/management/components/performance-review/ManagementPerformanceReviewPanel.tsx` | Current panel has local number/fallback normalization helpers; future work should replace silent zero/NaN-style display with backend confidence and diagnostics. |

`current-work.md` and the full `ai-activity-log.jsonl` were intentionally not
scanned.

---

## 2. Handoff Summary

The parent candidate creates a read-only BFF source-confidence contract for one
persona:

```text
GET /bff/management/operations-read-model/{persona_id}?period=latest
```

The response entry carries:

- `identity`: persona, stage, runtime, paper ledger, pool, sleeve, strategy,
  artifact, broker, period, and `as_of` identity;
- `data_confidence`: `formal`, `partial`, `fallback`, `degraded`, or
  `unavailable`;
- `performance`: finite metrics only, with missing/non-finite values
  represented as `null`;
- `sources[]`: source name, status, row count, freshness, error, and coverage;
- `diagnostics[]`: explicit missing join or degraded-source explanations.

For the focus persona `persona-20260528-04688755`, parent evidence says the
persona fleet source can resolve `runtime-crypto-paper` and fallback
performance (`pnl=48000`, `sharpe=1.76`, `drawdown_pct=0.064`), while formal
performance attribution and holdings do not match that persona. The parent
route represents this as `data_confidence: "fallback"` with diagnostics instead
of a dropped row or `nan`.

The immediate BFF/frontend handoff gap is therefore not a new direct frontend
join. The gap is frontend adoption of the BFF-owned confidence model, plus a
future bounded list/batch shape if table pages need confidence on many personas
without issuing unbounded per-row calls.

---

## 3. Current Surface Snapshot

### 3.1 Parent Candidate BFF Surface

| Route | Current role | Handoff guidance |
|---|---|---|
| `GET /bff/management/operations-read-model/{persona_id}` | Parent route merged to `dev` through PR #3050; returns one composed operations read-model entry. | Frontend should consume after the dev BFF is deployed/restarted as needed and generated client/types are refreshed. |
| `period` query param | Defaults to `latest`. | Preserve it in drilldown links from fleet/capital/ranking pages so the selected period is not lost. |
| 404 unknown persona | Parent tests expect `RESOURCE_NOT_FOUND`, not a fabricated row. | UI should show "persona not found" / stale-link state, not empty formal attribution. |
| `data_confidence` | Backend confidence verdict for the represented row. | Use as the primary visual authority badge; do not infer confidence from page-local heuristics. |
| `sources[]` | Backend source coverage and degradation details. | Render source coverage as first-class support evidence. |
| `diagnostics[]` | Missing joins and degraded data explanations. | Render diagnostics before action controls; never hide them behind numeric summaries. |

### 3.2 Frontend Routes Already Present

| Frontend route | Current role | Gap against parent candidate |
|---|---|---|
| `/management/capital` | Portfolio/capital entry point. | Needs source coverage and stable drilldown into persona read model. |
| `/management/persona-fleet` | Runtime/persona command center. | Needs `data_confidence`, source-status chips, and drilldown links carrying persona/runtime/period. |
| `/management/performance-attribution` | Causal performance drilldown. | Needs confidence banner, formal/fallback split, diagnostics panel, and no NaN/zero coercion. |
| `/management/persona-league` | Short-cycle ranking/comparison. | Needs ranking evidence coverage and links into read model/Human Review. |
| `/management/quarterly-ranking` | Governance-cycle ranking. | Needs eligibility/evidence coverage and review packet state. |
| `/management/human-inbox` | Human review/action surface. | Needs confidence/action-state context in review packets and receipts. |

Current `execute-plans/src/lib/bff-v1/paths.ts` does not have an
`operations-read-model` helper. Add one in the frontend task after the parent
BFF route merges, for example:

```ts
managementOperationsReadModel: (personaId: string) =>
  `${BASE}/management/operations-read-model/${enc(personaId)}`,
```

---

## 4. BFF Query Gap Matrix

| Need | Current state | BFF/frontend gap |
|---|---|---|
| Single-persona source confidence | Parent route provides one entry by `persona_id`. | Frontend adapter/type mapping is missing until Wave 1 work consumes the route. |
| Table-wide confidence | Parent route is one persona per request. | If Persona Fleet or ranking tables need confidence for many rows, parent/follow-up should add a bounded list or batch query instead of unbounded frontend N+1 calls. |
| Focus persona fallback | Parent route reports fallback plus `MISSING_ATTRIBUTION_MATCH`, `MISSING_HOLDINGS_MATCH`, and `FORMAL_ATTRIBUTION_MISSING_USING_FLEET_FALLBACK`. | Performance page must show fallback as fallback, not as formal attribution or zero-filled holdings. |
| Source coverage | Parent route returns `sources[]` with source status and row counts. | Frontend needs common source-status badge/tooltip treatment across capital, fleet, attribution, league, ranking, and review pages. |
| Diagnostics | Parent route returns `diagnostics[]`. | Frontend needs a diagnostics panel and row-level indicators; diagnostics should block unsafe action escalation when evidence is fallback/degraded/unavailable. |
| Null/non-finite metrics | Parent helper sanitizes `nan`/`inf` to `null`. | Frontend number formatters must render empty/missing states, not `0`, `NaN`, or implied formal evidence. |
| Action state | Source plan requires observe/request-review/pause/resume/demote/promote/rebalance/apply/containment states. | Parent route does not by itself implement action state; Wave 3/Human Review work must attach governed action availability and receipts. |
| Dev deploy proof | Parent PR #3050 is merged to `dev`. | Hosted proof still requires deploy/restart if needed and dev FE built from `execute-plans` with strict BFF wiring. |

---

## 5. Operator Journey Packet

### Journey A: Daily monitoring

1. Operator opens `/management/capital`.
2. UI shows capital/paper/canary/live separation, source coverage, degraded
   source count, and stale telemetry count.
3. Operator drills into `/management/persona-fleet` filtered by risk, stale
   telemetry, performance change, or needs-human state.
4. For a selected persona, frontend reads
   `/bff/management/operations-read-model/{persona_id}`.
5. The row/card shows identity, `data_confidence`, source-status chips,
   diagnostics, and only then action affordances.
6. If confidence is `fallback`, `degraded`, or `unavailable`, the next safe
   step is data-quality triage or Human Review, not promotion or capital
   increase.

### Journey B: Focus persona attribution triage

1. Operator selects `persona-20260528-04688755` from Persona Fleet.
2. Performance Attribution opens with the same persona id, runtime id, and
   period context.
3. UI reads the operations read model and shows `data_confidence: fallback`.
4. UI displays fallback metrics from persona-fleet summary as fallback evidence.
5. UI displays missing formal attribution and holdings diagnostics explicitly.
6. UI renders missing holdings cells as empty/diagnostic states, never `nan` and
   never zero unless the backend returned a real zero.

### Journey C: Incident triage

1. A capital or persona row reports drawdown, stale telemetry, degraded source,
   or missing attribution.
2. Operator scopes the issue by persona, runtime, ledger, pool, strategy,
   artifact, broker, and period from the read-model identity block.
3. If sources are missing or fallback-only, the UI creates or links a
   data-quality incident/review item.
4. If risk containment is necessary, the UI routes to Human Review with
   diagnostics attached.
5. Emergency containment can reduce or pause risk only through governed
   commands and receipts; it must not promote or increase allocation.

### Journey D: Governance input

1. Operator uses Persona League for short-cycle comparison and Quarterly
   Ranking for formal-cycle governance inputs.
2. Ranking rows link to the same operations read model and attribution detail.
3. Human Review receives a packet with ranking evidence, attribution evidence,
   source confidence, binding identity, policy constraints, and proposed action.
4. Apply state is shown separately from recommendation/submission/approval.
5. Portfolio Book and Persona Fleet link back to the apply receipt after a
   governed action completes.

---

## 6. Frontend Handoff Materials

Recommended frontend implementation sequence after the dev BFF includes PR #3050:

1. Add `paths.managementOperationsReadModel(personaId)` and a typed read helper.
2. Add an `OperationsReadModelEntry` adapter in
   `execute-plans/src/lib/bff-v1/management.ts` that accepts backend
   snake_case and exposes UI-friendly aliases without losing raw fields.
3. Add shared badge/tone maps for `data_confidence` and `source_status`.
4. Update Performance Attribution first because it is where the focus persona
   fallback/NaN issue is visible.
5. Update Persona Fleet drilldowns so selected rows carry persona id, runtime
   id, period, and confidence hints.
6. Add Portfolio Book source-coverage summary and links to attribution/Human
   Review.
7. Thread the same confidence/diagnostic block into Persona League, Quarterly
   Ranking, and Human Review packets.

UI rules:

- Use backend `data_confidence`; do not recompute confidence in the browser.
- Treat missing `data_confidence` as unknown/degraded, not as formal.
- Do not silently coerce `null`, `NaN`, missing joins, or absent metrics to
  zero.
- Keep fallback rows visually distinct from formal rows.
- Show source counts as what they are: formal rows, fallback summaries, missing
  diagnostics, excluded rows, or unavailable sources.
- Hide or disable high-impact action controls when confidence is fallback,
  degraded, or unavailable unless a Human Review packet explicitly authorizes
  the action.
- Do not add direct broker, runtime, capital, or governance mutation paths from
  these pages.

---

## 7. Parent / Reviewer Checklist

For parent `MGMT-OPS-001` review/closeout:

- Record PR #3050 merge commit `cea8d1f94` before downstream frontend work
  treats the route as available on `dev`.
- Confirm the route returns one entry with stable `identity`,
  `data_confidence`, `performance`, `sources`, and `diagnostics`.
- Confirm the focus persona fallback evidence remains visible and finite.
- Confirm unknown persona returns a typed 404, not an empty fabricated row.
- Confirm parent tests still cover formal, partial, fallback, degraded,
  unavailable, and 404 states.
- Confirm this sidecar did not broaden parent scope into frontend or governed
  action implementation.

For sidecar reviewer `Codex`:

- Confirm this packet is support-only and edits only
  `support/sidecars/MGMT-OPS-001/MGMT-OPS-001-SIDECAR-BFF-HANDOFF.md`.
- Confirm route claims are marked as parent candidate state, not already merged
  dev truth.
- Confirm the BFF query gap matrix names both the immediate adapter gap and the
  possible bounded list/batch gap.
- Confirm operator journeys preserve governed action boundaries and do not
  imply live capital mutation.

---

## 8. Verification Notes

Sidecar verification was source inspection only:

- Confirmed task state through `AI_NAME=Codex2 ./scripts/ai-status.sh show`.
- Confirmed PR #3051 is merged to `dev` at merge commit
  `f3f36553f5d1cf00373233123d4bd9491129467d`.
- Confirmed PR #3051 changed only this support packet and all visible GitHub
  checks reported success before merge.
- Confirmed parent PR #3050 is merged to `dev` at merge commit
  `cea8d1f94fd3a3f5efb831331435ced071f303d0`; parent task status still needs
  parent-owner closeout.
- Fast-forwarded the sidecar branch to `origin/dev` before writing this packet.
- Inspected parent candidate `origin/task/MGMT-OPS-001@9e6850539` without
  merging it into the original sidecar packet; closeout later merged
  `origin/dev` only after PR #3050 was already on `dev`.
- Read the parent evidence doc, operation read-model module, route references,
  and focused test file from the parent branch.
- Inspected frontend route/client files for existing management paths and lack
  of an operations-read-model helper.

No runtime, registry, governance, BFF implementation, frontend implementation,
L1 canonical document, or live environment changes were made by this sidecar.
