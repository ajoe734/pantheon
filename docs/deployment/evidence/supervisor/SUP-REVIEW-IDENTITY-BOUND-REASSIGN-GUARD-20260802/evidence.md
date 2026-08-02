# SUP-REVIEW-IDENTITY-BOUND-REASSIGN-GUARD-20260802 Evidence

Status: implementation complete; pending Human/Ops exact-head review on PR #4488.

## Result

Automatic recovery now preserves the designated reviewer when a review has an exact-head binding or a pending reviewer handoff. Unbound recovery remains available, but the candidate and persistence gates both require a genuinely independent reviewer identity.

Codex and Codex2 are treated as one ChatGPT review identity. A Codex-owned task therefore cannot be approved by Codex2, and an alternate-label exact-head approval cannot make the PR merge-ready or auto-merge-ready. Codex3 remains independent because no explicit configured account evidence binds it to that verified pair; a numeric lane suffix alone is not identity evidence.

Human/Ops reopened exact head `0b05455f0c96e1900825f888d36c638a61dcce51` because it overextended the verified alias set to Codex3. The bounded correction removes only that unsupported alias and adds a negative regression; the updated PR head requires a fresh exact-head review.

## Incident boundary

- PR #4414 / merge `dc5136394eb1041ceea1dcc066e55ac2179ca0e5` is referenced only for the existing handoff/identity continuity baseline.
- PR #4445 / merge `2350e4e85c69009b52e7fbb75e621b04f83f66c9` is referenced only for the reproduced reviewer-replacement incident and current V3 baseline.
- This task does not change V3 failure records, retry thresholds, provider readiness, `.orchestrator/config.json`, runtime JSON, deployment configuration, or product tasks.

## Verification

- Focused identity-bound regression selection: 10 passed.
- Full supervisor suite: 503 passed, 147 subtests passed.
- Full `scripts/test_ai_status.py`: 159 passed, 31 subtests passed.
- Full task review merge-gate suite: 95 passed.
- Python compilation and `git diff --check`: passed after the final evidence refresh.

The machine-readable acceptance mapping, exact test identifiers, owned/not-changing boundaries, and pending review fields are in `evidence.json` beside this file.
