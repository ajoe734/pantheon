# PSD-TUNNEL-CONTROL-001 — evidence

Task: Make dashboard tunnel startup explicit opt-in and revoke stale tunnel
permissions (PSD-03 tunnel/permission half, `docs/04/pantheon_pre_shutdown_gap_sa_sd_2026-09-02/SD.md` § 4.2).

## Scope

Only the tunnel/permission half of PSD-03. Yahoo/Anue connector retirement
(source_ingestion) is out of scope for this task and was left untouched.

## Changes

- `scripts/dashboard_autostart.sh`: `PANTHEON_DASHBOARD_MANAGE_TUNNEL` now
  defaults to `0` (was `1`). The cron-driven autostart no longer opens a
  public `cloudflared` quick tunnel unless an operator explicitly opts in by
  setting the env var to `1`.
- `.orchestrator/permission_broker.py`: moved the `cloudflared tunnel` and
  `bash scripts/start_dashboard_tunnel.sh` patterns from `SAFE_BASH_PATTERNS`
  (auto-allow) to `DEFER_BASH_PATTERNS` (routed to the approval queue).
  Workers no longer hold a standing grant to launch the public tunnel;
  explicit Human/Ops invocation still works through the normal approval path.

## Tests

- `scripts/test_dashboard_autostart.py::test_manage_tunnel_defaults_off_without_operator_opt_in`
  (new) — asserts the default is `0`.
- `.orchestrator/test_provider_permissions.py::test_start_dashboard_tunnel_script_requires_review`,
  `test_start_dashboard_tunnel_script_with_path_requires_review`,
  `test_cloudflared_tunnel_invocation_requires_review` (new) — assert
  `classify_command` returns `defer` for both tunnel launch surfaces.

## Verification run

```
cd .orchestrator && .venv-pantheon/bin/python3 -m pytest -q test_provider_permissions.py
# 117 passed, 18 subtests passed

.venv-pantheon/bin/python3 -m pytest -q \
  scripts/test_dashboard_autostart.py \
  scripts/test_dashboard_autostart_install.py \
  scripts/test_dashboard_server.py \
  scripts/test_dashboard_tunnel_keepalive.py \
  tests/broker/test_dashboard.py \
  tests/capital/test_dashboard.py
# 27 passed
```

Manual check of the fail-closed classification:

```
python3 -c "
import sys; sys.path.insert(0, '.orchestrator')
import permission_broker as pb
print(pb.classify_command('bash scripts/start_dashboard_tunnel.sh'))   # defer
print(pb.classify_command('cloudflared tunnel --url http://127.0.0.1:4180'))  # defer
"
```

## Acceptance mapping

- Tunnel startup is off unless explicitly opted in: `PANTHEON_DASHBOARD_MANAGE_TUNNEL` defaults to `0`.
- Stale tunnel grants are revoked or ignored safely: `SAFE_BASH_PATTERNS` no
  longer auto-allows the tunnel launch commands; they now defer to approval.
- Permission checks remain fail closed: unmatched/deferred tunnel commands
  route to the approval queue rather than falling through to auto-allow.
- Focused tests plus branch/PR/merge evidence are recorded: see Tests and
  Verification run above; branch/PR/merge evidence recorded at closeout.
