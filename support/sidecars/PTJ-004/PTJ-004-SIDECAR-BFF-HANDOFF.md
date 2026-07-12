# PTJ-004 BFF and Frontend Handoff Packet (Sidecar)

**Parent task:** `PTJ-004` — Persona Trade Journal BFF APIs and governed commands

**Parent owner/reviewer:** `Codex` / `Codex2`

**Sidecar owner/reviewer:** `Codex2` / `Codex`

**Helper kind:** `bff_handoff_packet`

**Prepared:** 2026-07-12

> Support artifact only. This packet does not change canonical truth, BFF code,
> runtime projections, reflection/memory authority, governance policy, or the
> `execute-plans` frontend. Parent and downstream owners decide what to absorb.

## 1. Scope and current parent state

The parent is in `review`. Its owner handoff at commit `0f9e105fe` reports 14
focused BFF tests passing and exposes persona-scoped journal, reflection, and
pattern reads plus three governed commands. The implementation commit named by
the handoff is `bb71e321b`; neither commit is treated here as review approval or
hosted proof.

The intended ownership boundary is:

| Layer | Authority / responsibility |
|---|---|
| execution and telemetry projections | order, fill, position, and execution facts |
| attribution | P&L, fees, slippage, benchmark facts |
| persona/reflection and memory governance | reflections, lesson candidates, review lifecycle |
| BFF (`PTJ-004`) | persona-scoped aggregation, masking, source confidence, and governed command admission |
| frontend (`PTJ-006`) | truthful rendering and navigation; no canonical joins or inferred facts |

## 2. Route inventory for frontend consumption

| Method | Route | Frontend use |
|---|---|---|
| GET | `/bff/personas/{persona_id}/trade-journal` | episode list, filters, cursor pagination |
| GET | `/bff/personas/{persona_id}/trade-journal/{trade_episode_id}` | Why → Timeline → Execution → Outcome → Reflection → Lessons/Audit detail |
| GET | `/bff/personas/{persona_id}/trade-reflections` | pending, failed, partial, and review inbox |
| GET | `/bff/personas/{persona_id}/trade-patterns` | pattern/sample/uncertainty view |
| POST | `/bff/personas/{persona_id}/trade-journal/{episode_id}/reflection:retry` | audited retry without changing the facts snapshot ref |
| POST | `/bff/personas/{persona_id}/trade-lessons/{lesson_id}:submit-review` | submit a candidate to governance review |
| POST | `/bff/personas/{persona_id}/trade-lessons/{lesson_id}:decide` | authorized review decision, not direct persona mutation |

Frontend must use the normal BFF client and strict-live transport. It must not
call projection files/services directly or add Pantheon frontend source here.

## 3. Query and response handoff

### Journal list

The list contract needs to preserve server-owned cursor pagination and the gap
spec filters: period, environment, strategy, instrument, side, episode status,
outcome, reflection state, and coverage state. PTJ-006 should persist supported
filters in the URL, pass the returned cursor unchanged, and reset the cursor
when any filter changes. It must not download all episodes and filter locally.

Each row should retain stable identifiers and backend-owned states needed for
deep links. Missing execution, attribution, rationale, or reflection material
must remain visible through coverage/source metadata rather than being filled
with defaults.

### Detail

The detail response is an aggregate view over canonical references. The UI
should render sections independently so a degraded reflection source does not
hide available execution truth, and a degraded attribution source does not
invent zero P&L. Timeline ordering and lifecycle labels come from the response;
the browser must not reconstruct episode boundaries from timestamps.

### Reflections and patterns

Reflection text is interpretation attached to an immutable facts snapshot, not
canonical execution fact. Counterfactuals, unknowns, partial coverage,
provider/model/prompt versions, and review state must remain distinguishable.
Pattern rows must show sample size and uncertainty. A lesson candidate is not
an endorsed or merged memory.

### Required client-state mapping

| BFF condition | Required UI behavior |
|---|---|
| complete | Render available sections with their source/as-of metadata. |
| partial or degraded | Keep available facts visible; show missing refs and a persistent degraded banner. |
| unavailable / `DEPENDENCY_UNAVAILABLE` | Show a retryable unavailable state; do not create an empty successful journal. |
| masked field | Render a permission-aware masked value; do not expose raw identifiers in URL, logs, or tooltips. |
| 401 | Enter the existing authentication recovery flow. |
| 403 / cross-persona denial | Show scoped access denial without revealing whether another persona's episode exists. |
| empty list | Show a genuine empty state only after a successful response, distinct from unavailable. |

## 4. Operator journeys

### A. Persona journal triage

1. Operator opens a Persona detail and selects Trade Journal.
2. List loads with environment continuously visible and server-side filters.
3. Operator selects an episode without losing filter/cursor context.
4. Detail identifies which sections are complete, partial, degraded, masked, or
   unavailable before presenting reflection or action controls.
5. Deep links carry stable IDs to attribution, Decision Journal, lineage,
   Memory Review, or Human Review; they do not carry sensitive raw account data.

### B. Closed episode awaiting reflection

1. Execution/outcome facts remain visible while reflection is pending or
   failed.
2. The UI identifies the immutable facts snapshot reference and current
   reflection state.
3. An authorized operator supplies a reason and `Idempotency-Key` to retry.
4. The UI waits for the command receipt/readback; it does not optimistically
   mark the episode reflected.
5. Duplicate submission reuses the accepted result; an idempotency conflict is
   surfaced rather than silently retried with changed input.

### C. Lesson review

1. Operator inspects the supporting episode(s), coverage, counterfactual labels,
   confidence, sample size, and expiry.
2. Submit-review creates a governed receipt; it does not apply a lesson.
3. Decide is shown only for an authorized reviewer and requires a reason and
   idempotency key.
4. Endorsed/merged/quarantined/rejected states are rendered from readback.
5. Any proposal affecting policy, risk, capital, artifact, or live behavior
   remains routed through the existing evaluation/approval/deployment gates.

### D. Missing or protected data

1. A missing projection dependency produces an explicit unavailable state.
2. Partial coverage lists missing refs and source/as-of information.
3. A viewer sees masked sensitive values; a cross-persona request receives a
   non-enumerating denial.
4. No LLM copy, zero value, inferred rationale, or timestamp-based join is used
   to make the screen appear complete.

## 5. Governed command client contract

For every POST, PTJ-006 must:

- require a non-empty operator reason and a stable `Idempotency-Key`;
- use the authenticated persona-scoped BFF route;
- disable repeat submission while the first request is pending;
- render the returned audit receipt and then refresh authoritative read state;
- distinguish a replayed duplicate from an idempotency conflict;
- preserve the facts snapshot reference on reflection retry;
- avoid optimistic success and never translate lesson review into direct
  policy, risk, capital, memory, artifact, or live-order mutation.

## 6. Frontend implementation packet for PTJ-006

Recommended order in `ajoe734/execute-plans`:

1. Add typed path/client helpers for all four reads and three commands.
2. Preserve backend snake-case fields or map them losslessly in one adapter;
   keep raw stable IDs and coverage/source metadata available to the UI.
3. Build shared treatments for environment, coverage, source freshness,
   masked values, reflection state, and command receipts.
4. Implement list filters/cursor handling and separate loading, empty,
   degraded, unavailable, unauthorized, and forbidden states.
5. Implement the episode detail sections independently, then reflection inbox
   and pattern view.
6. Add governed command dialogs with reason/idempotency/receipt behavior.
7. Add deep links and back-navigation context.
8. Cover desktop/mobile with strict-live tests for complete paper, missing
   refs, force close, reflection pending/failed, masking, denial, duplicate
   POST, idempotency conflict, and unavailable dependency.

## 7. Parent and reviewer checks

Before the parent route set is treated as a stable frontend dependency, its
reviewer should confirm against `bb71e321b`:

- list filters and cursor semantics match the published contract;
- detail/reflection/pattern responses expose explicit coverage, source/as-of,
  and confidence rather than synthetic facts;
- environment and persona scope are enforced on every read and command;
- masking is non-leaking and cross-persona denial does not enumerate records;
- 401, 403, partial, degraded, unavailable, and downstream-failure envelopes
  are stable enough for one client mapping;
- duplicate POST returns the same admitted result while conflicting reuse is
  rejected;
- retry preserves the facts snapshot reference and every accepted command
  returns an audit receipt;
- no command can directly mutate orders, policy, risk, capital, artifacts, or
  live behavior.

PTJ-006 should not claim hosted compatibility from the parent's local 14-test
result. Cross-repo strict-live and hosted proof remains `PTJ-007` scope.

## 8. Sidecar acceptance and handoff

- Support-only path: `support/sidecars/PTJ-004/PTJ-004-SIDECAR-BFF-HANDOFF.md`.
- No L1/canonical, BFF/runtime, governance, status-truth, or frontend source is
  changed by this packet.
- Parent implementation/review ownership remains unchanged.
- Assigned sidecar reviewer: `Codex`.
- Parent owner and PTJ-006 owner decide whether and how to absorb this packet.

Verification for this sidecar is limited to source inspection, Markdown diff
checks, task-scoped commit/PR checks, and reviewer handoff; it is not runtime or
hosted acceptance evidence.
