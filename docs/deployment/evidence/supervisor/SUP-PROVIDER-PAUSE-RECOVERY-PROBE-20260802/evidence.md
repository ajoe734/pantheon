# SUP-PROVIDER-PAUSE-RECOVERY-PROBE-20260802 evidence

Task: Make quota pauses probeable and self-recovering

Owner: Codex · Reviewer: Codex2 · Status: **pending independent review**

## Delivered contract

- `quota_reached` is classified as `quota_terminal` / degraded capacity even
  when an envelope also says “authentication probe”; it never creates a sticky
  auth pause.
- An expired quota or capacity pause stays in the dispatch gate and schedules a
  targeted recovery probe. Wall-clock expiry alone cannot launch a worker.
- Recovery probe subprocesses run from `probe_provider_reports()` before the
  runtime-admission lock. The locked cycle only schedules gates and reconciles
  the already-collected result.
- A cached success cannot clear a recovery gate. The probe must be `source=live`
  and timestamped no earlier than the gate boundary.
- Recovery calls remain available with
  `supervisor.auto_refresh_provider_capabilities=false`; targets are de-duplicated
  by dispatch group, capped per cycle, and backed off after a failed live probe.
- Real authentication failures remain sticky until a fresh authoritative live
  success. If a quota recovery probe instead reveals revoked auth, the same
  pause is upgraded to the auth gate.

## Scope boundaries

The task changes only provider-health classification, supervisor pause/probe
control flow, regression tests, and this evidence directory. It does not change
credentials, account or quota grouping, configured probe values, runtime-state
JSON, reviewer policy, service processes, rollout, or product code.

The generated task brief, dashboard mirrors, and assistant packet/lock files
are supervisor-owned workspace state and are deliberately excluded from the
task commit and PR scope.

## Dependency

`SUP-WORKER-FAILURE-ENVELOPE-GATE-20260802` is `done`; its closeout merge
`c25471b48d1c2e476d2a639db36f341c6dc16c8a` is the task branch base.

## Verification

- Checkout-scoped Python distribution provisioned successfully.
- Provider-health suite: 14 tests passed.
- Focused recovery/admission suite: 93 tests passed.
- Full supervisor regression: 486 tests passed in 9.131s.
- Python compile and `git diff --check` passed.

## Review and delivery

Independent Codex2 exact-head review, GitHub checks, PR number, and merge commit
remain pending. Those fields must be bound to this manifest before governed
approval and owner closeout.
