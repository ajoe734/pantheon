# BFF-B6-001-SEC-FIX Review Handoff

Date: 2026-05-25
Owner: Codex2
Reviewer: Claude
Status entering handoff: in_progress

## Scope

This handoff records the merged NL security-hardening implementation and the
remaining lifecycle step. It does not change BFF runtime behavior, canonical
architecture policy, or the reviewed implementation.

Implementation:
- Initial task commit: 64ade4239edc421aacc11b661d8dd5adecd714ea
- Follow-up task commit: 3a8d7b4cc4724e286567b4d2dd3e4774353e5afe
- Implementation PR: #587, merged into dev at 807eafdbee2c0f6fe906adbfc15def38f65f8121
- PR merged at: 2026-05-25T13:41:24Z
- GitHub checks: Branch CI Gate Commit trailers, Runtime mirror guard, and Smoke acceptance passed

## Delivered Behavior

The merged implementation covers the BFF-B6-001 acceptance scope:
- tenant-scoped management NL context collection for portfolio, trading pulse,
  cockpit, and persona fleet snippets
- read-store evidence filtering by tenant, linked entity, and source before
  projection and redaction
- 2048-byte management NL question cap before side effects
- word-boundary plus CJK/synonym high-risk classifier hardening
- fail-closed 503 when happy-path Agora audit writes fail before session,
  SSE, or idempotency side effects
- response evidenceRefs constrained to evidence linked to entities used in the
  composed answer snippets

## Owner Verification

Commands rerun during owner handoff:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile services/control-plane/bff/main.py services/control-plane/bff/read_store.py services/control-plane/bff/tests/test_bff_b6_management_nl_ask.py services/control-plane/bff/tests/test_bff_b6_003_nl_high_risk_refusal.py services/control-plane/bff/tests/test_bff_b6_001_security_hardening.py
```

Result: passed.

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/control-plane/bff/tests/test_bff_b6_management_nl_ask.py services/control-plane/bff/tests/test_bff_b6_003_nl_high_risk_refusal.py services/control-plane/bff/tests/test_bff_b6_001_security_hardening.py -q
```

Result: 23 passed in 10.38s.

```bash
git diff --check
```

Result: passed.

## Remaining Lifecycle Step

The task is still `in_progress` in the active status root. Owner handoff should
move it to `review`; Claude must perform the formal status approval before the
owner can run `AI_NAME=Codex2 ./scripts/ai-status.sh done`.
