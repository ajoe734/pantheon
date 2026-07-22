# AG-DYNUI-FULL-002 BFF and Frontend Handoff Packet

| Field | Value |
|---|---|
| Parent task | `AG-DYNUI-FULL-002` |
| Parent title | Implement live Strategy Workshop cards and readiness BFF |
| Parent owner / reviewer | `Codex` / `Codex2` |
| Sidecar task | `AG-DYNUI-FULL-002-SIDECAR-BFF-HANDOFF` |
| Sidecar owner / reviewer | `Codex2` / `Codex` |
| Helper kind | `bff_handoff_packet` |
| Generated | `2026-07-05` |
| Mutates canonical truth | `false` |
| Status | Ready for reviewer handoff |

This is a support artifact only. It does not change L1 canonical truth,
OpenAPI truth, route registries, BFF/runtime code, frontend code, governance
behavior, or parent task approval. The parent owner decides whether and how to
absorb this packet into `AG-DYNUI-FULL-002`.

## 1. Sources Read

| Source | Relevant finding |
|---|---|
| `AI_COLLABORATION_GUIDE.md` | L0 state coordinates task ownership; support packets do not override canonical architecture or task lifecycle truth. |
| `.orchestrator/task-briefs/ag_dynui_full_002_sidecar_bff_handoff.md` | Sidecar scope is support-only: BFF query gap, operator journey, and frontend handoff materials; no canonical truth changes. |
| `.orchestrator/skills/worker-anchor-commit.md` | Meaningful support-doc work should be committed through task branch workflow with explicit scope. |
| `.orchestrator/skills/task-closeout-finalization.md` | `review_approved` closeout requires task-scoped artifact verification, commit, PR, merge, then `done`; this sidecar is still `in_progress`. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-DYNUI-FULL-002-SIDECAR-BFF-HANDOFF` | Sidecar is `in_progress`, owner `Codex2`, reviewer `Codex`, artifact path is this file, helper parent is `AG-DYNUI-FULL-002`, `mutates_canonical=false`. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-DYNUI-FULL-002` | Parent is `in_progress`; acceptance requires live `cards`, `readiness`, and `readiness/reassess`; unknown/cross-tenant 404/403 envelopes; dict and `OperatorIdentity` identity tests; contract snapshot update if required; post-deploy live curl proof. |
| `docs/04/pantheon_agora_dynui_full_production_recovery_2026-07-05/INDEX.md` | Current production gap: hosted Trading Room can load but strategy aggregate is empty; live workshop `cards` and `readiness` are not production-level. |
| `docs/bff/execution-tasks/2026-07-05-agora-dynui-full-production-recovery/INDEX.md` | Wave 1 task `AG-DYNUI-FULL-002` owns live Strategy Workshop cards/readiness BFF routes and tests before downstream Trading Room materialization/E2E can be real. |
| `docs/bff/execution-tasks/2026-07-05-agora-dynui-full-production-recovery/AG-DYNUI-FULL-001-source-truth-and-parity-matrix.md` | FULL-001 routes `cards/readiness` and ready-strategy materialization to FULL-002/FULL-003; prior fixture-backed E2E is not production proof. |
| `support/evidence/AG-DYNUI-FULL-001-finalization.md` and `support/reviews/AG-DYNUI-FULL-001-review-claude2.md` | FULL-001 closed only source truth/parity matrix; residual work intentionally remains with FULL-002..007. |
| `services/control-plane/bff/agora/strategy_workshop/router.py` | Current local router has list/create/get/messages/events/completeness/stream and several deferred 501 stubs; it does not define `cards`, `readiness`, or `readiness/reassess`. |
| `services/control-plane/bff/agora/strategy_workshop/store.py` | Current store persists sessions, events, completeness snapshots, and idempotency keys only; no card projection or readiness assessment storage helpers exist yet. |
| `services/control-plane/openapi/agora_v1_3.openapi.yaml` and `services/control-plane/specs/agora/v4/*` | v1.3 OpenAPI/spec files already describe `WorkshopCard`, `StrategyReadinessAssessment`, and the three target paths, but the live/local FastAPI router does not currently expose them. |
| `services/control-plane/bff/contract_snapshots/backend_routes_manifest.json` | No target workshop `cards/readiness/reassess` paths are present in the backend route manifest snapshot. |
| `execute-plans` `origin/dev` at `f90959ae1fdd32c427ec86af1cc6d53065221357` | Frontend client and page already call `listWorkshopCards()` and `getWorkshopReadiness()`, but type/header/route-context gaps remain. Local execute-plans checkout is dirty/diverged and was not used as implementation truth. |
| Hosted dev BFF probes, `2026-07-05T11:55Z` | `/openapi.json` returns 200 but exposes no target `cards/readiness/reassess` paths. Authenticated calls to the three target paths return standardized 404 envelopes with correlation IDs. |

`current-work.md` and the full `ai-activity-log.jsonl` were intentionally not
scanned.

## 2. Current Gap Summary

`AG-DYNUI-FULL-002` should close the first live workshop gap in the full
production recovery packet: cards and readiness must come from scoped workshop
state/store, not from frontend fixtures or static design copies.

Current state:

- Local BFF code does not have route handlers for:
  - `GET /bff/agora/workshops/{workshop_id}/cards`
  - `GET /bff/agora/workshops/{workshop_id}/readiness`
  - `POST /bff/agora/workshops/{workshop_id}/readiness/reassess`
- Local specs/OpenAPI v1.3 describe those paths and schemas, but route
  implementation and route-manifest/live OpenAPI exposure are not aligned.
- Hosted dev BFF currently lists workshop CRUD/completeness/events/stream and
  related stubs, but not cards/readiness/reassess.
- execute-plans `origin/dev` already calls these client functions from
  `StrategyWorkshopPage`, so once the BFF lands there is an existing frontend
  consumer path.
- The frontend readiness type and reassess write posture need alignment before
  parent/frontend closeout can be claimed.

No sidecar-owned code change is made here.

## 3. BFF Query Gap Matrix

| Need | Current BFF state | Handoff guidance |
|---|---|---|
| Scoped cards read | No route handler in `strategy_workshop/router.py`; live OpenAPI does not expose the path. v1.3 schema defines typed `WorkshopCard` with 12 card types. | Add a scoped read route that first resolves Agora scope, verifies workshop ownership, then returns deterministic typed cards from workshop state/store. Do not infer card type from free LLM output. |
| Cards projection storage | `MemoryWorkshopStore` and `PostgresWorkshopStore` store sessions/events/completeness snapshots only. | Parent must either add explicit card projection persistence/helpers or define a deterministic projection from existing events/completeness snapshots. The output must be stable enough for `after_sequence`/ordered UI refresh. |
| Readiness read | No route handler; v1.3 `strategy_readiness.schema.json` expects `assessment_id`, `workshop_id`, `strategy_id`, `workshop_version_id`, `strategy_spec_registry_id`, `gates[3]`, `highest_ready_gate`, `assessment_version`, and evidence refs. | Return `data: null` only for an existing scoped workshop with no assessment if that is the chosen compatibility behavior; unknown or cross-scope workshops must still use 404/403 envelopes. |
| Readiness reassess | No route handler. OpenAPI v1.3 currently models reassess as `202 CommandResponse` with `If-Match` and `Idempotency-Key`; parent acceptance says reassess "updates or returns a fresh readiness assessment." | Parent should settle the response contract before frontend absorption: either synchronous `StrategyReadinessAssessment` or async command response plus subsequent read. In both cases preserve idempotency and optimistic concurrency where declared. |
| Unknown workshop | Existing `get_workshop`, `events`, `completeness`, and `stream` routes return BFF 404 when `store.get_session()` is absent. | Target routes should use the same envelope pattern, not FastAPI default/plain 404 and not 500. |
| Cross-tenant/user workshop | Existing routes call `_raise_cross_user_forbidden()` after loading a session. | Target routes must return 403 for ownership mismatch; tests should seed another tenant/user and assert no cards/readiness data leaks. |
| Identity shape | `strategy_workshop._scope()` delegates to `resolve_agora_user_scope()`, which reads attribute-style `OperatorIdentity`. Existing direct router injection tests only assert construction with a dict, not route calls. Trading Room tests have a regression fixture for attribute-style identity. | Parent tests should cover both dict-style test identity and production-like `OperatorIdentity`/attribute identity. If dict support is required, normalize before calling `resolve_agora_user_scope()` or mirror Trading Room's defensive scope extraction. |
| Contract exposure | Local `agora_v1_3.openapi.yaml` has the target paths; backend route manifest snapshot and hosted `/openapi.json` do not. | Parent closeout must prove implementation, route manifest/snapshot update if required, and hosted OpenAPI path visibility after deploy. |
| Live proof | Authenticated nonexistent-workshop probes currently return 404, but target paths are absent from live OpenAPI, so this does not prove route implementation. | Parent live proof must use an existing scoped workshop and show 200 for cards/readiness plus a successful reassess path; then separately show unknown/cross-tenant 404/403. |

## 4. Frontend Handoff Matrix

Frontend source truth for this packet is execute-plans `origin/dev`
`f90959ae1fdd32c427ec86af1cc6d53065221357`, not the dirty local checkout.

| Surface | Current state on `origin/dev` | Parent/frontend absorption guidance |
|---|---|---|
| `src/lib/bff-v1/agora/workshops.ts` | Exposes `listWorkshopCards`, `getWorkshopReadiness`, and `reassessWorkshopReadiness`. `getWorkshopReadiness()` treats `{ data: null }` and 404 as not assessed. | Keep this module as the BFF seam; page components should not add ad hoc fetches. Align return types and headers with the final BFF response contract. |
| Readiness type | `WorkshopReadinessAssessment` in `workshops.ts` is an older shape (`gate`, `passed`, `blockers`) and does not include schema fields like `highest_ready_gate` and `gates`. `StrategyWorkshopPage.tsx` imports `StrategyReadinessAssessment`, which is not exported by `workshops.ts`, and uses `readiness.highest_ready_gate`. | Parent or downstream FE task should align the exported type name and schema shape before relying on TypeScript/build proof. This sidecar does not patch execute-plans. |
| Reassess client | `reassessWorkshopReadiness()` sends only `{ gate }` body and returns `WorkshopReadinessAssessment`. It does not send `If-Match` or `Idempotency-Key`. | If BFF keeps OpenAPI v1.3 header requirements and/or async `202`, update the client signature and tests. If BFF intentionally returns sync assessment without those headers, update OpenAPI/spec snapshots accordingly. |
| Initial workshop load | `StrategyWorkshopPage` calls `getWorkshop`, `getWorkshopCompleteness`, `getWorkshopReadiness`, and `listWorkshopCards` when `workshopId` is present. | BFF can land first; the page already has a direct consumer for cards/readiness. Parent should include frontend smoke after live BFF deploy. |
| SSE refresh | Page refreshes completeness on `workshop.completeness.updated`, readiness on `workshop.readiness.updated`, and cards on several research/patch/version events. | Reassess implementation should publish `workshop.readiness.updated` or parent should document why polling/readback is used instead. |
| Add to Trading Room CTA | Page enables the button only when `readiness.highest_ready_gate === "trading_room"` and an `onAddToTradingRoom` handler exists. | FULL-002 needs readiness truth; explicit strategy/version navigation remains downstream `AG-DYNUI-FULL-004`. |
| Agora route wrapper | `routes/agora.tsx` passes `workshopId` into `StrategyWorkshopPage` and provides `onAddToTradingRoom`, but the handler currently navigates to `/agora/trading-room` without `strategyId`/`strategyVersion`. | Do not solve this in FULL-002 unless parent explicitly absorbs it. Record as dependency for FULL-004/003 materialization. |
| Frontend tests | `workshops.test.ts` covers list/get, `{ data: null }` completeness/readiness compatibility, card envelope aliases, and SSE parse. It does not cover reassess headers, readiness schema fields, or TypeScript export alignment. | Add/expect targeted client tests only if parent touches execute-plans. Otherwise leave this as handoff to the frontend owner. |

## 5. Operator Journey Packet

Target operator flow once parent BFF work lands:

1. Operator opens `/agora/strategy-workshop/{workshopId}` on the hosted dev FE.
2. FE loads the scoped workshop from `GET /bff/agora/workshops/{workshopId}`.
3. FE loads typed cards from `GET /bff/agora/workshops/{workshopId}/cards`.
   Cards render through `WorkshopCardRenderer`; no card type is inferred from
   raw LLM text.
4. FE loads readiness from `GET /bff/agora/workshops/{workshopId}/readiness`.
   The rail and hidden test shim read `highest_ready_gate`.
5. Operator or system triggers reassessment through
   `POST /bff/agora/workshops/{workshopId}/readiness/reassess`.
6. BFF updates/returns a fresh readiness assessment and/or emits
   `workshop.readiness.updated`.
7. FE refreshes readiness; if `highest_ready_gate` reaches `trading_room`, the
   Add to Trading Room CTA becomes active.
8. Downstream `AG-DYNUI-FULL-003/004` must then materialize a ready strategy
   and navigate with strategy/version context. FULL-002 should not fabricate
   strategy ids or versions to make the CTA appear complete.

Degraded states to preserve:

- No assessment yet for an existing scoped workshop: show not assessed or
  disabled reason, not a hard product error.
- Unknown workshop: 404 BFF envelope with correlation id.
- Cross-tenant/user workshop: 403 BFF envelope with no leaked payload.
- Reassess conflict/missing precondition: typed 409/412/428/400 behavior,
  depending on the final contract; do not silently retry or create a fake ready
  state.

## 6. Parent Implementation Checklist

Parent implementation/review should verify:

- `strategy_workshop/router.py` defines the three target routes with the same
  auth/scope envelope style as existing workshop routes.
- Target routes check `store.get_session()` before any projection read/write
  and enforce tenant/user match.
- Store support covers both memory and Postgres backends, or the route clearly
  documents a non-production backend limitation and does not claim production
  proof.
- Cards are ordered, typed, scoped, and stable; `after_sequence` and `limit`
  query behavior should match the frontend client's existing parameters or the
  client should be updated.
- Readiness payload matches `strategy_readiness.schema.json`, especially
  `highest_ready_gate` and the three-gate array.
- Reassess contract is reconciled between BFF implementation, OpenAPI v1.3,
  and execute-plans `reassessWorkshopReadiness()`.
- Tests cover dict-style test identity and production-like
  `OperatorIdentity`/attribute identity.
- Tests cover:
  - existing scoped workshop returns 200 cards;
  - existing scoped workshop returns 200 readiness or documented `data: null`;
  - reassess returns/queues fresh readiness;
  - unknown workshop returns 404 envelope;
  - cross-tenant/user workshop returns 403 envelope;
  - no route raises `INTERNAL_ERROR` for expected missing/cross-scope cases.
- Backend route manifest/OpenAPI snapshots are updated if the repository route
  manifest gate requires it.
- Hosted dev deploy proof shows the target paths in `/openapi.json` and 200
  live curls against an existing scoped workshop.

## 7. Recommended Closeout Evidence For Parent

Before `AG-DYNUI-FULL-002` moves to review/done, record:

- Pantheon branch, PR URL, merge commit SHA, and Branch CI result.
- Local validation commands and exact results, for example:
  - `pytest services/control-plane/bff/tests/test_agora_strategy_workshop.py -q`
  - any new focused route tests for cards/readiness/reassess
  - route manifest/OpenAPI snapshot check used by the repo
- If execute-plans changes are needed: execute-plans branch, PR, checks, merge
  SHA, and dev FE deploy evidence.
- Dev BFF deploy run or image/source SHA.
- Hosted live curl proof:

```sh
curl -fsS \
  -H 'Authorization: Bearer pantheon-dev-browser:operator,reviewer,approver:mfa' \
  -H 'X-Tenant-Id: pantheon-dev' \
  https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/openapi.json \
  | jq -r '.paths | keys[] | select(test("/bff/agora/workshops/.*/(cards|readiness)"))'

curl -fsS \
  -H 'Authorization: Bearer pantheon-dev-browser:operator,reviewer,approver:mfa' \
  -H 'X-Tenant-Id: pantheon-dev' \
  https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/bff/agora/workshops/<workshop_id>/cards

curl -fsS \
  -H 'Authorization: Bearer pantheon-dev-browser:operator,reviewer,approver:mfa' \
  -H 'X-Tenant-Id: pantheon-dev' \
  https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/bff/agora/workshops/<workshop_id>/readiness

curl -fsS \
  -X POST \
  -H 'Authorization: Bearer pantheon-dev-browser:operator,reviewer,approver:mfa' \
  -H 'X-Tenant-Id: pantheon-dev' \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: <uuid>' \
  -H 'If-Match: <etag-if-required>' \
  --data '{"gate":"trading_room"}' \
  https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/bff/agora/workshops/<workshop_id>/readiness/reassess
```

Also record negative proof for unknown and cross-tenant/cross-user workshop
ids, including status code, error code, and correlation id.

## 8. Verification Notes For This Sidecar

No runtime, canonical, BFF, registry, or frontend implementation was changed by
this sidecar. Verification was source inspection plus read-only live probes:

```sh
git status -sb
git branch --show-current
git remote -v
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-DYNUI-FULL-002-SIDECAR-BFF-HANDOFF
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-DYNUI-FULL-002
sed -n '1,240p' AI_COLLABORATION_GUIDE.md
sed -n '1,260p' .orchestrator/task-briefs/ag_dynui_full_002_sidecar_bff_handoff.md
sed -n '1,220p' .orchestrator/skills/worker-anchor-commit.md
sed -n '1,260p' .orchestrator/skills/task-closeout-finalization.md
sed -n '1,220p' docs/bff/execution-tasks/2026-07-05-agora-dynui-full-production-recovery/INDEX.md
sed -n '1,240p' docs/bff/execution-tasks/2026-07-05-agora-dynui-full-production-recovery/AG-DYNUI-FULL-001-source-truth-and-parity-matrix.md
sed -n '1,180p' docs/04/pantheon_agora_dynui_full_production_recovery_2026-07-05/INDEX.md
rg -n "cards|readiness|reassess|@router" services/control-plane/bff/agora/strategy_workshop/router.py
rg -n "cards|readiness|reassess" services/control-plane/bff/agora/strategy_workshop/store.py
nl -ba services/control-plane/bff/agora/strategy_workshop/router.py | sed -n '201,640p'
nl -ba services/control-plane/bff/agora/strategy_workshop/store.py | sed -n '71,790p'
nl -ba services/control-plane/openapi/agora_v1_3.openapi.yaml | sed -n '320,405p'
nl -ba services/control-plane/specs/agora/v4/strategy_readiness.schema.json | sed -n '1,220p'
nl -ba services/control-plane/specs/agora/v4/workshop_card.schema.json | sed -n '1,260p'
rg -n '"/bff/agora/workshops/\{workshop_id\}/(cards|readiness|readiness/reassess)"' services/control-plane/bff/contract_snapshots/backend_routes_manifest.json
git -C /home/lupin/code/execute-plans fetch origin --prune
git -C /home/lupin/code/execute-plans rev-parse origin/dev
git -C /home/lupin/code/execute-plans show origin/dev:src/lib/bff-v1/agora/workshops.ts
git -C /home/lupin/code/execute-plans show origin/dev:src/agora/pages/strategy-workshop/StrategyWorkshopPage.tsx
git -C /home/lupin/code/execute-plans show origin/dev:src/routes/agora.tsx
curl -sS -o /tmp/ag-dynui-full-002-openapi.json -w '%{http_code} %{url_effective}\n' https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/openapi.json
jq -r '.paths | keys[] | select(startswith("/bff/agora/workshops"))' /tmp/ag-dynui-full-002-openapi.json
curl -sS -D /tmp/ag-dynui-full-002-cards-headers.txt -o /tmp/ag-dynui-full-002-cards-body.json -w '%{http_code} %{url_effective}\n' -H 'Authorization: Bearer pantheon-dev-browser:operator,reviewer,approver:mfa' -H 'X-Tenant-Id: pantheon-dev' https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/bff/agora/workshops/nonexistent-workshop/cards
curl -sS -D /tmp/ag-dynui-full-002-readiness-headers.txt -o /tmp/ag-dynui-full-002-readiness-body.json -w '%{http_code} %{url_effective}\n' -H 'Authorization: Bearer pantheon-dev-browser:operator,reviewer,approver:mfa' -H 'X-Tenant-Id: pantheon-dev' https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/bff/agora/workshops/nonexistent-workshop/readiness
curl -sS -D /tmp/ag-dynui-full-002-reassess-headers.txt -o /tmp/ag-dynui-full-002-reassess-body.json -w '%{http_code} %{url_effective}\n' -H 'Authorization: Bearer pantheon-dev-browser:operator,reviewer,approver:mfa' -H 'X-Tenant-Id: pantheon-dev' -H 'Content-Type: application/json' -X POST --data '{"gate":"trading_room"}' https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/bff/agora/workshops/nonexistent-workshop/readiness/reassess
curl -sS -D /tmp/ag-dynui-full-002-workshops-headers.txt -o /tmp/ag-dynui-full-002-workshops-body.json -w '%{http_code} %{url_effective}\n' -H 'Authorization: Bearer pantheon-dev-browser:operator,reviewer,approver:mfa' -H 'X-Tenant-Id: pantheon-dev' https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/bff/agora/workshops?limit=5
```

Live probe summary:

- `/openapi.json`: HTTP 200.
- Hosted OpenAPI workshop paths currently include CRUD/completeness/events,
  stream, versions, research plans/runs, consultations, and conclude, but not
  `cards`, `readiness`, or `readiness/reassess`.
- Authenticated `GET /bff/agora/workshops?limit=5`: HTTP 200 with `data: []`
  for `tenant:pantheon-dev:user:pantheon-dev-browser`.
- Authenticated target-path calls for `nonexistent-workshop`: HTTP 404
  standardized `RESOURCE_NOT_FOUND` envelopes with `x-correlation-id`.

## 9. Reviewer Handoff

Reviewer (`Codex`) should verify:

1. This packet stays support-only and does not mutate canonical truth, runtime
   code, frontend code, route registries, or governance behavior.
2. The BFF gap matrix matches current local router/store/spec/manifest state.
3. The live probe interpretation is conservative: current 404s do not prove
   implemented target routes because hosted OpenAPI does not expose them.
4. The frontend handoff is based on execute-plans `origin/dev`, not the dirty
   local checkout.
5. Parent owner can use this packet without treating it as review approval for
   `AG-DYNUI-FULL-002` or any downstream production recovery task.
