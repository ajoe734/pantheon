# OCLAW-PMEM-004 - BFF And Management Runtime Surfaces

Owner: Claude2
Reviewer: Codex
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
