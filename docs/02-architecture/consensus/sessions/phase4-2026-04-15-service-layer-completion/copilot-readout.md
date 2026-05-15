# Copilot Readout — Replacement Coverage by Codex

> Agent of record: Copilot
> Replacement reviewer: Codex
> Reason: the Copilot auto lane has repeatedly quota-stalled in this repo, so Codex absorbs the acceptance-wording and external-dependency review for this session.

## Lane

- Agent: Codex (covering Copilot lane)
- Capability focus: Pressure-test acceptance wording, external dependency assumptions, and the boundary between compose-critical work and optional research/OSS follow-ons.

## Canonical Sources Read

- L0: `README.md`, `planning-session.json`
- L1: `OSS_INTEGRATION_CHECKLIST.md`, `OPENCLAW_RUNTIME_CONTRACT.md`, `Pantheon_單VM測試版_雙VM正式版_部署補充說明.md`
- L2: `starter-draft.md`, `phase2-phase6-gap-inventory.md`, `gemini-readout.md`, `codex-readout.md`

## Working Interpretation

- The first execution wave must stay compose-critical and test-stack honest. It should not quietly absorb phase 5 surface expansion or phase 6 "real OSS integration" claims just because those gaps appear in the cross-phase inventory.
- Acceptance wording has to distinguish three things that are currently easy to blur together:
  - core services that must boot in the default single-VM profile
  - optional research/worker profiles that may be present but do not gate compose smoke success
  - mocked or deferred external integrations, especially OpenClaw/LEAN-adjacent execution feedback paths
- Thin wrappers for future data/research services are only safe if the acceptance language says they are non-blocking for the core smoke path unless a real in-repo caller depends on them in this wave.

## Risks / Contradictions

- Risk 1: if `SVC-COMPOSE` says only "boots with the planned service profile," the session can over-claim completion while still hiding unresolved external dependencies behind optional workers or mocked paths.
- Risk 2: the residual-gap inventory spans phase 2 through phase 6, so reviewers may accidentally read optional OSS or research readiness items as phase 4 must-haves. The packet needs a harder scope boundary.
- Risk 3: `data-ingest`, `data-catalog`, or `feature` stub language can become misleading if it implies production-ready upstream dependency coverage instead of "runnable placeholder in a controlled single-VM test profile."

## Suggested Task Slice Additions

- `SVC-BASELINE` should explicitly declare which profiles are `core` versus `optional`, and which external credentials or adapters are intentionally mocked in the single-VM test stack.
- `SVC-COMPOSE` should require:
  - a named default profile that proves the control plane can boot without optional research adapters
  - smoke commands that do not require new external data sources beyond the repo's documented fallbacks
  - explicit notes for any optional workers or stubs that are included but not part of the phase 4 completion claim
- The consensus packet should state that phase 5 workbench growth and phase 6 true OSS adapter realization remain downstream of this service-layer wave.

## Citations

- [C1] `docs/02-architecture/consensus/sessions/phase4-2026-04-15-service-layer-completion/phase2-phase6-gap-inventory.md`: the remaining work across phases 2-6 includes packet coverage and true OSS integration, but those are not all compose-critical for phase 4.
- [C2] `OSS_INTEGRATION_CHECKLIST.md`: several adapters are still short of real integrated operation, so phase 4 acceptance must not imply those systems are production-realized.
- [C3] `OPENCLAW_RUNTIME_CONTRACT.md`: OpenClaw remains a contract dependency rather than a fully closed integration path, which argues for explicit mock/deferred wording in this wave.
- [C4] `Pantheon_單VM測試版_雙VM正式版_部署補充說明.md`: the single-VM environment is a constrained test target, so optional workers and external dependencies need stricter scoping and profile boundaries.
- [C5] `docs/02-architecture/consensus/sessions/phase4-2026-04-15-service-layer-completion/gemini-readout.md`: Gemini already highlights replay/resource concerns, which reinforces the need to keep the default smoke path smaller than the full optional research stack.
