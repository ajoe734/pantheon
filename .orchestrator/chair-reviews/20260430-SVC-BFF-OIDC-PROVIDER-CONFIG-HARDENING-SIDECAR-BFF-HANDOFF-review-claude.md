# Review: SVC-BFF-OIDC-PROVIDER-CONFIG-HARDENING-SIDECAR-BFF-HANDOFF

**Reviewer**: Claude  
**Reviewed at**: 2026-04-30  
**Task**: SVC-BFF-OIDC-PROVIDER-CONFIG-HARDENING-SIDECAR-BFF-HANDOFF  
**Artifact**: support/sidecars/SVC-BFF-OIDC-PROVIDER-CONFIG-HARDENING/SVC-BFF-OIDC-PROVIDER-CONFIG-HARDENING-SIDECAR-BFF-HANDOFF.md  
**Outcome**: **APPROVED**

---

## Review Summary

This sidecar packet is an accurate, support-only BFF/frontend handoff for `SVC-BFF-OIDC-PROVIDER-CONFIG-HARDENING`. All acceptance criteria are met.

---

## Checklist Verification

| Check | Result | Notes |
|---|---|---|
| Sidecar avoids canonical/runtime implementation edits | PASS | Only the support packet was added. No edits to L1 docs, BFF runtime code, compose, or registry. |
| Current implemented facts separated from parent gaps | PASS | Section 2 documents current BFF state with code evidence. Section 3 gap matrix clearly labels each remaining gap as a parent decision. |
| No IdP secrets leaked or invented | PASS | Section 5.2 uses `<idp-host>` placeholders only. No actual secrets or JWKS private keys. |
| Browser-to-BFF-only integration preserved | PASS | Operator journeys in Section 4 consistently route through BFF. No proposals for direct browser calls to runtime-manager, internal API, or governance. |
| Frontend-visible auth outcomes identified | PASS | Section 4.3 gives a complete 401/403/409/422 and downstream command-status split with UI handling guidance. |

---

## Acceptance Criteria Verification

| Criterion | Status |
|---|---|
| Create support artifacts only | PASS — single support markdown file added |
| Do not edit canonical truth | PASS — no L1/L2 canonical files touched |
| Hand off the packet to the assigned reviewer | PASS — handoff recorded in ai-status.json |

---

## Verification

```
python3 -m pytest services/control-plane/bff/test_bff_auth_facade.py -q
→ 52 passed in 3.94s
```

---

## Review Notes

The packet is well-structured. The gap matrix in Section 3 is particularly useful for parent task pickup — it distinguishes between code-supported (but undocumented in staging compose) env vars and genuinely unimplemented behaviors such as OIDC discovery and claim-based MFA.

Key parent pickup items noted accurately:
1. Wire non-secret OIDC env vars into VM1 staging compose/example env.
2. Decide OIDC discovery vs documented JWKS.
3. Explicitly test role claim contract (list, string, fallback, denial cases).
4. Add/test JWKS stale-cache + new `kid` rotation.
5. Confirm browser CORS headers for command headers used in staging-live.
6. Confirm downstream VM2 accepts the propagated OIDC token shape.

No changes required to the support artifact. Returning to Codex for closeout.
