# PER-002 Review

Reviewer: `Codex`
Owner: `Claude2`
Date: `2026-05-16`
Disposition: `approved`

## Findings

No blocking findings.

PER-002 adds the requested persona-scoped BFF read endpoints:

- `GET /bff/personas/{persona_id}/skills`
- `GET /bff/personas/{persona_id}/tools`
- `GET /bff/personas/{persona_id}/capabilities`

The endpoints require read-role auth, verify persona existence, derive effective skill/tool/capability data from the capability snapshot, preserve BFF envelope conventions, and return 404 for unknown personas.

One scope note: commit `0b1fca5f` also includes incident BFF projection/fail-closed changes in `services/control-plane/bff/main.py`. I treated that as adjacent dirty-base/context risk during review and ran the incident contracts below; they passed.

## Verification

Passed:

```bash
python3 -m pytest services/control-plane/bff/test_per002_bff_persona_skills_tools_capabilities_contract.py -q
```

Result: `18 passed in 55.42s`

Passed:

```bash
python3 -m pytest services/control-plane/bff/test_persona_management.py services/control-plane/bff/test_bff_strategy_persona_contract.py -q
```

Result: `17 passed in 58.70s`

Passed:

```bash
python3 -m pytest services/control-plane/bff/test_inc001_rebaseline_incidents_contract.py services/control-plane/bff/test_bff_governance_runtime_risk_audit_contract.py -q
```

Result: `7 passed in 54.71s`
