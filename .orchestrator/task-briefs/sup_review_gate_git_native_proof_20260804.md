# SUP-REVIEW-GATE-GIT-NATIVE-PROOF-20260804

Status: proposed
Owner: Claude
Reviewer: Human/Ops (independent from owner; see PR #4541/#4543 rollout note)
Depends on: SUP-REVIEW-PIPELINE-INTEGRITY-20260804 (#4541)

## Problem

#4541's `canonical-review-gate.yml` (part C of the original four-part fix)
tried to guarantee the `Pantheon canonical review gate` required check by
re-deriving review policy inside a GitHub Actions runner, via
`task_review_merge_gate.py::load_task_contract()` reading `ai-status.json`
out of the workflow's own checkout. That is structurally wrong: a
GitHub-hosted runner is a fresh clone with no access to this host's live
task board or its git-external authoritative event log
(`task_state_store.event_log`, an absolute host path outside the repo). The
checkout only ever sees whichever `ai-status.json` snapshot last happened to
be committed -- in practice, essentially never the live state of an
in-flight task.

Consequence, confirmed live: the check reported `task_state_unavailable` for
*every* task, including `OPS-GITHUB-CANONICAL-REVIEW-ENFORCEMENT-001`, which
had already gone through a real independent approval. Because it was a
required context with no exemption, this made the gate permanently
unsatisfiable for the whole fleet's future merges. Removed from `dev`'s
required-checks list the same day as an emergency mitigation (see #4541
follow-up activity, 2026-08-04 ~14:00Z).

## Fix

Stop trying to see live state from CI at all. Represent "this exact head was
approved" as a fact in the repository's own object graph instead of a fact
in a JSON file:

- `scripts/git/github_review_bridge.py::_push_review_proof_tag()`: on a
  governed `approve`/`reopen` decision, pushes an annotated git tag at the
  exact reviewed head SHA via GitHub's Git Data API (`git/tags` +
  `git/refs`) -- `refs/tags/pantheon-review/<decision>/<head-sha>`. Only
  runs after the existing review/status paths have confirmed the decision is
  real (unchanged fail-closed contract); idempotent on retry. This runs on
  the Pantheon host, at decision time, where the real state already lives --
  no new infrastructure.
- `scripts/git/canonical_review_gate_ci.py` (rewritten): the Action now asks
  exactly one question, answerable purely over the GitHub API with no
  checkout-dependent state: does
  `refs/tags/pantheon-review/approve/<current-head-sha>` exist? A tag is
  part of the repo's object graph; any `gh api` caller sees it immediately,
  including this runner. The workflow no longer needs `fetch-depth: 0` or
  `gh pr view`.
- `.github/workflows/canonical-review-gate.yml`: simplified to match --
  shallow checkout (only needed to run the script's own source), single
  `gh api` lookup, post success/failure.

**Caught during live verification, not left for later**: the first draft of
both the bridge's existence-check and the Action's lookup built the wrong
GitHub API URL -- percent-encoding `refs/tags/` itself instead of leaving it
as a literal path prefix and encoding only the tag name's internal slashes.
That 404s even when the tag exists. Found by pushing a real tag via `gh api`
and running the real (non-mocked) script against it before trusting any of
this, exactly the step skipped before #4541's v1 shipped. Fixed in both
files; re-verified live (create, idempotent retry, and the CI-side lookup)
against a real commit before this PR was opened.

## Explicitly not done in this task

`Pantheon canonical review gate` is **not** re-added to `dev`'s required
status checks by this PR. Given today's history with this exact context,
re-requiring it deserves one more confirmation after this merges and
`dev-root`/the supervisor pick it up: open one real PR, run a real
`ai-status.sh approve` against it, confirm the tag gets pushed and the
Action reports success on the real (not manually-crafted) artifact, *then*
re-add the required-check entry as a separate, deliberate step.

## Test plan

- `pytest scripts/git/test_github_review_bridge.py` -- 8 passed (2 new:
  idempotent retry, reopen uses a distinct tag namespace)
- `pytest scripts/git/test_canonical_review_gate_workflow.py` -- rewritten,
  17 passed (tag-existence lookup, exact-head-only-counts regression case,
  dry-run wiring, a mocked-subprocess test of the one function that actually
  shells out)
- `pytest scripts/git/test_task_review_merge_gate.py
  scripts/git/test_task_pr_triage.py scripts/git/test_git_workflow_helpers.py`
  -- 208 total across the touched directory, zero regressions
- `pytest scripts/test_ai_status.py` -- 169 passed, 31 subtests, zero
  regressions (this task does not touch `GITHUB_REVIEW_MODES` / the
  evidence-matching validation surface at all, deliberately, to avoid
  widening that separately audited integrity check)
- `pytest .orchestrator/test_github_bus.py` -- 28 passed, unaffected
- Live, non-mocked verification against `ajoe734/pantheon`: pushed a real
  tag via `gh api`, ran the real `canonical_review_gate_ci.py` against it
  (success), against an unrelated SHA (failure), and exercised
  `_push_review_proof_tag` end-to-end including the idempotent-retry path,
  all before opening this PR. Tags created during verification were deleted
  afterward.
