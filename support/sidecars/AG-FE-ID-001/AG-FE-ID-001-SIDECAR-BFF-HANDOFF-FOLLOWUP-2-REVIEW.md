# Review: AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2

| Field | Value |
|---|---|
| Reviewer | `Claude` |
| Date | `2026-06-20` |
| Verdict | `approved` |
| Mutates canonical truth | `false` (confirmed) |

## Verification Commands Run

```bash
AI_NAME=Claude python3 scripts/ai_status.py show AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2
rg -n -P '/bff/agora/me(?=[:"\s]|$)' services/control-plane/openapi/agora_v1.openapi.yaml services/control-plane/specs/agora/capability_manifest.json execute-plans/src/lib/bff-v1/agora/types.ts execute-plans/src/lib/bff-v1/paths.ts services/control-plane/bff/agora/router.py
rg -n -P '/bff/agora/capabilities(?=[:"\s]|$)' services/control-plane/openapi/agora_v1.openapi.yaml services/control-plane/specs/agora/capability_manifest.json execute-plans/src/lib/bff-v1/agora/types.ts execute-plans/src/lib/bff-v1/paths.ts services/control-plane/bff/agora/router.py
rg -n -P '/bff/agora/servant/ensure(?=[:"\s]|$)' services/control-plane/openapi/agora_v1.openapi.yaml services/control-plane/specs/agora/capability_manifest.json execute-plans/src/lib/bff-v1/agora/types.ts execute-plans/src/lib/bff-v1/paths.ts services/control-plane/bff/agora/servant/router.py services/control-plane/bff/tests/test_agora_router.py
test -f execute-plans/src/agora/AgoraApp.tsx && echo EXISTS || echo MISSING
test -f execute-plans/src/lib/bff-v1/agora/identity.ts && echo EXISTS || echo MISSING
test -f execute-plans/src/lib/bff-v1/agora/servant.ts && echo EXISTS || echo MISSING
```

## Findings Against Review Criteria

1. **Support-only scope compliance** — PASS. The only repo change is `support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md`. No canonical docs, OpenAPI, BFF runtime code, registry, or governance implementation was touched.

2. **Runtime BFF routes vs generated frontend route truth** — PASS. Verified by `rg` scan:
   - `/bff/agora/me` and `/bff/agora/capabilities` appear only in `services/control-plane/bff/agora/router.py`. Absent from `agora_v1.openapi.yaml`, `capability_manifest.json`, `types.ts`, and `paths.ts`.
   - `/bff/agora/servant/ensure` appears only in `services/control-plane/bff/agora/servant/router.py` and focused tests, with a confirmed HTTP 501 return. Also absent from generated sources.

3. **Correct treatment of blocked AG-BE-ID-002 and downstream AG-BE-ID-003** — PASS. The packet correctly states the dependency chain (AG-BE-ID-002 blocked → AG-BE-ID-003 todo → AG-FE-ID-001 cannot claim live servant/session success). The operator journey and absorption gates reinforce this.

4. **Actionable frontend client handoff** — PASS. Sections 6.1–6.3 give concrete minimal responsibilities for `identity.ts`, `servant.ts`, and `AgoraApp.tsx`. Section 8 lists clear parent absorption gates. Section 7 gives a step-by-step operator journey. All three frontend artifacts (`AgoraApp.tsx`, `identity.ts`, `servant.ts`) confirmed missing as stated.

5. **No canonical expansion** — PASS. No changes to L1 policy, OpenAPI, capability manifest, BFF runtime handlers, generated types, path helpers, registry, or governance implementation.

## Notes for Parent Owner (AG-FE-ID-001)

- Routes `/me` and `/capabilities` are interim runtime-only routes; the parent must decide: either accept them as interim BFF truth or open a blocker for OpenAPI/manifest reconciliation first.
- The 501 on `/servant/ensure` is the current authoritative state; `servant.ts` tests must prove 501 maps to `backend_not_ready`, not success.
- The packet correctly flags that AG-FE-ID-001 must not mark itself complete while AG-BE-ID-002 is blocked and AG-BE-ID-003 is todo, unless scope is explicitly reduced to blocked-shell-only.
