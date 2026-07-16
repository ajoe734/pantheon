# Task Brief: LOOP-PROD-RUNTIME-BOOT-CORRECTIVE-001

> Temporary coordination routing: until
> `OPS-WORKTREE-CENTRAL-STATUS-ROOT-POSTMERGE-001` is accepted, every owner or
> reviewer working this task must run governed state, review, handoff and
> closeout commands through `/home/lupin/code/pantheon/scripts/ai-status.sh`
> with its own real identity (`AI_NAME=Claude` for the owner or
> `AI_NAME=Codex2` for the reviewer).
> Do not use the task-worktree wrapper for state. Git and tests stay in the
> task worktree. Verify with central `show`.

## Responsibility

Owner Claude prepares evidence and hands off. Reviewer Codex2 alone performs
the independent completion review and protected signing. The owner must not
self-sign or fabricate completion.

## Current accepted fact

PR #3738 merged as
`8c9bc96e5e8728a2340355b9357355d0c7368ff2` and restored the exact dropped
append-only audit row. That merge is necessary but not sufficient.

PR #3742 later merged as
`6a24a3ebb36259a4259ccd2dcdc053826eb4e1d5`, but its merged
`corrective-001-checks.json` is not accepted: it still says PR #3742 is
`OPEN` and binds source inventory to `42acecd2b`, not final PR head
`5ba2c8808` or merge SHA `6a24a3ebb`. Treat that file as a stale draft.

## Remaining required result

Create a follow-up evidence candidate that:

- corrects the disabled old reviewer name `Claude2` to assigned reviewer
  `Codex2`;
- binds checks to the exact final candidate, restored audit blob, writer
  registry digest, complete source inventory, full validation results, and
  zero unregistered direct writers;
- records PR ancestry and the exact merged diff;
- is regenerated after composing current `origin/dev`, records PR #3742 as
  merged, and binds every hash/test result to the new follow-up PR's exact
  final head and merge ancestry;
- leaves live install/apply and 48-task materialization out of scope.

Then hand off to Codex2. Codex2 must either create a real protected
Ed25519-signed completion plus ledger/policy/revocation binding and formally
review the follow-up PR, or leave the task open with the exact unavailable
signing authority. GitHub green checks, a README correction, placeholder
signature, owner signature, or status-only approval are not completion.
