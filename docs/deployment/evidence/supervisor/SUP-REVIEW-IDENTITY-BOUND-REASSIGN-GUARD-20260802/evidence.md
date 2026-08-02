# SUP-REVIEW-IDENTITY-BOUND-REASSIGN-GUARD-20260802 Evidence

Status: implementation complete; pending Gemini exact-head review.

## Result

Automatic recovery now preserves the designated reviewer when a review has an exact-head binding or a pending reviewer handoff. Unbound recovery remains available, but the candidate and persistence gates both require a genuinely independent reviewer identity.

Codex and Codex2 are treated as one ChatGPT review identity. A Codex-owned task therefore cannot be approved by Codex2, and an alternate-label exact-head approval cannot make the PR merge-ready or auto-merge-ready.

## Incident boundary

- PR #4414 / merge `dc5136394eb1041ceea1dcc066e55ac2179ca0e5` is referenced only for the existing handoff/identity continuity baseline.
- PR #4445 / merge `2350e4e85c69009b52e7fbb75e621b04f83f66c9` is referenced only for the reproduced reviewer-replacement incident and current V3 baseline.
- This task does not change V3 failure records, retry thresholds, provider readiness, `.orchestrator/config.json`, runtime JSON, deployment configuration, or product tasks.

## Verification

- Focused identity-bound regression selection: 9 passed.
- Full supervisor suite: 490 passed, 74 subtests passed.
- Full `scripts/test_ai_status.py`: 159 passed, 31 subtests passed.
- Full task review merge-gate suite: 94 passed.
- Python compilation and `git diff --check`: passed before evidence authoring; both are rerun during finalization.

The machine-readable acceptance mapping, exact test identifiers, owned/not-changing boundaries, and pending review fields are in `evidence.json` beside this file.
