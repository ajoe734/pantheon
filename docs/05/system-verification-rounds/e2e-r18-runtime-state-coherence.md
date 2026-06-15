# E2E-R18 — Runtime-surface field coherence

**Round:** E2E-R18 (second campaign)
**Date:** 2026-06-15
**Branch / PR:** task/e2e-r18-runtime-state-coherence
**Business flow:** the operator console reads runtime state through both
`/bff/runtimes` and `/api/v1/operator/runtime-state`; per-runtime fields must
agree.

## Verification program

`scripts/verify_e2e_runtime_state_coherence.py` (+ unit test). Deepens E2E-R8
(id-set consistency) to FIELD level: for each runtime in both surfaces, asserts
deployment stage, status, and binding id agree.

## Live result (dev, 2026-06-15)

```
runtime-surface field coherence over 16 common runtimes:
  field mismatches (stage/status/binding): 0
OK: runtime stage/status/binding agree across both surfaces
```

## Finding

Good-news round: the two runtime surfaces are field-level coherent — every
runtime reports the same deployment stage (paper), status (active), and binding
id on both `/bff/runtimes` and `/api/v1/operator/runtime-state`. (Contrast with
E2E-R8, where the *persona-health* surface diverged from the active fleet; the
core runtime surfaces themselves agree.)

## Disposition

- **Shipped (code/CI):** the field-coherence verifier + logic test — a regression
  gate that fails if the two runtime surfaces ever disagree on stage/status/binding.
- CI wiring consolidated in E2E-R20.

## Next round

E2E-R19: a final distinct verifier, then E2E-R20 consolidation.
