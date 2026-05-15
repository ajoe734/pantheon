# Review: SVC-OPENCLAW-TOOL-WORKFLOW-BRIDGE-SIDECAR-BFF-HANDOFF

Reviewer: Claude
Date: 2026-04-30
Status: approved

## Disposition

Sidecar packet approved. The packet is support-only, does not modify canonical truth, and its route inventory and BFF gap descriptions are accurate against the current worktree.

## Verification Run

```bash
# Verify adapter routes match §3.1 inventory
grep -n "@app\.\(get\|post\)" services/openclaw-gateway-adapter/main.py | grep -E "/tools|/workflows|/audit|/lifecycle|/sessions|/capabilities|/upstream"

# Check current adapter line count
wc -l services/openclaw-gateway-adapter/main.py

# Verify BFF routes match §3.2 inventory
grep -n "@app\.\(get\|post\)" services/control-plane/bff/main.py | grep openclaw

# Verify BFF read_store has get_openclaw_ops_snapshot
grep -n "def get_openclaw_ops_snapshot" services/control-plane/bff/read_store.py

# Run BFF OpenClaw ops surface tests
python3 -m pytest services/control-plane/bff/test_openclaw_ops_surface.py -q
```

Results:

- All §3.1 adapter routes present: `/capabilities`, `/upstream/status`, `/sessions`, `/sessions/{session_id}`,
  `/lifecycle/sessions`, `/lifecycle/sessions/{session_id}`, `/lifecycle/sessions/{session_id}/cancel`,
  `/lifecycle/sessions/{session_id}/audit`, `/tools/policy`, `/tools`, `/tools/invoke`,
  `/workflows/trigger`, `/workflows/jobs/{job_id}`, `/audit/invocations` — confirmed in `main.py`.
- Adapter has grown to 1050 lines (was 922 at packet refresh). The additional lines are paper broker
  adapter routes (`/broker/audit`, `/broker/capabilities`) added by the separate `SVC-OPENCLAW-PAPER-BROKER-ADAPTER`
  task. These are outside this sidecar scope and correctly absent from §3.1.
- All four §3.2 BFF routes confirmed: `GET /api/v1/operator/openclaw/ops`,
  `GET /api/v1/operator/openclaw/tool-workflow-bridge`,
  `POST /api/v1/operator/openclaw/sessions`,
  `POST /api/v1/operator/openclaw/sessions/{session_id}/cancel`.
- `get_openclaw_ops_snapshot` function exists in `read_store.py` at line 5488.
- BFF OpenClaw ops surface tests: **4 passed**.

## Findings

None blocking.

Minor observation: the packet header still names "Sidecar Reviewer: Codex2" — chair reassigned review
to Claude before this pass. No fix required; the reviewer field is informational metadata in a
support-only document and does not affect canonical truth or task state.

## Scope Check

| Check | Result |
|---|---|
| Packet is support artifact only | Yes — verified no L1/runtime/BFF/frontend files changed |
| Route claims match adapter code at review time | Yes — all listed routes present |
| BFF gap description matches BFF code at review time | Yes — invoke/trigger commands absent from BFF as stated |
| Frontend guidance preserves fail-closed semantics | Yes — `allowedActions` gating, no client-side policy inference |
| Canonical truth modified | No |

## Notes for Owner (Codex) / Parent Owner (Claude)

- The BFF surface gap identified in §6 (tool invocation/workflow trigger commands not exposed) is accurate.
- The packet correctly separates what is implemented (adapter bridge routes + BFF read/session facades) from what remains (BFF-owned invoke/trigger command contract + frontend adoption).
- Parent owner should treat §4.2 command boundary guidance and §5 frontend handoff materials as advisory input when implementing the canonical task's remaining BFF command surface.
