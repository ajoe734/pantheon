# OpenClaw Persona, Model Pool, And Memory Gap Spec - 2026-07-03

Status: ready for execution-task dispatch

Owner: Codex

## Operator Problem

The management surface exposed OpenClaw LLM auth state, persona runtime state,
and usage counters as if they were one coherent subsystem. They are not coherent
yet. The operator asked for a full architecture review because Pantheon already
has a first-class Memory Plane, while recent OpenClaw persona work introduced
workspace-local memory concepts (`SOUL.md`, `USER.md`, `MEMORY.md`, `memory/`)
without a clear source-of-truth boundary.

This document records the gap and opens concrete execution tasks. It is not an
implementation closeout.

## Evidence From Current Dev Branch

### Persona registry is identity and policy metadata, not runtime provider state

- `services/control-plane/persona/persona_registry.py` defines `Persona` as the
  governance truth for identity, mandate, lifecycle, workspace/tool/route policy
  refs, and metadata. It explicitly says runtime state lives in
  `SessionPersona` and `CapabilitySnapshot`.
- There is no first-class provider/model routing field in the dataclass.

### BFF persona create stores traits but does not reconcile OpenClaw agents

- `services/control-plane/bff/main.py` `POST /bff/personas` persists name,
  mandate, strategy family, and `traits`.
- The create path registers cron/OODA scaffolding, but does not call
  `sync_persona_agents` or provision/update an OpenClaw agent.
- Agora servant ensure is the exception: it calls the OpenClaw servant sync path,
  so the general persona path and the servant path diverge.

### OpenClaw persona model selection exists, but is not canonical or fully reconciled

- `integrations/openclaw/model-pool-and-persona-routing.md` states the correct
  mental model: models/auth are a small shared provider pool; personas are many
  lightweight identities that reference pooled model refs.
- `integrations/openclaw/persona_agent_sync.py` reads `preferred_model` or
  `model`, but only from loose persona records and only if the value appears in
  a hard-coded known-model set.
- Existing OpenClaw agents are refreshed with `openclaw agents set-identity`,
  but their model is not updated in the existing-agent path.
- `scripts/openclaw-sync-persona-agents.py` duplicates the SOUL renderer and is
  missing the Memory section present in `persona_agent_sync.py`, so deploy-time
  SOUL content can drift from tested library behavior.

### Pantheon already has a canonical Memory Plane

- `services/memory/MEMORY_LAYER_DESIGN_NOTE.md` defines `PersonaMemory` as
  private to a persona and `InstitutionalMemoryEntry` as shared/system scope.
- The note says no session writes memory directly; writes must go through an
  owning service with a lifecycle event.
- `services/memory/main.py` implements:
  - `POST /api/memory/persona-entries`
  - `POST /api/memory/writebacks/persona`
  - `GET /api/memory/retrieve`
- `services/persona/cognitive_loop_runtime.py` already demonstrates memory
  writeback and reuse affecting persona decisions.

### BFF persona memory surface is disconnected from canonical Memory Plane

- `GET /bff/personas/{persona_id}/memory` calls
  `read_store.list_memory_updates_for_persona`.
- Current source only references that method from BFF; no implementation was
  found under `services/control-plane/bff`.
- Therefore the management persona memory view can return empty even when
  canonical `PersonaMemoryStore` contains durable persona memory.

### OpenClaw workspace memory is not reconciled with canonical memory

- OpenClaw governance docs say workspace and `MEMORY.md` are preserved by the
  OpenClaw HTTP agent path.
- The repo has no bridge that retrieves canonical memory and materializes it
  into OpenClaw workspace context.
- The repo has no governed bridge that converts OpenClaw turn outcomes into
  canonical memory writeback candidates.

## Correct Source-Of-Truth Boundary

| Concern | Source of truth | Runtime/materialized view |
|---|---|---|
| Persona identity, mandate, traits, ownership | Persona Registry | OpenClaw `SOUL.md` |
| Persona capabilities and tool/workflow access | CapabilitySnapshot + route/tool/consult policies | BFF persona capability surfaces, OpenClaw tool availability |
| LLM/provider auth and quota | OpenClaw provider/model pool | Management LLM Auth panel |
| Persona-to-model decision | Persona runtime route policy / model routing profile | OpenClaw agent `model.primary` |
| Long-term persona memory | Canonical Memory Plane `PersonaMemory` | OpenClaw `MEMORY.md` / `memory/context.json` materialized cache |
| Shared lessons and institutional facts | Canonical Memory Plane `InstitutionalMemoryEntry` | Retrieved context snippets for session start/turns |
| Interactive/session state | SessionPersona / OODA runtime | OpenClaw run/session context |

OpenClaw workspace files are not allowed to become a second truth source. They
are a cache/materialized prompt context derived from canonical Persona + Memory
Plane state, with governed writeback back into the Memory Plane.

## Gaps

| Gap | Impact | Required fix |
|---|---|---|
| No canonical persona model routing profile | Persona routing is implicit and stale; UI cannot explain why a persona uses Claude/Codex/OpenClaw | Add a first-class runtime/model policy contract and expose it through BFF |
| General persona create does not reconcile OpenClaw agents | New personas may exist in BFF but not as `openclaw/{persona_id}` agents | Call the same OpenClaw sync path for general personas or a shared reconciler |
| Existing OpenClaw agents do not get model updates | Changing model policy can leave live agents on the old provider | Update existing-agent model or recreate safely with evidence |
| Deploy script and tested sync library duplicate SOUL rendering | Live SOUL can miss memory instructions and drift from tests | Share one renderer or add parity tests/gates |
| BFF persona memory reads a missing read-store method | Management UI can show no memory even when canonical memory exists | Wire BFF memory endpoint to Memory Plane retrieval facade |
| No canonical-memory-to-OpenClaw materialization | Persona agent prompts do not reliably include durable learned memory | Add a memory bridge that writes generated workspace context from Memory Plane |
| No governed OpenClaw-to-memory writeback | OpenClaw turns cannot safely teach canonical PersonaMemory | Create candidate writeback flow with service authority and audit |
| LLM Auth panel conflates provider auth, persona runtime, quota, and reauth UX | Operator cannot tell if Codex/Claude/OpenClaw are truly usable or just mounted | Separate provider pool health from persona routing; add real smoke and quota evidence |
| Dev gates do not prove the architecture end-to-end | Local UI state can pass while runtime is broken | Add live probes covering BFF, OpenClaw agent response, memory retrieval/materialization, and no memory leakage |

## Target Architecture

1. Introduce a `PersonaRuntimeProfile` contract.
   - Contains `persona_id`, `workspace_ref`, `model_routing`, `memory_policy`,
     `sync_generation`, and `source_refs`.
   - `model_routing` supports `pool_default`, `preferred_pool_model`,
     `hard_pin`, and ordered fallback.
   - It references provider/model pool entries; it does not store auth secrets.

2. Make OpenClaw persona sync consume the runtime profile.
   - New and existing agents reconcile identity, workspace, model, and SOUL.
   - Existing-agent model drift is detected and repaired or explicitly blocked.
   - Deploy script uses the same renderer/contract as the tested library.

3. Make canonical memory feed OpenClaw, not the reverse.
   - On persona sync or session start, retrieve `scope=both` memory for the
     persona and render a bounded, auditable workspace cache.
   - Materialized files include source memory IDs, written timestamps,
     relevance scores, and generation timestamp.
   - OpenClaw can propose memory writebacks, but canonical creation still goes
     through `persona-memory-svc`/BFF governed writeback flow.

4. Wire BFF and Management UI to these boundaries.
   - Persona detail shows runtime profile, model routing, provider pool health,
     memory summary, and last materialization.
   - LLM Auth panel shows provider auth status, reauth flow state, live smoke,
     usage/quota source, and which personas currently depend on each provider.
   - UI copy must not imply a provider is usable until BFF + OpenClaw live smoke
     and readiness probe agree.

5. Add release/live gates.
   - Gates must prove:
     - persona registry create/update creates or updates `openclaw/{persona_id}`;
     - model routing changes update the OpenClaw agent model;
     - canonical memory appears in the OpenClaw workspace materialization;
     - BFF persona memory returns canonical memory;
     - private persona memory cannot leak across personas;
     - provider auth "ready" includes a real provider smoke result.

## Execution Packet

Execution tasks are in:

- `docs/bff/execution-tasks/2026-07-03-openclaw-persona-memory-gap/INDEX.md`

Dispatch script:

- `scripts/dispatch_openclaw_persona_memory_gap_2026-07-03.py`

## Completion Definition

This gap is not closed until:

1. all `OCLAW-PMEM-*` child tasks are done or reviewer-approved superseded;
2. the final closeout includes PRs, merge SHAs, local validation, hosted dev
   evidence, and residual risks;
3. the Management UI can show both:
   - which provider/model pool each persona is using; and
   - which canonical memory entries were materialized into OpenClaw context;
4. dev live probes prove both Codex-backed and Claude-backed paths are either
   usable or accurately degraded with a concrete reason;
5. no private PersonaMemory leaks to another persona in tests or live smoke.
