# Task Brief: OCLAW-PMEM-004-SIDECAR-BFF-HANDOFF

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Prepare OCLAW-PMEM-004 BFF and frontend handoff packet
- Status: in_progress
- Owner: Codex
- Reviewer: Claude
- Next: Review found one factual defect in section 2 (Existing Integration Points to Reuse), 'Provider readiness' row: it cites 'GET /bff/assistant/provider/readiness' as an existing route, but no such route exists. The actual implemented route is 'GET /bff/assistant/providers' (optional ?auth_probe=true query param, services/control-plane/bff/assistant/routes.py list_assistant_providers), backed by _assistant_provider_readiness() / OpenClawOpsClient().get_assistant_readiness() in services/control-plane/bff/main.py. Confirmed via contract_snapshots/execute_plans_bff_routes.json (only /bff/assistant/providers is listed; no provider/readiness path exists). All other cited routes (runtime-profile, persona memory, /api/memory/retrieve, persona_memory_bridge.py, reauth start/status/code) were verified accurate. Please correct that one row/citation and resubmit for review.

## Summary
平行支援 OCLAW-PMEM-004，先整理 BFF query gap、operator journey 與前端 handoff materials，不改 canonical truth。
