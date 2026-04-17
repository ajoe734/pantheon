# Review: OSS-NEXT-005 — vectorbt Task Materialization

Reviewer: Claude
Date: 2026-04-17
Status: APPROVED

## Scope

This review covers the vectorbt materialization artifacts produced by Codex for OSS-NEXT-005.

## Artifacts Reviewed

- `OSS_INTEGRATION_CHECKLIST.md` — vectorbt row (line 43)
- `RESEARCH_BACKEND_MATURITY_MATRIX.md` — vectorbt entry
- `services/research/vectorbt/ACTIVATION_CRITERIA.md`
- `services/research/vectorbt/requirements.txt`
- `integrations/vectorbt/integration.md`

## Acceptance Criteria Verification

| Criterion | Status | Evidence |
|---|---|---|
| Source selected | ✅ | `polakowo/vectorbt` PyPI OSS edition; Apache 2.0; commercial fork explicitly rejected |
| Version pinned | ✅ | `vectorbt==0.26.2` in `services/research/vectorbt/requirements.txt` |
| Governed adapter boundary defined | ✅ | `GovernedVectorbtInputAdapter`, `StubVectorbtBackend`, `VectorbtBackend`, `run_vectorbt_workflow()` fully specified in `integrations/vectorbt/integration.md §3` and `ACTIVATION_CRITERIA.md §Gate 1` |
| Smoke-test plan documented | ✅ | 6-step smoke-test plan in `ACTIVATION_CRITERIA.md §Smoke-Test Plan` and mirrored in `integration.md §5` |
| Checklist row present | ✅ | `OSS_INTEGRATION_CHECKLIST.md` has the vectorbt row with `version-pinned` status, scope, activation owner (Codex), and follow-on work wording |
| Backend no longer only a maturity-matrix mention | ✅ | `ACTIVATION_CRITERIA.md`, `requirements.txt`, and `integration.md` constitute a real task cut |

## Quality Notes

- Output contract JSON (artifact_bundle + registry_entry) is concrete and consistent with Qlib/TRL patterns.
- Governance boundary invariants are explicit: no SignalStore writes, no LEAN influence, draft-state-only registry entry.
- Stub/real backend split follows the established OSS pattern (`PANTHEON_VECTORBT_BACKEND=real` flag).
- Activation owner and follow-on implementation ownership distinction is clear.
- `RESEARCH_BACKEND_MATURITY_MATRIX.md` and `OSS_INTEGRATION_CHECKLIST.md` are internally consistent on status, owner, and missing-proof wording.

## Findings

No blocking issues. The materialization baseline is complete and well-scoped. Follow-on work (adapter implementation, smoke test, governance evidence) is clearly gated and documented in ACTIVATION_CRITERIA.md §Gate 1 and §Gate 2.

## Decision

**APPROVED** — task returns to Codex for finalization.
