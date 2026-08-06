# Review Evidence Manifest: SUP-PROVIDER-PROBE-HYSTERESIS-20260804

- **Task ID**: SUP-PROVIDER-PROBE-HYSTERESIS-20260804
- **Title**: Decouple provider capability probing from the dispatch hot path and add failure hysteresis
- **Owner**: Antigravity
- **Reviewer**: Claude
- **Branch**: `task/SUP-PROVIDER-PROBE-HYSTERESIS-20260804`
- **PR**: https://github.com/ajoe734/pantheon/pull/4581
- **Head SHA**: `e0708f24d`

## Delivered Changes
1. **Decoupled Probing & Config Defaults & Out-Of-Band Tick**: Set `auto_refresh_provider_capabilities` default to `False` in code (`supervisor.py`) and across all config files (`config.json`, `config.example.json`, `config.local.example.json`). Added an interval-gated background out-of-band refresh tick (`provider_capability_refresh_interval_seconds` defaulting to 300s) inside `probe_provider_reports` so full capability reports are regularly updated in the background without blocking the dispatch hot path.
2. **Staleness-Bounded Pre-Dispatch Refresh**: Updated `refresh_provider_auth_before_dispatch` in `supervisor.py` to use `provider_auth_probe_due` so dispatch uses cached auth probes unless overdue (avoiding unconditional live CLI probes).
3. **Gated Failure Hysteresis & Agent Adapters Debounce**: Added `consecutive_probe_failures` tracking in `apply_provider_probe_to_report`, ensuring consecutive failure streak is incremented ONLY when `source == "live"`. Re-evaluated `agent_adapters` in `provider_permissions.provider_capabilities` after applying provider report hysteresis so `can_auto_deliver` on `agent_adapters` inherits the hysteresis-held `auth_ready` state, preventing single transient probe failures (such as CLI timeouts under load) from immediately flipping `can_auto_deliver` to `False` at supervisor line 10349.
4. **Capability Report Persistence & Probe Fixes**: Applied `apply_provider_probe_to_report` within `provider_permissions.provider_capabilities` (without top-level circular imports). Removed blanket live->cached report scrubbing in `probe_provider_reports` that broke targeted recovery probe reconciliation, restored `if targeted:` persistence guard, and updated `reconcile_fresh_provider_probe_failures` skip guard to check `auth_ready is not False` so providers with active hysteresis holding `auth_ready=True` are not paused.
5. **Restored Capability Derivation**: Restored health-based derivation for `local_cli_worker_supported` and `supports_auto_approve` (`AccountHealth.HEALTHY` required) so DEGRADED providers are not advertised as auto-approve capable.
6. **Incumbent Immediate Flip Behavior (Acceptance 6)**: Configured `provider_probe_failure_hysteresis_threshold=1` in config reproduces immediate flip behavior (incumbent behavior one config flag away).
7. **Transition Telemetry & Verification**: Emitted `provider_capability_transitioned` activity log event whenever `auth_ready` toggles state with narrowed exception logging. Updated unit test `test_models_cache_probe_failure_is_degraded_not_credential_revocation` in `.orchestrator/test_supervisor.py` to drive failure streak to threshold and assert `capacity_retryable` pause creation, and added `test_agent_adapters_can_auto_deliver_hysteresis_debounce` to assert `can_auto_deliver` debounce at the line 10349 dispatch gate.

## Verification Executed
```bash
# 1. Provision python distribution
python3 scripts/dev/provision_python_distribution.py

# 2. Full supervisor and dispatch explanation test suite (618 passed)
.venv-pantheon/bin/python3 -m pytest .orchestrator/test_supervisor.py .orchestrator/test_explain_dispatch.py

# 3. Rewrite shadow verification (16 agents, 0 mismatch)
PYTHONPATH=.orchestrator .venv-pantheon/bin/python3 -m rewrite.shadow --config .orchestrator/config.json

# 4. Explain dispatch CLI verification
python3 scripts/explain_dispatch.py SUP-PROVIDER-PROBE-HYSTERESIS-20260804
```
Result: 600 pytest tests passed in test_supervisor.py (including cached probe streak guard, reconcile hysteresis skip guard, models cache streak threshold pause assertion, and agent_adapters can_auto_deliver hysteresis debounce assertion), 18 pytest tests passed in test_explain_dispatch.py (total 618 passed). Rewrite shadow verification passed 16/16 agents with 0 mismatch. `explain_dispatch` ran cleanly.


