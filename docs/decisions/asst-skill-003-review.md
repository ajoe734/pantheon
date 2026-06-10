---
type: review
task-id: ASST-SKILL-003
reviewer: Claude2
reviewed-at: 2026-06-09
outcome: approved
---

# Review: ASST-SKILL-003 – Frontend Generic Renderer (Catalog-Driven Surfaces)

## Verdict: Approved

PR #1177 (merged ae0c12ed) is correct and complete. The implementation
achieves the stated goal: the Management AI UI surfaces are fully driven
by the effective skill catalog, and the frontend no longer enumerates
capability IDs or handler refs in source code.

## Scope Reviewed

- `execute-plans/src/agora/pages/AskPersonas.tsx` (primary renderer)
- `execute-plans/src/lib/bff/managementAssistant.ts` (BFF helper layer)
- `execute-plans/src/lib/bff/managementAssistant.test.ts` (new test file)

## Checklist

**FE does not enumerate capabilities:**
- Hardcoded constants `ASSISTANT_SA_SD_GENERATE_SKILL_ID` and
  `ASSISTANT_SA_SD_GENERATE_HANDLER_REF` are removed.
- All skill IDs, labels, handler refs, and input schemas are read from
  the effective catalog descriptor at runtime.

**Deny-by-default route restriction:**
- `assistantCatalogRouteFromHandlerRef` only parses `bff.route:METHOD /bff/<path>`.
- Non-BFF prefixes (`/api/openclaw-adapter/…`) are rejected.
- Non-route handler refs (`openclaw.tool:…`) are rejected.
- Tests confirm all three cases.

**Surface routing:**
- `assistantSkillRenderSurface` maps `ui_surface`/`surface` descriptor
  fields to `button | command | card_action`.
- Surface fallback for unknown values: BFF-routable handler → button;
  otherwise → command. Reasonable conservative default.
- `card_action` skills render inside the error/alert area, matching the
  `degraded_card_action` semantics in the decision doc.

**Descriptor-driven input and enable logic:**
- `catalogSkillInputBody` builds the POST body using only properties
  declared in `input_schema.properties`; no extra keys leak.
- `catalogSkillDisabledReason` guards against missing/non-routable handler
  and missing required inputs before the action fires.
- `assistantSkillConfirmRequired` reads `confirm_policy.required` from
  the descriptor; triggers `window.confirm` as a placeholder confirmation
  gate. Acceptable for MVP; a proper modal is expected in a follow-up.

**Ownership alignment:**
- BFF helper `invokeAssistantCatalogRoute` dispatches against the
  descriptor-provided `handler_ref` without re-computing catalog truth
  in the frontend, consistent with the ownership decision in
  `docs/decisions/assistant-capability-skill-catalog-ownership.md`.

**No regressions on prior path:**
- `generateAssistantDevDocs` is retained; only a cast correction applied.
- `postManagementAssistantAsk`, `getAssistantOrchestratorStatus`, and
  `getAssistantTranscript` are unchanged.
- `assistantSkillDescriptors` is unchanged; new helpers compose over it.

## Minor Observations (non-blocking)

1. Dynamic status strings (`catalog_button_running`, `catalog_command_ready`,
   etc.) — any test that hard-matches status values should be updated if they
   were coupled to the old `generating_sa_sd` / `sa_sd_ready` strings.
2. Both camelCase and snake_case variants of each input key are set via
   `setDescriptorInput`. Correct: only keys declared in the descriptor's
   `input_schema.properties` are actually set, so unknown variants are silently
   skipped.
3. `catalogCommandSkills` are rendered below the main toolbar, which is
   a sensible layout for non-toolbar actions not tied to a BFF route.

## Conclusion

All task acceptance criteria are met. No architectural violations. The
catalog-driven renderer removes all hardcoded capability enumeration from
the frontend as required. Returning to owner (Codex) for finalization.
