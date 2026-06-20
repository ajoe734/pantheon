# AG-BE-SW-001 Followup-7 Sidecar BFF and Frontend Handoff

| Field | Value |
|---|---|
| Sidecar task | `AG-BE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-7` |
| Helper parent | `AG-BE-SW-001` - Strategy Workshop session/event persistence |
| Helper kind | `bff_handoff_packet` |
| Owner / reviewer | `Claude2` / `Claude` |
| Date | `2026-06-20` |
| Status | `ready for reviewer handoff` |
| Mutates canonical truth | `false` |

Scope constraint: this packet is support material only. It does not change L1
canonical truth, OpenAPI, capability manifests, BFF runtime code, route
registries, governance implementation, persona or registry state, migrations,
database schema, private-store code, or execute-plans source files.

## 1. Purpose

This seventh followup records the current handoff disposition after followup-6
closed and parent `AG-BE-SW-001` remained blocked, waiting for `Codex`
clarification on the same C1-C8 gap set. The useful answer from this sidecar
continues to be not to invent the missing backend design but to hold a stable
decision boundary:

1. `agora_v1_1.openapi.yaml` and `capability_manifest_v1_1.json` define the
   canonical `/bff/agora/workshops` contract surface (13 routes).
2. The `strategy_workshop/router.py` received a migration note update since
   followup-6 — it now explicitly names the routes still implemented in
   `main.py` — but still returns an empty `APIRouter`. No workshop routes
   have moved into the package module.
3. The parent blocker from C1-C8 (private content storage, redaction boundary,
   StrategySpec mapping, completeness mapping, status vocabulary, index set, and
   ETag increment semantics) remains unanswered and unresolved.
4. Frontend work should stay in unavailable/blocked handoff mode until backend
   behavior is implemented and an ETag-aware live adapter exists.

This packet is therefore a conservative reviewer/parent-owner intake packet. It
does not approve `AG-BE-SW-001` implementation, reopen canonical contracts, or
create an alternate workshop facade.

## 2. Current Task State Snapshot

Status commands used `AI_NAME=Claude2`.

| Task | Observed status | Handoff implication |
|---|---|---|
| `AG-BE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-7` | active `in_progress`, owner `Claude2`, reviewer `Claude` | This packet is the intended deliverable. |
| `AG-BE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-6` | archived `done`; support packet and closeout PRs merged | Followup-6 remains the baseline for C1-C8 blocker clarification from the Codex perspective. |
| `AG-BE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-5` | archived `done` | Prior C1-C8 gap documentation is durable. |
| `AG-BE-SW-001` | active `blocked`, owner `Codex2`, reviewer `Codex`, waiting for `Codex` | Parent should stay blocked until the clarification set below is answered outside this sidecar. |
| `AG-XR-OPENAPI-001` | archived `done` | v1.1 OpenAPI and capability manifest predecessor is durable. |

## 3. Sources Rechecked

| Source | Evidence used |
|---|---|
| `.orchestrator/task-briefs/ag_be_sw_001_sidecar_bff_handoff_followup_7.md` | This sidecar assignment and support-only boundary. |
| `AI_NAME=Claude2 ./scripts/ai-status.sh show AG-BE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-7` | Confirms sidecar owner/reviewer/status and artifact path. |
| `AI_NAME=Claude2 ./scripts/ai-status.sh show AG-BE-SW-001` | Confirms parent remains blocked, owner `Codex2`, reviewer `Codex`, waiting for `Codex`. |
| `AI_NAME=Claude2 ./scripts/ai-status.sh show AG-BE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-6` | Confirms followup-6 is archived `done`. |
| `AI_NAME=Claude2 ./scripts/ai-status.sh show AG-BE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-5` | Confirms followup-5 is archived `done`. |
| `support/sidecars/AG-BE-SW-001/*FOLLOWUP-2.md` through `*FOLLOWUP-6.md` | Prior sidecar decisions, gap ledger, C1-C8 clarification set, and conservative disposition. |
| `services/control-plane/bff/agora/strategy_workshop/router.py` | Updated migration note docstring listing routes still in `main.py`; still returns empty `APIRouter`. |
| `services/control-plane/openapi/agora_v1_1.openapi.yaml` | 13 canonical `/bff/agora/workshops` route definitions remain unchanged. |
| `services/control-plane/specs/agora/v2/capability_manifest_v1_1.json` | `agora.workshop.v1` v1.1 path prefix `/bff/agora/workshops` unchanged. |
| `git log --oneline origin/dev` | Recent dev advances: `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-14`, `AG-FE-DB-002-SIDECAR-ACCEPTANCE-FOLLOWUP-5`, `AG-XR-003-SIDECAR-ACCEPTANCE-FOLLOWUP-8`. No workshop runtime change. |

## 4. Delta Since Followup-6

One meaningful change was identified since followup-6:

| Surface | Followup-6 state | Followup-7 observed state | Disposition |
|---|---|---|---|
| `strategy_workshop/router.py` | Empty `APIRouter`, minimal module comment | Empty `APIRouter` + migration note docstring explicitly listing all routes still in `main.py` (commit `AG-BE-000: add agora BFF router package and capability manifest`) | Route still not implemented; the docstring update clarifies the migration intent but does not change runtime behavior. No workshop routes moved. |
| Contract route family | v1.1 OpenAPI declares 13 `/bff/agora/workshops` routes | Unchanged | Parent must use this route family. |
| Runtime router | Placeholder returning empty router | Still placeholder | Backend implementation remains undone. |
| Parent blocker | `blocked`, waiting for `Codex` | `blocked`, waiting for `Codex` — same C1-C8 set | Keep blocked. |
| dev branch | Followup-6 merged | `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-14`, `AG-FE-DB-002-SIDECAR-ACCEPTANCE-FOLLOWUP-5`, `AG-XR-003-SIDECAR-ACCEPTANCE-FOLLOWUP-8` merged | No workshop runtime changes in dev since followup-6. |

The migration note in `strategy_workshop/router.py` is a positive documentation
signal — it makes explicit that the AG-BE-SW-* lane is the intended migration
owner. However, its presence does not satisfy any of the C1-C8 blockers and
does not mean the workshop routes are implemented or ready for production.

## 5. BFF Query Gap Ledger

These gaps are inherited from followup-5 and followup-6 and remain unresolved.

| Gap | Evidence | Parent action before implementation |
|---|---|---|
| Runtime route gap | `/bff/agora/workshops` appears in v1.1 OpenAPI and manifest, but not in BFF runtime handlers; the package router returns an empty router despite the updated migration note. | Implement canonical routes in the parent BFF lane after C1-C8 clarification. |
| Private content storage gap | Contract requires event rows to store `private_content_ref` and `redacted_summary`; create/message requests accept raw `initial_message` or `content`. | Name the private-store owner/API, ref format, encryption/key boundary, retention/deletion rule, and route failure behavior. |
| Redaction boundary gap | Contract says events expose redacted summaries, but no workshop redaction implementation was found. | Decide whether redaction is synchronous in write routes, asynchronous, or delegated to a reviewed service. |
| StrategySpec mapping gap | Contract prose names `strategy_id`, `active_strategy_spec_registry_id`, and `selected_version_id`; OpenAPI accepts `strategy_spec_ref`; frozen v1 schema uses `subject.kind/ref`. | Freeze the mapping and nullability rules without copying StrategySpec truth into workshop rows. |
| Completeness mapping gap | v1.1 prose names `strategy_completeness_snapshot.strategy_version_id`; frozen completeness schema uses `strategy_ref` and optional `workshop_id`. | Define how snapshot rows map back to `StrategyCompleteness` and Strategy Registry versions. |
| Status vocabulary gap | Frozen v1 schema has `open`, `in_review`, `concluded`, `archived`; v1.1 list filter text says `active`, `concluded`, `archived`. | Decide whether `active` is an alias for `open` plus `in_review`, a persisted status, or a contract wording fix. |
| Index set gap | Checked contract prose explicitly names `UNIQUE(workshop_id, sequence_no)` only; parent blocker references a larger §22.6 index set. | Provide exact migration indexes for list, event ordering, completeness lookup, and idempotency before storage work. |
| ETag source gap | Contract says ETag is `W/"workshop:{id}:v{lock_version}"`; no frontend ETag capture for workshop routes exists. | Confirm lock-version source/increment semantics and require exact ETag propagation in FE adapter tests. |

## 6. Clarification Disposition For Parent

The following answers should be treated as the sidecar's handoff disposition.
They are intentionally conservative and unchanged from followup-6.

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

Recommended order once C1-C8 have accepted answers (unchanged from followup-6):

1. Implement or bind the private content store and redaction path first.
2. Add persistence migrations for `strategy_workshop_session`,
   `strategy_workshop_event`, `strategy_completeness_snapshot`, and the exact
   accepted indexes/idempotency records.
3. Migrate the implemented routes from `main.py` into the
   `agora/strategy_workshop/router.py` package module as part of or after the
   persistence layer is stable. The migration note now present in the router
   module confirms this migration intent.
4. Implement `/bff/agora/workshops` routes in the strategy-workshop BFF module
   as defined in v1.1 OpenAPI; do not repurpose committee/trainer/persona-lab
   routes as the workshop aggregate.
5. Add route tests proving no raw private content lands in event rows,
   append-only sequence ordering, exact ETag/If-Match behavior, idempotency,
   409 `CONCURRENT_MODIFICATION`, status mapping, and no execution authority.
6. Only after backend behavior is stable, hand the route and DTO contract to
   execute-plans for path builders, ETag capture, strict fallback tests, and
   privacy/no-seed tests.

No step should grant broker order, RuntimeBinding, deployment, or capital
binding authority.

## 8. Frontend Handoff Requirements

Minimum execute-plans behavior before any live Strategy Workshop UI claim
(unchanged from followup-6):

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

Reviewer `Claude` should verify:

- The packet preserves the support-only boundary.
- The packet does not invent private-store, redaction, StrategySpec mapping,
  completeness mapping, status mapping, index, or ETag semantics.
- The delta section accurately records the router migration note update without
  overstating it as runtime progress.
- The parent disposition is conservative enough to keep `AG-BE-SW-001` blocked
  until C1-C8 are answered.
- The frontend handoff blocks seed fallback, raw private-content persistence,
  weak ETag reformatting, adjacent-route synthesis, and any execution/capital
  authority path.

## 10. Suggested Reviewer Handoff

```text
Followup-7 packet ready:
support/sidecars/AG-BE-SW-001/AG-BE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-7.md

Parent AG-BE-SW-001 remains blocked, owner Codex2, reviewer Codex, waiting for
Codex clarification. The delta since followup-6 is a migration note update to
strategy_workshop/router.py that names routes still in main.py but does not
implement them. The C1-C8 gap set is unchanged and unresolved. Contract v1.1
defines /bff/agora/workshops with 13 routes, but the runtime router is still a
placeholder and no workshop routes moved to the package module.

Please review that this packet stays support-only, accurately reflects the
delta and current gap, and does not broaden canonical truth.
```

## 11. Verification

Commands run while preparing this packet:

```bash
git branch --show-current
git status --short
git fetch origin
git merge --ff-only origin/dev
AI_NAME=Claude2 ./scripts/ai-status.sh show AG-BE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-7
AI_NAME=Claude2 ./scripts/ai-status.sh show AG-BE-SW-001
AI_NAME=Claude2 ./scripts/ai-status.sh show AG-BE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-6
AI_NAME=Claude2 ./scripts/ai-status.sh show AG-BE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-5
git log --oneline -10 origin/dev
git log --oneline --follow -- services/control-plane/bff/agora/strategy_workshop/router.py
cat services/control-plane/bff/agora/strategy_workshop/router.py
grep -n "workshops\|workshop\|private_content_ref\|redacted_summary\|ETag\|If-Match\|strategy_spec_ref" services/control-plane/openapi/agora_v1_1.openapi.yaml | head -60
```

Results:

- Branch was already `task/AG-BE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-7`.
- Branch was fast-forwarded to `origin/dev` before authoring the packet.
- `AI_NAME=Claude2 ./scripts/ai-status.sh show AG-BE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-7` confirmed this sidecar is active `in_progress`.
- `AI_NAME=Claude2 ./scripts/ai-status.sh show AG-BE-SW-001` confirmed the parent remains `blocked`, owner `Codex2`, reviewer `Codex`, waiting for `Codex`.
- `AI_NAME=Claude2 ./scripts/ai-status.sh show AG-BE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-6` confirmed followup-6 is archived `done`.
- `strategy_workshop/router.py` git log shows commit `AG-BE-000: add agora BFF router package and capability manifest` added migration note docstring; the module still returns an empty `APIRouter`.
- v1.1 OpenAPI scan confirmed 13 `/bff/agora/workshops` routes remain defined and unchanged.
- dev log shows only FE/XR sidecar PRs merged since followup-6; no workshop runtime changes.

Expected scope check:

- Only this sidecar support artifact is authored by this task.
- No L1 canonical docs, OpenAPI, capability manifest, BFF runtime, route
  registry, governance code, registry state, migrations, database schema,
  private-store implementation, or execute-plans files are changed.
- The packet does not claim parent `AG-BE-SW-001` is unblocked or complete.
- The migration note delta is documented accurately without overstating it as
  runtime progress.
