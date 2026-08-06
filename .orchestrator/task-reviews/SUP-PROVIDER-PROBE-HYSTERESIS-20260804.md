# Review Evidence Manifest: SUP-PROVIDER-PROBE-HYSTERESIS-20260804

- **Task ID**: SUP-PROVIDER-PROBE-HYSTERESIS-20260804
- **Title**: Decouple provider capability probing from the dispatch hot path and add failure hysteresis
- **Owner**: Antigravity
- **Reviewer**: Claude
- **Branch**: `task/SUP-PROVIDER-PROBE-HYSTERESIS-20260804`
- **PR**: https://github.com/ajoe734/pantheon/pull/4581
- **Head SHA**: `60fd1e48e`

## Delivered Changes
1. **Decoupled Probing & Config Defaults**: Set `auto_refresh_provider_capabilities` default to `False` in code (`supervisor.py`) and across all config files (`config.json`, `config.example.json`, `config.local.example.json`) so capability probing runs out-of-band and never executes inline in the dispatch hot path.
2. **Staleness-Bounded Pre-Dispatch Refresh**: Updated `refresh_provider_auth_before_dispatch` in `supervisor.py` to use `provider_auth_probe_due` so dispatch uses cached auth probes unless overdue (avoiding unconditional live CLI probes).
3. **Gated Failure Hysteresis & Live Probe Streak Guard**: Added `consecutive_probe_failures` tracking in `apply_provider_probe_to_report`, ensuring consecutive failure streak is incremented ONLY when `source == "live"` (cached probe replays are no-op re-confirmations). Preserved early return for unsupported probes (`ready is None`), inherited baseline `auth_ready` from existing capabilities report in `provider_permissions.provider_capabilities`, and gated hysteresis strictly on transient capacity/timeout/error failures. Terminal `auth` and `quota_terminal` failures immediately report `auth_ready = False` (streak >= 1), preserving launch protection for revoked credentials and quota exhaustion.
4. **Capability Report Persistence & Probe Fixes**: Applied `apply_provider_probe_to_report` within `provider_permissions.provider_capabilities` (without top-level circular imports). Removed blanket live->cached report scrubbing in `probe_provider_reports` that broke targeted recovery probe reconciliation, restored `if targeted:` persistence guard, and updated `reconcile_fresh_provider_probe_failures` skip guard to check `auth_ready is not False` so providers with active hysteresis holding `auth_ready=True` are not paused.
5. **Restored Capability Derivation**: Restored health-based derivation for `local_cli_worker_supported` and `supports_auto_approve` (`AccountHealth.HEALTHY` required) so DEGRADED providers are not advertised as auto-approve capable.
6. **Transition Telemetry & Verification**: Emitted `provider_capability_transitioned` activity log event whenever `auth_ready` toggles state with narrowed exception logging. Added unit tests in `.orchestrator/test_supervisor.py` and `.orchestrator/test_explain_dispatch.py` covering single failure hysteresis, consecutive failure thresholds, cached probe replay streak protection, reconcile hysteresis holding skip guards, unsupported probe handling, end-to-end capability report hysteresis, and probe failure reconciliation skip guards.

## Verification Executed
```bash
# 1. Provision python distribution
python3 scripts/dev/provision_python_distribution.py

# 2. Full supervisor and dispatch explanation test suite (617 passed)
.venv-pantheon/bin/python3 -m pytest .orchestrator/test_supervisor.py .orchestrator/test_explain_dispatch.py

# 3. Provider pause recovery probe unit tests (4 passed)
.venv-pantheon/bin/python3 -m pytest .orchestrator/test_supervisor.py -k ProviderPauseRecoveryProbeTests
```
Result: 599 pytest tests passed in test_supervisor.py (including cached probe streak guard & reconcile hysteresis skip guard), 18 pytest tests passed in test_explain_dispatch.py (total 617 passed).

