# MGMT-SAFE-003 Review Packet

**Sidecar Kind:** review_packet  
**Parent Task:** MGMT-SAFE-003 — OpenClaw broker tool denial smoke  
**Prepared by:** Claude (initial), updated by Claude2 (2026-05-15, MGMT-SAFE-003-SIDECAR-REVIEW)
**Prepared at:** 2026-05-15  
**Intended reviewer:** Claude (MGMT-SAFE-003 reviewer — reassigned from Copilot after quota exhaustion)
**Supporting reviewer:** Codex (MGMT-SAFE-003-SIDECAR-REVIEW reviewer)

---

## 1. Task Overview

MGMT-SAFE-003 adds a repo-local safety smoke for the OpenClaw gateway adapter
that proves broker/live/paper/canary/capital/LEAN tool refs remain permanently
denied at the bridge layer even when:

- upstream OpenClaw advertises them as available, and
- the `OPENCLAW_ALLOWED_TOOLS` env-var allowlist includes them.

This is a fail-closed regression gate for EPIC-07 Safety.

---

## 2. Scope and Task-Owned Files

| File | Role |
|---|---|
| `services/openclaw-gateway-adapter/tool_workflow_bridge.py` | Core bridge: deny-by-default policy engine, audit log |
| `services/openclaw-gateway-adapter/test_tool_workflow_bridge.py` | Bridge unit tests (58 tests) |
| `scripts/run_openclaw_broker_tool_denial_smoke.py` | Smoke runner (19 checks) |
| `scripts/test_run_openclaw_broker_tool_denial_smoke.py` | Smoke runner tests |
| `support/evidence/MGMT-SAFE-003/openclaw-broker-tool-denial-smoke.json` | Machine-readable evidence JSON |
| `support/evidence/MGMT-SAFE-003/README.md` | Evidence scope and verification commands |

---

## 3. Implementation Summary

### Policy Engine (`tool_workflow_bridge.py`)

The `ToolPolicy` class enforces two independent denial layers:

**Always-blocked tool names** (normalized to lowercase, regardless of allowlist):
```
broker_order, submit_order, live_order, paper_order, canary_order,
capital_bind, capital_release, lean_deploy, live_execute, paper_execute,
canary_execute, broker_session_create, broker_session_cancel
```

**Always-blocked prefixes** (both tools and workflows):
```
broker.  live.  paper.  canary.  capital.  lean.
```

Evaluation order:
1. If tool name is in `_ALWAYS_BLOCKED_TOOLS` → `BRIDGE_TOOL_DENIED / always_blocked`
2. If tool name starts with any `_ALWAYS_BLOCKED_TOOL_PREFIXES` → `BRIDGE_TOOL_DENIED / always_blocked`
3. If allowlist is empty → `deny_all` (empty = deny all is the safe default)
4. If tool is not in allowlist → `not_in_allowlist`
5. Otherwise → allowed

Denied invocations are audited (append-only JSONL) and **never dispatched upstream**.

### Effective Tool Listing

The `/capabilities` endpoint returns `effective_tools` that excludes any tool
matching the always-blocked set before passing back to the caller. Even if the
upstream OpenClaw gateway advertises broker tools, they are stripped from the
effective listing.

---

## 4. Evidence Summary

Source: `support/evidence/MGMT-SAFE-003/openclaw-broker-tool-denial-smoke.json`

### Top-level assertions

| Assertion | Value |
|---|---|
| `broker_tools_denied_by_adapter_policy` | ✅ true |
| `denied_tools_not_dispatched_upstream` | ✅ true |
| `denied_workflows_not_dispatched_upstream` | ✅ true |

### Summary counters

| Field | Value |
|---|---|
| smoke_passed | ✅ true |
| rows | 19 |
| passed | 19 |
| blocked_tool_count | 8 |
| blocked_workflow_count | 6 |
| production_broker_enabled | ❌ false |
| live_execution_enabled | ❌ false |
| canary_execution_enabled | ❌ false |
| capital_binding_enabled | ❌ false |

### Smoke check results (19/19 passed)

| Check | Status |
|---|---|
| capabilities-remain-fail-closed | ✅ passed |
| effective-tools-exclude-broker-tools | ✅ passed |
| safe-control-tool-dispatches (research.search) | ✅ passed |
| tool-denied: broker.submit | ✅ passed |
| tool-denied: broker_order | ✅ passed |
| tool-denied: submit_order | ✅ passed |
| tool-denied: paper.execute | ✅ passed |
| tool-denied: live.order | ✅ passed |
| tool-denied: canary.order | ✅ passed |
| tool-denied: capital.bind | ✅ passed |
| tool-denied: lean.deploy | ✅ passed |
| safe-control-workflow-dispatches (research.daily_scan) | ✅ passed |
| workflow-denied: broker.submit | ✅ passed |
| workflow-denied: paper.execute | ✅ passed |
| workflow-denied: live.order | ✅ passed |
| workflow-denied: canary.deploy | ✅ passed |
| workflow-denied: capital.bind | ✅ passed |
| workflow-denied: lean.deploy | ✅ passed |
| audit-denials-without-upstream-dispatch | ✅ passed |

The `audit-denials-without-upstream-dispatch` check confirms that upstream tool
invocations only contain `research.search` and upstream workflow invocations only
contain `research.daily_scan` — blocked calls never reach upstream.

---

## 5. Verification Commands

```bash
# Reproduce the smoke (19/19 expected)
PYTHONDONTWRITEBYTECODE=1 python3 scripts/run_openclaw_broker_tool_denial_smoke.py \
  --json-out support/evidence/MGMT-SAFE-003/openclaw-broker-tool-denial-smoke.json

# Run smoke runner tests
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest scripts/test_run_openclaw_broker_tool_denial_smoke.py -q

# Run bridge unit tests (58 tests) — run via pytest from repo root
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  services/openclaw-gateway-adapter/test_tool_workflow_bridge.py -q
# Alternative: cd services/openclaw-gateway-adapter && python3 -m unittest test_tool_workflow_bridge -q

# Run full gateway adapter pytest suite (223 tests)
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest \
  services/openclaw-gateway-adapter scripts/test_run_openclaw_broker_tool_denial_smoke.py -q

# Syntax check
PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile \
  scripts/run_openclaw_broker_tool_denial_smoke.py \
  scripts/test_run_openclaw_broker_tool_denial_smoke.py \
  services/openclaw-gateway-adapter/tool_workflow_bridge.py \
  services/openclaw-gateway-adapter/test_tool_workflow_bridge.py
```

---

## 6. Safety Assertions

- No broker session created or invoked.
- No live execution path reached.
- No paper/canary execution path reached.
- No capital binding or release mutation performed.
- No LEAN deployment triggered.
- All denial decisions are audited locally before any upstream call.
- Upstream OpenClaw received only safe control tool/workflow refs (`research.*`).

---

## 7. Review Checklist for Claude

The reviewer (Claude) should verify:

- [ ] `_ALWAYS_BLOCKED_TOOLS` frozenset covers expected broker/live/paper/canary/capital/lean names.
- [ ] `_ALWAYS_BLOCKED_TOOL_PREFIXES` / `_ALWAYS_BLOCKED_WORKFLOW_PREFIXES` cover the expected namespaces.
- [ ] Empty allowlist → deny-all behavior is correct.
- [ ] `effective_tools` in capabilities endpoint excludes blocked tools even when upstream advertises them and allowlist includes them.
- [ ] Denied invocations are audited and not dispatched upstream (confirmed by `audit-denials-without-upstream-dispatch` row).
- [ ] `research.search` and `research.daily_scan` dispatch correctly (safe tool path not broken).
- [ ] All 19 smoke checks pass and evidence JSON matches current checked-in file.
- [ ] No production, live, canary, capital, or LEAN side effects in the implementation.

---

## 8. Reviewer Handoff Notes

MGMT-SAFE-003 is currently in `review_approved` status. Reviewer was reassigned
from Copilot (quota exhausted) to Claude, who has already approved the parent task.
This sidecar packet documents the evidence and implementation for the record:

- The evidence JSON at `support/evidence/MGMT-SAFE-003/openclaw-broker-tool-denial-smoke.json`
  is the machine-readable ground truth for all 19 checks.
- The bridge implementation is fully self-contained in
  `services/openclaw-gateway-adapter/tool_workflow_bridge.py`.
- No canonical architecture documents were modified by MGMT-SAFE-003.

If Claude approves this sidecar packet, use:
```bash
AI_NAME=Claude REVIEW_FILE=support/sidecars/MGMT-SAFE-003/MGMT-SAFE-003-SIDECAR-REVIEW.md \
  ./scripts/ai-status.sh approve MGMT-SAFE-003-SIDECAR-REVIEW \
  "Sidecar review packet verified — reviewer routing corrected to Claude, reproducibility commands fixed, evidence summary accurate."
```

Note: Parent task MGMT-SAFE-003 was already approved by Claude via the main review path.

If changes are needed, use `reopen` with concrete required changes.
