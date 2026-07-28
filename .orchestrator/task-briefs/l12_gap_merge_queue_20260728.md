# Task Brief: L12-GAP-MERGE-QUEUE-20260728

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Claude-priority merge queue for L12 closeout PRs: handle #4286 exact-head reapproval path, #4285 reviewer gate, #4290 closeout review/merge, and safe root-freeze/merge only when gates are green.
- Status: in_progress
- Owner: Codex
- Reviewer: Codex2
- Next: PR #4285 exact head `0fc918b747cf38262360b6045dadd25f157ed9d9` composes current `dev` `d97c25d3cc8860118dd4d0f3c9fafd38490d89c0` and both Branch CI runs are green. A task-scoped L12-EVO worker must hand this exact head to Codex2; only an ensuing exact-head approval may release the canonical review and root-freeze contexts before merge.

## Summary
- Drain the protected L12 closeout PR queue one exact reviewed head at a time.
- The temporary `Pantheon root merge freeze 2026-07-27` required context is
  released only after the assigned reviewer has approved the current PR head
  and all mechanical checks are green.
- Do not restart implementation, fabricate approval, enable auto-merge, force
  push, change branch protection, or reuse an approval/check from an older
  head.

## Source Task Contract
- Source graph:
  `docs/deployment/evidence/twelve-loop-gap/L12-THREE-PASS-GAP-AUDIT-20260728/execution-tasks.json`
- Original queue acceptance covers PRs `#4218`, `#4285`, `#4286`, and `#4287`.
- Human/Ops extended the active queue to audit closeout PR `#4290`, provider
  readiness PR `#4293`, and its closeout PR `#4299`; every head remains subject
  to the same exact-head review and root-freeze rules.
- Task artifact:
  `docs/deployment/evidence/twelve-loop-gap/L12-THREE-PASS-GAP-AUDIT-20260728/merge-queue/evidence.json`

## Queue Snapshot
- `#4218`: exact head
  `c9dea98fc03a24c67e52edc78f9802854e23a33c` merged as
  `9e4bb8e1fa9495d8802da58336b05ae68c7756ad`.
- `#4287`: exact head
  `c4c8ca256b3139ec1e32032e523b328f727eb10b` merged as
  `091ac6978d03e5fc31d7d6bf4967fe842101cf1c`.
- `#4286`: exact head
  `e91382b508b42456d75747fdf3cef92c7850d2ad` was independently approved,
  root-released, and merged as
  `cf94be38a548a31df020456904ea10ff95ffb4dd`.
- `#4290`: exact head
  `f77358301fdecd93de26c4ca96bc688e5ab2b969` was independently approved,
  root-released, and merged as
  `65802d99bf5ddca1213f6742af74dc125216fa82`; its canonical task is done.
- `#4293`: exact head
  `7c2ad997c3e42b08ee4b2a77df6ca9105992a1e1` was independently approved,
  root-released, and merged as
  `748d5b34a8a5c23edf75a82e36d43f2ac867a459`.
- `#4299`: provider-readiness closeout head
  `0abd9c33a5872e9655bb981f7b3177eb6beb6b5d` was independently approved,
  root-released, and merged as
  `ba727a676856e77c7b723f33f5b9e7ceb5ce1392`; its canonical task is done.
- `#4285`: remains open at
  `0fc918b747cf38262360b6045dadd25f157ed9d9`. The head composes current `dev`
  `d97c25d3cc8860118dd4d0f3c9fafd38490d89c0`, is zero base commits behind
  and six task commits ahead, and both fresh Branch CI runs passed Commit
  trailers, Runtime mirror guard, Python packaging provision, and Smoke
  acceptance. GitHub reports `BLOCKED` because the canonical review and
  root-freeze contexts are absent. Root release remains withheld until the
  L12-EVO task-scoped handoff and Codex2 exact-head approval.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
