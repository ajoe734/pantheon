# Task Brief: OPS-WORKTREE-CENTRAL-STATUS-ROOT-POSTMERGE-001

> Until this task proves the live install, owner Claude and reviewer Codex2
> must run all governed state, review, handoff and closeout commands through
> `/home/lupin/code/pantheon/scripts/ai-status.sh` with their own real
> `AI_NAME`. Git, deploy inspection and test commands stay in the task
> worktree. Do not trust the worktree-local board before acceptance.

Wait for `OPS-WORKTREE-CENTRAL-STATUS-ROOT-CORRECTIVE-001` to merge. Owner
Claude installs that exact merge into the Pantheon dev supervisor/runtime;
reviewer Codex2 independently verifies.

Capture pre-install process identities, source SHA and hashes. Apply only the
merged coordination-root change using the normal dev runtime deployment
procedure, restart only the required supervisor/worker services, and verify
the exact merged SHA is running.

Launch a disposable no-product-change test task in an isolated worktree with a
deliberately conflicting stale local board. From that worker, run governed
`show`, `note` and owner-to-reviewer `handoff`. Prove the central board and
central activity log receive the events exactly once, central locks/outbox are
healthy, and every worktree-local coordination file remains byte-identical.
Also prove a normal read-only git/test command executes inside the worktree,
not the central checkout.

Publish redacted before/after hashes, process/source identity, command output
and cleanup evidence through an evidence-only reviewed PR. Do not expose
secrets, mutate product code, or use the disposable task to bypass real task
review requirements.
