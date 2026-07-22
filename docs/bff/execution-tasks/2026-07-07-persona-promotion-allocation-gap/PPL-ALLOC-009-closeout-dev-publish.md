# PPL-ALLOC-009 - Closeout And Dev Publish

Owner: Codex
Reviewer: Claude
Depends on: `PPL-ALLOC-002`, `PPL-ALLOC-003`, `PPL-ALLOC-004`, `PPL-ALLOC-005`, `PPL-ALLOC-006`, `PPL-ALLOC-007`, `PPL-ALLOC-008`
Type: production closeout task

## Problem

This packet changes the product workflow. It is not complete until code,
tests, PRs, dev deployment, hosted smoke evidence, and residual risk accounting
prove the workflow end to end.

## Scope

- Verify every `PPL-ALLOC-*` implementation task is done or explicitly
  superseded by reviewed evidence.
- Confirm Pantheon PRs and Execute Plans PRs are pushed, reviewed, checks pass,
  and merged.
- Verify dev BFF and dev frontend deployments where runtime behavior changes.
- Run hosted smoke for:
  - create persona -> `paper_running`;
  - paper ranking -> promotion review -> human decision;
  - real ranking -> target weights -> rebalance proposal -> approval/apply
    receipt;
  - emergency breach -> containment without promotion/increase.
- Archive final evidence under the gap spec archive directory.

## Acceptance

- Final closeout records PR numbers, merge commits, deployed commits, and
  validation commands.
- Hosted smoke includes request/response evidence for BFF commands and browser
  route-ready evidence for frontend flows.
- Residual risks have owner, expiry, and blocking/non-blocking status.
- The closeout explicitly states whether each page inventory item reached the
  target state.

## Validation

```sh
git status -sb
python3 scripts/ai_status.py sync
python3 -m pytest services/control-plane/bff/tests/test_bff_persona_create_paper_bundle.py services/control-plane/bff/tests/test_bff_persona_allocation_policy.py services/control-plane/bff/tests/test_bff_rebalance_proposals.py services/control-plane/bff/tests/test_bff_emergency_containment.py -q
npm test -- src/management/pages/oversight/PromotionAllocation.test.tsx src/management/pages/oversight/PersonaFleetPage.test.tsx src/management/pages/oversight/HumanGateDetail.test.tsx
npm run lint
npm run build
gh pr status
git diff --check
```
