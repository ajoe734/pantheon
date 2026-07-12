# OCLAW-PMEM-004 - BFF And Management Runtime Surfaces

Owner: Codex
Reviewer: Claude
Parent: `OCLAW-PMEM-000`
Depends on: `OCLAW-PMEM-002`, `OCLAW-PMEM-003`

## Problem

Management UI currently exposes provider/auth, persona runtime, quota/usage, and
reauth state without enough proof that the provider is actually usable by
OpenClaw or that persona memory is canonical. BFF persona memory also reads from
a missing read-store method instead of the Memory Plane.

## Scope

- Wire `GET /bff/personas/{persona_id}/memory` to canonical Memory Plane
  retrieval with operator-safe summaries and traceability.
- Add/extend BFF surfaces for persona runtime profile, provider pool readiness,
  personas depending on each provider, last live smoke, usage/quota source, and
  last memory materialization.
- Separate provider auth/re-auth state from persona model routing state in DTOs.
- Ensure Claude/Codex reauth flows expose required code-entry state, completion
  state, readiness recheck, and degraded reasons.
- Update Management UI to show the runtime profile and memory source-of-truth
  boundaries without implying readiness from mount state alone.

## Acceptance

- Persona detail memory comes from canonical Memory Plane or reports a precise
  unavailable reason.
- LLM Auth panel distinguishes provider auth, provider live smoke, quota source,
  persona dependency count, and reauth flow status.
- Reauth code-entry UX appears when provider flow requires a code and rechecks
  readiness after completion.
- Tests cover BFF DTOs, degraded Memory Plane, failed provider smoke, and UI
  rendering for codex/claude/openclaw states.

## Delivered BFF surfaces

- `GET /bff/personas/{persona_id}/memory` retrieves through the canonical
  Memory Plane `/api/memory/retrieve` contract. It does not fall back to BFF
  snapshots or workspace files and returns an operator-safe `memory_source`
  reason when the Memory Plane is unconfigured, denied, unavailable, or
  returns an invalid response.
- `GET /bff/assistant/providers/usage-summary` separates provider auth,
  provider live-smoke proof, provider-reported quota source, BFF-observed
  usage, persona dependencies, and reauth state. A missing dependency inventory
  reports `persona_dependency_inventory_unavailable`; readiness explicitly
  records that mount readiness is not sufficient provider proof.
- Persona runtime profiles expose runtime routing and memory materialization
  metadata independently of provider authentication state.

The Management LLM Auth panel in `ajoe734/execute-plans` consumes these truth
objects and renders provider auth, live smoke, readiness, dependency count,
quota source, and reauth state independently. The frontend change is delivered
from a clean task worktree based on the remote branch containing the current
Management UI; frontend source is not copied into Pantheon.

## Verification evidence

Run on 2026-07-12 from `task/OCLAW-PMEM-004`:

```text
pytest -q services/control-plane/bff/tests/test_bff_b2_list_detail_facade.py \
  services/control-plane/bff/tests/test_management_nl_assistant_provider.py -q
```

Result: all selected tests passed. The only output was the existing FastAPI
`on_event` deprecation warning.

Frontend verification in the `execute-plans` task worktree:

```text
npm test -- --run src/management/components/openclaw/OpenClawLlmAuthPanel.test.tsx \
  src/lib/bff-v1/__tests__/managementAi.test.ts
npm run build
```

Result: 36 focused tests passed and the production build completed. Existing
Rollup circular-chunk, CSS minification, and bundle-size warnings remain.
