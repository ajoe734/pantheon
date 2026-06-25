# AG-BE-SW-001 Followup-4 Sidecar BFF and Frontend Handoff

| Field | Value |
|---|---|
| Sidecar task | `AG-BE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-4` |
| Helper parent | `AG-BE-SW-001` - Strategy Workshop session/event persistence |
| Helper kind | `bff_handoff_packet` |
| Owner / reviewer | `Codex` / `Codex2` |
| Date | `2026-06-20` |
| Status | `ready for reviewer handoff` |
| Mutates canonical truth | `false` |

Scope constraint: this packet is support material only. It does not change L1
canonical truth, OpenAPI, capability manifests, BFF runtime code, route
registries, governance implementation, persona or registry state, migrations,
database schema, or execute-plans source files.

## 1. Purpose

This fourth followup refreshes the parent handoff after `AG-XR-OPENAPI-001`
closed the contract-layer gap that earlier packets still described as missing:
`services/control-plane/openapi/agora_v1_1.openapi.yaml` now contains the
canonical `/bff/agora/workshops` route family, and
`services/control-plane/specs/agora/v2/capability_manifest_v1_1.json` now adds
`/bff/agora/workshops` as the v1.1 workshop prefix.

The parent task is still blocked before implementation. The useful handoff now
is not "invent a workshop route"; it is:

- separate v1.1 contract truth from current runtime implementation state
- carry forward the four active parent blockers as BFF/FE constraints
- tell execute-plans exactly what not to wire until the backend contract
  ambiguities are resolved

This packet does not unblock `AG-BE-SW-001`; it packages decision support for
the parent owner and reviewer.

## 2. Sources Rechecked

| Source | Evidence used |
|---|---|
| `.orchestrator/task-briefs/ag_be_sw_001_sidecar_bff_handoff_followup_4.md` | Sidecar assignment and support-only boundary. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-4` | Confirms active sidecar owner/reviewer/status/artifact. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-SW-001` | Confirms parent is blocked and waiting for Codex clarification. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-XR-OPENAPI-001` | Confirms v1.1 OpenAPI/capability contract task is archived `done` through PR #1841/#1848. |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/contract-closure/03_servant_and_workshop_contracts.md` | Prose authority for canonical `/bff/agora/workshops`, persistence rows, ETag/If-Match/idempotency, and "workshop state is not StrategySpec truth". |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/contract-closure/07_dispatch_unblock_matrix_v2.md` | Records that `AG-XR-OPENAPI-001` was the required predecessor for `AG-BE-SW-001`. |
| `services/control-plane/openapi/agora_v1_1.openapi.yaml` | Additive v1.1 OpenAPI: 13 workshop routes, concurrency model, request schemas, events/completeness descriptions. |
| `services/control-plane/specs/agora/v2/capability_manifest_v1_1.json` | v1.1 capability prefix for `agora.workshop.v1`: `/bff/agora/workshops`. |
| `services/control-plane/specs/agora/strategy_workshop.schema.json` | Frozen v1 schema still uses `status` enum `open`, `in_review`, `concluded`, `archived` and `subject.kind/ref`. |
| `services/control-plane/specs/agora/strategy_completeness.schema.json` | Frozen completeness schema uses `strategy_ref` and optional `workshop_id`; it is not the v1.1 snapshot table shape. |
| `services/control-plane/bff/agora/strategy_workshop/router.py` | Runtime package router remains a placeholder returning an empty APIRouter; migration is not implemented here. |
| `/home/lupin/code/execute-plans/src/lib/bff-v1/paths.ts` | Local frontend checkout has no `/bff/agora/workshops` path builders. |
| `/home/lupin/code/execute-plans/src/lib/bff-v1/client.ts` and `headers.ts` | Current client returns parsed JSON only, injects `If-Match` from `ifMatchVersion`, and does not expose response ETag to callers. |

Frontend checkout caveat: `/home/lupin/code/execute-plans` was observed on
`main...origin/main [ahead 2, behind 467]`. Treat frontend observations as
local checked-source evidence, not as a remote `main` tip statement.

## 3. Delta From Prior Packets

| Item | Prior packet stance | Current handoff update |
|---|---|---|
| Generic workshop facade | Packets 1/2/3 correctly said frozen v1 had no generic workshop route. | Contract-layer gap is now closed by `agora_v1_1.openapi.yaml`: canonical family is `/bff/agora/workshops`. Runtime implementation is still not present in the package router. |
| Committee/trainer surfaces | Prior packets mapped existing committee, trainer, evaluation, skill-coaching, and persona-lab surfaces. | Those remain adjacent surfaces. They are not substitutes for the v1.1 `strategy_workshop_session` aggregate unless the parent explicitly maps them. |
| Parent decisions D1-D9 | Still useful for adjacent route family planning. | D1 is now narrowed: do not choose between generic route and current route family at contract level; v1.1 picked `/bff/agora/workshops`. The remaining decision is how to implement and migrate runtime/frontend without conflating old aliases. |
| OpenAPI truth | Prior packets mostly referenced `agora_v1.openapi.yaml`. | Parent implementation should now read `agora_v1_1.openapi.yaml` plus contract-closure prose before touching code. |
| Frontend strict mode | Prior packets said no seed fallback for current route family. | Add v1.1-specific rule: no Strategy Workshop live UI claim until frontend can read ETag, send exact `If-Match`, and avoid storing private raw message content outside the BFF/private store path. |

## 4. Current Contract vs Runtime Map

| Surface | Contract state | Runtime/frontend state | Handoff rule |
|---|---|---|---|
| Workshop route family | v1.1 OpenAPI declares 13 `/bff/agora/workshops` routes. | `strategy_workshop/router.py` is a placeholder; current runtime routes listed in old packets remain in `main.py`. | Implement parent against `/bff/agora/workshops`; do not call committee/trainer routes as an implicit workshop aggregate. |
| Capability manifest | v1.1 manifest declares `agora.workshop.v1` prefix `/bff/agora/workshops`. | Frozen v1 manifest still contains adjacent paths; both are additive contract layers. | FE capability checks must account for v1.1 prefix before enabling Strategy Workshop UI. |
| Persistence rows | Prose defines `strategy_workshop_session`, `strategy_workshop_event`, and `strategy_completeness_snapshot`. | No implementation/migration was changed by this sidecar. | Parent must create persistence/migration only from the prose/v1.1 contract, not from sidecar tables. |
| Event privacy | Contract says event rows carry `private_content_ref` and `redacted_summary`; v1.1 event list omits private content and returns redacted summaries. | Code search found no current `private_content_ref` or `redacted_summary` implementation in the workshop BFF layer; only unrelated redaction/private-store references exist. | Parent must clarify/create the private content store boundary before accepting raw workshop messages. |
| StrategySpec relation | Prose says workshop state references active draft in the existing Strategy Registry; table includes `strategy_id`, `active_strategy_spec_registry_id`, and `selected_version_id`. v1.1 create request uses optional `strategy_spec_ref`. | Frozen v1 schema uses `subject.kind/ref`; completeness schema uses `strategy_ref`. | Parent must freeze the mapping before schema/migration/DTO work. Do not copy StrategySpec truth into workshop rows. |
| Status vocabulary | Frozen `StrategyWorkshop` schema has `open`, `in_review`, `concluded`, `archived`; v1.1 list filter says `active`, `concluded`, `archived`. | No runtime workshop status mapper exists in the package router. | Parent must define whether `active` is a query alias for `open` + `in_review`, or update one contract layer through the proper route. |
| ETag/optimistic lock | v1.1 aggregate GET returns `ETag: W/"workshop:{id}:v{lock_version}"`; every mutating route after create requires `If-Match` plus `Idempotency-Key`. | Local execute-plans client formats `If-Match` as a quoted version and does not expose response headers/ETag from GET. | FE adapter needs response-header access and exact ETag propagation, not just numeric lock-version formatting. |
| Frontend path helpers | v1.1 declares `/bff/agora/workshops`. | Local `paths.ts` has no workshop builders and no `agora_v1_1` generated client. | Frontend handoff must add path builders/types/tests after backend implementation and type-generation are stable. |

## 5. Parent Blockers Reframed As BFF/FE Constraints

| Blocker | Why it blocks backend implementation | Frontend consequence |
|---|---|---|
| Private content store is undefined | `WorkshopCreateRequest.initial_message` and `WorkshopMessageRequest.content` are raw private content, while persistence requires event rows to store only `private_content_ref` plus `redacted_summary`. There is no checked workshop private-store API or pre-stored `private_content_ref` field in the request schema. | FE must not cache, replay, persist, or expose raw workshop messages as durable local state. It can submit raw content only to a backend route whose private-store behavior is implemented and reviewed. |
| StrategySpec reference mapping is unresolved | v1.1 request uses `strategy_spec_ref`; prose table stores `strategy_id`, `active_strategy_spec_registry_id`, and `selected_version_id`; v1 schema models `subject.kind/ref`. The exact relationship is not frozen in code. | FE must display strategy references as opaque refs returned by BFF. Do not construct `strategy_id` or selected version IDs from route params, local seed IDs, or UI labels. |
| Index set is incomplete in checked sources | Parent summary cites section 22.6 indexes, but checked contract prose only names `UNIQUE(workshop_id, sequence_no)` for events. The exact database index set is not present in the support sources read for this packet. | FE should not assume pagination/sort behavior beyond the OpenAPI query parameters. Event replay should use `after_sequence` only after backend validates ordering/index behavior. |
| Status vocabulary conflicts | v1 schema enum is `open/in_review/concluded/archived`, while v1.1 list filter documents `active/concluded/archived`. | FE filters must not ship an `active` tab as a durable API dependency until the parent defines whether it maps to `open`, `in_review`, or both. |

## 6. Safe Operator Journey

Current safe journey while `AG-BE-SW-001` remains blocked:

```text
Operator opens a Strategy Workshop page
  -> frontend may show unavailable / pending-backend state for v1.1 workshop
     routes
  -> frontend must not synthesize a workshop aggregate from committee,
     trainer, persona-lab, evaluation, or local seed state
  -> frontend must not persist private raw message content locally as durable
     state
  -> no write CTA is live unless the backend can return an ETag and the client
     can submit exact If-Match + Idempotency-Key
  -> no route may create RuntimeBinding, route broker orders, bind capital, or
     mutate StrategySpec canonical truth directly
```

Expected journey after the parent resolves blockers and implements the BFF:

```text
Operator creates or opens /bff/agora/workshops
  -> BFF derives tenant/user identity from auth
  -> BFF stores private raw content in the private content path and writes only
     private_content_ref + redacted_summary to strategy_workshop_event
  -> BFF returns a workshop aggregate with ETag
  -> frontend stores and reuses that ETag for message/version/research/
     consultation/conclude mutations
  -> frontend reads /events for redacted ordered event history
  -> frontend reads /completeness for latest snapshot
  -> frontend lists/selects StrategySpec draft versions by refs returned by BFF
  -> research-runs and consultations remain governed handoffs
  -> conclude finalizes the workshop state without writing live execution
     authority
```

Failure/degraded behavior:

| Failure | FE behavior |
|---|---|
| `401` or `403` | Render auth/scope blocked state; hide write CTAs. |
| `404` workshop | Render missing-workshop state; do not reconstruct from local seed. |
| `409 CONCURRENT_MODIFICATION` | Refetch latest aggregate, read returned/current ETag, and require the operator to retry from refreshed state. |
| Missing ETag on aggregate | Treat as backend contract failure; disable mutating CTAs. |
| Private-store unavailable | Disable create/message flows; do not fall back to local persistence. |
| Status filter ambiguity | Use BFF-returned status labels only; do not hard-code an `active` bucket as canonical. |

## 7. Frontend Handoff Requirements

Minimum execute-plans work once the backend implementation exists:

| Area | Requirement |
|---|---|
| Path builders | Add `agoraWorkshops`, `agoraWorkshop`, `agoraWorkshopMessages`, `agoraWorkshopEvents`, `agoraWorkshopCompleteness`, `agoraWorkshopVersions`, `agoraWorkshopVersionSelect`, `agoraWorkshopResearchRuns`, `agoraWorkshopConsultations`, `agoraWorkshopConclude`, and `agoraWorkshopStream`. |
| Types | Generate/import v1.1 request types for `WorkshopCreateRequest`, `WorkshopMessageRequest`, `VersionCreateRequest`, `WorkshopResearchRunRequest`, `WorkshopConsultationRequest`, and `WorkshopConcludeRequest`. |
| ETag support | Add a client method that returns both parsed body and response headers, or a workshop adapter that captures ETag from GET/create responses. Existing `bffFetch<T>()` returns JSON only. |
| If-Match support | Send the exact ETag value expected by the backend. Existing `ifMatchVersion()` formats a quoted numeric/string version, not `W/"workshop:{id}:v{lock_version}"`. |
| Privacy discipline | Keep raw `initial_message` and message `content` transient. Do not write them to local storage, seed fixtures, analytics, logs, or test snapshots. |
| Strict mode | In `VITE_BFF_FALLBACK=strict`, missing workshop adapter or transport failure must render unavailable/error state, never seed success. |
| Tests | Cover path generation, no body idempotency keys, ETag capture, exact If-Match propagation, 409 refresh behavior, no seed fallback, no local private-content persistence, and no broker/RuntimeBinding/capital action imports. |

Recommended adapter shape after backend implementation:

```ts
type AgoraWorkshopClient = {
  list(query?: { status?: string; page_token?: string; page_size?: number }): Promise<WorkshopList>;
  create(input: WorkshopCreateRequest, idempotencyKey: string): Promise<WorkshopWithEtag>;
  get(workshopId: string): Promise<WorkshopWithEtag>;
  postMessage(workshopId: string, input: WorkshopMessageRequest, etag: string, idempotencyKey: string): Promise<WorkshopEventAccepted>;
  events(workshopId: string, afterSequence?: number): Promise<WorkshopEventList>;
  completeness(workshopId: string): Promise<CompletenessSnapshot>;
  versions(workshopId: string): Promise<WorkshopVersionList>;
  createVersion(workshopId: string, input: VersionCreateRequest, etag: string, idempotencyKey: string): Promise<WorkshopVersionRef>;
  selectVersion(workshopId: string, versionId: string, etag: string, idempotencyKey: string): Promise<WorkshopWithEtag>;
  researchRun(workshopId: string, input: WorkshopResearchRunRequest | undefined, etag: string, idempotencyKey: string): Promise<ResearchRunRef>;
  consultation(workshopId: string, input: WorkshopConsultationRequest, etag: string, idempotencyKey: string): Promise<ConsultationRef>;
  conclude(workshopId: string, input: WorkshopConcludeRequest | undefined, etag: string, idempotencyKey: string): Promise<WorkshopWithEtag>;
  stream(workshopId: string, lastEventId?: string): EventSource;
};
```

Do not add broker, RuntimeBinding, deployment, or capital commands to this
client.

## 8. Consolidated Open Decisions After v1.1

| Decision | Owner to resolve | Required before |
|---|---|---|
| D10: Runtime implementation home for `/bff/agora/workshops` | Parent BFF owner | Any backend commit claiming AG-BE-SW-001 implementation. |
| D11: Private content store API/ref format/retention | Parent BFF owner plus privacy/governance reviewer if needed | `POST /workshops` or `POST /messages` accepts private raw content. |
| D12: `strategy_spec_ref` to `strategy_id` / `active_strategy_spec_registry_id` / `selected_version_id` mapping | Parent BFF owner | Migration, DTO adapter, and FE version list/select UI. |
| D13: `active` list filter vs `open/in_review` schema statuses | Parent BFF owner | FE status tabs and backend query validation. |
| D14: Exact DB index set beyond event sequence uniqueness | Parent BFF owner | Migration acceptance and event pagination claims. |
| D15: ETag representation and FE client support | Parent BFF + FE owners | Any mutating frontend workshop CTA. |
| D16: execute-plans v1.1 generated types and route helpers | FE owner after backend contract stability | Live Strategy Workshop UI enablement. |

Earlier packet decisions D2-D9 remain relevant for adjacent committee/trainer
surfaces, but they should no longer be used to justify a second competing
`/strategy-workshops` route family.

## 9. Suggested Verification

Current-state sidecar checks:

```bash
git diff --check -- \
  .orchestrator/task-briefs/ag_be_sw_001_sidecar_bff_handoff_followup_4.md \
  support/sidecars/AG-BE-SW-001/AG-BE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-4.md

AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-4
AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-SW-001
AI_NAME=Codex ./scripts/ai-status.sh show AG-XR-OPENAPI-001

python3 -c "import yaml; yaml.safe_load(open('services/control-plane/openapi/agora_v1_1.openapi.yaml'))"
python3 -m json.tool services/control-plane/specs/agora/v2/capability_manifest_v1_1.json >/dev/null
```

Expected scope check:

- Only this sidecar support artifact and the task-scoped brief are authored by
  this task.
- No L1 canonical docs, OpenAPI, capability manifest, BFF runtime, route
  registry, governance code, registry state, migrations, database schema, or
  execute-plans files are changed.
- The packet does not claim parent `AG-BE-SW-001` is unblocked or complete.

Results recorded by Codex before reviewer handoff:

- `git diff --check --no-index /dev/null support/sidecars/AG-BE-SW-001/AG-BE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-4.md >/tmp/ag-be-sw-followup4-diff-check.out; test ! -s /tmp/ag-be-sw-followup4-diff-check.out` passed.
- `git diff --check -- .orchestrator/task-briefs/ag_be_sw_001_sidecar_bff_handoff_followup_4.md support/sidecars/AG-BE-SW-001/AG-BE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-4.md` passed for tracked diff whitespace.
- `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-4` confirmed the sidecar remains active `in_progress`, owner `Codex`, reviewer `Codex2`.
- `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-SW-001` confirmed the parent remains blocked on private-store, StrategySpec mapping, index-set, and status-vocabulary clarification.
- `AI_NAME=Codex ./scripts/ai-status.sh show AG-XR-OPENAPI-001` confirmed the v1.1 OpenAPI/capability predecessor is archived `done`.
- `python3 -c "import yaml; yaml.safe_load(open('services/control-plane/openapi/agora_v1_1.openapi.yaml'))"` passed.
- `python3 -m json.tool services/control-plane/specs/agora/v2/capability_manifest_v1_1.json >/dev/null` passed.
- `LC_ALL=C rg -n '[^[:ascii:]]' support/sidecars/AG-BE-SW-001/AG-BE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-4.md .orchestrator/task-briefs/ag_be_sw_001_sidecar_bff_handoff_followup_4.md || true` found no non-ASCII in this packet; the task brief retains its generated Chinese summary.

## 10. Handoff

Reviewer focus for `Codex2`:

- Verify the packet accurately distinguishes v1.1 contract truth from current
  runtime implementation.
- Verify the parent blockers are carried as BFF/FE constraints without
  inventing a private store, schema mapping, status mapping, or index set.
- Verify the frontend handoff is conservative enough for strict live mode and
  does not permit local private-content persistence, seed fallback, broker
  routing, RuntimeBinding writes, or capital-binding behavior.

This packet should be used as support material for the parent
`AG-BE-SW-001` lane. It is not canonical route truth by itself and should not
be absorbed without the parent implementation/review path.
