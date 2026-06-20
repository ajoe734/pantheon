# AG-BE-SW-001 Followup-5 Sidecar BFF and Frontend Handoff

| Field | Value |
|---|---|
| Sidecar task | `AG-BE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-5` |
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

This fifth followup turns the active parent blocker into an implementation
handoff checklist. Followup-4 already established that `AG-XR-OPENAPI-001`
closed the contract-layer route gap by adding canonical
`/bff/agora/workshops` v1.1 routes, while runtime implementation remains
absent from the strategy-workshop package router.

This packet does not invent missing backend behavior. It identifies exactly
which parent decisions must exist before `AG-BE-SW-001` can safely implement
workshop session, event, and completeness persistence.

## 2. Sources Rechecked

| Source | Evidence used |
|---|---|
| `.orchestrator/task-briefs/ag_be_sw_001_sidecar_bff_handoff_followup_5.md` | Sidecar assignment, support-only boundary, owner/reviewer. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-5` | Confirms followup-5 is active, owner `Codex`, reviewer `Codex2`, artifact path. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-SW-001` | Confirms parent is blocked and waiting for Codex on private-store, StrategySpec mapping, index-set, and status-vocabulary clarification. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-XR-OPENAPI-001` | Confirms v1.1 OpenAPI and capability predecessor is archived `done`. |
| `support/sidecars/AG-BE-SW-001/*FOLLOWUP-2.md` through `*FOLLOWUP-4.md` | Prior packets covering adjacent routes, frontend gap, trainer/committee distinction, and v1.1 contract/runtime split. |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/contract-closure/03_servant_and_workshop_contracts.md` | Route family, persistence row names, event privacy rule, ETag/If-Match/idempotency contract. |
| `services/control-plane/openapi/agora_v1_1.openapi.yaml` | v1.1 request shapes and 13 canonical `/bff/agora/workshops` routes. |
| `services/control-plane/specs/agora/v2/capability_manifest_v1_1.json` | `agora.workshop.v1` v1.1 prefix `/bff/agora/workshops`. |
| `services/control-plane/specs/agora/strategy_workshop.schema.json` | Frozen v1 workshop schema and status enum `open`, `in_review`, `concluded`, `archived`. |
| `services/control-plane/specs/agora/strategy_completeness.schema.json` | Frozen v1 completeness schema using `strategy_ref` plus optional `workshop_id`. |
| `services/control-plane/bff/agora/strategy_workshop/router.py` | Still an empty placeholder returning `APIRouter(tags=["agora-workshop"])`. |
| `/home/lupin/code/execute-plans/src/lib/bff-v1/paths.ts` | No local `/bff/agora/workshops` path builders were found. |
| `/home/lupin/code/execute-plans/src/lib/bff-v1/client.ts` and `headers.ts` | Local client returns parsed JSON only; `If-Match` is built from `ifMatchVersion` as `"value"` unless callers override raw headers. |

Frontend checkout caveat: `/home/lupin/code/execute-plans` was observed on
`main...origin/main [ahead 2, behind 467]`. Treat frontend observations as
checked-local-source evidence, not as a claim about remote `main` tip.

## 3. Current Parent Blocker Split

| Blocker | What is known | What is not safe to infer |
|---|---|---|
| Private content store | Contract prose requires `strategy_workshop_event` to store `private_content_ref` and `redacted_summary`, not private raw content. v1.1 create/message requests still accept raw `initial_message` or `content`. | Do not invent encryption keys, retention, private-store table shape, ref format, redaction algorithm, or failure semantics. |
| StrategySpec reference mapping | v1.1 create accepts optional `strategy_spec_ref`; prose persistence names `strategy_id`, `active_strategy_spec_registry_id`, and `selected_version_id`; frozen v1 schema uses `subject.kind/ref`; completeness uses `strategy_ref`. | Do not derive registry ids from labels, URL params, local seed ids, or copied StrategySpec payloads. |
| Index set | Prose explicitly names event uniqueness by `(workshop_id, sequence_no)` and parent summary references a larger section 22.6 index set. The exact checked-source index list was not found in the sources rechecked for this sidecar. | Do not invent migration indexes or claim event pagination/storage proof beyond the checked contract. |
| Status vocabulary | Frozen v1 schema has `open`, `in_review`, `concluded`, `archived`; v1.1 list filter text names `active`, `concluded`, `archived`. | Do not choose whether `active` means `open`, `in_review`, or both without parent decision. |

These are real blockers for implementation. They are not frontend-only polish
items, because each one affects persisted data, route behavior, concurrency
semantics, or UI state claims.

## 4. Parent Clarification Requests

The parent owner should record answers to the following before writing runtime
or migration code:

| ID | Clarification needed | Minimum acceptable answer |
|---|---|---|
| C1 | Private content ownership | Name the private content store owner/module, ref format, encryption/key boundary, retention/deletion rule, and whether workshop BFF calls it directly or through a service adapter. |
| C2 | Redaction boundary | Define whether redacted summaries are generated synchronously in the write route, asynchronously by event processing, or supplied by a vetted upstream service. |
| C3 | Write failure behavior | Define whether create/message fails closed when private-store or redaction fails, and what error code/details frontend should receive. |
| C4 | StrategySpec ref mapping | Map `strategy_spec_ref` to persisted `strategy_id`, `active_strategy_spec_registry_id`, and `selected_version_id`, including what is nullable on workshop create. |
| C5 | Completeness mapping | Define how `strategy_completeness_snapshot.strategy_version_id` relates to frozen `StrategyCompleteness.strategy_ref` and optional `workshop_id`. |
| C6 | Status mapping | Decide whether v1.1 `status=active` is a query alias for `open` plus `in_review`, a distinct persisted status, or wording to be corrected through contract update. |
| C7 | Index set | Provide the exact migration index set, including event ordering, tenant/user list query, status filters, completeness lookup, and any unique idempotency key storage. |
| C8 | ETag source of truth | Confirm whether ETag version comes only from `strategy_workshop_session.lock_version`, and whether every mutating route increments it exactly once. |

If any of C1-C8 remains unanswered, the conservative parent action is to keep
`AG-BE-SW-001` blocked rather than implementing a best-effort persistence layer.

## 5. Backend Implementation Guardrails For Parent

When the parent is unblocked, the implementation should be reviewed against
these sidecar guardrails:

| Area | Required guardrail |
|---|---|
| Router home | Implement canonical `/bff/agora/workshops` in the strategy-workshop BFF surface selected by the parent. Do not reuse committee/trainer routes as an implicit workshop aggregate. |
| Event privacy | Persist only `private_content_ref`, `redacted_summary`, `payload_refs_json`, trace metadata, sequence, actor, and event type in `strategy_workshop_event`. Raw private content must not be stored in event payloads or tests. |
| Persistence separation | Workshop rows reference Strategy Registry state; they must not copy StrategySpec truth or become the canonical StrategySpec store. |
| Concurrency | GET aggregate must return ETag `W/"workshop:{id}:v{lock_version}"`; mutating routes after create must require both exact `If-Match` and `Idempotency-Key`. |
| Conflict response | ETag mismatch must return 409 `CONCURRENT_MODIFICATION` with current ETag and latest aggregate link/details. |
| Ordering | Events must be append-only and ordered by `sequence_no`; duplicate sequence should be rejected by storage, not hidden by route logic. |
| Completeness | Completeness snapshots are derived/read artifacts for workshop readiness; they do not promote strategies, bind capital, or create runtime bindings. |
| Safety boundary | No workshop route may route broker orders, create `RuntimeBinding`, mutate capital binding, or promote live execution authority. |

## 6. Frontend Handoff Delta

The frontend handoff remains stricter after this packet:

| Area | Current checked-local state | Required before live Strategy Workshop UI |
|---|---|---|
| Path builders | No `paths.agoraWorkshops*` builders were found in local `paths.ts`. | Add builders only for the canonical `/bff/agora/workshops` v1.1 family after backend behavior is implemented. |
| ETag capture | Local `bffFetch<T>()` returns parsed JSON, not response headers. | Add an adapter/client path that returns the body plus response headers, or otherwise captures the exact ETag from create/get. |
| If-Match propagation | Local `headers.ts` quotes `ifMatchVersion` as `"value"`; callers can only send exact weak ETag by overriding raw headers carefully. | Workshop adapter should send the exact ETag string returned by BFF, not reformat a lock version. |
| Private message handling | UI may need raw `initial_message` and message `content` only as transient form input. | Do not write raw workshop messages to local storage, seed fixtures, analytics, logs, snapshots, or durable client cache. |
| Status tabs | Contract vocabulary is unresolved between `active` and frozen schema statuses. | Do not ship an API-dependent `active` tab until parent freezes the status mapping. |
| Strict mode | Missing adapter or BFF failure must not produce seed success. | In `VITE_BFF_FALLBACK=strict`, render unavailable/error state and disable mutating CTAs. |
| Adjacent routes | Committee, trainer, evaluation, skill-coaching, and persona-lab routes remain adjacent surfaces from prior packets. | Do not synthesize a v1.1 workshop aggregate from adjacent route families or local seed state. |

Minimum frontend tests after backend delivery:

- path generation for all 13 v1.1 workshop routes
- ETag capture from create/get and exact `If-Match` reuse on mutations
- idempotency key is header-only and never in request bodies
- 409 refresh/retry behavior uses current ETag from backend details
- raw message content is not persisted in local storage, logs, fixtures, or snapshots
- strict live mode does not fall back to seed data
- no imports or calls into broker, RuntimeBinding, deployment, or capital authority code paths

## 7. Parent Review Gates

| Gate | Pass condition |
|---|---|
| G1 private store | Reviewer can point to the implemented private-store adapter/API and prove event rows contain only refs and redacted summaries. |
| G2 schema mapping | Reviewer can trace `strategy_spec_ref` through the persisted registry refs without copied StrategySpec truth. |
| G3 migration/indexes | Reviewer can compare migration indexes to the clarified index set and event sequence uniqueness. |
| G4 status handling | Reviewer can see an explicit status mapping or a contract update, not an ad hoc route filter. |
| G5 concurrency | Reviewer can prove ETag source, `If-Match` validation, idempotency storage, lock increments, and 409 body shape. |
| G6 FE readiness | Frontend cannot enable live workshop UI until path builders, ETag-aware client support, strict fallback tests, and privacy tests exist. |
| G7 safety | No route or UI path grants live trading, RuntimeBinding, broker order, deployment, or capital-binding authority. |

## 8. Suggested Verification

Current-state sidecar checks:

```bash
git diff --check -- \
  .orchestrator/task-briefs/ag_be_sw_001_sidecar_bff_handoff_followup_5.md \
  support/sidecars/AG-BE-SW-001/AG-BE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-5.md

AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-5
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

- `git diff --check --no-index /dev/null support/sidecars/AG-BE-SW-001/AG-BE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-5.md >/tmp/ag-be-sw-followup5-diff-check.out; test ! -s /tmp/ag-be-sw-followup5-diff-check.out` passed.
- `git diff --check -- .orchestrator/task-briefs/ag_be_sw_001_sidecar_bff_handoff_followup_5.md support/sidecars/AG-BE-SW-001/AG-BE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-5.md` passed for tracked diff whitespace.
- `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-5` confirmed the sidecar is active `in_progress`, owner `Codex`, reviewer `Codex2`.
- `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-SW-001` confirmed the parent remains blocked on private-store, StrategySpec mapping, index-set, and status-vocabulary clarification.
- `AI_NAME=Codex ./scripts/ai-status.sh show AG-XR-OPENAPI-001` confirmed the v1.1 OpenAPI/capability predecessor is archived `done`.
- `python3 -c "import yaml; yaml.safe_load(open('services/control-plane/openapi/agora_v1_1.openapi.yaml'))"` passed.
- `python3 -m json.tool services/control-plane/specs/agora/v2/capability_manifest_v1_1.json >/dev/null` passed.
- `LC_ALL=C rg -n '[^[:ascii:]]' support/sidecars/AG-BE-SW-001/AG-BE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-5.md .orchestrator/task-briefs/ag_be_sw_001_sidecar_bff_handoff_followup_5.md` found no non-ASCII in this packet; the generated task brief retains its Chinese summary.

## 9. Handoff

Reviewer focus for `Codex2`:

- Verify this packet preserves the support-only boundary.
- Verify C1-C8 capture the parent clarifications needed before
  `AG-BE-SW-001` implementation.
- Verify the frontend handoff prevents local seed synthesis, raw private
  content persistence, weak ETag reformatting, and any execution/capital
  authority path.

Parent owner follow-up:

- Keep `AG-BE-SW-001` blocked until C1-C8 are answered, or record the accepted
  answers before implementation.
- Once answers exist, implement only the canonical `/bff/agora/workshops`
  route family and persistence boundary; do not absorb adjacent committee or
  trainer routes as the workshop aggregate.
