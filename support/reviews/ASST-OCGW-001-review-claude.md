# Review: ASST-OCGW-001 — Add OpenClaw gateway credential mount contract

Reviewer: Claude
Reviewed at: 2026-06-01
PR: #711 (merged into dev at ae897f2d729f61fcdc42fb4da5e606f259c7405f)
Verdict: **approved**

## Acceptance Criteria Verification

### 1. compose exposes dedicated codex and claude mount envs ✅
docker-compose.yml (openclaw-gateway, openclaw-gateway-adapter services) and
docker-compose.staging-full.yml both expose:
- `PANTHEON_ASSISTANT_CODEX_HOST_HOME`
- `PANTHEON_ASSISTANT_CODEX_CONTAINER_HOME`
- `PANTHEON_ASSISTANT_CLAUDE_HOST_CONFIG_DIR`
- `PANTHEON_ASSISTANT_CLAUDE_CONTAINER_CONFIG_DIR`
- `PANTHEON_ASSISTANT_CREDENTIAL_MOUNT_MODE`

Volume mounts use the same env vars with `/srv/pantheon-assistant/` defaults.
docker-compose.control.yml is control-plane-only (no openclaw services) — correct omission.

### 2. mount defaults are disabled or explicit ✅
Defaults are explicit service-user paths (`/srv/pantheon-assistant/.codex`,
`/srv/pantheon-assistant/.claude`). If the host paths don't exist,
`_validate_contract()` returns `missing_host_mount` (degraded, not silently ready).

### 3. no human home path is accepted ✅
`_host_policy_status()` rejects:
- `~` prefix, `$HOME` substring
- `/Users/` prefix (macOS user homes)
- `/root` and `/root/` prefix
- `/home/<any>` except `/home/pantheon-assistant/`

Rejection happens before any `os.stat()` call, preventing probe of private paths.

### 4. mount paths are reported only as sanitized readiness metadata ✅
`to_metadata()` returns `host_source` (enum: `"rejected"` or
`"dedicated_service_user"`), `container_target` (logical name), `status`,
`mount_mode`, `owner_check`. No raw filesystem paths in the API surface.
Tests confirm raw paths do not appear in `repr(metadata)`.

### 5. tests cover missing mount, wrong owner, and forbidden home path ✅
All 6 tests pass (`pytest services/openclaw-gateway-adapter/tests/test_assistant_credential_mounts.py -v`):
- `test_forbidden_human_home_is_rejected_before_stat` — linux human home
- `test_macos_human_home_is_rejected_before_stat` — macOS /Users/
- `test_missing_mount_reports_sanitized_degraded_status` — missing mount
- `test_wrong_owner_reports_sanitized_mismatch` — wrong UID
- `test_valid_mounts_return_ready_without_raw_paths` — happy path, no path leak
- `test_invalid_mode_and_container_path_are_rejected` — invalid mode + forbidden container path

## Additional Observations

- Permission check (`st_mode & 0o077`) enforces no group/other access on the
  credential directory, which is a correct additional guard.
- `_owner_uid_from_env()` gracefully handles missing `PANTHEON_ASSISTANT_CREDENTIAL_OWNER_UID`
  by falling back to `pwd.getpwnam()`, and returns `None` (→ `owner_unverified`) when
  neither source resolves — safe fail-closed behavior.
- The Dockerfile contains no credential baking — correct, credentials are runtime mounts only.

## Conclusion

Implementation satisfies all five acceptance criteria. Code is well-structured,
policy enforcement is fail-closed, and the test suite adequately covers the
required scenarios. Approved for owner closeout.
