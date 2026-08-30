# Current Operational GAP Disposition (2026-08-30)

## Overview

All 20 identified operational gaps (**OP-G01** through **OP-G20**) from the full product operation audit are mapped to definitive states based on current `origin/dev` code evidence, prior delivery proofs, and the target execution DAG. Original audit semantics are strictly preserved.

---

## GAP Disposition Matrix

| GAP ID | Severity | State | Primary Owner Task | Evidence / Resolution Rationale |
|---|---:|---|---|---|
| `OP-G01` | P0 | `active` | `OPGAP-BE-AGORA-ROUTER-20260830` | Agora research default adapter generates fake `real` candidate truth without backend execution. Decouple Agora routing, ensure default adapter returns simulation/unavailable unless real backend execution succeeds. |
| `OP-G02` | P0 | `active` | `OPGAP-BE-AGORA-ROUTER-20260830` | Agora PerformanceSuggestionProducer lacks production caller/wiring. Connect telemetry/paper outcome consumers to naturally trigger suggestion generation with durable readback. |
| `OP-G03` | P0 | `closed` | `None (Closed)` | Current source FE/BFF not deployed as atomic pair. Closed on 2026-08-30 by verified hosted pair deployment (`pairId=6899d0daadb3dea2dbc3ae93456cf5818675dbd9a5c4284f676b80b5ce59c1a1`, accepted at `2026-08-30T06:28:46Z`, FE `bd03c863e3c2c1c64b9b7797f27cefaf84df17c1` + BFF `e7f010dccee33185bc260d06048f09e6d2125f28`). |
| `OP-G04` | P0 | `active` | `OPGAP-DEPLOY-RELIABILITY-20260830` | Release workflow summary wraps failures or skipped critical steps as green/success. Enforce strict fail-closed release gates and explicit per-step proof. |
| `OP-G05` | P1 | `active` | `OPGAP-BE-BFF-CORE-20260830` | Auth readiness synchronously depends on OpenClaw provider network latency. Decouple session/tenant auth from provider probes and use degraded asynchronous cache. |
| `OP-G06` | P0 | `active` | `OPGAP-FE-MGMT-CRUD-POSTMORTEM-20260830` | Non-Persona generic CRUD in `createEntity.ts` uses writeOverlay or gets rejected in strict live. Wire canonical durable BFF endpoints for visible entities or disable unbacked CRUD actions. |
| `OP-G07` | P1 | `active` | `OPGAP-FE-BUNDLE-CLEANUP-20260830` | Production bundle imports writeOverlay which imports seed/mock files. Eliminate 37 residual mock/seed files and remove writeOverlay reachability from production bundle. |
| `OP-G08` | P1 | `active` | `OPGAP-BFF-MAIN-ASSEMBLY-20260830` | BFF composition cleanup incomplete (`main.py` monolithic 68k lines, 453 decorators). Extract all 441 decorators and handlers into 18 domain routers, reducing `main.py` to pure composition root. [Merged with Finding F21]. |
| `OP-G09` | P1 | `active` | `OPGAP-BE-AGORA-ROUTER-20260830` | Agora routers cross-import private stores/helpers across domain boundaries. Inject shared stores and helpers from composition root and eliminate cross-router private imports. |
| `OP-G10` | P2 | `active` | `OPGAP-BE-COMMAND-PLANE-RETIREMENT-20260830` | Generic legacy action adapter `_execute_bff_action_adapter` and dead command plane artifacts remain. Delete dead generic action adapter and unreferenced legacy files while retaining `command_executor.py` without reverse-main imports. [Merged with Finding F24]. |
| `OP-G11` | P0 | `verify` | `OPGAP-HOSTED-BACKEND-ACCEPTANCE-20260830` | 12-loop cross-loop deployed proof is opt-in via environment variables. Automate default execution of 12-loop cross-loop deployed proof in backend acceptance. |
| `OP-G12` | P1 | `verify` | `OPGAP-HOSTED-BACKEND-ACCEPTANCE-20260830` | Source Management lacks hosted effect proof (add-disabled, validate, canary, reconcile-only). Execute hosted canary journey and verify automatic return to reconcile-only mode. |
| `OP-G13` | P1 | `active` | `OPGAP-BE-BFF-CORE-20260830` | Synchronous FastAPI TestClient verification tool deadlocks on AnyIO event loop. Pin compatible async ASGI dependencies and migrate ASGI test suites to async transport with hard timeouts. |
| `OP-G14` | P1 | `in_progress` | `AGORA-AGC-14-HOSTED-DEMO-AUTHENTIC-V5-20260829` | Management and Agora authenticated hosted UI lacks direct verifiable evidence. Reuses active in_progress task `AGORA-AGC-14-HOSTED-DEMO-AUTHENTIC-V5-20260829` (Agora-only authentic hosted demo); no duplicate OPGAP FE acceptance task is materialized. |
| `OP-G15` | P1 | `active` | `OPGAP-FE-AGORA-WORKSHOP-20260830` | Research adapters default to stub/deferred in Compose while UI expects real candidates. Display explicit stub/deferred/real provenance in UI and gate candidate truth to non-stub outputs. |
| `OP-G16` | P0 | `active` | `OPGAP-DEPLOY-RELIABILITY-20260830` | Deployment lease and rollback share fragile remote GitHub API dependency. Implement bounded heartbeat retry/grace and allow rollback from local sealed authority. |
| `OP-G17` | P0 | `active` | `OPGAP-BE-RUNTIME-BINDING-20260830` | Registry -> Deployment -> RuntimeBinding executable loader/market projection is not naturally produced. Emit immutable loader projection and market policy from canonical Registry for Runtime Manager verification. |
| `OP-G18` | P1 | `active` | `OPGAP-BE-MANAGEMENT-ROUTER-20260830` | Management Postmortem lacks canonical read owner (derived from incident timeline strings). Provide canonical postmortem read model, list/detail API contracts, and durable ID readback. |
| `OP-G19` | P0 | `verify` | `OPGAP-HOSTED-DEV-PROMOTION-20260830` | Source-to-Agora Read Projection deploy gate verification failure on new receipt/run/source. Ensure Agora read projection binds latest receipt/run/source and verifies successfully during promotion. |
| `OP-G20` | P0 | `verify` | `OPGAP-HOSTED-DEV-PROMOTION-20260830` | Paper signal producer runtime health and full signal->order/fill/heartbeat lifecycle not closed in live promotion. Execute nonprod deployment with latest candidate, prove producer enters healthy, and complete signal->order/fill readback. |

---

## State Breakdown

### 1. Closed GAPs
- **OP-G03**: Closed by verified hosted pair deployment (`pairId=6899d0daadb3dea2dbc3ae93456cf5818675dbd9a5c4284f676b80b5ce59c1a1`, accepted at `2026-08-30T06:28:46Z`).

### 2. In Progress / Existing Reused GAPs
- **OP-G14**: Sole owner is active `in_progress` task `AGORA-AGC-14-HOSTED-DEMO-AUTHENTIC-V5-20260829` (Agora-only authentic hosted demo). Resumes and completes under its governed workflow; no duplicate OPGAP FE acceptance task is materialized.

### 3. Verify GAPs
- **OP-G11**: Verified in `OPGAP-HOSTED-BACKEND-ACCEPTANCE-20260830` via live 12-loop cross-plane deployed proof.
- **OP-G12**: Verified in `OPGAP-HOSTED-BACKEND-ACCEPTANCE-20260830` via hosted Source Management canary journey.
- **OP-G19**: Verified in `OPGAP-HOSTED-DEV-PROMOTION-20260830` via gate-before-switch Agora read projection binding verification.
- **OP-G20**: Verified in `OPGAP-HOSTED-DEV-PROMOTION-20260830` via live paper-signal-producer health and order/fill readback.

### 4. Active Implementation GAPs
- **OP-G01** (Agora research candidate truth): `OPGAP-BE-AGORA-ROUTER-20260830`
- **OP-G02** (Agora performance suggestion wiring): `OPGAP-BE-AGORA-ROUTER-20260830`
- **OP-G04** (Deployment reliability gates): `OPGAP-DEPLOY-RELIABILITY-20260830`
- **OP-G05** (BFF core auth & provider decoupling): `OPGAP-BE-BFF-CORE-20260830`
- **OP-G06** (Management generic CRUD durable owner): `OPGAP-FE-MGMT-CRUD-POSTMORTEM-20260830`
- **OP-G07** (Frontend production bundle cleanup): `OPGAP-FE-BUNDLE-CLEANUP-20260830`
- **OP-G08** (BFF main composition root assembly & route extraction): `OPGAP-BFF-MAIN-ASSEMBLY-20260830`
- **OP-G09** (Agora cross-router private import elimination): `OPGAP-BE-AGORA-ROUTER-20260830`
- **OP-G10** (Generic action adapter retirement & command cleanup): `OPGAP-BE-COMMAND-PLANE-RETIREMENT-20260830`
- **OP-G13** (Async ASGI TestClient dependency & deadlock fix): `OPGAP-BE-BFF-CORE-20260830`
- **OP-G15** (Agora workshop research provenance & candidate flow): `OPGAP-FE-AGORA-WORKSHOP-20260830`
- **OP-G16** (Deployment lease & sealed rollback resilience): `OPGAP-DEPLOY-RELIABILITY-20260830`
- **OP-G17** (Registry to RuntimeBinding executable projection): `OPGAP-BE-RUNTIME-BINDING-20260830`
- **OP-G18** (Management Postmortem canonical read model): `OPGAP-BE-MANAGEMENT-ROUTER-20260830`

---

## Finding Integrations & Scope Exclusions

### 1. Merged Findings
- **Finding F21** (Monolithic BFF composition root / `main.py` route sprawl): Formally merged into **OP-G08** (`OPGAP-BFF-MAIN-ASSEMBLY-20260830`). Remediated by decomposing `main.py` into 18 domain routers and pure composition root.
- **Finding F24** (Generic action adapter retirement / dead compatibility code): Formally merged into **OP-G10** (`OPGAP-BE-COMMAND-PLANE-RETIREMENT-20260830`). Remediated by deleting `_execute_bff_action_adapter` and unreferenced legacy files while retaining `command_executor.py`.

### 2. Unresolved Scope Exclusions
- **Finding F22** (Mobile security-program certification & hardening): Unresolved scope exclusion; mobile device testing and formal security program certifications are explicitly excluded from this desktop-first functional plan.
- **Finding F23** (Real live-capital broker execution & funded trading authority): Unresolved scope exclusion; real live capital and external broker execution remain strictly fail-closed; this plan operates exclusively within paper/non-capital scope.
- **Finding F25** (Speculative external framework expansions): Unresolved scope exclusion; speculative unadmitted OSS framework integrations remain outside the bounded 20-gap remediation catalog.
