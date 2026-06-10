---
task_id: ASST-SKILL-002
reviewer: Claude
review_date: 2026-06-09
verdict: approved
---

# Review: ASST-SKILL-002 — Pilot: migrate SA/SD button to governed skill

## Scope Verified

Reviewed PR #1171 (merge c0b1f8a2). Changed files:
- `services/openclaw-gateway-adapter/tool_workflow_bridge.py`
- `services/control-plane/bff/assistant/orchestrator_status.py`
- `execute-plans/src/agora/pages/AskPersonas.tsx`
- `docs/decisions/assistant-capability-skill-catalog-ownership.md`
- Supporting tests (test_tool_workflow_bridge.py, test_orchestrator_status.py, test_openclaw_ops_surface.py, test_compose_activation.py)
- `docker-compose.yml` (minor)

## Acceptance Criteria Checklist

- [x] `assistant.sa_sd.generate` registered as governed skill with mode_gate kernel — descriptor in `tool_workflow_bridge.py` with `allowed_modes = ("kernel_debug", "kernel_repair")`, deny-by-default gate.
- [x] `handler_ref` points at existing `bff.route:POST /bff/assistant/dev-docs/generate` handler — no handler logic changed.
- [x] Skill appears in `GET /api/openclaw-adapter/tools` effective catalog when permitted — `list_effective_tools()` returns it in `effective_skills` when `assistant.sa_sd.generate` is allowlisted.
- [x] FE renders SA/SD affordance from descriptor, not hardcoded button — `{saSdGenerateSkill && (...)}` in AskPersonas.tsx; button only shown when catalog confirms the skill.
- [x] Gate enforcement: `handleGenerateDevDocs` verifies `saSdGenerateSkill` present AND `handler_ref` matches before invoking.
- [x] Tests prove catalog presence, gate enforcement, and user-mode/viewer-role denial — `test_sa_sd_skill_descriptor_points_to_bff_dev_docs_handler`, `test_sa_sd_skill_descriptor_denies_user_mode_and_viewer_role` in bridge tests; BFF projection tested in `test_orchestrator_status.py`.

## Verification Run

```
python3 -m pytest services/openclaw-gateway-adapter/test_tool_workflow_bridge.py -q → 64 passed
python3 -m pytest services/control-plane/bff/assistant/tests/test_orchestrator_status.py services/control-plane/bff/test_openclaw_ops_surface.py -q → 16 passed
```

## Observations

- `_ADAPTER_OWNED_ASSISTANT_TOOLS` correctly includes the new skill ID, preventing upstream tool-metadata passthrough for this descriptor.
- BFF `orchestrator_status.py` projects `effective_skills` from adapter response without recomputing catalog truth — consistent with ownership decision doc.
- `confirm_policy` on the SA/SD descriptor is `{"required": False}` which is appropriate for a non-destructive generation command.
- Handler ref guard in the FE (`assistantSkillHandlerRef(saSdGenerateSkill) !== ASSISTANT_SA_SD_GENERATE_HANDLER_REF`) is a good defensive check that protects against catalog descriptor drift.

## Verdict

**Approved.** Implementation is end-to-end correct and matches the EPIC ASST-SKILL template intent. No changes requested.
