# AG-BE-SW-001 Sidecar: BFF and Frontend Handoff Packet

| Field | Value |
|---|---|
| Task ID | `AG-BE-SW-001-SIDECAR-BFF-HANDOFF` |
| Helper kind | `bff_handoff_packet` |
| Parent task | `AG-BE-SW-001` - Agora strategy workshop support slice |
| Sidecar owner / reviewer | Codex2 / Codex |
| Prepared by | Codex2 |
| Date | 2026-06-20 |
| Mutates canonical truth | false |
| Status | Ready for sidecar review |

## Purpose

This support-only packet gives the parent owner the current BFF query gap,
operator journey, and execute-plans handoff notes for the Agora strategy
workshop surface. It does not modify L1 canonical truth, OpenAPI, BFF runtime
code, route registries, governance policy, persona/registry state, or
execute-plans source.

The current checkout already contains substantial Agora workshop-adjacent BFF
coverage: committee sessions, committee memos, evidence-pack metadata,
training examples, evaluation lists, skill-coaching lists, and persona-lab
handoff routes. The main handoff risk is that these routes are not yet exposed
through a complete execute-plans live adapter, and some route/schema details
still need the parent owner to choose the canonical frontend consumption path.

## Current BFF Truth

| Surface | Current state | Evidence |
|---|---|---|
| Capability manifest | `agora.workshop.v1` is frozen with strategy-workshop and completeness schemas plus route prefixes for evaluations, training examples, skill coaching, committee sessions, and persona lab. | `services/control-plane/specs/agora/capability_manifest.json` |
| Workshop schemas | `StrategyWorkshop` describes workshop session identity, subject, status, participant personas, completeness refs, and research plan refs. `ResearchPlan` and `ResearchRunSummary` exist as Agora research schemas that a workshop can reference. | `services/control-plane/specs/agora/strategy_workshop.schema.json`; `services/control-plane/specs/agora/research_plan.schema.json`; `services/control-plane/specs/agora/research_run_summary.schema.json` |
| Package router | `create_agora_router()` mounts a `strategy_workshop` sub-router, but `services/control-plane/bff/agora/strategy_workshop/router.py` is a placeholder that returns an empty router. | `services/control-plane/bff/agora/router.py`; `services/control-plane/bff/agora/strategy_workshop/router.py` |
| Implemented route home | Workshop-adjacent routes are still implemented in `services/control-plane/bff/main.py`, not in the package sub-router. | `services/control-plane/bff/main.py` |
| OpenAPI route catalog | Agora v1 OpenAPI lists the `agora-workshop` route family for evaluation runs/suites, training examples, skill coaching, committee sessions, memos, evidence packs, and persona-lab submit-commit. | `services/control-plane/openapi/agora_v1.openapi.yaml` |
| Backend route snapshot | Backend route manifest includes the same workshop route family, including `GET /bff/agora/committee-sessions`, `GET|POST /bff/agora/committee/sessions`, memo routes, evidence-pack routes, and persona-lab submit-commit. | `services/control-plane/bff/contract_snapshots/backend_routes_manifest.json` |
| Committee lifecycle | `GET|POST /bff/agora/committee/sessions`, detail, open, and close routes create/read/update committee-mode Agora sessions and publish ask-channel SSE events. | `services/control-plane/bff/main.py`; `services/control-plane/bff/test_ask_003_committee_lifecycle.py` |
| Committee memos | Memo list/create/detail/publish routes exist. Publish creates an Agora handoff to management review and emits SSE events. | `services/control-plane/bff/main.py`; `services/control-plane/bff/test_ask_004_memo_publish_contract.py` |
| Evidence packs | `POST /bff/agora/committee/{sessionId}/evidence-pack` and `/files` create evidence-pack metadata and append validated file metadata with idempotency and audit records. | `services/control-plane/bff/main.py`; `services/control-plane/bff/test_bff_agora_extended_contract.py` |
| Training examples | `GET|POST /bff/agora/training-examples` exists, supports idempotent writes, and supports dry-run behavior in the core Agora write path. | `services/control-plane/bff/main.py`; `services/control-plane/bff/test_bff_agora_core_contract.py` |
| Evaluation, skill coaching, persona lab | `GET /bff/agora/evaluation-runs`, `GET /bff/agora/evaluation-suites`, `GET /bff/agora/skill-coaching/sessions`, `GET /bff/agora/persona-lab/runs`, and persona-lab submit-commit are present. | `services/control-plane/bff/main.py`; `services/control-plane/bff/test_bff_agora_extended_contract.py`; `services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py` |

Important frontend-facing consequence: the BFF has route truth for several
workshop-adjacent operations, but the route family is not yet a single typed
`StrategyWorkshop` facade in execute-plans. A frontend slice should not infer a
generic `/bff/agora/workshops` API or synthesize workshop DTOs from local seed
data.

## BFF Query Gap Analysis

| Gap | Impact | Parent-owner decision needed |
|---|---|---|
| No generic workshop route | The schemas define `StrategyWorkshop`, but this checkout has no explicit `GET|POST /bff/agora/workshops` route. The live route family is committee/training/evaluation/persona-lab oriented. | Decide whether AG-BE-SW-001 consumes the existing route family as-is or introduces a generic workshop facade in a later canonical task. |
| Package router migration incomplete | `agora/strategy_workshop/router.py` is a placeholder while runtime routes live in `main.py`. | Decide whether AG-BE-SW-001 is only support/handoff work or whether a future parent implementation migrates routes into the package router. |
| Broad OpenAPI object shapes | Several OpenAPI responses and write bodies are `type: object`, not bound to `StrategyWorkshop`, `StrategyCompleteness`, memo, evidence-pack, or persona-lab DTO schemas. | Before typed frontend generation, bind response/request schemas or publish a frontend DTO adapter contract that names required fields. |
| Alias selection | Route truth contains overlapping session aliases: `/bff/agora/sessions`, `/bff/agora/committee-sessions`, and `/bff/agora/committee/sessions`. | Pick the canonical execute-plans path helpers for list/detail/create flows and document which aliases remain compatibility-only. |
| Evidence upload shape mismatch | OpenAPI declares `/evidence-pack/files` as `multipart/form-data`, while the current BFF implementation accepts JSON file metadata and validates ids, MIME, size, checksum, and count. | Decide whether frontend uploads binary files through a separate storage flow then sends metadata, or whether the BFF route/OpenAPI should be reconciled. |
| Persona-lab path/status mismatch | OpenAPI names `{run_id}` and returns `200`; the BFF and execute-plans route snapshot use `{draftId}` and the BFF returns `202` for submit-commit. | Freeze the canonical parameter name and response status before FE writes a strict adapter. |
| Research handoff gap | `ResearchPlan` and `ResearchRunSummary` schemas exist, and `GET /bff/agora/research-tasks` exists under `agora.research.v1`, but there is no workshop route that directly emits a `ResearchPlan`. | If AG-BE-SW-001 is meant to cover workshop-to-research-plan generation, define whether that is a new BFF write, an assistant route, or a downstream research service handoff. |
| Frontend live adapter absence | execute-plans path helpers and `bffAgora` adapter cover signals, inbox, journal, postmortems, and ask sessions, but not the workshop route family. | Add route builders, live adapters, DTO adapters, and strict-mode tests before enabling production workshop UI writes. |
| CTA authority ambiguity | Some write routes currently require read/operator role checks but do not expose a per-object `allowedActions` block for frontend CTA authority. | Decide whether FE gates these actions by route capability, global role, object state, or a new backend-shaped action authority field. |

## Operator Journey

### Current safe journey

```text
Operator opens Agora workshop-related UI
  -> frontend may use existing live-backed BFF areas only where adapters exist
  -> for workshop pages, current execute-plans code primarily renders local seed
     state or generic management approval actions
  -> direct BFF consumers can read committee sessions, evaluation lists,
     skill-coaching sessions, persona-lab runs, and training examples
  -> write routes must carry idempotency headers where supported
  -> strict live frontend mode must not silently fall back to local seed data
     for committee, training, evaluation, skill coaching, or persona-lab state
```

### Proposed journey after parent unblocks frontend contract work

```text
Operator opens Agora Committee / Strategy Workshop
  -> frontend resolves Agora capability/scope and confirms agora.workshop.v1
  -> frontend calls the chosen list route for committee/workshop sessions
  -> operator creates or opens a committee-mode session
  -> BFF derives actor identity from auth headers and persists session state
  -> operator attaches evidence-pack metadata to the session
  -> personas/operators add memo drafts
  -> memo publish creates the management-review handoff and emits ask-channel SSE
  -> operator can view linked training/evaluation/persona-lab surfaces
  -> persona-lab submit-commit creates a management handoff instead of directly
     mutating persona registry or runtime binding
```

### Failure and degraded journey

```text
401/403 from any workshop route
  -> render auth or scope error; do not show write CTAs

404 from committee detail/memo routes
  -> render missing session/memo state; do not reconstruct from local seed data

409 from memo creation
  -> show duplicate memo-id/idempotency guidance and retry with a new memo id
     or original Idempotency-Key

Evidence file validation failure
  -> show BFF validation details; do not upload unsupported MIME, oversize,
     duplicate, or checksumless files

Missing adapter or route-schema ambiguity in strict live mode
  -> emit a BFF gap handoff; do not enable success-path write UI
```

## Frontend Handoff Notes

Current execute-plans facts checked in `/home/lupin/code/execute-plans`:

| Area | Current frontend state | Handoff note |
|---|---|---|
| Path helpers | `src/lib/bff-v1/paths.ts` has Agora path builders for signals, inbox, journal, postmortems, and ask sessions only. | Add builders for committee sessions, committee memos, evidence packs, training examples, evaluation runs/suites, skill coaching sessions, persona-lab runs, and persona-lab submit-commit after the parent picks canonical aliases. |
| Agora live adapter | `src/lib/bff/agora.ts` adapts daily, signals, inbox, journal, and ask sessions. | Add `workshop` or `committee` adapter methods with strict live error propagation and no local seed fallback in strict mode. |
| Committee UI | `src/agora/pages/CommitteeRoom.tsx` uses local `seed` session state, `bff.personas.list()`, `useHandoff()`, and local evidence validation constants. | Replace seed state with BFF list/detail/create/open/close/memo/evidence-pack adapters. Keep local evidence validation as client preflight only; BFF remains authoritative. |
| Trainer Studio | `src/agora/pages/TrainerStudio.tsx` uses seed feedback, `bff.personas.list()`, and `mutations.createApproval()` for persona-update approvals. | Do not treat this as live `GET|POST /bff/agora/training-examples` coverage. Add a training-example adapter before claiming live workshop training flows. |
| Evaluation Suites | `src/agora/pages/EvaluationSuites.tsx` uses local seed suites and a timer-based mock rerun. | Add `GET /bff/agora/evaluation-suites` and `GET /bff/agora/evaluation-runs` adapters; no local rerun success in strict live mode. |
| Persona Lab | `src/agora/pages/PersonaLab.tsx` uses live-ish `bff.skills.list()` and `bff.tools.list()`, but draft save/test/submit are local toasts. | Add `GET /bff/agora/persona-lab/runs` and `POST /bff/agora/persona-lab/{draftId}/actions/submit-commit` integration only after parameter/status truth is frozen. |
| Skill Coaching | `src/agora/pages/SkillCoaching.tsx` uses local skill drafts and management approval mutation. | Add `GET /bff/agora/skill-coaching/sessions` adapter if this page should reflect live coaching sessions. Keep approval mutation separate from workshop training-example writes. |
| Tests | Existing execute-plans live adapter tests cover current BFF adapters, not the workshop route family. | Add path-builder, DTO-adapter, strict fallback, write idempotency, and no-seed-regression tests for the selected routes. |

Recommended frontend adapter shape once the parent freezes aliases:

```ts
type AgoraWorkshopClient = {
  committeeSessions: {
    list(): Promise<CommitteeSession[]>;
    create(input: CreateCommitteeSessionInput, idempotencyKey: string): Promise<CommitteeSession>;
    get(sessionId: string): Promise<CommitteeSession>;
    open(sessionId: string, idempotencyKey: string): Promise<CommitteeSession>;
    close(sessionId: string, input: CloseCommitteeSessionInput, idempotencyKey: string): Promise<CommitteeSession>;
    memos(sessionId: string): Promise<CommitteeMemo[]>;
    createMemo(sessionId: string, input: CreateMemoInput, idempotencyKey: string): Promise<CommitteeMemo>;
    publishMemo(sessionId: string, memoId: string, idempotencyKey: string): Promise<CommitteeMemo>;
  };
  trainingExamples: {
    list(): Promise<TrainingExample[]>;
    create(input: TrainingExampleInput, idempotencyKey: string): Promise<TrainingExample>;
  };
  evaluations: {
    suites(): Promise<EvaluationSuite[]>;
    runs(): Promise<EvaluationRun[]>;
  };
  personaLab: {
    runs(): Promise<PersonaLabRun[]>;
    submitCommit(draftId: string, input: PersonaLabCommitInput, idempotencyKey: string): Promise<HandoffResult>;
  };
};
```

The frontend should treat every route as observation, workshop, training, or
management-review handoff only. No Agora workshop UI should route broker orders,
create runtime bindings, mutate capital bindings, or promote artifacts directly.

## Parent Absorption Checklist

Codex should use this packet as support material and decide which items are
absorbed into the parent AG-BE-SW-001 lane:

| Check | Expected parent outcome |
|---|---|
| Route family decision | Freeze whether FE should call existing committee/training/evaluation/persona-lab routes or wait for a generic `StrategyWorkshop` facade. |
| Alias policy | Mark one set of paths canonical for execute-plans and leave the others as compatibility aliases. |
| DTO binding | Bind broad OpenAPI object bodies to named schemas or publish frontend adapter DTO requirements. |
| Evidence upload truth | Reconcile JSON metadata upload behavior with OpenAPI multipart wording before real file upload UI work. |
| Persona-lab truth | Reconcile `{draftId}` vs `{run_id}` and `202` vs `200` response status. |
| CTA authority | Decide route/capability/object-state authority for create/open/close/memo/publish/evidence-pack CTAs. |
| Strict frontend plan | Add route builders, adapters, tests, and strict-mode no-seed behavior before exposing these flows as live. |
| Safety boundary | Preserve "handoff/review only" semantics; no route may grant live trading, runtime binding, or capital authority. |

## Verification Notes

Suggested reviewer checks for this sidecar:

```bash
git diff --check -- support/sidecars/AG-BE-SW-001/AG-BE-SW-001-SIDECAR-BFF-HANDOFF.md
python3 -m pytest services/control-plane/bff/test_bff_agora_extended_contract.py services/control-plane/bff/test_ask_003_committee_lifecycle.py services/control-plane/bff/test_ask_004_memo_publish_contract.py services/control-plane/bff/tests/test_agora_router.py -q
```

Expected scope check:

- Only this sidecar support artifact is authored by the task.
- No L1 canonical docs, OpenAPI, BFF runtime implementation, route registry,
  governance code, registry code, or execute-plans files are changed.
- The packet does not claim AG-BE-SW-001 is complete without parent decisions
  on aliasing, DTO binding, evidence upload shape, and frontend live adapters.

## Handoff

This packet is ready for Codex review. It should be used as support material for
the parent owner to decide whether AG-BE-SW-001 consumes the current
workshop-adjacent BFF route family or scopes a later canonical `StrategyWorkshop`
facade and execute-plans adapter implementation.
