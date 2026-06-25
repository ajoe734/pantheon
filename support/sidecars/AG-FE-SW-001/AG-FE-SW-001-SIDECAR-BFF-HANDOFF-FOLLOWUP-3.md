# AG-FE-SW-001 Sidecar Follow-up 3: BFF and Frontend Handoff Delta

| Field | Value |
|---|---|
| Task ID | `AG-FE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3` |
| Helper kind | `bff_handoff_packet` |
| Parent task | `AG-FE-SW-001` - TradingDeskShell + Strategy Workshop tab |
| Parent owner / reviewer | `Claude` / `Codex` |
| Sidecar owner / reviewer | `Codex` / `Claude` |
| Date | 2026-06-22 |
| Pantheon dev base inspected | `fd871d2c0011e4818febd51fe1d8d7e0e9d84a15` |
| execute-plans ref inspected | `origin/dev` at `40fef8769435fa479c87c2892417a76186913ecf` |
| Prior packets | `AG-FE-SW-001-SIDECAR-BFF-HANDOFF.md`, `AG-FE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md` |
| Mutates canonical truth | `false` |
| Status | Ready for reviewer handoff |

This is a support-only follow-up to the prior AG-FE-SW-001 handoff packets. It
does not edit L1 truth, OpenAPI, JSON schemas, BFF runtime, route registry,
governance/runtime code, or execute-plans frontend source. The parent owner
decides whether to absorb this delta into the main AG-FE-SW-001 implementation.

---

## Sources Rechecked

| Source | Follow-up finding |
|---|---|
| `AI_COLLABORATION_GUIDE.md` | Sidecar packets are support records and do not override canonical architecture or policy truth. |
| `.orchestrator/task-briefs/ag_fe_sw_001_sidecar_bff_handoff_followup_3.md` | Scope is support-only BFF query gap, operator journey, and frontend handoff material; no canonical truth changes. |
| `.orchestrator/skills/worker-anchor-commit.md` | Meaningful support-file progress must be committed with explicit task scope. |
| `.orchestrator/skills/task-closeout-finalization.md` | Repo file changes require task commit, PR, review, merge, then owner closeout before `done`. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-FE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3` | Active task is `in_progress`, owner `Codex`, reviewer `Claude`, artifact target is this file. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-FE-SW-001` | Parent is now `in_progress`; acceptance still requires exact design/spec alignment, live-strict `workshops.ts`, no invented fields/routes/widgets, and no Management exposure. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-FE-ID-001` | Dependency is recorded `done`; execute-plans PR #66 merged Agora identity/servant BFF clients and an `AgoraApp` shell. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-XR-OPENAPI-004` | v1.3 OpenAPI/capability/schema bundle is archived `done`; frontend type generation from v1.3 remains a downstream concern. |
| `support/sidecars/AG-FE-SW-001/AG-FE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md` | Prior route ledger and stop lines remain valid; this packet adds current parent/frontend integration cut. |
| `services/control-plane/bff/agora/strategy_workshop/router.py` | Workshop list/create/get/messages/events/completeness/stream routes remain runtime-live; versions, legacy research-runs, consultations, and conclude remain explicit 501 stubs. |
| `services/control-plane/bff/agora/research/router.py` | Plan-first research plan/run and candidate-pool routes exist outside the Strategy Workshop client slice. |
| `git diff --name-status a93bdb7..HEAD -- services/control-plane/bff/agora ...` | Since Follow-up 2, inspected Pantheon deltas touch trading-room support, not `strategy_workshop` or research plan route behavior. |
| `/home/lupin/code/execute-plans` | Working tree is detached at `574cc541`; use remote refs for current delivery inspection rather than this detached checkout alone. |
| `/home/lupin/code/execute-plans origin/dev` | Latest fetched delivery ref includes PR #66 (`AG-FE-ID-001`) and has `src/agora/AgoraApp.tsx`, `identity.ts`, and `servant.ts`. |
| `origin/dev:src/App.tsx` | `/agora` still mounts legacy `AgoraLayout`; `AgoraApp` is present but not mounted by the inspected route tree. |
| `origin/dev:src/agora/AgoraApp.tsx` | Three tab skeleton exists for `trading-room`, `strategy-workshop`, and `strategy-performance`, but panels are "coming soon" placeholders. |
| `origin/dev:src/lib/bff-v1/paths.ts` | No workshop path builders exist. |
| `origin/dev:src/lib/bff-v1/agora` | Identity and servant clients/tests exist; no `workshops.ts` client exists. |
| `origin/dev:src/lib/bff-v1/agora/types.ts` | Contract snapshot still exposes v1.0 `StrategyWorkshop`; no v1.3 `WorkshopCard`, `WorkshopStreamEvent`, readiness, patch, or version-comparison types are present. |

`current-work.md` and the full `ai-activity-log.jsonl` were not scanned.

---

## Delta From Follow-up 2

The main new fact is that `AG-FE-ID-001` has landed in execute-plans `origin/dev`
while `AG-FE-SW-001` itself has moved to `in_progress`.

That changes the parent handoff cut:

- Do not re-create an identity or servant shell from scratch.
- Reuse or intentionally replace the `AgoraApp` tab skeleton from PR #66.
- Wire Strategy Workshop routes through the actual React router, because the
  inspected `App.tsx` still mounts old `AgoraLayout` for `/agora`.
- Add the missing workshop BFF client and path builders rather than extending
  identity or servant clients.
- Treat v1.3 types as still missing from execute-plans even though Pantheon has
  the v1.3 OpenAPI/schema bundle.

No new runtime implementation was found for:

- `GET /bff/agora/workshops/{workshop_id}/cards`
- `/bff/agora/workshops/{workshop_id}/patch-proposals*`
- `/bff/agora/workshops/{workshop_id}/version-comparisons`
- `/bff/agora/workshops/{workshop_id}/readiness*`

No Strategy Workshop parent implementation was found in execute-plans
`origin/dev`:

- no mounted `/agora/trading-room`, `/agora/strategy-workshop`, or
  `/agora/strategy-performance` route entries;
- no `StrategyWorkshopPage`;
- no `src/lib/bff-v1/agora/workshops.ts`;
- no workshop path builders in `src/lib/bff-v1/paths.ts`;
- no v1.3 `WorkshopCard` or `WorkshopStreamEvent` generated types.

---

## Parent Integration Cut

| Parent area | Current state | Follow-up 3 handoff |
|---|---|---|
| Identity/scope | `identity.ts` exists and uses strict live behavior through `withStrictLiveOrMock`. | Parent should call identity/capability readiness through this client boundary, not page-level `fetch()`. |
| Servant ensure | `servant.ts` has `agoraServantClient.ensure()` using `bffFetch` and live-mode idempotency/request headers. | Parent should reuse this shell readiness path before enabling workshop controls. |
| Agora shell | `AgoraApp.tsx` has three IA tabs, but `App.tsx` still mounts `AgoraLayout`. | Parent must choose the route-mount strategy: replace `/agora` with `AgoraApp`, or merge the tab shell into the mounted route tree. Leaving `AgoraApp` unmounted does not satisfy AG-FE-SW-001. |
| Strategy Workshop tab | `AgoraApp` renders a placeholder panel for `strategy-workshop`. | Replace only that panel with the Strategy Workshop page; do not invent card/action data. |
| Routes | Legacy `/agora/daily`, `/agora/signals`, `/agora/notebook`, etc. remain mounted under `AgoraLayout`. | Add the contract IA routes and redirects explicitly. Do not assume tab state alone creates deep links. |
| BFF client | No `src/lib/bff-v1/agora/workshops.ts`. | Add a strict client for the seven runtime-live workshop routes first. |
| Path builders | `paths.ts` has signals/inbox/journal/postmortems/ask only. | Add only canonical `/bff/agora/workshops*` builders that are runtime-live. |
| Types | v1.0 `StrategyWorkshop` and `StrategyCompleteness` exist; v1.3 card/stream/readiness/patch/version types do not. | Parent can type the runtime-live workshop client from current runtime envelopes, but must not claim v1.3 card/readiness/action support without regenerating or mirroring those types. |
| Branch base | execute-plans remote reports `HEAD branch: dev`; `origin/main` is older than `origin/dev` in the inspected repo. | Parent should confirm the intended clean frontend task branch base before coding. This packet records the observation only and does not change canonical workflow docs. |

---

## Runtime-Live Workshop Surface To Use First

`AG-FE-SW-001` can still safely build a strict client for these routes:

| Need | Route | Required frontend behavior |
|---|---|---|
| List workshops | `GET /bff/agora/workshops` | Use for landing and picker state; include tenant/user/workshop-aware query keys. |
| Create workshop | `POST /bff/agora/workshops` | Send `Idempotency-Key`; do not persist raw `initial_message` in frontend storage. |
| Load detail | `GET /bff/agora/workshops/{workshop_id}` | Capture HTTP `ETag` and `meta.etag`. |
| Append message | `POST /bff/agora/workshops/{workshop_id}/messages` | Send latest `If-Match` plus fresh `Idempotency-Key`; refetch on 409. |
| Event history | `GET /bff/agora/workshops/{workshop_id}/events` | Render redacted/private-ref event records only; do not reconstruct raw text. |
| Completeness baseline | `GET /bff/agora/workshops/{workshop_id}/completeness` | Render empty/unassessed state when `data` is null-like; do not invent grades or next questions. |
| SSE stream | `GET /bff/agora/workshops/{workshop_id}/stream` | Expect first `workshop.connected`; dedupe by SSE event id; use `Last-Event-ID` for reconnect. |

Plan-first research routes are runtime-live but should stay in a separate
research client or a later card/research slice unless the parent narrows scope
explicitly:

- `GET|POST /bff/agora/workshops/{workshop_id}/research-plans`
- `GET /bff/agora/research-plans/{plan_id}`
- `POST /bff/agora/research-plans/{plan_id}/approve`
- `POST /bff/agora/research-plans/{plan_id}/cancel`
- `GET|POST /bff/agora/research-plans/{plan_id}/runs`
- `GET /bff/agora/research-runs/{run_id}`
- `POST /bff/agora/research-runs/{run_id}/cancel`
- `GET /bff/agora/research-runs/{run_id}/artifacts`

---

## Updated Operator Journey Cut

### Journey A: Enter Agora shell

1. Operator enters `/agora/trading-room`, `/agora/strategy-workshop`, or
   `/agora/strategy-performance`.
2. Router must land in the design-approved shell, not the legacy side-menu page.
3. Shell loads Agora identity and capabilities via `identity.ts`.
4. Shell ensures servant status via `servant.ts`.
5. If identity or servant readiness fails, Strategy Workshop write controls stay
   disabled and the error remains visible in strict live mode.

### Journey B: Open Strategy Workshop landing

1. Operator enters `/agora/strategy-workshop`.
2. Frontend calls `GET /bff/agora/workshops` through `workshops.ts`.
3. Empty state shows create affordance only after identity/servant readiness.
4. Existing sessions link to `/agora/strategy-workshop/:workshopId`.
5. No local seed workshop may satisfy the live-strict route.

### Journey C: Create workshop

1. Operator submits an initial hypothesis.
2. Frontend calls `POST /bff/agora/workshops` with a fresh `Idempotency-Key`.
3. Frontend keeps the draft in component state only; raw text is not persisted.
4. BFF returns the created session.
5. UI navigates to the detail route from the BFF response, not from an invented
   optimistic object.

### Journey D: Continue workshop conversation

1. Detail view calls `GET /bff/agora/workshops/{workshop_id}` and stores `ETag`.
2. Detail view loads `/events` and `/completeness`, then opens `/stream`.
3. Composer calls `POST /messages` with `If-Match` and `Idempotency-Key`.
4. On 409, refetch detail/events and preserve the unsent operator draft only in
   component state.
5. On missing card/readiness/patch routes, show unavailable/blocker state rather
   than fabricating a success projection.

---

## Stop Lines To Preserve

```text
Do not count AG-FE-ID-001's AgoraApp tab skeleton as the AG-FE-SW-001
Strategy Workshop implementation. In the inspected execute-plans origin/dev
tree, AgoraApp is not mounted by App.tsx and the strategy-workshop panel is only
a placeholder.
```

```text
Do not implement Strategy Workshop pages through direct fetch(), internal
/api/v1/* endpoints, Management clients, broker routes, RuntimeBinding writes,
capital binding, or local seed fallback. The parent slice must use
src/lib/bff-v1/agora/* client boundaries only.
```

```text
Do not render live typed WorkshopCard projections until a runtime
GET /bff/agora/workshops/{workshop_id}/cards handler exists. The v1.3 schema is
contract truth, but the inspected BFF runtime does not expose that route.
```

```text
Do not implement live patch proposal, version comparison, readiness reassess,
consultation, conclude, or workshop-level legacy research-runs actions in
AG-FE-SW-001. The inspected runtime either lacks handlers or returns explicit
501 stubs for those edges.
```

```text
Do not depend on v1.3 frontend types until execute-plans regenerates or mirrors
the v1.3 bundle. The inspected origin/dev tree still lacks WorkshopCard,
WorkshopStreamEvent, readiness, patch proposal, and version-comparison types.
```

```text
Do not ignore the execute-plans branch-base ambiguity observed by this sidecar.
origin/dev contains the latest Agora identity shell while origin/main is older
in the inspected repo. Parent owner should confirm the intended clean task
branch base before coding.
```

---

## Parent Checklist

| Check | Expected parent result |
|---|---|
| Frontend base | Confirm clean execute-plans task branch from the current intended delivery base; do not code from the detached local checkout. |
| Route mount | Mount or merge `AgoraApp` so the three primary tabs are actually reachable by URL. |
| Deep links | Add `/agora/trading-room`, `/agora/strategy-workshop`, `/agora/strategy-workshop/:workshopId`, and `/agora/strategy-performance`. |
| Legacy route handling | Redirect or retire legacy `daily`, `watchlist`, `signals`, and `notebook` routes according to the IA decision, not ad hoc. |
| Identity/servant reuse | Reuse `identity.ts` and `servant.ts` boundaries; do not duplicate auth/session logic in the workshop page. |
| Workshop client | Add `src/lib/bff-v1/agora/workshops.ts` for the seven runtime-live routes. |
| Path builders | Add workshop builders in `src/lib/bff-v1/paths.ts` only for runtime-live canonical routes. |
| Strict live mode | No local seed success for workshop list/create/detail/events/completeness/stream. |
| Mutation headers | Create sends `Idempotency-Key`; message append sends `If-Match` and `Idempotency-Key`. |
| SSE adapter | Accept current runtime SSE shape `{id,type,timestamp,data}` and support `Last-Event-ID`. |
| Type boundary | Do not claim v1.3 card/stream/readiness/action support until generated types and runtime handlers exist. |
| Safety | No broker order, live/canary capital binding, RuntimeBinding write, Management route, or hidden command surface enters Agora. |

---

## Reviewer Checklist

Claude should verify:

| Check | Expected result |
|---|---|
| Scope | This sidecar authored only this support artifact. |
| Delta accuracy | Follow-up 3 records the AG-FE-ID shell landing and the remaining AG-FE-SW route/client gaps. |
| Runtime accuracy | Workshop route ledger still matches `strategy_workshop/router.py`; no `/cards`, readiness, patch, or version route is claimed live. |
| Frontend accuracy | execute-plans `origin/dev` has identity/servant and unmounted `AgoraApp`, but no Strategy Workshop page/client/path builders. |
| Branch-base note | The observed `origin/dev` versus `origin/main` mismatch is framed as a parent confirmation risk, not a canonical workflow edit. |
| Safety boundary | Packet preserves Agora no-order/no-capital/no-RuntimeBinding authority. |

Recommended reviewer approval command after PR review:

```bash
AI_NAME=Claude REVIEW_FILE=support/sidecars/AG-FE-SW-001/AG-FE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3.md \
  REVIEW_NOTES_ZH="Support-only follow-up packet approved: AG-FE-SW-001 parent integration cut, AG-FE-ID shell reuse, remaining Strategy Workshop route/client/type gaps, runtime stop lines, and no-order/live-strict guardrails are documented without canonical/runtime/schema/frontend changes." \
  ./scripts/ai-status.sh approve AG-FE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3 \
  "Support-only AG-FE-SW-001 BFF/frontend handoff follow-up approved for parent owner absorption."
```

Recommended reviewer reopen command:

```bash
AI_NAME=Claude ./scripts/ai-status.sh reopen AG-FE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3 \
  "Describe the factual correction, missing route distinction, or parent handoff detail needed before approval."
```

---

## Validation

Focused validation run from this task worktree:

```bash
LC_ALL=C rg -n "[^[:ascii:]]" support/sidecars/AG-FE-SW-001/AG-FE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3.md
git diff --no-index --check /dev/null support/sidecars/AG-FE-SW-001/AG-FE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3.md
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  services/control-plane/bff/tests/test_agora_strategy_workshop.py \
  services/control-plane/bff/tests/test_workshop_stream_ag_be_sw_004.py \
  services/control-plane/bff/tests/test_agora_research_run_projection.py \
  -q
```

No canonical truth, runtime, schema, or execute-plans files are changed by this
sidecar.

Results:

- ASCII scan: no output.
- New-file whitespace check: no output.
- Focused BFF tests: `81 passed in 198.58s`.
