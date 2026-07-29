# L12-FE-TRUTH-001 — Render authoritative loop truth

Wave 3, lane `frontend-truth`, owner `Antigravity`, reviewer `Claude`; target `execute-plans:dev`, depends on `L12-TRUTH-001` and external overlap owner `PPL-ALLOC-009`.

Outcome: the hosted operator view shows all twelve desired/controller/failure/actual/provenance states and visually distinguishes seed, registry-only, scheduled, stale, and live.

Scope is exclusively the separate `execute-plans` repository under its `src`, tests, and task evidence paths.

Acceptance: no false-green state; desktop/mobile, keyboard, accessibility and reduced-motion pass; strict live-BFF requests pass; exact FE/BFF identity is visible.

Proof: frontend contracts/components, 1440/390 screenshots, axe/focus evidence, strict network evidence, and deployment identities. The full machine contract is canonical in `tasks.json`.
