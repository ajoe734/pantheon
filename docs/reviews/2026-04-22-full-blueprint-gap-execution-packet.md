# 2026-04-22 Full Blueprint Gap Execution Packet

Status: execution packet synced to current repo truth
Source: repo audit against canonical L1/L2 docs, implementation coverage, and representative tests
Prepared by: Codex

## Purpose

This packet records the confirmed "full blueprint" residual gap and keeps the supervisor-runnable execution task map intact.

Items that were already closed after the initial audit remain in the task table for traceability, but the gap classes below describe only the gaps that still remain today.

It separates the remaining follow-up into the classes below:

1. real implementation gaps that still need code
2. truth-hardening work where production paths still depend on local snapshot fallback
3. canonical progress/doc drift where planning or overview surfaces no longer match repo reality
4. repo-local runtime/control-plane closeout
5. execution-proof follow-on

## Confirmed Gap Classes

### A. Real implementation gaps

No APP-003 route-family implementation gap remains in the current worktree.

`CW-02`, `CW-04`, `KW-05`, and `TW-02` were implemented after the initial audit and are now route-live in the current BFF. Their task rows remain below for execution traceability because owner/reviewer lifecycle truth still lives in `ai-status.json` until review closes.

### B. Truth-hardening gaps

- `RW-01` Research Ticket still permits local snapshot fallback in the production path
- `RW-03` Analyze still permits local snapshot fallback in the production path
- `KW-01` Institutional Memory still permits local snapshot fallback in the production path

### C. Progress/document drift

- research/OSS maturity documents disagree on current `Qlib` / `TRL` / `OpenClaw` status

### D. Repo-local runtime/control-plane closeout

- `services/control-plane/persona/main.py` still serves a TODO/stub runtime path
- `services/control-plane/router/main.py` still relies on local classify/permission scaffolding as the authoritative path
- `services/channels/web/main.py` still exposes placeholder SSE output

### E. Execution-proof follow-on

- `EP4` is present and stable
- `EP5-001` remains executable follow-on preparation work
- `EP5-002` remains explicitly deferred until a later human gate

## Materialized Execution Tasks

Traceability note: `APP-003-CW02-IMPL-001` and `APP-003-KW05-IMPL-001` remain listed here because this packet is their execution-origin record, even though both route families are now live in the repo. Current task ownership and lifecycle truth lives in `ai-status.json`.

| Task ID | Owner | Reviewer | Depends On | Scope |
|---|---|---|---|---|
| `APP-003-TRUTH-SYNC-001` | Codex | Codex2 | - | Rebaseline canonical workbench/progress truth so backlog, overview payloads, and packet docs match the current repo implementation state. |
| `APP-003-RW01-HARDEN-001` | Codex | Qwen | - | Remove `RW-01` production dependence on local snapshot fallback and keep service-backed truth authoritative. |
| `APP-003-RW03-HARDEN-001` | Qwen | Codex | `APP-003-RW01-HARDEN-001` | Remove `RW-03` production dependence on local snapshot fallback and keep service-backed analysis truth authoritative. |
| `APP-003-KW01-HARDEN-001` | Qwen | Codex2 | - | Remove `KW-01` production dependence on local snapshot fallback and keep memory-service truth authoritative. |
| `APP-003-CW02-IMPL-001` | Claude | Codex | - | Implement the ratified Debate Transcript BFF route family and ordered transcript semantics. |
| `APP-003-CW04-IMPL-001` | Qwen | Claude | `APP-003-CW02-IMPL-001` | Implement the ratified Red-team Memo BFF route family and governance handoff semantics. |
| `APP-003-TW02-IMPL-001` | Codex | Codex2 | - | Implement the ratified Trainer Parameter Controls BFF route family and patch validation semantics. |
| `APP-003-KW05-IMPL-001` | Codex2 | Codex | - | Implement the ratified Strategy Spec browse/detail/history/compare BFF route family. |
| `OSS-003-DOC-SYNC-001` | Codex | Claude2 | - | Reconcile `Qlib` / `TRL` / `OpenClaw` maturity docs with the current smoke-tested and governed repo evidence. |
| `PER-001-RUNTIME-INTEGRATION-001` | Claude | Gemini | - | Replace repo-local persona/router/web placeholder runtime behavior with the governed OpenClaw-backed runtime path or an explicitly truthful degraded surrogate. |
| `EP5-001` | Gemini | Codex | - | Prepare the canary-ready execution path: broker/venue config boundary, scaled capital gate, operator checklist, and rollback drill harness. |

## Deferred But Not Materialized

| Task ID | Reason |
|---|---|
| `EP5-002` | Explicitly human-gated in the canonical execution-proof ladder; do not dispatch until `EP5-001` is complete and the later gate is approved. |

## Expected Outcome

After the remaining tasks from this packet are executed:

- the remaining APP-003 backlog will represent real unfinished modules instead of stale planning text
- the remaining BFF implementation hole list will stop overstating already-landed route families as still-open code gaps
- the remaining hardening work will move production paths away from local fallback defaults
- supervisor-visible task board truth will once again match the actual repo state
