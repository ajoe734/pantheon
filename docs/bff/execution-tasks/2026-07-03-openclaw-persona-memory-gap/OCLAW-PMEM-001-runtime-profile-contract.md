# OCLAW-PMEM-001 - Persona Runtime Profile And Model Routing Contract

Owner: Claude
Reviewer: Codex
Parent: `OCLAW-PMEM-000`
Depends on: none

## Problem

Persona identity, route policy, OpenClaw agent model selection, and provider pool
auth are currently joined by convention. `preferred_model` can appear in loose
persona records, but the Persona Registry and BFF route policy surfaces do not
define a canonical runtime/model routing contract.

## Scope

- Define a `PersonaRuntimeProfile` contract with `persona_id`,
  `workspace_ref`, `model_routing`, `memory_policy`, `sync_generation`, and
  `source_refs`.
- Define model routing modes: `pool_default`, `preferred_pool_model`,
  `hard_pin`, and ordered fallback.
- Ensure model routing references provider/model pool IDs, never auth secrets.
- Extend BFF/persona read surfaces so operators can inspect the resolved runtime
  profile and source refs.
- Add contract tests for default, preferred, hard-pin, fallback, and invalid
  provider fail-closed behavior.

## Acceptance

- A documented JSON/schema or typed contract exists for `PersonaRuntimeProfile`.
- BFF exposes a read-only persona runtime profile surface.
- Existing route policy behavior remains compatible; unknown provider/model
  references fail closed with operator-visible reason.
- Tests prove personas are many-to-few over a shared provider pool, not
  one-provider-auth-per-persona.
