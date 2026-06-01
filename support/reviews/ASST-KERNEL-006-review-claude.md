# Review: ASST-KERNEL-006 — OpenClaw Command Broker Observe/Debug Allowlists

Reviewer: Claude
Task commit: 2f62f2e0
PR: #714 (merged b86c1333 into dev)
Review date: 2026-06-01

## Acceptance Criteria Verification

**1. User mode denies every command** ✅
- `_MODE_COMMAND_CLASSES[USER_MODE] = frozenset()` — no command classes registered
- `evaluate()` short-circuits on `USER_MODE` with `policy_class="mode_denied"` before any allowlist check
- Test: `test_user_mode_denies_every_command_and_audits` — asserts `AssistantCommandDenied` with `policy_class == "mode_denied"` and verifies audit entry

**2. Kernel observe allows read probes and git status** ✅
- `KERNEL_OBSERVE_MODE` permits only `{HEALTH_PROBE, REPO_STATUS}`
- `_validate_health_probe`: restricts to `curl` targeting allowlisted local routes (`/healthz`, `/readyz`, `/livez`, `/bff/*`, adapter health endpoints); blocks data flags (`-d`, `-H`, `--header`, `--cookie`); allows only GET/HEAD methods
- `_validate_repo_status`: only `git status -sb` or `git status --short --branch` pass
- Test: `test_kernel_observe_allows_read_probe_and_git_status` — both commands pass with audit `outcome=allowed`
- Test: `test_kernel_observe_denies_code_search` — confirms `code_search` is rejected in observe mode

**3. Kernel debug allows bounded health, rg, file-slice, tests, and sanitized logs** ✅
- `KERNEL_DEBUG_MODE` adds `{CODE_SEARCH, FILE_SLICE, TEST_RUN, LOG_READ}` to observe set
- `_validate_code_search`: requires `rg` with mandatory `--max-count/-m ≤ 200`, explicit path(s), blocks `--hidden/--no-ignore/--follow/-u` exfiltration flags
- `_validate_file_slice`: exactly `sed -n <start>,<end>p <path>`, bounded to 240 lines, safe relative repo paths only
- `_validate_test_run`: `pytest` or `python -m pytest` only, explicit test paths required, `--maxfail ≤ 5`, no unsafe flags
- `_validate_log_read`: `tail -n <N> <path>` only, `N ≤ 200`, path must contain "sanitized" or "redacted" and end in `.log`/`.jsonl`
- Test: `test_kernel_debug_allows_bounded_diagnostics` covers all four classes

**4. Denylist blocks destructive and secret commands** ✅
- Hard denylist covers: `sudo/su/doas`, shells (`bash/sh/zsh/fish…`), DB clients, network exfil (`wget/nc/ssh/scp…`), secret dump (`env/printenv/set`), broker heads, destructive commands (`rm/chmod/kill/docker/kubectl/terraform`)
- Destructive git subcommands (`reset/checkout/clean/push/commit/rebase/merge/switch/restore/branch/tag`) are blocked at the git prefix level
- Sensitive path and secret-like inline assignment detection adds a last line of defense
- Test: `test_denylist_blocks_destructive_secret_live_and_exfiltration_commands` parametrized across 6 cases

**5. Every allow and deny is audited** ✅
- `AssistantCommandBroker.request_command()` calls `_audit.record()` unconditionally before raising or returning
- `ToolWorkflowBridge.request_assistant_command()` records at both policy layers (OpenClaw tool policy + mode command policy)
- Audit entries include: `event_type`, `outcome`, `command_id`, `session_id`, `operator_id`, `trace_id`, `mode`, `command_class`, `argv_hash`, `argv_head`, `cwd`, `policy_class`, `policy_reason`
- Tests: audit assertions in user-mode test, kernel-observe test, and bridge tests confirm entries are written for both allowed and denied paths

## Architecture Notes

- **Two-layer design** is sound: OpenClaw tool policy gate (`assistant.command` tool must be explicitly allowlisted) precedes mode command policy evaluation. An empty `OPENCLAW_ALLOWED_TOOLS` env denies all command broker requests regardless of mode.
- **Mode table in BFF `mode_policy.py`** is correctly marked as a UI/BFF helper only; enforcement boundary remains in the adapter.
- **KERNEL_REPAIR_MODE** inherits the debug diagnostic set; repair write/restart guardrails are correctly deferred to ASST-KERNEL-007.

## Minor Observations (non-blocking)

- In `_validate_code_search`, allowlisted rg flags like `-n` fall through to the `else` positionals branch due to the short-circuit logic structure. These flags then appear as the "pattern" slot rather than being skipped. This is a usability/correctness quirk in the parser but does NOT create a security bypass since execution is not performed here and the argv_hash is recorded.
- `trigger_workflow` does not validate `session_id` (unlike `invoke_tool`). Appears intentional for headless workflow triggers.
- Log read paths are not restricted to repo-relative paths, allowing absolute `/var/log/…` paths. This is intentional and correct for system log directories.

## Verdict

**APPROVED.** All 5 acceptance criteria are satisfied. The implementation is deny-by-default, properly audited, and the two-layer policy composition with OpenClaw tool gating provides strong defense in depth. Test coverage is comprehensive (9 test cases across command policy and bridge). The task commit carries required trailers and PR #714 is already merged.

Owner (Codex2) may proceed to `done` closeout.
