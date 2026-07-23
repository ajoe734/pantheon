# Task Brief: AG-COMPAT-002-GATE

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Finalize Agora cross-repository compatibility gate
- Status: in_progress
- Owner: Codex2
- Reviewer: Codex
- Next: Auto-reassigned AG-COMPAT-002-GATE away from unavailable lane Claude (disabled, paused, sidecar-only, or auth-down); owner Claude -> Codex2.

## Summary
把 pending/zero placeholder manifest 換成 exact FE/BFF pair，部署前驗證 commits/hashes/dev reachability，失配 gate-before-switch 並測 rollback。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

## Implementation Record (2026-07-23, review pending)

- Anchor commit `2977fcc060ba9226af8e8587a5c7cb99b3a20844`
  replaces the v1.8 pending/zero manifest with the exact accepted v1.13 pair.
- Backend identity: runtime
  `6e08b040eebd2c317a9b44741d8badbf878e26ad`, contract
  `9e909de182f9f2379d23e8e6b81eefec29ffbce7`, machine handoff
  `d8d7c7690068d8a5223b747cd2284eb9f4da2b5e`.
- Frontend identity: runtime
  `c76a838342b08331849f994d8f756155d2e3b961`, machine handoff
  `580a668570f0e85eb0aee4a953a411f25c43340e` (merged into execute-plans
  `dev` by `8fb854ef4036271ffb74147206a5e2cbf50ffdce`).
- Exact hashes: bundle
  `b1d488c3b35aa1c691e5b464362ac5a2fdd1efc442249e15be9bb143f379f870`,
  OpenAPI
  `36d1be5bc033ea1a55610f3f523fc478704fdfad1f06fec620e741bed9bf6f86`,
  generated types
  `5ef7b5752304b65f041f2e5a724f0e16f28d4a1db36484eefc2cf0c8b77c092e`.
- The verifier reads both handoffs and generated artifacts from the named Git
  commits, requires all runtime/contract/handoff commits to be ancestors of
  their protected `dev` refs, compares current checkout bytes with the accepted
  handoff/contract bytes, and permits deployment only for status `accepted`
  with no blockers.
- `nonprod-deploy.yml` checks out execute-plans `dev` and runs the gate before
  environment-lease acquisition and the deploy command. It exposes no
  `--allow-pending` accepting path.
- Focused tests cover the positive pair; tampered type, OpenAPI, bundle,
  contract-commit, and handoff values; unreachable frontend runtime/handoff
  commits; pending/rejected no-switch behavior; workflow ordering; and rollback
  restoration of the prior accepted symlink/manifest pair.

Verification:

- `/home/lupin/pantheon/.venv/bin/python -m pytest -q
  scripts/test_agora_compat_manifest.py
  docs/contracts/agora/test_generate_backend_contract.py
  scripts/test_dev_environment_lease_deploy_contract.py
  scripts/test_dev_project_cutover_contract.py
  scripts/test_loop_prod_tel_002_hosted_workflow.py
  scripts/test_check_shared_deploy_workflow_disabled.py` -> 47 passed.
- `python3 scripts/agora_compat_manifest.py deployment-gate ...` against the
  clean execute-plans `origin/dev` worktree at
  `8fb854ef4036271ffb74147206a5e2cbf50ffdce` -> passed.
- `python3 docs/contracts/agora/generate_backend_contract.py bundle --check`
  and `python3 docs/contracts/agora/generate_backend_contract.py verify` ->
  passed.
