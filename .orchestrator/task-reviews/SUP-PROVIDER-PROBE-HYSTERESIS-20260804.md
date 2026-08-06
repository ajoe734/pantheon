# Review Evidence Manifest: SUP-PROVIDER-PROBE-HYSTERESIS-20260804

- **Task ID**: SUP-PROVIDER-PROBE-HYSTERESIS-20260804
- **Title**: Decouple provider capability probing from the dispatch hot path and add failure hysteresis
- **Owner**: Antigravity
- **Reviewer**: Claude
- **Branch**: `task/SUP-PROVIDER-PROBE-HYSTERESIS-20260804`
- **PR**: https://github.com/ajoe734/pantheon/pull/4581
- **Head SHA**: `497593ac344651761f0d1fd681617259a6a260a7`

## Delivered Changes
1. **Decoupled Probing & Config Defaults**: Set `auto_refresh_provider_capabilities` default to `False` in code (`supervisor.py`) and across all config files (`config.json`, `config.example.json`, `config.local.example.json`) so capability probing runs out-of-band and never executes inline in the dispatch hot path.
2. **Gated Failure Hysteresis & Unsupported Probe Handling**: Added `consecutive_probe_failures` tracking in `apply_provider_probe_to_report`, preserved early return for unsupported probes (`ready is None`), and gated hysteresis strictly on transient capacity/timeout/error failures. Terminal `auth` and `quota_terminal` failures immediately report `auth_ready = False` (streak >= 1), preserving launch protection for revoked credentials and quota exhaustion.
3. **Capability Report Persistence**: Applied `apply_provider_probe_to_report` within `provider_permissions.provider_capabilities` so consecutive failure counts accumulate and are not wiped when capabilities reports are rebuilt.
4. **Restored Capability Derivation**: Restored health-based derivation for `local_cli_worker_supported` and `supports_auto_approve` (`AccountHealth.HEALTHY` required) so DEGRADED providers are not advertised as auto-approve capable.
5. **Transition Telemetry & Verification**: Emitted `provider_capability_transitioned` activity log event whenever `auth_ready` toggles state with narrowed exception logging. Added unit tests in `.orchestrator/test_explain_dispatch.py` and shadow verification evidence via `rewrite.shadow`.

## Verification Executed
```bash
# 1. Full supervisor test suite (597 passed)
PYTHONPATH=.orchestrator .venv-pantheon/bin/python3 -m pytest .orchestrator/test_supervisor.py -q

# 2. Dispatch explanation & hysteresis tests (17 passed)
.venv-pantheon/bin/python3 -m unittest discover -s .orchestrator -p "test_explain_dispatch.py"

# 3. Rewrite shadow verification (0 mismatches across 16 agents & 11 failure kinds)
PYTHONPATH=.orchestrator .venv-pantheon/bin/python3 .orchestrator/rewrite/shadow.py --config .orchestrator/config.json
```
Result: 597 pytest tests passed, 17 unittest tests passed, shadow validation reported 0 mismatches across 16 agents and 11 failure kinds.

