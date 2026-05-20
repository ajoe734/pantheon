# CBL-004-V2 Closeout Evidence

Task: CBL-004-V2
Owner: Codex2
Reviewer: Codex
Status at closeout: review_approved

## Reviewed Delivery

- PR: #334
- Merge commit: f2ba5e83a35bed6aa4e48cc5b459548fd57950fc
- Delivered artifacts:
  - services/capital/binding_live/lifecycle.py
  - services/capital/main.py
  - tests/capital/test_binding_lifecycle.py
- Canonical L1 docs modified: no

The reviewed implementation provides side-effect-free BindingTTL,
BindingRevocationPolicy, suspend/reactivate/revoke lifecycle evaluation,
fail-closed TTL expiry, and endpoint-level capital HTTP error mapping.

## Closeout Verification

Commands run from task/CBL-004-V2 on 2026-05-20:

```bash
pytest -q tests/capital/test_binding_lifecycle.py tests/capital/test_binding_live_readiness.py
python3 -m py_compile services/capital/binding_live/lifecycle.py tests/capital/test_binding_lifecycle.py
git diff --check f2ba5e83^1 f2ba5e83 -- services/capital/main.py services/capital/binding_live/lifecycle.py tests/capital/test_binding_lifecycle.py
pytest -q tests/capital services/capital/test_service.py services/control-plane/governance/test_persona_capital_binding.py
```

Results:

- lifecycle/readiness tests: 15 passed
- py_compile: passed
- diff whitespace check: passed
- broader capital/governance regression: 80 passed

## Publication Refresh

PR #338 initially reported `BEHIND` after checks passed, so the task branch
was refreshed with `origin/dev` before final publication. This evidence file
was updated after that refresh so the branch head remains a task-scoped
CBL-004-V2 commit with the required closeout trailers.
