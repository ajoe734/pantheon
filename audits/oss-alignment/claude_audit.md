# Claude OSS Alignment Audit — AUD-CLAUDE-001

**Auditor:** Claude  
**Date:** 2026-04-02  
**Scope:** P3-001, P4-001, EX-001, OC-001, OC-003  
**Reference model:** OSS_INTEGRATION_CHECKLIST.md, WORK_REBASELINE.md

---

## Summary

| Task | Classification | Verdict |
|---|---|---|
| P3-001 | Valid local adapter design | Not LEAN-integrated: no dep pinned, duck-typing only, no smoke test |
| P4-001 | Valid local contract (router is local-only) | Persona stub is TODO; if OpenClaw is upstream, `/invoke` target is wrong |
| EX-001 | **OVERSTATED** — marked `done` | Artifact loader files do not exist in repo; no Object Store integration |
| OC-001 | Valid local governance contract | Not upstream OpenClaw integration; no upstream mapping exists |
| OC-003 | Valid local schema contract | Not upstream OpenClaw mapping; no adapter from upstream outputs |

---

## Findings

### P3-001 — Wire LEAN runtime signal consumer

**What exists:**  
`signal_consumer.py`, `executor.py`, `symbol_parser.py` in `services/execution/lean-runtime/`.

**What is valid as-is:**  
- The adapter interface design is sound: drain/validate/dedup/stale-check/conflict-resolve/execute pipeline is correct.
- `executor.py` dispatch table (BUY/SELL/EXIT/HOLD × LONG/SHORT × quantity_type) correctly models LEAN's order API semantics.
- `signal_consumer.py` is deliberately decoupled from LEAN imports to allow unit testing — this design choice is documented and intentional.

**What is only a local contract, not real integration:**  
- `requirements.txt` contains only `jsonschema>=4.21.0`. No LEAN dependency is present.
- `symbol_parser.py` returns string literals (`"Market.USA"`, `"SecurityType.Equity"`) instead of actual `QuantConnect.Market` and `QuantConnect.SecurityType` enum values.
- `executor.py::_resolve_symbol()` calls `algo.Symbol(parsed.ticker)` — this is not a valid LEAN API. Real LEAN requires `Symbol.Create(ticker, SecurityType, Market)` with actual enum objects imported from `QuantConnect`.
- `to_lean_create_call()` in `symbol_parser.py` returns a Python source string suitable for logging only; it does not produce a callable LEAN symbol.
- No `lean.json` project config or QuantConnect project file exists.
- No smoke test that runs inside an actual LEAN runtime exists.

**Missing upstream integration steps:**  
1. LEAN runtime dependency path must be defined (e.g., `lean-cli` image or QuantConnect Python package import path).
2. `_resolve_symbol()` must be updated to call `Symbol.Create()` with actual LEAN enum values, or the bridge between string constants and LEAN enums must be documented as a required integration adapter.
3. A minimal LEAN algorithm smoke test (backtest) that exercises `SignalConsumer.drain()` must be created or explicitly planned.

---

### P4-001 — Draft control-plane routing contract

**What exists:**  
`services/control-plane/router/main.py`, `contract.md`.

**What is valid as-is:**  
- The router is a correctly designed local FastAPI service. Its permission logic (`_evaluate_permission`, `_CHANNEL_ROLE`, `_CHANNEL_TIER`) is purely local and does not depend on any upstream OSS.
- The deny-first permission check, intent classification, and rate-limit policy are all local — no upstream dependency required or implied.
- The contract is sound and was already reviewed and approved.

**What is only a local contract, not real integration:**  
- `PERSONA_URL = os.getenv("PERSONA_URL", "http://localhost:8002")` points to a local persona agent.
- `services/control-plane/persona/main.py` is an all-stub LangGraph service: every node (`intent_classify`, `skill_select`, `memory_lookup`, `respond`) returns hardcoded placeholder values. The `GRAPH` is `None` when LangGraph is not installed.
- The router's `POST /invoke` call reaches a persona agent that produces `"[system not ready — upstream schemas not locked]"` as its response.
- If "OpenClaw" refers to a real upstream repo/runtime, the persona agent is currently a local reimplementation surrogate, not an integration of it.

**Missing upstream integration steps:**  
1. Determine whether the local persona agent is intended as a permanent local surrogate for upstream OpenClaw or as a temporary stub.
2. If upstream OpenClaw is the intended runtime, define the integration adapter: what OpenClaw API/SDK does the router's `POST /invoke` actually call, and where is the upstream source?
3. If the local persona agent is permanent, formally designate it as "local surrogate — not upstream OpenClaw integration" in `contract.md` and `OSS_INTEGRATION_CHECKLIST.md`.

---

### EX-001 — Define artifact loader contract for paper and live execution

**What exists:**  
Task status in `ai-status.json` shows `done`. `current-work.md` shows "Contract locked and approved by Claude."

**What is actually in the repo:**  
`services/execution/artifact-loader/` **does not exist**. No contract file. No schema file. No loader code.

**Verdict: OVERSTATED.**  
EX-001 is marked `done` but there are no artifact files in the repository. This was previously flagged as a blocker by Claude in an earlier session. The task status must be corrected.

**What a real EX-001 integration requires:**  
- A contract document defining loader behavior for `paper` and `live` promotion states.
- An artifact metadata schema compatible with REG-001's Object Store projection fields.
- Actual LEAN Object Store integration: `ObjectStore.Save()` / `ObjectStore.Read()` are LEAN-native APIs that require the LEAN runtime dependency.
- Promotion-state enforcement: loader must reject `candidate`, `draft`, `retired` artifacts before reading artifact body.
- A smoke test that exercises at minimum a paper-mode artifact load from a mock Object Store.

**Required correction:**  
EX-001 status must be reset to `in_progress` or `blocked` and the missing files must be created before this task can be considered done.

---

### OC-001 — Permission model with allowlist and denylist

**What exists:**  
`services/control-plane/permissions/contract.md`, `tool_policy_schema.json`, `review_oc001_claude.md`.

**What is valid as-is:**  
- The local deny-first permission model is a correct and complete local governance contract.
- The 6 mandatory deny rules, approval hooks, and `tool_policy_schema.json` are sound local artifacts.
- The task was reviewed and approved as a local governance contract — that verdict stands.

**What is only a local contract, not upstream integration:**  
- The contract does not reference any upstream OpenClaw permission API, SDK method, or configuration format.
- `OSS_INTEGRATION_CHECKLIST.md` shows OpenClaw as `not-started`. There is no upstream OpenClaw repo selection, no version pinned, no adapter.
- The local permission model defines *our* policy semantics. It does not demonstrate that we have integrated *upstream OpenClaw's* permission/authorization mechanism.
- If upstream OpenClaw has its own tool-use control system, the local `tool_policy_schema.json` is not yet mapped to it.

**Missing upstream integration steps:**  
1. Select and pin upstream OpenClaw source.
2. Determine whether upstream OpenClaw has a tool-use permission model or authorization hook.
3. If it does: write an adapter that maps upstream OpenClaw permission semantics → local deny-first model, and document the mapping.
4. If it does not: formally document in `OSS_INTEGRATION_CHECKLIST.md` that OC-001 is a local governance layer *on top of* OpenClaw, not a mapping *from* it.

---

### OC-003 — StrategySpec and WorkflowHandoff objects

**What exists:**  
`services/control-plane/specs/contract.md`, `strategy_spec.schema.json`, `workflow_handoff.schema.json`, `review_oc003_claude.md`.

**What is valid as-is:**  
- The StrategySpec and WorkflowHandoff object models are correctly designed local schemas.
- Governance boundary, registry_hints, and governance_context are well-structured.
- The task was reviewed and approved as a local contract — that verdict stands.

**What is only a local contract, not upstream mapping:**  
- The schemas are standalone local definitions. They do not reference or derive from any upstream OpenClaw output format.
- `WORK_REBASELINE.md §3` explicitly classifies OC-003 as requiring upstream mapping: "map upstream OpenClaw outputs into local StrategySpec and workflow handoff objects."
- Currently there is no adapter that takes upstream OpenClaw strategy/workflow outputs and converts them to local StrategySpec objects.
- There is no documented field mapping from upstream OpenClaw → `strategy_spec.schema.json`.

**Missing upstream integration steps:**  
1. Select upstream OpenClaw source (same dependency as OC-001).
2. Inspect upstream OpenClaw's workflow output format (what does it produce after an orchestration run?).
3. Write an adapter: upstream OpenClaw output → local StrategySpec, documenting field-by-field mapping.
4. Document any fields in `strategy_spec.schema.json` that have no upstream equivalent (these are local extensions).

---

## Required Follow-up Tasks

The following new tasks or corrections are mandatory under the integration-first model:

| # | Task | Type | Blocks |
|---|---|---|---|
| 1 | Correct EX-001 status back to `in_progress` / `blocked`; create the missing artifact loader files | Correction | REG-002, FB-003 |
| 2 | P3-001: Define LEAN integration bridge — pin LEAN dependency, fix `_resolve_symbol()` to use real `Symbol.Create()`, write smoke test | Spike / integration task | P3-001 close-out |
| 3 | Select and pin upstream OpenClaw source | Upstream selection | OC-001, OC-002, OC-003 adapter work |
| 4 | OC-001: Write adapter from upstream OpenClaw permissions → local deny-first model (or formally document local-surrogate designation) | Adapter task | OC-001 final lock |
| 5 | OC-003: Write adapter from upstream OpenClaw outputs → local StrategySpec (or document extension fields) | Adapter task | RS-002, OC-003 final lock |
| 6 | P4-001: Decide persona agent fate — upstream OpenClaw runtime integration vs. permanent local surrogate — and document decision in contract.md | Decision / doc task | LP-001, OC-002 |

---

## Recommended Task/Status Corrections

| Task | Current Status | Recommended Correction |
|---|---|---|
| EX-001 | `done` | Reset to `in_progress` or `blocked` — no artifact files in repo |
| P3-001 | `review` | Add follow-up task for LEAN integration bridge before calling done |
| OC-001 | `done` | Valid as local contract; add note: "upstream OpenClaw integration pending OpenClaw source selection" |
| OC-003 | `done` | Valid as local contract; add note: "upstream adapter pending OpenClaw source selection" |
| P4-001 | `done` | Valid as local contract; add note: "persona agent is local stub — upstream OpenClaw integration decision deferred" |

---

## What Does Not Need to Change

- Signal schema (P2-001, P2-002): pure local contract, correct as-is.
- Router permission logic (P4-001 core): local deny-first model is correct and does not require upstream changes.
- OC-001 and OC-003 local schema validity: both schemas are internally consistent and well-designed as local contracts. The gap is not in their design, but in the absence of upstream adapter work.
- FB-001, FB-002: local governance contracts for feedback — valid as-is per WORK_REBASELINE.md §2.A/B.
