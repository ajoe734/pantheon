# Review: SVC-OPENCLAW-TOOL-WORKFLOW-BRIDGE-SIDECAR-BFF-HANDOFF

Reviewer: Codex2
Date: 2026-04-30
Status: changes requested

## Findings

1. `support/sidecars/SVC-OPENCLAW-TOOL-WORKFLOW-BRIDGE/SVC-OPENCLAW-TOOL-WORKFLOW-BRIDGE-SIDECAR-BFF-HANDOFF.md:82`
   The packet states that no tool/workflow bridge routes are implemented yet and that `services/openclaw-gateway-adapter/main.py` has 671 lines. Current repo state has 922 lines and now exposes bridge routes at `/api/openclaw-adapter/tools/policy`, `/api/openclaw-adapter/tools`, `/api/openclaw-adapter/tools/invoke`, `/api/openclaw-adapter/workflows/trigger`, `/api/openclaw-adapter/workflows/jobs/{job_id}`, and `/api/openclaw-adapter/audit/invocations`.

   This makes the current-surface snapshot and verification notes stale at review time. The packet is support-only, but its reviewer checklist explicitly requires current-route statements to match adapter/BFF code at review time. Please refresh sections 3, 4, 6, and 8 to distinguish the now-implemented adapter bridge routes from the still-missing BFF composed/frontend contract.

## Verification Run

```bash
rg -n "@app\\.(get|post|put|delete)|api/openclaw|_CAPABILITY_SNAPSHOT|tool_resolution|workflow_cron_hooks" services/openclaw-gateway-adapter/main.py
sed -n '760,930p' services/openclaw-gateway-adapter/main.py
wc -l services/openclaw-gateway-adapter/main.py
rg -n "RS-04|OC-|OpenClaw|tool-workflow|tool workflow" services/control-plane/bff/BFF_SURFACE_INVENTORY.md services/control-plane/bff/BFF_API_CONTRACT.md
```

Results:

- Adapter route list no longer matches the packet; current bridge routes are present in `main.py`.
- BFF inventory/API contract still show only the read-only OSS activation-ready OpenClaw surface, so the BFF gap remains real but needs updated wording.
- No canonical truth or implementation file was changed during review.
