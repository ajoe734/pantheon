# SUP-L12-MERGED-ROW-RECONCILE-20260729 — merged-but-nonterminal L12 row reconcile

Owner: `Claude2` · Reviewer: `Antigravity` · Phase: Twelve Loop Remediation /
Wave 0 Closeout Reconcile

Source audit:
`docs/04/pantheon_twelve_loop_gap_2026-07-26/archive/THREE_PASS_GAP_AUDIT_2026-07-29T1025Z.md`
(Wave 0 item 2).

This is a reconcile packet, not a completion claim. It inventories every L12 /
SUP-L12 canonical row whose delivery is already merged into `dev` but whose row
is still nonterminal, classifies each one by the *reason* it is nonterminal, and
produces immutable merged evidence only for the row that genuinely cannot be
recovered by normal owner closeout.

No implementation was restarted. No already-merged commit was rewritten.

## Result in one line

Exactly one L12 row is stranded: `L12-MANIFEST-REVIEW-GAP-TASKS-20260729`. Its
recovery evidence is `reconcile/L12-MANIFEST-REVIEW-GAP-TASKS-20260729.md`, and
the real governed gate that guards the recovery passes against it.

## Inventory method

Two independent sweeps, so a naming mismatch in one cannot hide a row from both:

1. **PR-side.** Every merged PR in `ajoe734/pantheon` (last 200), mapped from
   `task/<id>` head branch to a canonical row, then filtered to rows that are
   not `done` and not archived.
2. **Commit-side.** For every nonterminal row in the canonical status file,
   `git log --grep '<task-id>' origin/dev` — this catches deliveries whose
   branch name never matched the task id.

Both sweeps returned the same three rows.

```bash
gh pr list --state merged --limit 200 --json number,title,headRefName,mergedAt,mergeCommit
git log --oneline --grep '<task-id>' origin/dev
"$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh" show '<task-id>'   # AI_NAME=Claude2
```

Canonical state read at `origin/dev` = `715308c381a73b80cfe974689f00b4a42877255f`,
command root `/home/lupin/pantheon-ci-deploy/dev-root` at
`c1e396495d37a1c9dfeea5704e7eb73db6acde0e`.

## Merged-but-nonterminal L12 rows

| Row | Status | Owner / Reviewer | Merged delivery | Why nonterminal | Disposition |
|---|---|---|---|---|---|
| `L12-MANIFEST-REVIEW-GAP-TASKS-20260729` | `blocked` | Antigravity / Claude2 | PR #4330, head `7b68b423590855ea8d39ea718103b29a612a948a`, merge `d9cbbbfa2b0d4076f939a6d0fcc921406993d7af` | Immutable merged commit trailers say `LLM-Agent: Codex` / `Reviewer: Antigravity`; the row is `Antigravity` / `Claude2`. `done` fails closed permanently. | **Stranded → `reconcile_merged_done`.** Evidence produced here. |
| `L12-FLEET-STATUS-SYNC-001` | `review_approved` | Codex / Antigravity | PR #4282, head `e806affaa279f8b9d4b41bae6117a9431c99b90e`, merge `a0020c5ac50e510467a5e80c412c7703245cf4dd` | The merged head carries `Reviewer: Codex2`, not the canonical `Antigravity`. The owner already cut closeout PR #4297 whose head `38057216e8e2a02f2acb3f375a119286af6e01b2` carries the *correct* trailers. #4297 is `OPEN`/`BEHIND`. | **Not stranded.** Normal owner closeout works once #4297 is refreshed onto `dev` and merged. Do not reconcile. |
| `SUP-L12-FLEET-RUNTIME-RELIABILITY-20260729` | `review` | Codex / Codex2 | PR #4333, head `a099f7e1b`, merge `5b3bc8aa82e91b422a8bb1cc0c63a5960a0a362a` | Merged head subject `SUP-L12-FLEET-RUNTIME: correct report` omits the full task id and its `LLM-Agent: Antigravity` does not match owner `Codex`; but the row is in an *active* review with follow-on PR #4363 open. | **Not stranded.** Row is mid-review, not closeout-drifted. Reconcile would archive work that is still being reviewed. Leave to the review lane. |

### Why only the first one qualifies

`reconcile_merged_done` is a recovery path, not a shortcut. Applying it to a row
that still has a live, mergeable closeout route would archive the row and
permanently kill the reviewer's ability to act on the open PR. Rows 2 and 3 both
have a live route, so they are deliberately left alone. This distinction is the
substance of the task; producing three evidence files would have been the wrong
answer.

## Why `L12-MANIFEST-REVIEW-GAP-TASKS-20260729` cannot be closed normally

`scripts/ai_status.py` derives expected trailers from the *current* row and
matches them against the worktree HEAD it is closing:

```python
expected_fields = {
    "LLM-Agent": actor,
    "Task-ID": task_id,
    "Reviewer": canonical_agent_name(task.get("reviewer")),
}
```

The merged delivery `7b68b423590855ea8d39ea718103b29a612a948a` says
`LLM-Agent: Codex` / `Reviewer: Antigravity`; the row says owner `Antigravity`,
reviewer `Claude2`. The commit is on `dev` and immutable, so the mismatch cannot
be repaired. Two further doors are shut:

- the row is `blocked`, which `command_done` does not accept;
- `command_approve` refuses any row not in `review`, so the reviewer cannot
  re-approve into a clean state either.

`reconcile_merged_done` accepts `blocked` directly and validates merged evidence
plus merged delivery instead of trailers. It is the designed path here.

The independent review that the recovery relies on is real and was verified
outside canonical state — the `Pantheon canonical review gate` commit status
`51263870393` on head `7b68b4235908`, `approve by Claude2`, posted
`2026-07-29T02:40:50Z`. The row's own
`github_review_bridge.review_error = "gh: Unprocessable Entity (HTTP 422)"`
explains the drift: the GitHub pull-request-review API call failed while the
required commit status — the gate Pantheon actually enforces — succeeded.

## Evidence file placement (deliberate)

`reconcile/L12-MANIFEST-REVIEW-GAP-TASKS-20260729.md` is **not** under
`.orchestrator/task-briefs/`.

`supervisor.py` treats `.orchestrator/task-briefs/` as regenerable scratch
(`_REUSABLE_DIRTY_PREFIXES`) and rewrites those files from the live row on every
dispatch via `_generated_worker_task_brief`. That regeneration rewrites the
`- Status:` line. `validate_merged_done_evidence` requires the on-disk file to be
byte-identical to its merged commit *and* to contain a literal
`^- Status: review_approved$`. So a brief stored under that prefix is guaranteed
to break both gates the moment the supervisor touches it again — which is
observable right now as modified brief files in the live command root.

The evidence directory is outside every regenerated prefix, so the blob stays
byte-stable. The gate imposes no directory requirement, only a tracked,
repo-relative, non-symlinked path.

## Verification

`verify_reconcile_evidence.py` imports `scripts/ai_status.py` and calls the real
`validate_merged_done_evidence`. It reimplements no gate, because a
reimplementation can drift from the governed validator and still print success.
The module it imported is byte-identical to the live command root's copy
(`sha256 4524bb9f261638695f9972cd468e71793d92e888409f5a42a68d0ab8818c314b`),
recorded in the script's own output.

### Positive run (preflight)

```bash
python3 docs/deployment/evidence/twelve-loop-gap/SUP-L12-MERGED-ROW-RECONCILE-20260729/verify_reconcile_evidence.py \
  --task L12-MANIFEST-REVIEW-GAP-TASKS-20260729 \
  --evidence-file docs/deployment/evidence/twelve-loop-gap/SUP-L12-MERGED-ROW-RECONCILE-20260729/reconcile/L12-MANIFEST-REVIEW-GAP-TASKS-20260729.md \
  --evidence-commit "$(git rev-parse HEAD)" --evidence-target-ref HEAD \
  --delivery-commit 7b68b423590855ea8d39ea718103b29a612a948a \
  --command-root "$PWD" --delivery-root "$PWD"
# result=PASS, exit 0
```

**Disclosed substitution.** `--evidence-target-ref HEAD` replaces `origin/dev`
for the *evidence* ancestry check only, because the evidence file cannot be an
ancestor of `dev` before this PR merges. Every other gate ran unmodified,
including the delivery ancestry check against the real `origin/dev`. The script
labels this run `"mode": "preflight"` and emits an explicit
`preflight_substitution` field, so the substitution cannot be read as a full
production pass. After merge the same command with the merged evidence commit
and no `--evidence-target-ref` is the production run.

### Negative controls

A validator that only ever prints PASS proves nothing. Four falsifiers, each
returning exit 1 with a distinct gate message:

| Control | Gate message |
|---|---|
| evidence path that does not exist | `RECONCILE_EVIDENCE_FILE is not a regular file: …/DOES-NOT-EXIST.md` |
| delivery commit not merged to `dev` | `Cannot reconcile task: delivery commit 7e2d0ed06… is not merged into origin/dev.` |
| same evidence pointed at the wrong task row | `Cannot reconcile task: merged evidence does not bind the canonical task, owner metadata.` |
| evidence commit predating the file | `Cannot reconcile task: evidence file is absent from the supplied evidence commit.` |

## Operator recipe (`Human/Ops` only)

Only `Human/Ops` may run `reconcile_merged_done`; `command_reconcile_merged_done`
rejects every other actor. The owner of this task cannot execute the recovery,
only produce and prove the evidence it consumes.

**Precondition that is easy to miss.** `validate_merged_done_evidence` resolves
`RECONCILE_EVIDENCE_FILE` against `ROOT = Path(ai_status.py).resolve().parents[1]`
— the *command root*, which is a detached checkout at
`PANTHEON_COMMAND_RUNTIME_SHA`, currently `c1e39649`. A file merged to `dev`
after that SHA is untracked there and the command aborts on the first gate. The
command root must be promoted to a `dev` commit containing the evidence file
first. Check, do not assume:

```bash
git -C "$PANTHEON_COMMAND_ROOT" ls-files --error-unmatch -- \
  docs/deployment/evidence/twelve-loop-gap/SUP-L12-MERGED-ROW-RECONCILE-20260729/reconcile/L12-MANIFEST-REVIEW-GAP-TASKS-20260729.md
```

Then:

```bash
AI_NAME=Human/Ops \
RECONCILE_EVIDENCE_FILE=docs/deployment/evidence/twelve-loop-gap/SUP-L12-MERGED-ROW-RECONCILE-20260729/reconcile/L12-MANIFEST-REVIEW-GAP-TASKS-20260729.md \
RECONCILE_EVIDENCE_COMMIT=<dev commit carrying that file> \
RECONCILE_DELIVERY_REPOSITORY=ajoe734/pantheon \
RECONCILE_DELIVERY_ROOT=/home/lupin/pantheon-ci-deploy/dev-root \
RECONCILE_DELIVERY_COMMIT=7b68b423590855ea8d39ea718103b29a612a948a \
"$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh" reconcile_merged_done \
  L12-MANIFEST-REVIEW-GAP-TASKS-20260729 \
  "Reconcile merged PR #4330 delivery to done; owner closeout is blocked by immutable commit trailers naming Codex/Antigravity while the row is Antigravity/Claude2."
```

Dry-run the exact gate before running the real command:

```bash
python3 docs/deployment/evidence/twelve-loop-gap/SUP-L12-MERGED-ROW-RECONCILE-20260729/verify_reconcile_evidence.py \
  --task L12-MANIFEST-REVIEW-GAP-TASKS-20260729 \
  --evidence-file docs/deployment/evidence/twelve-loop-gap/SUP-L12-MERGED-ROW-RECONCILE-20260729/reconcile/L12-MANIFEST-REVIEW-GAP-TASKS-20260729.md \
  --evidence-commit <dev commit carrying that file> \
  --delivery-commit 7b68b423590855ea8d39ea718103b29a612a948a
# mode=production, result=PASS expected before invoking reconcile_merged_done
```

## Adjacent finding — merged L12 branches with no canonical row at all

Out of scope for this task (there is no row to reconcile, and
`reconcile_merged_done` requires one), recorded because the inventory surfaced it
and it is invisible to any row-based audit.

Ten merged `task/*` branches carry an L12 / SUP-L12 identifier that resolves to
neither an active row nor an archive entry under the governed `show`:

`L12-GAP-DOC-DISPATCH-20260729`, `L12-GAP-RUNTIME-FLEET-AUDIT-20260729`,
`L12-GAP-FINAL-FLEET-DISPATCH-20260729`,
`L12-GAP-CURRENT-THREE-PASS-DISPATCH-20260729`,
`L12-GAP-TRIPLE-AUDIT-DOC-20260728`,
`L12-MANIFEST-FINAL-CLOSEOUT-20260729-035014`,
`SUP-L12-WAVE0-PREEMPTION-PROTECT-20260729`,
`SUP-L12-URGENT-ONLY-PREEMPTION-20260729`,
`SUP-L12-PREFERRED-LANE-FALLBACK-20260729`,
`SUP-L12-PREFERRED-LANE-HELPER-CLAIM-20260729`.

Three further rowless branches are *not* a gap — they are auxiliary closeout or
reconcile branches whose parent task is archived:
`SUP-L12-REVIEW-PRIORITY-GATE-CLOSEOUT-EVIDENCE-20260729`,
`L12-MANIFEST-RESTART-PROOF-RECONCILE-20260729`,
`SUP-L12-OPEN-PR-DRAIN-RECONCILE-20260729`.

This needs a `Human/Ops` decision on whether rowless delivery is acceptable for
supervisor-hygiene branches, not a worker action.

## Boundary

Changed: this evidence directory only. Not changed: `.orchestrator/config.json`,
supervisor or dispatch code, any canonical status/archive file, any already
merged commit, any other task's row, and every `.orchestrator/task-briefs/` file.
