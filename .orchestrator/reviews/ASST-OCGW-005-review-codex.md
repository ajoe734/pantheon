# Review: ASST-OCGW-005 - Credential refresh smoke and runbook

Reviewer: Codex
Date: 2026-06-02
Status: APPROVED

## Scope

Reviewed the credential-refresh smoke/runbook delivery originally merged in
PR #773 (`be40fd94`) and the follow-up review fix in PR #775
(`eadb75ad`, pending merge at review time).

Reviewed artifacts:

- `scripts/openclaw-assistant-provider-smoke.sh`
- `docs/runbooks/openclaw-assistant-credential-refresh.md`
- `docs/04/pantheon_assistant_kernel_user_2026-05-31/EXECUTION_TASKS.md`
- `services/openclaw-gateway-adapter/assistant_claude_provider.py`
- `services/openclaw-gateway-adapter/main.py`
- `services/openclaw-gateway-adapter/tests/test_assistant_claude_provider.py`
- `services/openclaw-gateway-adapter/tests/test_assistant_credential_mounts.py`

## Acceptance Coverage

| Criterion | Verdict | Evidence |
|---|---|---|
| Smoke checks no-op health and tiny non-interactive invocation | Pass | Smoke calls `/livez`, provider readiness with `auth_probe=true`, Codex tiny invoke, and Claude tiny invoke. |
| Runbook documents host-refresh, container-refresh, degraded modes | Pass | Runbook covers host refresh, `rw` container refresh, `ro` caveat, and degraded operator action. |
| Expired auth produces degraded provider status | Pass | Codex auth failure maps to `codex_auth_unavailable`; Claude auth failure maps to `claude_auth_failure` and degraded invocation. |
| No secret contents or mounted paths appear in logs/API output | Pass after fix | Initial review found Claude invoke serialized raw `config_dir`; PR #775 changes response serialization to `claude_config` and adds regression coverage. |
| `ro` or `rw` requirement recorded per provider | Pass | Runbook records `rw` as required for refresh for both `.codex` and `.claude`, with `ro` limited to temporary inspection. |

## Findings

Blocking issue found and fixed during review:

- `ClaudeProviderResult.to_dict()` originally returned the raw container
  `config_dir` path. Since the smoke prints invoke bodies, this could expose
  `/home/pantheon-assistant/.claude` in logs/API output. Follow-up commit
  `eadb75ad` now serializes only the sanitized `claude_config` target, and
  `test_provider_result_to_dict_ok` asserts the raw path is absent.

No remaining blocking issues after the follow-up fix.

## Verification

Commands run locally:

```bash
pytest services/openclaw-gateway-adapter/tests/test_assistant_claude_provider.py services/openclaw-gateway-adapter/tests/test_assistant_credential_mounts.py services/openclaw-gateway-adapter/test_main.py
pytest services/openclaw-gateway-adapter/tests
python3 -m py_compile services/openclaw-gateway-adapter/assistant_claude_provider.py services/openclaw-gateway-adapter/main.py
python3 -m compileall -q services/openclaw-gateway-adapter
bash -n scripts/openclaw-assistant-provider-smoke.sh
git diff --check
```

Results:

- Focused pytest: 84 passed.
- Adapter tests directory: 66 passed.
- Syntax, compileall, shell syntax, and diff whitespace checks passed.

## Conclusion

Approved for Codex2 owner finalization after PR #775 merges into `dev`.
