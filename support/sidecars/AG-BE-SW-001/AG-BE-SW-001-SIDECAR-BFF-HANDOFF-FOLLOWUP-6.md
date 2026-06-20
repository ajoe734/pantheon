# AG-BE-SW-001 Followup-6 Sidecar BFF and Frontend Handoff

| Field | Value |
|---|---|
| Sidecar task | `AG-BE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-6` |
| Helper parent | `AG-BE-SW-001` - Strategy Workshop session/event persistence |
| Helper kind | `bff_handoff_packet` |
| Owner / reviewer | `Codex` / `Codex2` |
| Date | `2026-06-20` |
| Status | `ready for reviewer handoff` |
| Mutates canonical truth | `false` |

Scope constraint: this packet is support material only. It does not change L1
canonical truth, OpenAPI, capability manifests, BFF runtime code, route
registries, governance implementation, persona or registry state, migrations,
database schema, private-store code, or execute-plans source files.

## 1. Purpose

This sixth followup records the current handoff disposition after followup-5
closed and parent `AG-BE-SW-001` remained blocked, waiting for `Codex`
clarification. The useful answer from this sidecar is not to invent the missing
backend design. It is to make the parent decision boundary explicit:

1. `agora_v1_1.openapi.yaml` and `capability_manifest_v1_1.json` define the
   canonical `/bff/agora/workshops` contract surface.
2. The checked runtime still has no implementation of that route family in the
   strategy-workshop package router.
3. The parent blocker remains valid until private content storage, redaction,
   StrategySpec reference mapping, completeness mapping, status mapping, index
   set, and ETag source-of-truth decisions are answered by the parent path.
4. Frontend work should stay in unavailable/blocked handoff mode until backend
   behavior is implemented and an ETag-aware live adapter exists.

This packet is therefore a conservative reviewer/parent-owner intake packet. It
does not approve `AG-BE-SW-001` implementation, reopen canonical contracts, or
create an alternate workshop facade.

## 2. Current Task State Snapshot

Status commands used `AI_NAME=Codex`.

| Task | Observed status | Handoff implication |
|---|---|---|
| `AG-BE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-6` | active `in_progress`, owner `Codex`, reviewer `Codex2` | This packet is the intended deliverable. |
| `AG-BE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-5` | archived `done`; support packet and closeout PRs merged | Followup-5 remains the baseline for C1-C8 blocker clarification. |
| `AG-BE-SW-001` | active `blocked`, owner `Codex2`, reviewer `Codex`, waiting for `Codex` | Parent should stay blocked until the clarification set below is answered outside this sidecar. |
| `AG-XR-OPENAPI-001` | archived `done` | v1.1 OpenAPI and capability manifest predecessor is durable. |

The local execute-plans checkout used only for read-only evidence was observed
on `main...origin/main [ahead 2, behind 467]`. Treat frontend observations below
as checked-local-source evidence, not as remote `main` truth.

## 3. Sources Rechecked

| Source | Evidence used |
|---|---|
| `.orchestrator/task-briefs/ag_be_sw_001_sidecar_bff_handoff_followup_6.md` | This sidecar assignment and support-only boundary. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-6` | Confirms sidecar owner/reviewer/status and artifact path. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-SW-001` | Confirms parent remains blocked on the same private-store, mapping, index, and status questions. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-5` | Confirms previous support packet is archived `done` with Codex2 approval. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-XR-OPENAPI-001` | Confirms v1.1 OpenAPI/capability predecessor is archived `done`. |
| `support/sidecars/AG-BE-SW-001/*FOLLOWUP-2.md` through `*FOLLOWUP-5.md` | Prior sidecar decisions, adjacent-route caveats, and C1-C8 clarification set. |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/contract-closure/03_servant_and_workshop_contracts.md` | Canonical route family, persistence row names, private event content rule, and ETag/idempotency contract. |
| `services/control-plane/openapi/agora_v1_1.openapi.yaml` | 13 canonical `/bff/agora/workshops` route definitions and request schemas. |
| `services/control-plane/specs/agora/v2/capability_manifest_v1_1.json` | `agora.workshop.v1` v1.1 path prefix `/bff/agora/workshops`. |
| `services/control-plane/specs/agora/strategy_workshop.schema.json` | Frozen v1 `StrategyWorkshop` schema and status enum. |
| `services/control-plane/specs/agora/strategy_completeness.schema.json` | Frozen v1 completeness schema using `strategy_ref` plus optional `workshop_id`. |
| `services/control-plane/bff/agora/strategy_workshop/router.py` | Still returns an empty `APIRouter(tags=["agora-workshop"])`. |
| `services/control-plane/bff/agora/router.py` | Includes the placeholder strategy-workshop router in the top-level Agora router. |
| `/home/lupin/code/execute-plans/src/lib/bff-v1/client.ts` | `bffFetch<T>()` returns parsed JSON, not response headers/ETag. |
| `/home/lupin/code/execute-plans/src/lib/bff-v1/headers.ts` | `ifMatchVersion` formats `If-Match` as a quoted scalar, not the exact weak workshop ETag. |
| `/home/lupin/code/execute-plans/src/lib/bff-v1`, `/src/lib/bff`, `/src/agora` scans | No checked-local workshop route helpers or live adapter were found. |

## 4. Delta Since Followup-5

No new runtime workshop implementation was found in this worktree. The material
state remains:

| Surface | Current state | Followup-6 disposition |
|---|---|---|
| Contract route family | v1.1 OpenAPI declares 13 `/bff/agora/workshops` routes. | Parent must use this route family, not `/strategy-workshops` and not adjacent committee/trainer routes as a substitute aggregate. |
| Capability manifest | v1.1 `agora.workshop.v1` points at `/bff/agora/workshops`. | Capability truth exists, but it is not runtime readiness evidence. |
| Runtime router | `strategy_workshop/router.py` is still a placeholder. | Backend implementation remains undone. |
| Parent blocker | Parent remains `blocked`, waiting for `Codex`. | This sidecar's Codex clarification is: keep blocked unless C1-C8 get explicit parent answers. |
| Frontend adapter | Local execute-plans has no workshop builders and no ETag-aware workshop client. | Frontend cannot truthfully enable live Strategy Workshop UI. |

## 5. BFF Query Gap Ledger

| Gap | Evidence | Parent action before implementation |
|---|---|---|
| Runtime route gap | `/bff/agora/workshops` appears in v1.1 OpenAPI and manifest, but not in BFF runtime handlers; the package router returns an empty router. | Implement canonical routes in the parent BFF lane after blocker clarification. |
| Private content storage gap | Contract requires event rows to store `private_content_ref` and `redacted_summary`; create/message requests accept raw `initial_message` or `content`. | Name the private-store owner/API, ref format, encryption/key boundary, retention/deletion rule, and route failure behavior. |
| Redaction boundary gap | Contract says events expose redacted summaries, but no workshop redaction implementation was found. | Decide whether redaction is synchronous in write routes, asynchronous, or delegated to a reviewed service. |
| StrategySpec mapping gap | Contract prose names `strategy_id`, `active_strategy_spec_registry_id`, and `selected_version_id`; OpenAPI accepts `strategy_spec_ref`; frozen v1 schema uses `subject.kind/ref`. | Freeze the mapping and nullability rules without copying StrategySpec truth into workshop rows. |
| Completeness mapping gap | v1.1 prose names `strategy_completeness_snapshot.strategy_version_id`; frozen completeness schema uses `strategy_ref` and optional `workshop_id`. | Define how snapshot rows map back to `StrategyCompleteness` and Strategy Registry versions. |
| Status vocabulary gap | Frozen v1 schema has `open`, `in_review`, `concluded`, `archived`; v1.1 list filter text says `active`, `concluded`, `archived`. | Decide whether `active` is an alias for `open` plus `in_review`, a persisted status, or a contract wording fix. |
| Index set gap | Checked contract prose explicitly names `UNIQUE(workshop_id, sequence_no)` only; parent summary references a larger section 22.6 index set. | Provide exact migration indexes for list, event ordering, completeness lookup, and idempotency before storage work. |
| ETag source gap | Contract says ETag is `W/"workshop:{id}:v{lock_version}"`; frontend client currently does not capture response headers. | Confirm lock-version source/increment semantics and require exact ETag propagation in FE adapter tests. |

## 6. Clarification Disposition For Parent

The following answers should be treated as the sidecar's handoff disposition.
They are intentionally conservative.

| ID | Parent question | Sidecar disposition |
|---|---|---|
| C1 | What private content store should workshop create/message use? | Not answerable from checked sources. Keep parent blocked until a named store/API/ref/encryption/retention contract exists. |
| C2 | Who produces `redacted_summary` and when? | Not answerable from checked sources. Do not accept workshop message writes until redaction behavior is owned and reviewed. |
| C3 | What happens when private-store or redaction fails? | Fail closed should be assumed, but exact status/error body must be specified before implementation. |
| C4 | How does `strategy_spec_ref` map to persisted registry fields? | Not safe to infer. Parent must define mapping to `strategy_id`, `active_strategy_spec_registry_id`, and `selected_version_id`. |
| C5 | How does completeness snapshot map to frozen `StrategyCompleteness`? | Not safe to infer. Parent must define `strategy_version_id` versus `strategy_ref` ownership. |
| C6 | What is the status vocabulary? | Not safe to infer. Do not implement an `active` persisted status or FE tab until mapping is frozen. |
| C7 | What is the exact index/idempotency storage set? | Not safe to infer. Parent must supply the migration index set beyond event sequence uniqueness. |
| C8 | What is the ETag source of truth and increment rule? | Likely `strategy_workshop_session.lock_version`, but parent must confirm exact increment-once semantics for every mutating route. |

If any C1-C8 item remains unanswered, the parent should not write runtime or
migration code. A blocked parent is more truthful than a best-effort workshop
persistence layer that stores private content, maps StrategySpec refs, or emits
ETags incorrectly.

## 7. Safe Parent Implementation Sequence

Recommended order once C1-C8 have accepted answers:

1. Implement or bind the private content store and redaction path first.
2. Add persistence migrations for `strategy_workshop_session`,
   `strategy_workshop_event`, `strategy_completeness_snapshot`, and the exact
   accepted indexes/idempotency records.
3. Implement `/bff/agora/workshops` routes in the selected strategy-workshop
   BFF module; do not repurpose committee/trainer/persona-lab routes as the
   workshop aggregate.
4. Add route tests proving no raw private content lands in event rows,
   append-only sequence ordering, exact ETag/If-Match behavior, idempotency,
   409 `CONCURRENT_MODIFICATION`, status mapping, and no execution authority.
5. Only after backend behavior is stable, hand the route and DTO contract to
   execute-plans for path builders, ETag capture, strict fallback tests, and
   privacy/no-seed tests.

No step should grant broker order, RuntimeBinding, deployment, or capital
binding authority.

## 8. Frontend Handoff Requirements

Minimum execute-plans behavior before any live Strategy Workshop UI claim:

| Area | Requirement |
|---|---|
| Path builders | Add only canonical `/bff/agora/workshops` builders after backend implementation. |
| Response headers | Add an adapter/client method that returns parsed body plus response headers or otherwise captures the exact ETag from create/get. |
| If-Match | Reuse the exact ETag returned by BFF; do not reformat lock versions through generic `ifMatchVersion`. |
| Raw private content | Keep `initial_message` and `content` transient. Do not persist them in local storage, fixtures, logs, analytics, snapshots, or durable cache. |
| Strict mode | In `VITE_BFF_FALLBACK=strict`, missing adapter, transport failure, 404, 409, or missing ETag must render unavailable/error state, not seed success. |
| Status filters | Do not ship API-dependent `active` filters until C6 is answered. |
| Adjacent routes | Committee, trainer, evaluation, skill-coaching, and persona-lab routes remain adjacent surfaces. They do not synthesize a Strategy Workshop aggregate. |
| Tests | Cover path generation, ETag capture, exact If-Match, header-only idempotency, 409 refresh, no raw-content persistence, no seed fallback, and no broker/RuntimeBinding/capital imports. |

Current honest operator journey:

```text
Operator opens Strategy Workshop UI
  -> frontend checks whether backend workshop route family is available
  -> current state renders blocked/unavailable for live workshop aggregate
  -> raw message input is not durably persisted locally
  -> no create/message/version/research/consult/conclude CTA is live
  -> no local seed or adjacent route family is treated as a successful workshop
```

Expected journey after parent delivery:

```text
Operator creates or opens /bff/agora/workshops
  -> BFF stores raw private content through the accepted private-store path
  -> BFF persists only private_content_ref + redacted_summary in event rows
  -> BFF returns aggregate body plus ETag
  -> frontend stores exact ETag for later If-Match writes
  -> events and completeness views use BFF-returned redacted/snapshot data
  -> research, consultation, version, and conclude operations stay governed
     workshop handoffs, not execution authority
```

## 9. Reviewer Focus

Reviewer `Codex2` should verify:

- The packet preserves the support-only boundary.
- The packet does not invent private-store, redaction, StrategySpec mapping,
  completeness mapping, status mapping, index, or ETag semantics.
- The parent disposition is conservative enough to keep `AG-BE-SW-001` blocked
  until C1-C8 are answered.
- The frontend handoff blocks seed fallback, raw private-content persistence,
  weak ETag reformatting, adjacent-route synthesis, and any execution/capital
  authority path.

## 10. Suggested Reviewer Handoff

```text
Followup-6 packet ready:
support/sidecars/AG-BE-SW-001/AG-BE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-6.md

Parent AG-BE-SW-001 remains blocked waiting for Codex clarification. The
sidecar disposition is to keep it blocked until C1-C8 are answered through the
parent path. Contract v1.1 defines /bff/agora/workshops, but runtime router is
still a placeholder and execute-plans lacks an ETag-aware workshop client.

Please review that this packet stays support-only, accurately reflects the
contract/runtime/frontend gap, and does not broaden canonical truth.
```

## 11. Verification

Commands run while preparing this packet:

```bash
git status -sb
git branch --show-current
git remote -v
git fetch origin
git merge --ff-only origin/dev
sed -n '1,220p' AI_COLLABORATION_GUIDE.md
sed -n '1,260p' .orchestrator/task-briefs/ag_be_sw_001_sidecar_bff_handoff_followup_6.md
sed -n '1,260p' .orchestrator/skills/worker-anchor-commit.md
sed -n '1,260p' .orchestrator/skills/task-closeout-finalization.md
sed -n '1,240p' ai-status.json
AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-6
AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-SW-001
AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-5
AI_NAME=Codex ./scripts/ai-status.sh show AG-XR-OPENAPI-001
rg -n "/bff/agora/workshops|strategy_workshop|private_content_ref|redacted_summary|If-Match|ETag|workshop" services/control-plane/bff/agora services/control-plane/openapi/agora_v1_1.openapi.yaml services/control-plane/specs/agora/v2/capability_manifest_v1_1.json docs/04/pantheon_agora_cross_repo_2026-06-20/contract-closure/03_servant_and_workshop_contracts.md
rg -n "/bff/agora/workshops|listAgoraWorkshops|createAgoraWorkshop|postAgoraWorkshopMessage|strategy_workshop_session|strategy_completeness_snapshot|private_content_ref|redacted_summary" services/control-plane/bff services
rg -n "agoraWorkshops|workshops|If-Match|ETag|bffFetch" /home/lupin/code/execute-plans/src/lib/bff-v1 /home/lupin/code/execute-plans/src/lib/bff /home/lupin/code/execute-plans/src/agora
git -C /home/lupin/code/execute-plans status -sb
```

Results:

- Branch was already `task/AG-BE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-6`.
- Branch was fast-forwarded to `origin/dev` before authoring the packet.
- `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-6` confirmed this sidecar is active `in_progress`.
- `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-SW-001` confirmed the parent remains `blocked`, waiting for `Codex`.
- `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-5` confirmed followup-5 is archived `done`.
- `AI_NAME=Codex ./scripts/ai-status.sh show AG-XR-OPENAPI-001` confirmed v1.1 OpenAPI/capability predecessor is archived `done`.
- Runtime scan found `/bff/agora/workshops` in v1.1 OpenAPI/manifest only, not as an implemented BFF handler; `strategy_workshop/router.py` remains the placeholder route home.
- execute-plans scan found generic `bffFetch`, `If-Match`, and ETag-related helpers, but no checked-local workshop route helper or ETag-aware workshop adapter.
- execute-plans local checkout was `main...origin/main [ahead 2, behind 467]`; frontend evidence is local-source caveated.

Expected scope check:

- Only this sidecar support artifact and task-scoped brief are authored by this
  task.
- No L1 canonical docs, OpenAPI, capability manifest, BFF runtime, route
  registry, governance code, registry state, migrations, database schema,
  private-store implementation, or execute-plans files are changed.
- The packet does not claim parent `AG-BE-SW-001` is unblocked or complete.
