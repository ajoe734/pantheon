# Round 008 - BFF suite census + authz "failures" verified as NON-bugs

- Date: 2026-06-14
- Path: full/curated BFF contract suite census after R003-R007; deep-verify the
  security-relevant remaining failures before any fix.
- Branch: task/verify-r8-bff-suite (off dev). No code change.

## Census

The full BFF contract suite is large (>8 min, times out). A curated batch of the 16
files that still had failures after R005 now runs **5 failed / 164 passed** - i.e.
R003-R007 already cleared the vast majority. The 5 remaining are HETEROGENEOUS (no single
mechanical pattern left) and classified below.

## The 5 remaining failures (root-caused)

### 2x stale deprecated-route (test_bff_governance_runtime_risk_audit) - test fix
Tests call `/bff/{type}s/{id}/actions/{action}` and expect 202, but the code correctly
returns **410** with a replacement pointer `/bff/actions/{type}/{id}/{action}`
(deprecated_since 2026-05-25). Code is right; tests use the retired route. Fix = repoint
tests to the new `/bff/actions/...` surface (left to owner: the new handler is a generic
path-param route and the risk/alerts replacement needs confirming; not rushed).

### 2x authz 200-vs-403 - VERIFIED NOT A SECURITY BUG (this is the round's key result)
- `test_bff_me_strict_auth_rejects_viewer_without_read_role`: expects 403 for a `viewer`
  JWT, gets 200. But `_ROLE_CAPABILITY_MAP` (main.py:4809) INTENTIONALLY grants
  `viewer -> [metric.read, strategy.view, persona.view]`. A viewer IS a read role by design,
  so 200 is correct. The test's premise ("viewer has no read role") is outdated.
- `test_get_source_ops_requires_operator_role`: expects 403 for a viewer on
  `/api/v1/operator/source/ops`, gets 200. But ALL ~25 `/api/v1/operator/*` READ surfaces
  use `_require_read_role` (only openclaw command/mutation routes use a stricter role) - a
  consistent, deliberate convention: operator-console read surfaces are visible to any
  reader. This single test asserting operator-only is the outlier.

CRITICAL: the naive "fix" would tighten production authz (`_require_read_role` ->
`_require_operator_role`). That was REJECTED after verification - it would break the
25-endpoint convention and the sibling `degraded_returns_200` test. Code is correct and
consistent; the two tests carry outdated stricter-than-design premises. Recommended owner
fix: correct the test premises (use a genuinely no-read principal for the negative
assertion), NOT a production authz change. Not autonomously rewritten here because they are
security-relevant assertions better changed with owner sign-off.

### 1x field drift (test_rw05_artifact_compare) - test fix
`KeyError: 'non_comparable_artifacts'` - the compare response shape no longer exposes that
key under that name. Needs the current field name; small follow-up.

## Net
R008 is verification: it confirms R003-R007 cleared the BFF rot down to 5 heterogeneous
items, and - most importantly - proves the 2 authz failures are stale-premise tests, NOT an
authorization hole, preventing a harmful production authz tightening. The 5 are precisely
diagnosed for owner follow-up.
