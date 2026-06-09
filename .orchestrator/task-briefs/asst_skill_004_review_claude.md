# Review: ASST-SKILL-004 — Toolbar Capability Migration to Skills

Reviewer: Claude
Date: 2026-06-09
Status: APPROVED

## Scope Verification

The task brief scoped this as: wrap remaining toolbar capabilities (control-mode, resync, openclaw) into catalog skill descriptors, pointing `handler_ref` at existing BFF routes, with access gating via descriptor+policy.

The commit (`97d639dc`) delivers exactly that — no new BFF command router, no new OpenClaw registry, no control-mode store changes. Four descriptors added:

| Skill id | Mode gate | Handler ref | Result surface |
|---|---|---|---|
| `assistant.openclaw.ask` | kernel_debug, kernel_repair | `POST /bff/management/nl/ask` | `assistant_management_answer` |
| `assistant.control_mode.status` | kernel_observe, kernel_debug, kernel_repair | `GET /bff/assistant/control-mode` | `assistant_control_mode_status` |
| `assistant.transcript.resync` | kernel_observe, kernel_debug, kernel_repair | `GET /bff/assistant/sessions/{sessionId}/transcript` | `assistant_transcript_resync` |
| `assistant.orchestrator.status` | kernel_observe, kernel_debug, kernel_repair | `GET /bff/assistant/orchestrator/status` | `assistant_orchestrator_status` |

## Findings

**No blocking issues.**

1. **Mode gate correctness**: `openclaw.ask` is a write operation and correctly excluded from `kernel_observe`. The three readback skills are available in all operator modes including observe. This is the right policy split.

2. **Path template resolution** (`resolveCatalogPath` in `managementAssistant.ts`): Clean implementation with URL encoding and camelCase/snake_case aliasing. Fails closed (throws) on missing path params — correct security posture.

3. **`setDescriptorInput` guards**: Descriptor input fields are only injected when declared in the skill's `input_schema.properties`. The many `setDescriptorInput` calls in `catalogSkillInputBody` are therefore safe — extraneous fields are silently dropped by the descriptor resolver.

4. **`handleManagementAsk` removal**: ~80 lines of hard-coded Management AI dispatch removed; behavior preserved through catalog dispatch + `assistant_management_answer` result surface handler. Migration is complete and clean.

5. **Dev allowlist update** (`docker-compose.yml`): Default `OPENCLAW_ALLOWED_TOOLS` correctly extended to include all 4 new tool ids. Compose activation test updated in sync.

6. **Decision doc**: ASST-SKILL-004 section accurately records the design rationale and does not broaden architecture beyond this task's scope.

7. **Test coverage**: 85 tests passing. New Python tests cover descriptor correctness and mode-gate boundary (observe excludes ask). New TypeScript tests cover path substitution (URL encoding) and fail-closed behavior. All required cases are covered.

## Verdict

APPROVED. Implementation is correct, well-tested, and tightly scoped. Ready for owner finalization.
