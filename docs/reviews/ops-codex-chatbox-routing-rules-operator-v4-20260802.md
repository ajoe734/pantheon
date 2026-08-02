# OPS-CODEX Chatbox Routing Rules Operator V4 Review Evidence

Task ID: `OPS-CODEX-CHATBOX-ROUTING-RULES-OPERATOR-V4-20260802`

## Review Target

- Repository: `ajoe734/pantheon`
- Merge target: `dev`
- Starting base: `79e02ee059387044eec1d21a283e4848f814f49a`
- Task branch: `task/OPS-CODEX-CHATBOX-ROUTING-RULES-OPERATOR-V4-20260802`
- Pull request: `#4499`
- Exact head: GitHub `headRefOid` bound by governed handoff and approval
- Scope: `AGENTS.md`, this task's brief, and this review evidence only

## Owner Decision And Source Boundary

- The explicit operator constraint is authoritative: configured `Codex` and
  `Codex2` identities may use different accounts.
- The useful coordination and single-dispatch rules from stale PR #4401 are
  retained, but its account-equivalence and reviewer-disqualification paragraph
  is not retained.
- Stale PR #4405 is evidence for #4401 and does not review this corrected head.
- No supervisor, provider, runtime, generated-state, product, service,
  deployment, or task-catalog change is part of this delivery.

## Deterministic Policy Assertions

Run the following from the repository root. Each required fragment represents
a retained governance clause; each forbidden fragment detects the stale or an
equivalent global identity/capacity/reviewer rule.

```python
from pathlib import Path

text = Path("AGENTS.md").read_text()
required = [
    "## Chatbox Work Classification And Dispatch Authority",
    "the operator explicitly authorizes that chatbox to implement the repair",
    "the work is bounded to one clearly identified component or failure",
    "does not redesign cross-component architecture, supervisor",
    "uses a clean task branch or worktree",
    "does not expand the repair into adjacent systems",
    "specifically authorized dashboard failure repair",
    "System-wide inspection, development-progress synthesis, cross-component",
    "handling this lane must not implement the resulting product",
    "inspect current state read-only and deduplicate against active tasks",
    "write a concrete work plan with objective, current evidence, gaps",
    "split the plan into governed task packets with task ID",
    "queue those packets through the governed assistant dev bridge",
    "never by hand-editing queue or state JSON",
    "wait for a supervisor receipt and canonical task materialization",
    "reporting blockers without taking over implementation",
    "Codex extension subagents may perform read-only exploration",
    "must not apply patches,",
    "act as a parallel\nimplementation fleet",
    "Code-writing work belongs to supervisor-dispatched",
    "The Pantheon supervisor is the only routine implementation dispatcher",
    "must not create a parallel scheduling path",
    "or both queue and implement the same",
    "must not directly patch `/home/lupin/pantheon-ci-deploy/dev-root`",
    "The existing Live Repair Rule below is the only exception",
    "must remain minimal and temporary",
    "Configured agent identities, including `Codex` and `Codex2`, remain distinct",
    "must follow\ncurrent configuration and task-scoped live authentication or quota evidence",
    "Do not infer identity equivalence, capacity equivalence, or reviewer",
]
forbidden = [
    "`Codex` and `Codex2` currently share one ChatGPT account",
    "must not be treated as independent capacity",
    "must not be treated as independent review identities",
    "Codex and Codex2 share the same account",
    "Codex and Codex2 are one capacity lane",
    "Codex and Codex2 may not review each other",
]
missing = [fragment for fragment in required if fragment not in text]
present = [fragment for fragment in forbidden if fragment in text]
assert not missing, f"missing required policy fragments: {missing}"
assert not present, f"forbidden policy fragments present: {present}"
print(f"policy assertions passed: {len(required)} required, {len(forbidden)} forbidden")
```

## Owner Validation

- `review_status`: `review_pending`
- `reviewer`: `Human/Ops`
- `reviewed_head`: not yet assigned
- `candidate_policy_commit`: `805b8b7003162197904dbf65768f53417705cc6a`
- `git diff --check origin/dev...HEAD`: passed
- Worktree `git diff --check`: passed
- Deterministic policy assertions: passed, 29 required and 6 forbidden
- `python3 scripts/git/check_commit_trailers.py --range origin/dev..HEAD
  --skip-merge`: passed for the anchor commit
- Runtime mirror guard inputs: passed; no generated-state changes and no
  embedded `execute-plans` mirror
- Exact three-file scope: passed
- Replacement PR publication: passed at preliminary policy head
  `894bff475e85598e8d280724dc4d0657b12a457d`
- Publication-artifact head `8b9ae09c1c4c4dd80bed19ddd61e8623bcc1a0fe`:
  all 8 exact-head GitHub checks passed
- Final review head: use the GitHub `headRefOid` and check results recorded in
  the governed handoff; this manifest intentionally does not self-reference
  its containing commit

## Independent Decision

- Decision: `review_pending`
- Human/Ops must inspect PR #4499's final exact head, confirm this exact
  three-file scope, run or independently verify the deterministic assertions,
  and bind this manifest through the governed approval command with
  `REVIEW_PR=4499` and GitHub's exact `headRefOid` as `REVIEW_HEAD_SHA`.
- Until that approval exists, this document is owner-prepared evidence only and
  does not authorize merge or `done`.

## Supersession And Rollback

- PRs #4401 and #4405 were closed as superseded after #4499 existed, with links
  to the replacement. Their branches and history were not deleted.
- After Human/Ops exact-head approval and governed merge, rollback is a revert
  of the replacement PR merge commit.
