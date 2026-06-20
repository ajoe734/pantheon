# AG-BE-SW-001 Followup-2 Sidecar BFF and Frontend Handoff

| Field | Value |
|---|---|
| Sidecar task | `AG-BE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` |
| Helper parent | `AG-BE-SW-001` - Workshop session/event persistence |
| Helper kind | `bff_handoff_packet` |
| Owner / reviewer | `Codex` / `Codex2` |
| Date | `2026-06-20` |
| Status | `ready for sidecar review` |
| Mutates canonical truth | `false` |

Scope constraint: this packet is support material only. It does not change L1
canonical truth, OpenAPI, capability manifests, BFF runtime code, route
registries, governance implementation, persona/registry state, database
migrations, or execute-plans source.

## 1. Purpose

This followup packet gives the parent owner and reviewer a narrower decision
matrix for the Agora strategy workshop surface after the first
`AG-BE-SW-001-SIDECAR-BFF-HANDOFF` packet. It focuses on:

- the current BFF route/query gap around `agora.workshop.v1`
- the safe operator journey while parent `AG-BE-SW-001` remains blocked
- the execute-plans adapter and page handoff needed before live workshop UI
  claims are made

The parent task is currently blocked because its summary asks for SD section
and route/table details that are not present in the checked local design
sources. This sidecar does not unblock or implement the parent task; it packages
the existing route facts so the parent owner can decide what, if anything, is
safe to absorb later.

## 2. Sources Rechecked

| Source | Why it matters |
|---|---|
| `.orchestrator/task-briefs/ag_be_sw_001_sidecar_bff_handoff_followup_2.md` | Sidecar assignment and support-only boundary. |
| `ai-status.json` through `AI_NAME=Codex ./scripts/ai-status.sh show ...` | Active sidecar state and blocked parent state. |
| `support/sidecars/AG-BE-SW-001/AG-BE-SW-001-SIDECAR-BFF-HANDOFF.md` | First sidecar packet to avoid duplicating or broadening claims. |
| `services/control-plane/specs/agora/capability_manifest.json` | Frozen `agora.workshop.v1` capability and route prefixes. |
| `services/control-plane/specs/agora/strategy_workshop.schema.json` | `StrategyWorkshop` schema exists, but no direct generic workshop route is present. |
| `services/control-plane/openapi/agora_v1.openapi.yaml` | Current OpenAPI route catalog for workshop-adjacent routes and broad object shapes. |
| `services/control-plane/bff/agora/router.py` | Package router includes the strategy-workshop placeholder sub-router. |
| `services/control-plane/bff/agora/strategy_workshop/router.py` | Placeholder module lists migrated-future routes, but returns an empty router. |
| `services/control-plane/bff/main.py` | Runtime route implementation home for current workshop-adjacent BFF routes. |
| `services/control-plane/bff/test_bff_agora_core_contract.py` | Training example write and core Agora read evidence. |
| `services/control-plane/bff/test_bff_agora_extended_contract.py` | Evidence pack, persona-lab, evaluation, skill-coaching, and read fallback evidence. |
| `services/control-plane/bff/test_ask_003_committee_lifecycle.py` | Committee session lifecycle behavior. |
| `services/control-plane/bff/test_ask_004_memo_publish_contract.py` | Committee memo creation/publish behavior and management handoff. |
| `services/control-plane/bff/test_bff_b2_005_agora_canonical_aliases.py` | Alias relationship between `/bff/agora/committee-sessions` and `/bff/agora/sessions`. |
| `/home/lupin/code/execute-plans/src/lib/bff-v1/paths.ts` | Current frontend path helpers lack workshop route builders. |
| `/home/lupin/code/execute-plans/src/lib/bff/agora.ts` | Current frontend Agora live adapter covers daily/signals/inbox/journal/ask only. |
| `/home/lupin/code/execute-plans/src/agora/pages/*.tsx` | Current Agora workshop/trainer/evaluation/persona-lab pages still use seed/local actions for this route family. |

Frontend checkout caveat: `/home/lupin/code/execute-plans` was observed on
`main...origin/main [ahead 2, behind 467]`. Treat those frontend observations as
local checked-source evidence, not a statement about remote `main` tip.

## 3. BFF Route Decision Matrix

| Surface | Current BFF truth | Frontend handoff rule |
|---|---|---|
| Generic workshop facade | `StrategyWorkshop` schema exists, but no `GET|POST /bff/agora/workshops` route was found in OpenAPI, route manifests, or runtime handlers. | Do not implement a generic `workshops` adapter or local DTO synthesis unless parent explicitly freezes that route contract. |
| Package sub-router | `create_agora_router()` includes `create_strategy_workshop_router(...)`, but `strategy_workshop/router.py` returns an empty `APIRouter`. Runtime routes remain in `main.py`. | Do not claim package-router migration as complete. Parent may either leave migration deferred or scope a later canonical implementation. |
| Committee session alias | `GET /bff/agora/committee-sessions` is a core alias over `agora_sessions` and is tested as sharing the same read surface as `/bff/agora/sessions`. Extended lifecycle routes live under `/bff/agora/committee/sessions`. | For FE list/detail/create/open/close, parent should freeze whether the frontend uses the extended `/committee/sessions` family or the alias only for list compatibility. |
| Committee lifecycle | `GET|POST /bff/agora/committee/sessions`, detail, open, and close exist with `Idempotency-Key` required on writes. They use local BFF session store and ask-channel SSE events. | FE write adapters must always set `Idempotency-Key`, reject body idempotency keys, and propagate 401/403/404/409 without seed fallback. |
| Committee memos | Memo list/create/detail/publish routes exist. Publish creates a `consult_memo_to_management_review` handoff and emits ask-channel SSE events. | FE should model memo publish as a management-review handoff, not direct registry/runtime promotion. |
| Evidence pack | `POST /bff/agora/committee/{sessionId}/evidence-pack` and `/files` accept JSON metadata. `/files` requires a `files` array or single file metadata object and validates MIME, size, count, and metadata fields. | FE must not build a binary multipart upload UI until OpenAPI is reconciled with the JSON metadata implementation. |
| Training examples | `GET|POST /bff/agora/training-examples` exists; writes support idempotency and dry-run behavior. | Trainer/skill pages should not claim live training coverage until they call this route through a strict adapter rather than local examples/toasts. |
| Evaluation lists | `GET /bff/agora/evaluation-suites` and `/evaluation-runs` return list envelopes from the read surface. | Evaluation UI should be read-only/live-list first; local timer reruns must not represent backend evaluation execution. |
| Skill coaching | `GET /bff/agora/skill-coaching/sessions` returns a list envelope from local snapshot or service store. | Skill coaching UI can add a read adapter, but draft approval writes remain separate management approvals unless parent defines a workshop write. |
| Persona lab | `GET /bff/agora/persona-lab/runs` exists. `POST /bff/agora/persona-lab/{draftId}/actions/submit-commit` returns `202` and creates a persona management handoff. OpenAPI still says `{run_id}` and `200`. | Parent must freeze `{draftId}` vs `{run_id}` and `202` vs `200` before strict FE submit wiring. |
| Research handoff | `agora.research.v1` has `GET /bff/agora/research-tasks` and research schemas, but no workshop route directly emits a `ResearchPlan`. | "Create research task" UI should remain a handoff/queue action unless parent defines a new BFF write route. |

## 4. Query And DTO Gaps To Hand To Parent

| Gap | Why it blocks live FE claims | Parent decision needed |
|---|---|---|
| Missing `StrategyWorkshop` facade route | The schema can describe a workshop, but current route truth is committee/training/evaluation/persona-lab oriented. | Either consume current route family as the workshop surface or define `GET|POST /bff/agora/workshops` in a canonical task. |
| Broad OpenAPI object schemas | Several request/response bodies are still `type: object`, so generated FE clients cannot rely on named DTOs. | Bind named schemas or publish an adapter DTO contract with required fields. |
| Alias policy | `/committee-sessions`, `/sessions`, and `/committee/sessions` are all present with different roles. | Freeze canonical FE path helpers and mark compatibility aliases. |
| Evidence upload mismatch | OpenAPI describes multipart upload, while BFF and current frontend helper use JSON file metadata. | Decide binary upload through storage+metadata or update OpenAPI to match JSON metadata. |
| Persona-lab route mismatch | Runtime/tests use `{draftId}` and `202`; OpenAPI says `{run_id}` and `200`. | Freeze route parameter and status before FE submit integration. |
| CTA authority | Current route responses do not expose a per-object `allowedActions` block for every workshop CTA. | Decide whether FE gates by role, route capability, object state, or backend-shaped action authority. |
| Research plan generation | No direct workshop-to-`ResearchPlan` route is present. | Decide whether this is a new workshop write, assistant route, or research-service handoff. |

## 5. Current Frontend State Snapshot

| Area | Observed state in local execute-plans checkout | Handoff requirement |
|---|---|---|
| Path helpers | `src/lib/bff-v1/paths.ts` has Agora helpers for signals, inbox, journal, postmortems, and ask sessions only. | Add path builders for committee sessions, memos, evidence packs, training examples, evaluations, skill coaching, persona-lab runs, and submit-commit only after parent freezes aliases. |
| Agora live adapter | `src/lib/bff/agora.ts` adapts daily, signals, inbox, journal, and ask sessions. | Add a separate `workshop` or `committee` adapter with strict live error propagation; no seed fallback in strict mode. |
| Committee UI | `CommitteeRoom.tsx` initializes from local `seed`, mutates local session state, generates mock persona responses, and sends evidence metadata through direct `fetch(COMMITTEE_EVIDENCE_ENDPOINTS.uploadFiles(...))`. | Replace local session/memo/evidence mutations with BFF adapters. Remove page-level direct fetch from live path. |
| Evidence helper | `src/lib/v3/committeeEvidence.ts` matches current JSON metadata upload constraints and endpoint strings. | Keep as client-side preflight only; BFF validation remains authoritative. |
| Evaluation UI | `EvaluationSuites.tsx` uses local seed suites and timer-based mock rerun. | Wire list adapters first; do not show rerun success unless a backend route exists. |
| Persona lab UI | `PersonaLab.tsx` uses live-ish skills/tools lists, but draft save/test/submit are local toasts/mock output. | Add persona-lab runs and submit-commit adapter after route/status truth is frozen. |
| Skill coaching UI | `SkillCoaching.tsx` uses local drafts/examples and management approval mutation. | Do not conflate management approval mutation with live `training-examples` or skill-coaching session routes. |
| Trainer Studio | `TrainerStudio.tsx` uses seed feedback and management approval mutation for persona updates. | Treat as local triage/approval UI until training/evaluation/persona-lab adapters exist. |
| Tests | `liveAdapters.test.ts` covers current Agora signal list/detail behavior, not workshop route family. | Add path, DTO, strict fallback, idempotency, and no-seed-regression tests for selected routes. |

## 6. Safe Operator Journey

Current safe journey while parent remains blocked:

```text
Operator opens an Agora workshop-related page
  -> frontend may read only routes with implemented strict adapters
  -> pages without workshop adapters must not silently fall back to local seed data
     in live/strict mode
  -> direct BFF consumers may read committee sessions, evaluation lists,
     skill-coaching sessions, persona-lab runs, and training examples
  -> every write route must use Idempotency-Key and must not put idempotencyKey
     in the request body
  -> memo publish and persona-lab submit-commit create management handoffs
  -> no workshop UI may create runtime bindings, route broker orders, or mutate
     capital authority
```

Preferred journey after parent freezes route aliases and DTOs:

```text
Operator opens Agora Committee / Strategy Workshop
  -> frontend resolves Agora capability/scope
  -> frontend lists the selected canonical committee/workshop session route
  -> operator creates or opens a committee-mode session
  -> operator attaches evidence-pack metadata
  -> personas/operators create memo drafts
  -> memo publish creates management-review handoff and emits ask-channel SSE
  -> operator views linked training/evaluation/persona-lab read surfaces
  -> persona-lab submit-commit creates a persona management handoff
```

Failure and degraded behavior:

| Failure | FE behavior |
|---|---|
| `401` or `403` | Render auth/scope blocked state; hide or disable write CTAs. |
| `404` committee session or memo | Render missing state; do not reconstruct from seed data. |
| `409` memo id conflict | Show duplicate/idempotency guidance and retry with original key or a new memo id. |
| `422` evidence validation | Surface BFF validation details for MIME, size, count, and metadata fields. |
| Missing adapter in strict mode | Emit a BFF gap handoff; do not enable success-path write UI. |

## 7. Parent Absorption Gates

| Gate | Required parent evidence |
|---|---|
| Route family decision | One canonical FE path family is selected for committee/workshop session list/detail/create/open/close. |
| DTO binding | Request and response fields are either bound to named schemas or captured in a frontend adapter DTO contract. |
| Evidence upload truth | JSON metadata vs multipart binary truth is reconciled before binary upload UI work. |
| Persona-lab truth | `{draftId}`/`{run_id}` and `202`/`200` are frozen before submit wiring. |
| Strict adapter work | FE path builders, live adapters, DTO adapters, and strict-mode no-seed tests exist before claiming live workshop UI. |
| Idempotency | FE write tests prove `Idempotency-Key` is present and body idempotency keys are not sent. |
| CTA authority | Create/open/close/memo/publish/evidence/submit controls are gated by accepted backend authority, not local role guesses alone. |
| Safety boundary | No Agora workshop route or UI path grants live trading, RuntimeBinding, broker order, or capital binding authority. |

## 8. Suggested Parent Adapter Shape

Once the parent freezes aliases and DTOs, a minimal frontend client can be:

```ts
type AgoraWorkshopClient = {
  committeeSessions: {
    list(): Promise<CommitteeSession[]>;
    create(input: CreateCommitteeSessionInput, idempotencyKey: string): Promise<CommitteeSession>;
    get(sessionId: string): Promise<CommitteeSession>;
    open(sessionId: string, idempotencyKey: string): Promise<CommitteeSession>;
    close(sessionId: string, input: CloseCommitteeSessionInput, idempotencyKey: string): Promise<CommitteeSession>;
    listMemos(sessionId: string): Promise<CommitteeMemo[]>;
    createMemo(sessionId: string, input: CreateMemoInput, idempotencyKey: string): Promise<CommitteeMemo>;
    publishMemo(sessionId: string, memoId: string, idempotencyKey: string): Promise<CommitteeMemo>;
  };
  evidencePacks: {
    create(sessionId: string, input: EvidencePackInput, idempotencyKey: string): Promise<CommitteeEvidencePack>;
    attachFiles(sessionId: string, input: EvidenceFileMetadataInput, idempotencyKey: string): Promise<CommitteeEvidencePack>;
  };
  trainingExamples: {
    list(): Promise<TrainingExample[]>;
    create(input: TrainingExampleInput, idempotencyKey: string): Promise<TrainingExample>;
  };
  evaluations: {
    suites(): Promise<EvaluationSuite[]>;
    runs(): Promise<EvaluationRun[]>;
  };
  skillCoaching: {
    sessions(): Promise<SkillCoachingSession[]>;
  };
  personaLab: {
    runs(): Promise<PersonaLabRun[]>;
    submitCommit(draftId: string, input: PersonaLabCommitInput, idempotencyKey: string): Promise<HandoffResult>;
  };
};
```

Do not add broker, RuntimeBinding, deployment, or capital commands to this
client. The workshop surface is observation, training, review, and handoff
only.

## 9. Suggested Verification

Current-state sidecar checks:

```bash
git diff --check -- support/sidecars/AG-BE-SW-001/AG-BE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md
AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2
AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-SW-001
python3 -m pytest services/control-plane/bff/test_bff_agora_extended_contract.py services/control-plane/bff/test_ask_003_committee_lifecycle.py services/control-plane/bff/test_ask_004_memo_publish_contract.py services/control-plane/bff/test_bff_agora_core_contract.py services/control-plane/bff/tests/test_agora_router.py -q
```

Results recorded by Codex:

- `git diff --check -- support/sidecars/AG-BE-SW-001/AG-BE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md` passed.
- Focused BFF pytest command above passed after aligning and merging current `origin/dev`: `89 passed, 2 warnings in 44.25s`.
- execute-plans was read-only evidence only; no frontend tests were run because this sidecar does not modify that repo and the local checkout is far behind remote `main`.

Parent follow-up checks before implementation claims:

```bash
rg -n "/bff/agora/workshops|StrategyWorkshop|committee/sessions|evidence-pack|persona-lab" services/control-plane/openapi services/control-plane/bff services/control-plane/specs/agora
cd /home/lupin/code/execute-plans
rg -n "committee|training-examples|evaluation-runs|evaluation-suites|skill-coaching|persona-lab" src/lib/bff-v1 src/lib/bff src/agora
npx vitest run src/lib/bff/__tests__/liveAdapters.test.ts
```

Expected scope check:

- Only this sidecar support artifact and task-scoped brief/status artifacts are
  touched by this task.
- No L1 canonical docs, OpenAPI, capability manifest, BFF runtime, registry,
  governance, migration, or execute-plans files are changed.
- The packet does not claim parent `AG-BE-SW-001` is unblocked or complete.

## 10. Handoff

This packet is ready for `Codex2` review. The parent owner should treat it as
decision support for alias, DTO, upload, persona-lab, idempotency, and frontend
strict-adapter planning. It should not be absorbed as canonical route truth
unless a later parent task promotes the relevant decisions through the normal
contract path.
