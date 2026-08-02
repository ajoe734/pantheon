# SUP-RUNTIME-IMMUTABLE-CONFIG-DEPLOY-GUARD-OPERATOR-V10-20260802

Status: `review_pending`

Owner: Codex2  
Reviewer: Human/Ops

## Result

Provisioning, `sync-dev-root`, and the dev root deploy path now fail closed
unless supervisor command authority is a standalone, clean
`/home/lupin/pantheon-ci-deploy/command-runtimes/<40-hex-commit>` checkout.
Mutable `dev-root` remains available for staging sync, deployment transport,
dashboard recovery, and status hosting, but it is no longer written into the
watchdog supervisor command.

This task changed source and tests only. It did not run a deploy, invoke
`--promote`, write the live supervisor config, signal a live PID, install a
watchdog, start a candidate, or test rollback against the live VM.

## Immutable admission and config guard

`provision_live_supervisor_config.py` delegates root admission to the merged
promotion identity layer. The root must be a direct child of the canonical
runtime parent, have a lowercase full-SHA basename equal to `HEAD`, have the
same tracked tree fetched independently from accepted `dev`, use the trusted
Pantheon remote identity, have a clean tree/index, and expose regular required
entrypoints. Both watchdog shell entrypoints must be executable.

The local `origin/dev` ref is not trusted for admission. Tests deliberately
make it stale while the independently fetched accepted-dev identity remains
current. Mutable, symlinked, unaccepted, and dirty roots are rejected.

Direct config provisioning is now first-install or no-op only:

- when no live config exists, the admitted immutable root may create it with
  file and parent-directory fsync;
- when an existing config renders identically, it is preserved without inode
  replacement;
- when an existing config differs, provisioning refuses to prewrite it and
  requires the governed promotion transaction.

## Sync behavior

`sync-dev-root.sh` still fetches the exact remote-tracking `origin/dev` ref. It
may stash/reset the mutable staging checkout only when that checkout is not the
active supervisor cwd. A current mutable incumbent is fetched but neither
stashed nor reset before bootstrap capture. A split immutable active root is
never fetched, stashed, reset, or directly updated.

When the accepted target differs, sync materializes a standalone candidate
under `command-runtimes/<target-sha>`, refreshes its local accepted-dev ref,
installs it with no-replace rename plus directory fsync, and runs the same
immutable validator. It then invokes the candidate's promotion wrapper:

- `--promote --repo <candidate>` for an immutable incumbent;
- the same command plus `--bootstrap-mutable-incumbent` for the one-time
  mutable incumbent path delivered by PR #4524.

Sync contains no direct intentional-restart write and no `TERM`. Promotion
handoff failure leaves the test incumbent alive. A current immutable root with
no config drift is a no-op. Same-root drift fails closed without prewriting the
config or attempting a same-root promotion.

## Dev deploy behavior

The root deploy materializes or reuses the exact `PANTHEON_DEPLOY_SHA` under
the immutable runtime parent and revalidates it before supervisor work. A
missing live config is treated as first install only after both the canonical
PID marker and procfs scan disprove a live supervisor incumbent. An existing
different root is handed to normal or mutable-bootstrap promotion; an exact
current root is accepted only when provisioning is a config no-op. The final
drift check is read-only and never uses `--fix`.

Watchdog installation and health run from the admitted immutable root.
Dashboard autostart still runs its status-hosting recovery against the
canonical status root, and managed BFF/build worktree isolation remains
unchanged.

## Verification

- Focused immutable provisioning, sync, deploy, and isolation matrix:
  `43 passed`.
- Promotion, runtime-health, immutable provisioning, sync, drift, watchdog,
  and deploy qualification matrix: `349 passed`.
- AI status and core supervisor matrix: `744 passed, 189 subtests passed`.
- Python compilation, shell syntax, commit-trailer range validation, and
  `git diff --check`: passed.

A supplemental run including `scripts/test_status_command_runtime_pin.py`
reported `354 passed`, `14 subtests passed`, and two failures:

- `test_active_lease_allows_mutation_and_preserves_worktree`
- `test_concurrent_worktree_writers_keep_final_state_and_event_ids`

Both fixtures omit the exact process generation now required by
`ai_status.py`. The merged PR #4524 evidence records the same two non-task
baseline failures. This task changes neither `scripts/ai_status.py` nor
`scripts/test_status_command_runtime_pin.py`; the failures are disclosed and
not counted as green qualification.

## Delivery and governance boundary

The source commits are:

- `135ff591e` — provisioning/sync/deploy handoff anchor;
- `8bd047615` — negative admission and first-install anchor;
- `9230f4baa` — no-replace runtime installation and config durability.

No supervisor scheduling, canonical status JSON, queue/lease policy, provider
readiness, provider homes, account/quota grouping, Human/Ops authority, or
Codex/Codex2 identity relationship changed. No global mutual-review rule was
added.

Human/Ops must review the final exact PR head and bind `evidence.json` before
merge. Green checks alone are not review authority. Rollout remains blocked to
the separate V9 canary, which must select a candidate containing the merge and
perform the actual transactional promotion, ten-cycle acceptance, and rollback
proof. Source rollback is a normal revert of this task PR before that canary.
