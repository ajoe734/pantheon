# Handoff preflight

`scripts/git/handoff_preflight.py` runs the mechanical delivery gates locally,
before an owner hands off for review.

## Why it exists

Between 2026-09-05 14:00Z and 2026-09-06 06:30Z, fourteen owner→review→reopen
round trips were observed. Eleven were mechanical or procedural, not judgement:

| Rejection cause | Times | Detectable before handoff |
| --- | --- | --- |
| Missing commit trailers | 2 | yes |
| Target-runtime syntax (PEP 701 f-string on Python 3.11) | 1 | yes |
| Packet acceptance naming agents instead of roles | 3 | yes |
| Dependency already archived read as unresolved | 1 | yes (defect) |
| Changed files outside the declared artifact contract | 1 | yes |
| Evidence manifest citing a commit that does not resolve | 1 | yes |
| Delivered scope short of the contract | 3 | partly |
| Defects only a reviewer can find | 3 | no |

Each round trip costs a full owner dispatch, a full review dispatch, and a
supervisor lease cycle. The reviewer time spent re-deriving a missing
`Task-ID:` trailer is time not spent finding the cross-tenant authorization
break that the same reviewer found on PR #5620 — where CI was green and 257
tests passed.

This tool does not judge whether a delivery is correct. It judges whether the
delivery is *submittable*, so review capacity reaches the defects that need it.

## Usage

Inside a worker (the status runtime is already bound):

```bash
python3 scripts/git/handoff_preflight.py \
  --task-id BFF-TEST-ARCH-001 \
  --base origin/dev --head HEAD
```

Outside a bound runtime — `ai_status show` requires
`PANTHEON_TASK_STATE_STORE_MODE=authoritative`, so supply the row instead:

```bash
python3 scripts/git/handoff_preflight.py \
  --task-id BFF-TEST-ARCH-001 --task-json /tmp/task.json \
  --base origin/dev --head HEAD --repo /path/to/worktree
```

Exit codes: `0` clean, `1` at least one gate failed, `2` the gate could not run
(unreadable task row, bad revision range) — never conflate `1` and `2`.

## Checks

1. **commit-trailers** — applies `check_commit_trailers.check_message`, the same
   validator the required *Branch CI Gate* job runs, to every non-merge commit
   in `base..head`. Reuses that module rather than restating the rules, because
   two definitions of a required gate drift.
2. **artifact-scope** — every changed file must match a declared artifact.
   `services/x/**` authorizes a subtree, `services/x/test_*.py` and
   `services/x/*/test*.py` are segment-local globs, a bare path is exact. A file
   outside the contract is named individually.
3. **evidence-manifest** — the declared manifest must exist, parse as JSON, and
   every 40-hex commit id it cites must resolve in this repository or in a
   configured sibling checkout. Missing sibling checkouts are listed in the
   failure, so "wrong commit id" is distinguishable from "that repository is not
   checked out here".

## Known limits

- Delivered-scope completeness is not checked. Whether five declared router
  domains were all decomposed is a judgement the reviewer still owns.
- Sibling repository paths come from the coordination registry. On this host
  `execute_plans` resolves to `<status-root>/../code/execute-plans`, which does
  not exist — the real checkout is a sibling of `pantheon`. Until that entry is
  corrected, paired-delivery evidence citing an execute-plans head is reported
  as unresolvable, with the missing checkout named in the failure detail.
- The target-runtime check is not implemented here. Run focused tests under the
  pinned interpreter (`python:3.11-slim`) as a separate step; a worker's local
  3.12+ interpreter accepts syntax the runtime rejects.
