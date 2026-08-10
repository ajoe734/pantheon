# Task Brief: SUP-RUNTIME-V10-PROMOTION-GIT-DIR-ENOTDIR-20260808

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Fix governed promotion ENOTDIR failure on candidate .git directory check
- Status: in_progress
- Owner: Codex
- Reviewer: Claude2
- Next: Human/Ops authorizes source-only investigation and repair of the reproduced candidate .git ENOTDIR descriptor failure. Preserve no-symlink/no-gitfile safeguards; add regression coverage and exact evidence. No sync promotion, live config/process mutation, manual checkout, signal, packet manipulation, or candidate deletion is authorized. Supervisor must dispatch implementation.

## Summary
- The immutable candidate guard was correct: standalone promotion candidates
  must keep a local `.git/` directory and must reject symlinks and gitfiles.
- The bootstrap path incorrectly reused that immutable-only handle for the
  mutable incumbent. Pantheon's `dev-root` is a legitimate linked worktree, so
  its regular `.git` gitfile deterministically raised the reported `ENOTDIR`
  before rollback materialization.
- The repair adds a separate descriptor-bound mutable Git layout capture that
  accepts a regular, stable gitfile, rejects symlinks and hard-linked gitfiles,
  binds the external Git/common directories without following symlink path
  components, verifies canonical remote and trusted dev tree, and rejects
  tracked/index drift while permitting mutable runtime-only untracked files.
- Candidate identity descriptors are also moved above fd 0/1/2 so Git child
  stdio pipes cannot replace `/dev/fd` directory identities.
- Review evidence:
  `docs/deployment/evidence/twelve-loop-gap/SUP-RUNTIME-V10-PROMOTION-GIT-DIR-ENOTDIR-20260808/evidence.json`.

## Acceptance
- Base runtime source reproduces the exact `Candidate Git directory ...
  [Errno 20] Not a directory: '.git'` error against the real `dev-root`.
- Task source binds that same root to HEAD/tree/canonical remote and seven
  governed launch sources without executing a promotion.
- Immutable candidate symlink/gitfile rejection remains unchanged.
- Full promotion and sync contract suites pass.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
