# Review Evidence Manifest: SUP-PROVIDER-PROBE-HYSTERESIS-20260804

- **Task ID**: SUP-PROVIDER-PROBE-HYSTERESIS-20260804
- **Title**: Decouple provider capability probing from the dispatch hot path and add failure hysteresis
- **Owner**: Claude (reassigned from Antigravity 2026-08-06)
- **Reviewer**: Antigravity (reassigned from Claude 2026-08-06)
- **Branch**: `task/SUP-PROVIDER-PROBE-HYSTERESIS-20260804`
- **PR**: https://github.com/ajoe734/pantheon/pull/4581
- **Base**: `cd98dbf9a85ab77257f417080b514fc137b8e5c2` (merge-base with `dev`)
- **Reviewed code commit**: `0f52e40e9e7bb6a8e7f65855e8b5a731be56cb59`

The entire deliverable is the single code commit above. This manifest is the one
commit stacked on top of it, so the reviewed commit is an ancestor of the PR head
and is verifiable with `git merge-base --is-ancestor 0f52e40e9 HEAD`. The previous
manifest revision named `e0708f24d`, a commit abandoned by an earlier branch reset
and present on no ref; that was review blocker 2 and is corrected here.

## Delivered Changes

1. **Probing decoupled from the dispatch hot path.** `auto_refresh_provider_capabilities`
   now defaults to `false` in `supervisor.py` and in `config.json`,
   `config.example.json` and `config.local.example.json`. An interval-gated
   out-of-band refresh tick (`provider_capability_refresh_interval_seconds`,
   default 300s) lives in `probe_provider_reports`, so full capability reports
   refresh on their own timer and dispatch never triggers a live probe itself.
2. **Staleness-bounded pre-dispatch read.** `refresh_provider_auth_before_dispatch`
   uses `provider_auth_probe_due`, so dispatch reads the last good cached report
   unless a probe is actually overdue.
3. **Failure hysteresis.** `apply_provider_probe_to_report` tracks
   `consecutive_probe_failures` and holds `auth_ready=True` across transient
   failure kinds (`probe_timeout`, `probe_error`, capacity) until the streak
   reaches `provider_probe_failure_hysteresis_threshold` (default 3). Terminal
   auth/credential revocations and `quota_terminal` still flip immediately at
   streak 1. `false -> true` remains immediate. The streak only advances for
   `source == "live"` probes.
4. **The hold now actually reaches the dispatch gate.** `ClaudeCLIAdapter` and
   `AntigravityAdapter` derived `auth_ready` purely from their own local checks,
   which are exactly as flaky under load as the probe being debounced, and never
   read `provider_capabilities["providers"][key]["auth_ready"]`. New
   `adapters.base.hysteresis_held_auth_ready` lets them honour an active hold, and
   the `agent_adapters` rebuild in `provider_permissions.provider_capabilities`
   now passes the whole report rather than `report["providers"]`, matching every
   other `build_adapter` call site. Together these make the held value visible at
   the `can_auto_deliver` gate in `agent_auto_dispatch_block_reason`. The hold
   requires an active streak (`1 <= streak < threshold`), so it is a debounce and
   never a pin.
5. **No irreversible pin for probe-less providers.** Inheritance of a previous
   `auth_ready=True` is gated on the provider actually having a live `auth_probe`.
   Only `apply_provider_probe_to_report` advances the streak and it is live-gated,
   so without this a probe-less provider's streak stayed `0 < threshold` forever
   and a genuine credential revocation could never take effect. This affected
   `copilot` and `grok` (no `auth_probe` key) and the `shared_credential_group`
   antigravity lanes.
6. **Transition telemetry.** A distinct `provider_capability_transitioned`
   activity event carries old/new `can_auto_deliver`, the raw probe status/error,
   and the failure streak, emitted only when the effective value actually changes.
7. **Health-based capability derivation restored.** `local_cli_worker_supported`
   and `supports_auto_approve` require `AccountHealth.HEALTHY`, so DEGRADED
   accounts are not advertised as auto-approve capable.
8. **Incumbent one flag away.** Setting
   `provider_probe_failure_hysteresis_threshold: 1` restores the incumbent
   single-failure flip; setting `auto_refresh_provider_capabilities: true`
   restores inline refresh.
9. **Probe field refresh preserved.** A probe with no `ready` verdict still
   refreshes `auth_error`, `auth_method` and `last_auth_probe_at` as the
   pre-hysteresis code did.

## Verification Executed

```bash
PYTHONPATH=.orchestrator .venv/bin/python -m pytest \
  .orchestrator/test_supervisor.py .orchestrator/test_explain_dispatch.py
# -> 620 passed, 162 subtests passed

PYTHONPATH=.orchestrator .venv/bin/python -m rewrite.shadow --config .orchestrator/config.json
# -> max_parallel 16 agents 0 mismatch; account_limit 16 agents 0 mismatch;
#    failure_pause 11 kinds 0 mismatch

PYTHONPATH=.orchestrator .venv/bin/python scripts/explain_dispatch.py \
  SUP-PROVIDER-PROBE-HYSTERESIS-20260804
# -> runs clean
```

### Negative control (the test-vacuity blocker)

The previous `test_agent_adapters_can_auto_deliver_hysteresis_debounce` patched
`supervisor.load_json` while the code under test used the copy
`provider_permissions` imported into its own namespace, and pointed
`paths.provider_capabilities` at a nonexistent file, so the fixture was dead and
the test asserted only that this host has an authenticated Claude CLI. It is
replaced by three tests that were confirmed to fail when the feature is removed.

With `hysteresis_held_auth_ready` forced to return `False` and the live-probe
inheritance gate reverted:

```
FAILED    test_agent_adapters_can_auto_deliver_hysteresis_debounce
SUBFAILED (streak=1) test_claude_adapter_honours_hysteresis_held_auth_ready
SUBFAILED (streak=2) test_claude_adapter_honours_hysteresis_held_auth_ready
FAILED    test_probeless_provider_auth_ready_is_not_pinned_by_hysteresis
4 failed, 2 passed
```

All four pass with the feature present. The tests mock the CLI presence and the
local auth check, so they do not depend on this host having an authed CLI.

### Acceptance 5: streak-driven dispatch outcome

Consecutive live `probe_timeout` failures driven through
`apply_provider_probe_to_report`, then read back through the real
`ClaudeCLIAdapter` and `agent_auto_dispatch_block_reason`, with threshold 3 and
the adapter's local auth check forced to `False`:

```
cycle 1: streak=1 auth_ready=True  can_auto_deliver=True  dispatch_blocked=False
cycle 2: streak=2 auth_ready=True  can_auto_deliver=True  dispatch_blocked=False
cycle 3: streak=3 auth_ready=False can_auto_deliver=False dispatch_blocked=True
cycle 4: streak=4 auth_ready=False can_auto_deliver=False dispatch_blocked=True
cycle 5: streak=5 auth_ready=False can_auto_deliver=False dispatch_blocked=True

provider_capability_transitioned events: 1
  claude2: True -> False streak=3 probe_status=probe_timeout
           error='timeout waiting for claude auth status'
```

A single transient failure does not block dispatch; the threshold does, and
exactly one transition event is emitted at the flip.

### Probe-less pin repro (review blocker 4)

Prior report holding `copilot` and `grok` at `auth_ready: true` with no
`auth_probe`, with `_copilot_auth_ready` forced to `False`:

```
copilot: auth_ready=False local_cli_worker_supported=False streak=None
grok:    auth_ready=False local_cli_worker_supported=False streak=None
```

The freshly computed revocation is honoured and the record is self-consistent.
Before the fix both reported `auth_ready=True` with
`local_cli_worker_supported=False`.

## Known Limitation

The hold covers `auth_ready` only. If `command_exists` itself flakes and the CLI
binary appears missing, `can_auto_deliver` still goes false immediately, because
delivery has no command to invoke in that state. Extending hysteresis to binary
presence is deliberately out of scope here.
