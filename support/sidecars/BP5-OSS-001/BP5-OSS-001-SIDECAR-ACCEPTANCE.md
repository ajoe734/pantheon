# BP5-OSS-001 — Acceptance Packet

**Sidecar task:** BP5-OSS-001-SIDECAR-ACCEPTANCE  
**Parent task:** BP5-OSS-001 — "Pin the OpenClaw source and governed adapter boundary"  
**Parent owner:** Codex  
**Parent reviewer:** Claude  
**Parent status:** `review_approved` (as of 2026-04-15)  
**Packet author:** Claude (helper-claimed sidecar)  
**Packet reviewer:** Codex  
**Created:** 2026-04-15  
**Purpose:** Support artifact only. Does not modify canonical truth. Provides Codex with a structured acceptance checklist, dependency map, and finalization briefing to close BP5-OSS-001 as `done`.

---

## 1. Acceptance Criteria Verification

The two formal acceptance criteria from `ai-status.json`:

| Criterion | Status | Evidence |
|---|---|---|
| OpenClaw source and version pin are explicit and tied to one governed adapter boundary | **PASSED** | `integrations/openclaw/integration.md` §1 (tag `v2026.4.7`, commit `5050017543011b61df67744ebc6368d889c25a95`, image + digest locked); adapter seam documented in `integrations/openclaw/adapter/README.md` |
| The integration baseline includes a smoke-test plan that the repo can actually execute | **PASSED** | `scripts/openclaw-smoke-test.sh` — 6/6 checks passed; `integrations/openclaw/smoke_test.md` documents all steps and prerequisites |

Both criteria confirmed by Claude's review (see `integrations/openclaw/review_bp5_oss_001_claude.md`, verdict: APPROVED) and by Codex's prior approval of the Qwen-revised artifacts (see `integrations/openclaw/review_oss001_codex_approved_zh.md`).

---

## 2. Full Artifact Checklist

All deliverables listed in the task and confirmed present in the repo:

| Artifact | Present | Notes |
|---|---|---|
| `integrations/openclaw/integration.md` | ✓ | Pin, soak rationale, adapter boundary, facade clarification, out-of-scope items all documented |
| `integrations/openclaw/governance.md` | ✓ | OC-001/002/003 alignment, deny-first model, kill-switch independence, upgrade governance |
| `integrations/openclaw/smoke_test.md` | ✓ | Prerequisites, canonical command, step-by-step verification, expected outputs |
| `integrations/openclaw/evidence_pack.md` | ✓ | Consolidates all pin evidence and maps to smoke script steps |
| `integrations/openclaw/adapter/README.md` | ✓ | Adapter implementation home, non-goals, BP5-OSS-001-verified locked inputs |
| `integrations/openclaw/fixtures/raw_research_handoff.minimal.json` | ✓ | Smoke test fixture |
| `scripts/openclaw-smoke-test.sh` | ✓ | Executable baseline; `set -euo pipefail`; 6 checks; pass/fail counters |
| `services/control-plane/specs/normalize_handoff.py` | ✓ | jsonschema Draft7Validator for both StrategySpec and WorkflowHandoff |
| `OSS_INTEGRATION_CHECKLIST.md` | ✓ | OpenClaw row promoted to `governed` with accurate done/deferred split |
| `OPENCLAW_RUNTIME_CONTRACT.md` | ✓ | Runtime boundary document (pre-existing canonical; not modified by this task) |

---

## 3. Dependency Map

### 3a. What BP5-OSS-001 Depends On

| Dependency | Status | Notes |
|---|---|---|
| `OPENCLAW_RUNTIME_CONTRACT.md` (canonical L1) | Exists; not modified | Provides OC-001/002/003 definitions referenced by `governance.md` |
| `services/control-plane/specs/strategy_spec.schema.json` | Exists | Used by `normalize_handoff.py` for StrategySpec validation |
| `services/control-plane/specs/workflow_handoff.schema.json` | Exists | Used by `normalize_handoff.py` for WorkflowHandoff validation |
| `services/control-plane/specs/contract.md` | Exists | Referenced by smoke_test.md |

No unresolved upstream blockers. All dependencies were available at execution time.

### 3b. What Depends on BP5-OSS-001

| Downstream task | Depends on BP5-OSS-001 for | Notes |
|---|---|---|
| `BP5-OSS-002` — Realize the OpenClaw runtime adapter | A stable upstream pin and governed adapter boundary to implement against | Must not start until BP5-OSS-001 is `done` |

### 3c. Key Items Deferred to BP5-OSS-002

The following are explicitly out of scope for BP5-OSS-001 and must be tracked under BP5-OSS-002:

1. Selecting and implementing the final transport between Pantheon and the OpenClaw gateway
2. Bootstrapping a configured OpenClaw gateway with Pantheon-specific runtime settings
3. Invoking a real workflow through a live adapter path
4. Proving end-to-end job execution and raw output capture from a configured runtime
5. Generating unique `strategy_id` per handoff (current `normalize_handoff.py` uses a fixed `strat-{topic}` pattern — acceptable for smoke, must be addressed before production use)

---

## 4. Review Summary

**Claude's review** (`integrations/openclaw/review_bp5_oss_001_claude.md`):
- Verdict: APPROVED
- All 6 acceptance sub-criteria verified
- One non-blocking note for BP5-OSS-002: `normalize_handoff.py` needs a nonce on `strategy_id` to avoid registry collisions

**Codex's prior review** (`integrations/openclaw/review_oss001_codex_approved_zh.md`):
- Verdict: APPROVED
- Confirmed schema boundary alignment: `StrategySpec` fields vs. `WorkflowHandoff` envelope fields
- Confirmed fixture-driven normalization validates against canonical schemas
- Non-blocking: workspace path consistency in `smoke_test.md` (two path examples present, not a gate)

Both reviewers agree: the task meets its acceptance criteria and is ready to be finalized.

---

## 5. Finalization Briefing for Codex

As the parent owner with the task at `review_approved`, Codex should:

1. Confirm this packet is sufficient for handoff record
2. Run a final spot-check if desired (e.g., `bash scripts/openclaw-smoke-test.sh --skip-docker`)
3. Use the status script to close BP5-OSS-001:

```bash
AI_NAME=Codex bash scripts/ai-status.sh done BP5-OSS-001 \
  "BP5-OSS-001 finalized: upstream pin locked (v2026.4.7 / 5050017 / image + digest), governed adapter boundary documented, smoke test 6/6 passed, both reviewers approved. Deferreds captured in acceptance packet for BP5-OSS-002."
```

4. BP5-OSS-002 may then be unblocked by this closure.

---

## 6. Sidecar Scope Declaration

This file is a **support artifact only**.

- It does not modify any canonical truth files (L0, L1, L2 documents)
- It does not modify `ai-status.json`, `current-work.md`, or `ai-activity-log.jsonl` directly
- It does not alter the runtime contract, integration files, or schemas
- All canonical artifact modifications in `integrations/openclaw/` were made by the parent task (BP5-OSS-001) under Codex's ownership

This packet is intended to be absorbed into the parent task's closure by the parent owner at their discretion.
