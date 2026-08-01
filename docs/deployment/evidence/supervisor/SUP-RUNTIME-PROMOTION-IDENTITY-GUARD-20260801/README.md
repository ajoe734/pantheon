# Evidence Manifest: SUP-RUNTIME-PROMOTION-IDENTITY-GUARD-20260801

## Task Summary
- **Task-ID**: `SUP-RUNTIME-PROMOTION-IDENTITY-GUARD-20260801`
- **Title**: Enforce persistent command-runtime and exact process identity
- **Owner**: Antigravity
- **Reviewer**: Human/Ops

## Scope & Implementation
Added identity guard capabilities into `scripts/promote_supervisor_runtime.py`:
1. `validate_candidate_root`: Validates that candidate root resolves under `/home/lupin/pantheon-ci-deploy/command-runtimes/<40-hex-commit>` with exact basename-to-HEAD SHA matching, rejecting `/tmp`, worker worktrees, symlink escapes, deleted roots, and invalid naming. Supports `--discover-only` mode to perform preflight validation without state alteration.
2. `capture_config_bytes_identity` & `revalidate_config_bytes_identity`: Captures live config file bytes, length, and SHA256 before handoff, and revalidates config bytes identity fail-closed.
3. `validate_git_identity`: Verifies origin remote `ajoe734/pantheon`, exact HEAD commit ancestor on `origin/dev`, and clean tracked tree state.
4. `discover_incumbent_supervisor_process`: Inspects `/proc/<pid>` under admission lock to extract PID, starttime, executable, full cmdline, cwd, and environment contract.
5. `evaluate_governed_launch_contract`: Validates governed interpreter, cwd, scrubbed environment keys, and log output path.
6. Expanded `evaluate_promotion_invariants` to include invariants 10-13 (`candidate_root_valid`, `git_identity_valid`, `process_identity_valid`, and `governed_launch_contract_valid`).

## Verification Output
1. Unit tests pass (35/35 passed):
   ```bash
   .venv-pantheon/bin/python3 -m pytest -v scripts/test_promote_supervisor_runtime.py
   .venv-pantheon/bin/python3 -m pytest -v scripts/test_supervisor_runtime_health.py
   ```
2. Live runtime probe against `$PANTHEON_STATUS_ROOT` confirms `process_identity_valid` and `governed_launch_contract_valid` correctly discover live supervisor PID `3523046` bound to `012dab969455e7146f2437159d7d38fc5904a195`.
3. Discover-only preflight mode (`--discover-only`) demonstrably rejects temporary worker worktrees (`out_of_prefix_root`, `candidate_root_in_disallowed_dir`, `discover_only_preflight_failed`).

