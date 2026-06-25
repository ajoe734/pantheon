# AG-FE-SW-001 Sidecar: BFF and Frontend Handoff Packet

| Field | Value |
|---|---|
| Task ID | `AG-FE-SW-001-SIDECAR-BFF-HANDOFF` |
| Helper kind | `bff_handoff_packet` |
| Parent task | `AG-FE-SW-001` - TradingDeskShell + Strategy Workshop tab |
| Parent owner / reviewer | `Claude` / `Codex` |
| Sidecar owner / reviewer | `Codex` / `Claude` |
| Date | 2026-06-22 |
| Current Pantheon dev base | `552ba2f0595e0d236952dea8a1dc36e1df6a673d` |
| Mutates canonical truth | `false` |
| Status | Ready for reviewer handoff |

This packet is support material only. It does not edit L1 canonical truth,
OpenAPI, JSON schemas, BFF runtime, database migrations, route registries,
governance policy, OpenClaw adapter code, or execute-plans frontend code. The
parent owner decides which parts to absorb into the main frontend task.

---

## Sources Read

| Source | Relevant finding |
|---|---|
| `AI_COLLABORATION_GUIDE.md` | L0 state controls task lifecycle; sidecar packets do not override canonical architecture or policy truth. |
| `.orchestrator/task-briefs/ag_fe_sw_001_sidecar_bff_handoff.md` | Sidecar scope is support-only: BFF query gap, operator journey, frontend handoff material; no canonical truth changes. |
| `.orchestrator/skills/worker-anchor-commit.md` | Task-owned support changes require narrow scope and explicit commit discipline. |
| `.orchestrator/skills/task-closeout-finalization.md` | Repo file changes require task commit, PR, review, merge, then owner closeout before `done`. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-FE-SW-001-SIDECAR-BFF-HANDOFF` | Active sidecar is `in_progress`, owner `Codex`, reviewer `Claude`, artifact target is this file. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-FE-SW-001` | Parent is `todo`; depends on `AG-FE-ID-001` and `AG-XR-OPENAPI-004`; scope is Strategy Workshop shell/page and `workshops.ts` live-strict client. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-BE-SW-001` | Archived `done`; workshop session/event/completeness persistence and list/create/get/message/event/completeness routes are implemented. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-BE-SW-004` | Archived `done`; workshop SSE aggregate stream is implemented and reviewed. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-DES-CARD-001` | Archived `done`; v1.3 typed `WorkshopCard` schema exists for 12 card types. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-DES-SSE-001` | Archived `done`; v1.3 typed workshop SSE event schema exists. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-XR-OPENAPI-004` | Archived `done`; v1.3 OpenAPI/capability/schema bundle merged. |
| `services/control-plane/bff/agora/strategy_workshop/router.py` | Runtime route truth for workshop list/create/get/messages/events/completeness/stream; versions, legacy workshop research-runs, consultations, and conclude are still 501 stubs. |
| `services/control-plane/bff/agora/research/router.py` | Research plan/run route family is implemented in the research router, not the workshop router's legacy `research-runs` stub. |
| `services/control-plane/specs/agora/v4/workshop_card.schema.json` | 12 typed card payloads are schema-defined with constrained payloads; frontend must not infer card type from markdown. |
| `services/control-plane/specs/agora/v4/workshop_stream_event.schema.json` | v1.3 schema defines the target typed event envelope and event catalog. |
| `services/control-plane/openapi/agora_v1_3.openapi.yaml` | Lists `/cards`, research plan/run, patch-proposal, version-comparison, readiness, and stream contract surfaces. |
| `services/control-plane/specs/agora/v4/capability_manifest_v1_3.json` | `agora.workshop.v1` has `execution_authority: none`; `agora.research.v1` is `research_only`. |
| `services/control-plane/bff/tests/test_agora_strategy_workshop.py` | Tests cover workshop persistence, ownership scoping, ETag, idempotency, privacy rule, and completeness snapshots. |
| `services/control-plane/bff/tests/test_workshop_stream_ag_be_sw_004.py` | Tests cover workshop stream headers, first `workshop.connected` ack, replay buffer, message ack fan-out, and 501 deferred stubs. |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/contract-closure/05_execute_plans_agora_ui_ia_and_dependencies.md` | Canonical frontend IA is `/agora/trading-room`, `/agora/strategy-workshop`, `/agora/strategy-performance`; all reads/writes go through `src/lib/bff-v1/agora/*`. |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure-round2/05_workshop_card_contracts.md` | Card field contract source for the Strategy Workshop conversation column and completeness rail. |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure-round2/06_winner_branch_e2e_and_isolation.md` | Cross-user, Agora-vs-Management, privacy, SSE, and no-order acceptance rules. |
| `/home/lupin/code/execute-plans/src/App.tsx` | Current frontend still mounts legacy Agora routes; no `/agora/strategy-workshop` route is present in the checked dev checkout. |
| `/home/lupin/code/execute-plans/src/agora/AgoraLayout.tsx` | Current nav is legacy side-menu IA, not the new three-tab TradingDeskShell IA. |
| `/home/lupin/code/execute-plans/src/lib/bff-v1/paths.ts` | Current path builders do not include workshop route helpers. |
| `/home/lupin/code/execute-plans/src/lib/bff/agora.ts` | Current live adapter covers daily/signals/inbox/journal/ask only; no workshop client is present. |
| `/home/lupin/code/execute-plans/src/lib/bff-v1/agora/types.ts` | Generated Agora types include v1 workshop/completeness/research types but not v1.3 `WorkshopCard`, `WorkshopStreamEvent`, readiness, patch, or version compare types. |

`current-work.md` and the full `ai-activity-log.jsonl` were not scanned.

---

## Current State Summary

`AG-FE-SW-001` is now mostly unblocked at the contract dependency level:

- `AG-XR-OPENAPI-004` is archived `done`; v1.3 OpenAPI/schema/capability bundle
  exists in Pantheon.
- `AG-BE-SW-001` is archived `done`; the core workshop session/event API exists.
- `AG-BE-SW-004` is archived `done`; the workshop SSE stream exists.
- `AG-DES-CARD-001` and `AG-DES-SSE-001` are archived `done`; typed card and
  typed stream schemas exist.

The parent frontend implementation is still not present in the checked
execute-plans dev checkout:

- no `TradingDeskShell` route family;
- no `/agora/strategy-workshop` route;
- no `StrategyWorkshopPage`;
- no `src/lib/bff-v1/agora/workshops.ts`;
- no workshop path builders;
- no v1.3 generated `WorkshopCard` or `WorkshopStreamEvent` frontend types.

The main risk for the parent owner is conflating three different layers:

1. **Runtime-live BFF routes** that can be called now.
2. **v1.3 contract/type-generation routes** present in OpenAPI/schema files but
   not necessarily implemented at runtime.
3. **Frontend page/client work** that still has to be built in execute-plans.

---

## BFF Query Ledger

### Runtime-live for AG-FE-SW-001

| Surface | Runtime status | Frontend handoff rule |
|---|---|---|
| `GET /bff/agora/workshops` | Implemented. Lists user/tenant-scoped workshop sessions with `status`, `cursor`, and `limit` query support. | `workshops.ts.listWorkshops()` can use this for the workshop picker and `/agora/strategy-workshop` landing state. |
| `POST /bff/agora/workshops` | Implemented. Requires `Idempotency-Key`; body is `initial_message`, optional `title`, optional `strategy_spec_ref`, optional `metadata`. | `workshops.ts.createWorkshop()` must send idempotency and must not store raw `initial_message` in frontend persistence. |
| `GET /bff/agora/workshops/{workshop_id}` | Implemented. Returns `ETag` header and `meta.etag` with format `W/"workshop:{id}:v{N}"`. | Detail route `/agora/strategy-workshop/:workshopId` should read and cache the ETag for mutations. |
| `POST /bff/agora/workshops/{workshop_id}/messages` | Implemented. Requires `If-Match` and `Idempotency-Key`; appends a private-content-ref event; stale ETag returns 409 with `current_etag` and `latest_href`. | Composer must fetch detail first, send the current ETag, and handle 409 by refetching. |
| `GET /bff/agora/workshops/{workshop_id}/events` | Implemented. Lists events after optional `after_sequence`. | Conversation timeline can read event history, but raw private text is not present. |
| `GET /bff/agora/workshops/{workshop_id}/completeness` | Implemented. Returns latest completeness snapshot or null-like empty state when no snapshot exists. | Completeness rail can render "not assessed yet" without inventing grades. |
| `GET /bff/agora/workshops/{workshop_id}/stream` | Implemented. Returns `text/event-stream`, immediate `workshop.connected` event, 500-event replay buffer, and heartbeats. | Stream adapter should use `Last-Event-ID`, dedupe by event id, and resync through `GET /workshops/{id}` on gaps. |
| `GET /bff/agora/workshops/{workshop_id}/research-plans` and related research plan/run routes | Implemented in `agora/research/router.py`. | Keep in a separate `research.ts` or later card task unless `AG-FE-SW-001` only needs shell-level links. |

### Contract/type-generation available, runtime not confirmed

| Surface | Contract status | Runtime status | Frontend handoff rule |
|---|---|---|---|
| `WorkshopCard` schema | Present at `services/control-plane/specs/agora/v4/workshop_card.schema.json`. | No inspected BFF handler for `GET /bff/agora/workshops/{id}/cards`. | Generate types, but do not fabricate card projections. Full card rendering belongs to `AG-FE-SW-002` or a runtime route owner. |
| `GET /bff/agora/workshops/{id}/cards` | Present in `agora_v1_3.openapi.yaml`. | No inspected runtime handler in `services/control-plane/bff/agora`. | Parent may prepare client shape behind a disabled adapter, but must not claim live cards. |
| Patch proposals | Present in v1.3 OpenAPI/schema. | No inspected runtime handler. | Do not add patch proposal UI/actions in `AG-FE-SW-001`. |
| Version comparisons | Present in v1.3 OpenAPI/schema. | No inspected runtime handler. | Do not build `VersionCompareCard` as live data unless the route lands first. |
| Readiness gates | Present in v1.3 OpenAPI/schema. | No inspected runtime handler. | Completeness rail can use the existing completeness snapshot; do not claim readiness route integration. |
| Typed stream schema | Present in v1.3 schema. | Runtime currently emits simpler SSE events shaped as `{id,type,timestamp,data}`. | Adapter must bridge actual runtime shape without pretending every schema field is emitted today. |

### Runtime stubs to treat as stop lines

`strategy_workshop/router.py` still returns `501 Not Implemented` for:

- `GET /bff/agora/workshops/{workshop_id}/versions`
- `POST /bff/agora/workshops/{workshop_id}/versions`
- `POST /bff/agora/workshops/{workshop_id}/versions/{version_id}/select`
- `POST /bff/agora/workshops/{workshop_id}/research-runs`
- `POST /bff/agora/workshops/{workshop_id}/consultations`
- `POST /bff/agora/workshops/{workshop_id}/conclude`

Important nuance: plan-first research routes are implemented under
`/bff/agora/workshops/{workshop_id}/research-plans` and
`/bff/agora/research-*`. The legacy workshop-level `research-runs` stub should
not be used by the frontend.

---

## BFF Query Gap Matrix For Parent

| Need | Current disposition | Parent action |
|---|---|---|
| Load workshop list | Resolved by runtime `GET /bff/agora/workshops`. | Implement `workshops.ts` route builder/client and strict-mode tests. |
| Create workshop | Resolved by runtime `POST /bff/agora/workshops`. | Send `Idempotency-Key`; show 400/409 distinctly; do not store raw initial message outside component state. |
| Load workshop detail | Resolved by runtime `GET /bff/agora/workshops/{id}`. | Persist returned ETag in query metadata for the composer. |
| Append workshop message | Resolved by runtime `POST /bff/agora/workshops/{id}/messages`. | Send `If-Match` and `Idempotency-Key`; refetch on 409. |
| Read event history | Resolved by runtime `GET /bff/agora/workshops/{id}/events`. | Render redacted/private-ref event records only; no raw-text reconstruction. |
| Render completeness rail baseline | Partially resolved by runtime `GET /bff/agora/workshops/{id}/completeness`. | Render empty/unassessed state when absent; do not invent grades or next questions. |
| Subscribe to workshop updates | Resolved by runtime `GET /bff/agora/workshops/{id}/stream`. | Use runtime event shape and reconnection rules; do not assume full v1.3 envelope is already emitted. |
| Render typed conversation cards | Contract exists, runtime route missing. | Stop or gate behind `AG-FE-SW-002` / BFF cards route; do not synthesize `WorkshopCard.payload`. |
| Render patch/version/readiness actions | Contract exists, runtime route missing. | Stop until route owner lands implementation. |
| Generate v1.3 frontend types | Backend bundle exists, execute-plans current checkout lacks v1.3 types. | Regenerate or mirror types before strict client/page work. |
| New IA shell | Design exists, current execute-plans checkout still uses legacy Agora routes/nav. | Implement `TradingDeskShell` and route redirects exactly from contract-closure `05`; do not add another side-menu page. |

---

## Operator Journey For AG-FE-SW-001

### Journey A: Open Strategy Workshop landing

1. Operator enters `/agora/strategy-workshop`.
2. Frontend loads Agora identity/capabilities through existing Agora BFF client
   boundaries, then calls `GET /bff/agora/workshops`.
3. If the list is empty, UI shows an empty workshop state and create action.
4. If workshops exist, UI lists scoped sessions and links to
   `/agora/strategy-workshop/:workshopId`.
5. Strict live mode must not fall back to local seed sessions.

### Journey B: Create workshop from an initial hypothesis

1. Operator submits an initial strategy hypothesis.
2. Frontend calls `POST /bff/agora/workshops` with:
   - `Idempotency-Key`
   - `initial_message`
   - optional `title`
   - optional `strategy_spec_ref`
   - optional `metadata`
3. BFF persists a session and an event that stores only a
   `private_content_ref`; raw text must not be persisted by the frontend.
4. UI navigates to `/agora/strategy-workshop/:workshopId`.
5. UI renders the created workshop from the BFF response, not from a local
   optimistic object that invents fields.

### Journey C: Load existing workshop detail

1. Frontend calls `GET /bff/agora/workshops/{workshop_id}`.
2. Frontend stores the returned `ETag` with the query result.
3. Frontend calls `GET /events` and `GET /completeness`.
4. Frontend opens `GET /stream`.
5. The first stream event should be `workshop.connected`; absence of cards or
   completeness is not a successful card projection.

### Journey D: Continue the conversation

1. Operator sends a new message through the composer.
2. Frontend calls `POST /messages` with the latest `If-Match` ETag and a fresh
   `Idempotency-Key`.
3. BFF appends a private-content-ref event and emits `workshop.message.ack`.
4. UI appends the acknowledgement or refetches events.
5. On 409, UI refetches detail/events and asks the operator to retry against
   the fresh ETag; it must not silently overwrite.

### Journey E: Degraded or incomplete backend state

| Backend response | UI behavior |
|---|---|
| 401/403 | Show auth/scope failure; no write controls. |
| 404 | Show missing workshop; do not reveal whether a guessed ID exists for another user. |
| 409 | Refetch detail and events; preserve operator draft locally only in component state. |
| 428 | Fetch detail to obtain ETag, then retry only after explicit user action. |
| 501 | Show not-implemented/blocker state; do not route to internal APIs or seeds. |
| Missing `/cards` route | Show card projection unavailable; do not fabricate typed cards from events or markdown. |
| Stream disconnect/gap | Reconnect with `Last-Event-ID`; on unrecoverable gap, refetch snapshot/detail/events. |

---

## Frontend Handoff

### Files the parent likely owns in execute-plans

| File | Parent intent |
|---|---|
| `src/agora/TradingDeskLayout.tsx` or equivalent shell | Implement the three-tab TradingDeskShell, not another legacy nav page. |
| `src/agora/pages/strategy-workshop/StrategyWorkshopPage.tsx` | Implement Strategy Workshop route/page with conversation column, completeness rail placeholder, and servant composer boundaries. |
| `src/lib/bff-v1/agora/workshops.ts` | Add strict BFF client methods for runtime-live workshop routes. |
| `src/lib/bff-v1/paths.ts` | Add workshop path builders only through canonical `/bff/agora/workshops*` routes. |
| `src/App.tsx` | Add `/agora/trading-room`, `/agora/strategy-workshop`, `/agora/strategy-workshop/:workshopId`, and `/agora/strategy-performance` route entries and redirects per IA decision. |
| `src/agora/AgoraLayout.tsx` or replacement | Replace legacy side-menu-first IA with the design-approved shell route model. |
| `src/lib/bff-v1/agora/types.ts` or generated type source | Refresh to include v1.3 `WorkshopCard`, `WorkshopStreamEvent`, readiness, patch, and version compare types if the parent uses them. |

### Minimum `workshops.ts` client

Suggested client methods for the parent slice:

```ts
type WorkshopClient = {
  listWorkshops(input?: {
    status?: string;
    cursor?: string;
    limit?: number;
  }): Promise<ListEnvelope<StrategyWorkshop>>;

  createWorkshop(
    body: {
      initial_message: string;
      title?: string;
      strategy_spec_ref?: string;
      metadata?: Record<string, unknown>;
    },
    options: { idempotencyKey: string },
  ): Promise<DetailEnvelope<StrategyWorkshop>>;

  getWorkshop(workshopId: string): Promise<DetailEnvelope<StrategyWorkshop> & {
    etag: string;
  }>;

  postWorkshopMessage(
    workshopId: string,
    body: { content: string; attachment_refs?: string[] },
    options: { ifMatch: string; idempotencyKey: string },
  ): Promise<{ event_id: string; sequence_no: number }>;

  listWorkshopEvents(
    workshopId: string,
    input?: { after_sequence?: number },
  ): Promise<ListEnvelope<WorkshopEvent>>;

  getWorkshopCompleteness(
    workshopId: string,
  ): Promise<DetailEnvelope<StrategyCompleteness | null>>;

  openWorkshopStream(
    workshopId: string,
    options?: { lastEventId?: string },
  ): EventSource;
};
```

Adjust names to local conventions, but keep the behavior:

- no direct `fetch()` from pages;
- no local seed fallback in strict live mode;
- no internal `/api/v1/*` calls;
- no Management command clients;
- no broker, capital, canary/live, or `RuntimeBinding` writes.

### Current execute-plans checkout gap

Checked local execute-plans state is detached and read-only for this sidecar.
The current files show:

| Area | Observed state | Parent handoff |
|---|---|---|
| Routes | `/agora` still routes to legacy pages such as `daily`, `notebook`, `ask`, `committee`, `trainer`, `evaluations`; no `/agora/strategy-workshop`. | Parent must add the new IA routes and redirects. |
| Navigation | `AgoraLayout.tsx` still renders grouped side nav. | Parent should use the design-approved TradingDeskShell/tab composition instead of extending this nav as the primary IA. |
| BFF paths | `paths.ts` has Agora signals/inbox/journal/postmortems/ask only. | Add workshop path builders. |
| BFF adapter | `bff/agora.ts` adapts daily/signals/inbox/journal/ask only. | Add or create `bff-v1/agora/workshops.ts`; do not piggyback workshop calls onto generic adapter code with seeds. |
| Generated types | `agora/types.ts` has v1 workshop/completeness/research types but not v1.3 card/stream/readiness/patch/version types. | Refresh generated types from v1.3 before depending on those types. |
| Cards | No inspected StrategyWorkshop card components. | Parent can place placeholders/empty states; full typed card rendering needs `AG-FE-SW-002` and runtime `/cards` truth. |

---

## Stop-Line Blockers To Carry Forward

Use these exact blocker patterns if the parent reaches these edges before the
owning route/task lands.

```text
AG-FE-SW-001 blocked on runtime WorkshopCard projection: v1.3 schema/OpenAPI
define WorkshopCard and GET /bff/agora/workshops/{workshop_id}/cards, but the
inspected BFF routers do not expose that runtime route. Frontend must not
fabricate typed card payloads from markdown, events, or local seed data.
```

```text
AG-FE-SW-001 blocked on v1.3 frontend types: execute-plans current checkout
lacks WorkshopCard, WorkshopStreamEvent, readiness, patch proposal, and version
compare generated types. Regenerate or mirror the v1.3 bundle before strict
typed implementation.
```

```text
AG-FE-SW-001 blocked on version/readiness/patch action runtime: v1.3 OpenAPI
lists patch proposals, version comparisons, and readiness routes, but no
inspected runtime BFF handlers were found. Do not implement live actions or
success states for these surfaces until the owning backend task lands them.
```

```text
AG-FE-SW-001 blocked on route/IA ambiguity if the implementation attempts to
extend legacy Agora side-menu pages instead of the contract-closure primary IA:
/agora/trading-room, /agora/strategy-workshop, and /agora/strategy-performance.
Follow the IA decision or request reviewer clarification.
```

---

## Parent Absorption Checklist

| Check | Expected parent result |
|---|---|
| Branch/worktree | Use a clean execute-plans task branch, not the detached local checkout observed by this sidecar. |
| Design read order | Read contract-closure `05`, round2 `05_workshop_card_contracts.md`, `03_workshop_sse_contract.md`, and `06_winner_branch_e2e_and_isolation.md` before coding. |
| Runtime route client | Implement `workshops.ts` against only runtime-live workshop routes first. |
| Strict mode | No local seed success in live strict mode; errors and 501s remain visible. |
| ETag/idempotency | Detail fetch captures ETag; all message mutations send `If-Match` and `Idempotency-Key`; create sends `Idempotency-Key`. |
| Stream adapter | Adapter accepts current runtime event shape and supports reconnect via `Last-Event-ID`. |
| Query/cache keys | Include tenant/user/workshop in keys; do not leak cross-user state. |
| Card boundary | Do not synthesize card projections until runtime `/cards` is available or the owner narrows scope. |
| No-order policy | Agora workshop UI remains discussion, research, and handoff only; no broker order, capital binding, RuntimeBinding, or live promotion. |
| Tests | Add path/client tests, strict fallback tests, ETag/idempotency tests, stream adapter tests, and route guard tests. |

---

## Reviewer Checklist

Claude should verify:

| Check | Expected result |
|---|---|
| Scope | Only this support artifact is intentionally authored by the sidecar. |
| Runtime accuracy | Workshop route ledger matches `strategy_workshop/router.py` and BFF tests. |
| Contract/runtime distinction | `/cards`, patch, readiness, and version compare are not claimed as runtime-live. |
| Frontend accuracy | Current execute-plans checkout is described as missing route/page/client/type work. |
| Stop lines | Parent blockers are explicit and prevent invented fields/routes/actions. |
| Safety boundary | Packet preserves Agora no-order/no-capital/no-RuntimeBinding authority. |

Recommended reviewer approval command after PR review:

```bash
AI_NAME=Claude REVIEW_FILE=support/sidecars/AG-FE-SW-001/AG-FE-SW-001-SIDECAR-BFF-HANDOFF.md \
  REVIEW_NOTES_ZH="Support-only AG-FE-SW-001 BFF/frontend handoff packet approved: runtime-live workshop routes, v1.3 contract/type-generation boundaries, frontend gaps, stop-line blockers, and no-order safety guardrails are documented without canonical/runtime changes." \
  ./scripts/ai-status.sh approve AG-FE-SW-001-SIDECAR-BFF-HANDOFF \
  "Support-only AG-FE-SW-001 BFF/frontend handoff packet approved for parent owner absorption."
```

Recommended reviewer reopen command:

```bash
AI_NAME=Claude ./scripts/ai-status.sh reopen AG-FE-SW-001-SIDECAR-BFF-HANDOFF \
  "Describe the factual correction, missing route distinction, or parent handoff detail needed before approval."
```

---

## Validation

Focused validation run from this task worktree:

```bash
LC_ALL=C rg -n "[^[:ascii:]]" support/sidecars/AG-FE-SW-001/AG-FE-SW-001-SIDECAR-BFF-HANDOFF.md
git diff --no-index --check /dev/null support/sidecars/AG-FE-SW-001/AG-FE-SW-001-SIDECAR-BFF-HANDOFF.md
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  services/control-plane/bff/tests/test_agora_strategy_workshop.py \
  services/control-plane/bff/tests/test_workshop_stream_ag_be_sw_004.py \
  services/control-plane/bff/tests/test_agora_research_run_projection.py \
  -q
```

Results:

- ASCII scan: no output.
- Diff whitespace check: no output.
- Focused BFF tests: `81 passed in 156.81s`.

No canonical truth, runtime, schema, or execute-plans files are changed by this
sidecar.
