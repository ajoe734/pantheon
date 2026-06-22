# AG-FE-SW-001 Sidecar Follow-up 4: Parent Branch Gap Ledger

| Field | Value |
|---|---|
| Task ID | `AG-FE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-4` |
| Helper kind | `bff_handoff_packet` |
| Parent task | `AG-FE-SW-001` - TradingDeskShell + Strategy Workshop tab |
| Parent owner / reviewer | `Claude` / `Codex` |
| Sidecar owner / reviewer | `Codex` / `Claude` |
| Date | 2026-06-22 |
| Pantheon dev base inspected | `1f13146d2b69e76d6c5b2cc96c45d2e9d1ce0910` |
| execute-plans dev ref inspected | `origin/dev` at `40fef8769435fa479c87c2892417a76186913ecf` |
| execute-plans parent branch inspected | `origin/task/AG-FE-SW-001` at `476aa043c3b5196823a50106f956331262123b40` |
| Prior packets | `AG-FE-SW-001-SIDECAR-BFF-HANDOFF.md`, `FOLLOWUP-2.md`, `FOLLOWUP-3.md` |
| Mutates canonical truth | `false` |
| Status | Ready for reviewer handoff |

This is a support-only follow-up for the active AG-FE-SW-001 parent branch. It
does not edit L1 truth, OpenAPI, JSON schemas, BFF runtime, route registry,
governance/runtime code, or execute-plans frontend source. The parent owner
decides whether to absorb this gap ledger into the main frontend task.

---

## Sources Rechecked

| Source | Follow-up finding |
|---|---|
| `AI_COLLABORATION_GUIDE.md` | Sidecar packets are support records and do not override canonical architecture or policy truth. |
| `.orchestrator/task-briefs/ag_fe_sw_001_sidecar_bff_handoff_followup_4.md` | Scope is support-only BFF query gap, operator journey, and frontend handoff material; no canonical truth changes. |
| `.orchestrator/skills/worker-anchor-commit.md` | Meaningful support-file progress must be committed with explicit task scope. |
| `.orchestrator/skills/task-closeout-finalization.md` | Repo file changes require task commit, PR, review, merge, then owner closeout before `done`. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-FE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-4` | Active task is `in_progress`, owner `Codex`, reviewer `Claude`, artifact target is this file. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-FE-SW-001` | Parent remains `in_progress`; acceptance requires exact spec/schema alignment, `workshops.ts` live strict, no invented fields/routes/widgets, and no Management exposure. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-FE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3` | Prior support packet is archived `done`; PR #2229 merged at `df924c80c03989db2c1d51d16f4c62e0bb9486d3`. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-FE-ID-001` | Agora identity/servant shell remains `done`; execute-plans PR #66 merged identity/servant clients and `AgoraApp`. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-XR-OPENAPI-004` | v1.3 OpenAPI/capability/schema bundle remains archived `done`; frontend type generation remains downstream. |
| `support/sidecars/AG-FE-SW-001/AG-FE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3.md` | Prior stop lines remain valid and are directly relevant to the active parent branch. |
| `services/control-plane/bff/agora/strategy_workshop/router.py` | Runtime-live workshop routes are list/create/get/messages/events/completeness/stream. Versions, legacy `research-runs`, consultations, and conclude remain explicit 501 stubs. |
| `services/control-plane/bff/agora/research/router.py` | Plan-first research routes exist under `/research-plans` and `/research-runs`; they are not the legacy workshop-level `research-runs` stub. |
| `services/control-plane/bff/tests/test_agora_strategy_workshop.py` | BFF tests enforce `initial_message`, mandatory `Idempotency-Key`, `If-Match`, ETag format, 409 recovery details, `after_sequence`, and private content refs. |
| `services/control-plane/bff/tests/test_workshop_stream_ag_be_sw_004.py` | Stream tests enforce `workshop.connected`, `workshop.message.ack`, `Last-Event-ID`, replay, and stream headers. |
| `/home/lupin/code/execute-plans origin/dev` | `origin/dev` is still PR #66 (`AG-FE-ID-001`) and lacks the Strategy Workshop parent implementation. |
| `/home/lupin/code/execute-plans origin/task/AG-FE-SW-001` | Parent branch exists with commit `476aa04` adding `TradingDeskLayout.tsx`, `StrategyWorkshopPage.tsx`, `workshops.ts`, and `App.tsx` route wiring. |

`current-work.md` and the full `ai-activity-log.jsonl` were not scanned.

---

## Delta From Follow-up 3

Follow-up 3 said AG-FE-SW-001 had not yet produced a parent implementation.
That has changed. The execute-plans parent branch now contains a single skeleton
commit:

```text
476aa04 AG-FE-SW-001: TradingDeskShell + Strategy Workshop tab skeleton
```

Changed frontend files on that branch:

```text
M src/App.tsx
A src/agora/TradingDeskLayout.tsx
A src/agora/pages/StrategyWorkshopPage.tsx
A src/lib/bff-v1/agora/workshops.ts
```

The shell/page direction is useful, but the current branch does not yet satisfy
the earlier support-packet stop lines. It should be treated as a draft needing
correction before review, not as a ready parent implementation.

---

## Parent Branch Findings

| Area | Observed parent branch state | Handoff disposition |
|---|---|---|
| Trading desk shell | `TradingDeskLayout.tsx` adds command bar, three-tab top nav, right servant drawer placeholder, bottom strip, and `<Outlet />`. | Direction matches the requested shell skeleton, but servant readiness from `identity.ts`/`servant.ts` is not integrated into this new shell. |
| Route mount | `App.tsx` adds a new `/agora` route with `TradingDeskLayout`, then keeps the existing sibling `/agora` route with `AgoraLayout`. | Reviewer should require a single unambiguous `/agora` route tree or explicit redirects. Two sibling `/agora` routes risk legacy AgoraLayout winning or leaving deep links ambiguous. |
| Deep links | `/agora/trading-room`, `/agora/strategy-workshop`, `/agora/strategy-workshop/:workshopId`, and `/agora/strategy-performance` are added under `TradingDeskLayout`. | Good start, but `/agora` index and legacy route redirect behavior are not resolved in the inspected diff. |
| `paths.ts` | No workshop path builders were added. | Parent acceptance requested `workshops.ts` live strict through BFF boundaries. Follow-up 3 asked for workshop builders in `paths.ts`; current branch still hardcodes strings in `workshops.ts`. |
| `workshops.ts` envelope shape | `listWorkshops()` expects `{items, cursor}`; `createWorkshop()` and `getWorkshop()` expect bare `StrategyWorkshop`. Runtime returns `{data, meta}` envelopes and GET detail also returns HTTP `ETag`. | Must adapt to runtime envelopes and expose/carry the ETag. Current client will misread live responses. |
| `createWorkshop()` request | Client sends `{subject, participant_persona_ids, metadata}`. Runtime requires `{initial_message, title?, strategy_spec_ref?, metadata?}` and rejects extra fields. | This is a blocking contract mismatch. |
| Mutation headers | `createWorkshop()` relies on generic POST idempotency injection, but the public API has no explicit option. `postWorkshopMessage()` does not pass `If-Match` or expose idempotency. | `POST /messages` will return 428 in live mode. Parent must GET detail first, retain `ETag`, and pass `ifMatchVersion` or exact `If-Match` through the BFF client. |
| Events query | `listWorkshopEvents()` sends `after`; runtime expects `after_sequence`. | Blocking query mismatch for incremental event history. |
| Conversation source | `StrategyWorkshopPage` loads `listWorkshopCards()` and renders card payload JSON. Runtime has no inspected `/cards` handler. | Violates stop line. Use runtime `/events` and completeness baseline, or show card projection unavailable. |
| Readiness source | `StrategyWorkshopPage` calls `getWorkshopReadiness()`. Runtime has no inspected readiness handler. | Violates stop line. Do not call readiness in AG-FE-SW-001 unless the runtime route lands. |
| Deferred actions | `workshops.ts` exposes versions, `research-run`, `consultation`, `conclude`, cards, readiness, reassess. | Versions/conclude are 501, cards/readiness lack inspected handlers, and `research-run`/`consultation` use singular paths that do not match the runtime stubs. Remove or quarantine behind later tasks. |
| Plan-first research | Current branch uses legacy/singular `research-run`; runtime-live research is `/workshops/{id}/research-plans` and `/research-plans/{id}/runs`. | Do not route research through workshop-level legacy stubs in this parent slice. |
| SSE | `openWorkshopStream()` exists but the page does not use it. It also has no `Last-Event-ID` path or dedupe/resync handling. | Keep as later work or implement against actual runtime SSE shape `{id,type,timestamp,data}`. |
| List/detail navigation | Page uses raw `<a href=...>` links. | Not the main BFF contract risk, but reviewer may prefer React Router links to avoid full reloads inside the shell. |
| Create journey | Page has no create action despite the route client exposing `createWorkshop()`. | AG-FE-SW-001 acceptance says the Strategy Workshop page can create/load a workshop; current branch loads but does not create. |
| Identity/servant readiness | `TradingDeskLayout` does not call `identity.ts` or `servant.ts`; `AgoraApp` still contains that logic but is not mounted by the new route tree. | Parent should either merge AG-FE-ID readiness into `TradingDeskLayout` or mount the AG-FE-ID shell intentionally. |

---

## Blocking Corrections For Parent Review

These are the highest-risk corrections to request before AG-FE-SW-001 review.

1. Remove the second sibling `/agora` route ambiguity in `App.tsx`.
2. Add or use workshop path builders in `src/lib/bff-v1/paths.ts`; avoid hardcoded workshop strings in `workshops.ts`.
3. Make `workshops.ts` match runtime envelopes:
   - `GET /workshops` returns `{data, meta.next_cursor}`;
   - `POST /workshops` accepts `initial_message`, optional `title`, `strategy_spec_ref`, `metadata`;
   - `GET /workshops/{id}` returns `{data, meta.etag}` and an HTTP `ETag`;
   - `POST /messages` returns `{data: {event_id, sequence_no}, meta}`.
4. Make message append require a current ETag and idempotency key at the public API boundary; do not rely on an implicit POST helper while omitting `If-Match`.
5. Change event pagination from `after` to `after_sequence`.
6. Stop loading `/cards` and `/readiness` in `StrategyWorkshopPage` until runtime handlers exist.
7. Remove or quarantine client methods for versions, cards, readiness, readiness reassess, conclude, singular `research-run`, and singular `consultation` from the parent slice.
8. Render runtime events and completeness empty state, not fabricated typed card projections.
9. Add the create-workshop journey or explicitly leave the parent task blocked, because current page only lists/loads.
10. Reuse AG-FE-ID identity/servant readiness in the mounted shell, not the unmounted `AgoraApp` placeholder.

---

## Runtime-Live Surface To Keep In Parent

The parent branch should narrow to these runtime-live routes first.

| Need | Runtime route | Required parent behavior |
|---|---|---|
| List workshops | `GET /bff/agora/workshops` | Parse `data` and `meta.next_cursor`; keep user/tenant-scoped query keys. |
| Create workshop | `POST /bff/agora/workshops` | Body is `initial_message`, optional `title`, optional `strategy_spec_ref`, optional `metadata`; send `Idempotency-Key`; do not persist raw prompt. |
| Load detail | `GET /bff/agora/workshops/{workshop_id}` | Capture HTTP `ETag` and/or `meta.etag` for future mutations. |
| Append message | `POST /bff/agora/workshops/{workshop_id}/messages` | Body is `content` plus optional `attachment_refs`; send `If-Match` and `Idempotency-Key`; handle 428 and 409. |
| Event history | `GET /bff/agora/workshops/{workshop_id}/events` | Query is `after_sequence`; render private-content refs/redacted records only. |
| Completeness baseline | `GET /bff/agora/workshops/{workshop_id}/completeness` | Treat `data: null` as unassessed; do not invent grades. |
| SSE stream | `GET /bff/agora/workshops/{workshop_id}/stream` | Expect `workshop.connected` first; support `Last-Event-ID`, dedupe by SSE id, and refetch snapshot on gaps. |

Plan-first research routes are runtime-live, but should stay out of this parent
slice unless the parent explicitly narrows into research cards:

```text
GET|POST /bff/agora/workshops/{workshop_id}/research-plans
GET      /bff/agora/research-plans/{plan_id}
POST     /bff/agora/research-plans/{plan_id}/approve
POST     /bff/agora/research-plans/{plan_id}/cancel
GET|POST /bff/agora/research-plans/{plan_id}/runs
GET      /bff/agora/research-runs/{run_id}
POST     /bff/agora/research-runs/{run_id}/cancel
GET      /bff/agora/research-runs/{run_id}/artifacts
```

---

## Stop Lines To Preserve

```text
Do not let the active AG-FE-SW-001 parent branch call
GET /bff/agora/workshops/{workshop_id}/cards or readiness routes as live data.
The v1.3 contract exists, but the inspected BFF runtime does not expose those
handlers.
```

```text
Do not expose versions, conclude, legacy workshop-level research-runs, or
consultations as successful frontend actions. The inspected runtime returns
explicit 501 stubs for versions, research-runs, consultations, and conclude;
the active parent branch also uses singular research-run/consultation paths
that do not match the registered stubs.
```

```text
Do not treat WorkshopCard, WorkshopReadinessAssessment, or WorkshopStreamEvent
types manually declared in workshops.ts as generated v1.3 frontend truth. If the
parent needs these types, regenerate or mirror the v1.3 bundle explicitly and
only wire routes that are runtime-live.
```

```text
Do not satisfy Strategy Workshop mutation acceptance without an ETag path.
Runtime requires If-Match for POST /messages and returns 428 without it.
```

```text
Do not leave duplicate sibling /agora route trees as the parent IA solution.
The implementation must make the design-approved TradingDeskShell reachable
without ambiguity and decide how legacy Agora routes redirect or remain scoped.
```

---

## Reviewer Checklist

Claude should verify:

| Check | Expected result |
|---|---|
| Scope | This sidecar authored only this support artifact. |
| Parent branch delta | Packet reflects `origin/task/AG-FE-SW-001` at `476aa04`, not only `origin/dev`. |
| Contract mismatches | `createWorkshop`, envelopes, ETag/If-Match, `after_sequence`, cards/readiness, deferred actions, and duplicate `/agora` routing are called out. |
| Runtime accuracy | Route ledger matches `strategy_workshop/router.py` and `research/router.py`; no `/cards` or readiness handler is claimed live. |
| Safety boundary | Packet preserves Agora no-order/no-capital/no-RuntimeBinding authority and does not suggest Management route reuse. |

Recommended reviewer approval command after PR review:

```bash
AI_NAME=Claude REVIEW_FILE=support/sidecars/AG-FE-SW-001/AG-FE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-4.md \
  REVIEW_NOTES_ZH="Support-only follow-up packet approved: AG-FE-SW-001 parent branch gap ledger documents the active execute-plans skeleton, runtime-live BFF surface, blocking client/page mismatches, duplicate /agora route risk, and stop lines without canonical/runtime/schema/frontend changes." \
  ./scripts/ai-status.sh approve AG-FE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-4 \
  "Support-only AG-FE-SW-001 parent branch gap ledger approved for parent owner absorption."
```

Recommended reviewer reopen command:

```bash
AI_NAME=Claude ./scripts/ai-status.sh reopen AG-FE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-4 \
  "Describe the factual correction, missing route distinction, or parent branch handoff detail needed before approval."
```

---

## Validation

Focused validation run from this task worktree:

```bash
LC_ALL=C rg -n "[^[:ascii:]]" support/sidecars/AG-FE-SW-001/AG-FE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-4.md
git diff --no-index --check /dev/null support/sidecars/AG-FE-SW-001/AG-FE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-4.md
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
- Focused BFF tests: `81 passed in 247.10s`.
