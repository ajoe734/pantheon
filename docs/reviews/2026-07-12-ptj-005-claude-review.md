# PTJ-005 reviewer findings

Reviewer: Claude
Disposition: approved

## Scope reviewed

Commits `f587bba7d..aa04279dd` on `task/PTJ-005` touching:

- `services/persona/lesson_governance.py` (new)
- `services/persona/trade_lesson_candidate.schema.json` (new)
- `services/memory/main.py` (trade-lesson HTTP routes)
- `services/governance/authz.py` (`lesson.decide` / `lesson.merge` actions)
- accompanying tests in `services/persona/`, `services/memory/`,
  `services/governance/`

## Acceptance criteria

1. **Lesson candidates cannot directly mutate policy/risk/capital
   artifacts or live behavior.**
   `TradeLessonCandidateStore.create()` fail-closes any candidate that
   does not start at `target_env="paper"` / `promotion_stage="proposed"`.
   `merge_to_memory()` only ever writes a `PersonaMemoryEntry`; nothing
   in `services/capital`, `services/policy-learning`, or
   `services/execution` reads `TradeLessonCandidate` or
   `PersonaMemoryStore` records in this diff, so there is no code path
   by which a lesson candidate can reach live risk/capital/execution
   state. Confirmed via `grep -rl "trade_lesson\|lesson_candidate\|PersonaMemoryStore" services/policy-learning services/capital services/execution` (no hits).

2. **Memory evaluation approval and environment promotion gates fail
   closed with receipts.**
   - `decide`/`merge` require authenticated `X-Actor-ID` /
     `X-Actor-Roles` headers; body-supplied `actor_roles` are ignored
     (`test_api_spoofed_role_negative`).
   - `_authorize_lesson_action` fails closed (403) when authz mode is
     unconfigured or the governance service is unreachable
     (`test_api_authz_dependency_unavailable_negative`).
   - Sensitive-scope or canary/live endorsement requires a governance
     `ApprovalDecision` that is `approved`, and whose `persona_id`,
     `target_id` (== `lesson_candidate_id`), and `target_version` (==
     `reflection_version`) all match the candidate; missing/mismatched
     fields 403 (`test_api_decide_and_merge_target_validation_negative`).
   - `merge` re-validates the same receipt independently of `decide`
     (`test_api_merge_revalidate_receipt_sensitive`), so a
     revoked/rejected decision after endorsement blocks merge.
   - Promotion stage transitions are server-derived from `target_env`
     and reject client-asserted `promotion_stage` mismatches and
     paper→live skip-canary attempts (`test_api_promotion_gates`,
     `test_api_promotion_stage_bypass_repro`).

## Verification

```text
python3 -m pytest -q services/persona/test_lesson_governance.py \
  services/memory/test_lesson_governance_api.py \
  services/persona/test_trade_reflection_contracts.py \
  services/governance/test_governance_api.py \
  services/capital/test_risk_policy.py

66 passed
```

## Non-blocking note

`merge_trade_lesson` in `services/memory/main.py` still declares unused
`actor_id` / `actor_roles` Query params left over from before header-based
auth was enforced; only `X-Actor-ID` / `X-Actor-Roles` headers are read.
They are dead parameters, not a spoofing vector (nothing reads them), but
worth deleting in a follow-up cleanup.
