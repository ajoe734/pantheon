# PR #4399 exact-head re-review — changes required

Task: `SUP-PREEMPTION-EXACT-HEAD-REVIEW-20260731`

Review target: `a924a6f3c0c54982d7efe145750cc99c57bc7f2e`

Decision: **changes required**

The shared lifecycle helper, dependency eligibility, unchanged-event cooldown,
failure-loop triage, logical worker capacity, five-minute stability grace, and
terminal-cleanup boundary all passed inspection and the focused suite. The
overall exact-head review still fails because provider readiness is not shared
between dispatch and priority preemption.

At the reviewed commit, `.orchestrator/supervisor.py:13301` gates dispatch with
`agent_auto_dispatch_block_reason(..., provider_report)`. That gate rejects
unsupported adapters, `can_auto_deliver=false`, a missing local CLI worker,
missing auto-approval support, and failed auth. In contrast,
`higher_priority_ready_task_exists` at lines 12849-13032 has no
`provider_report` parameter and line 12935 calls `agent_can_take_task`, which
only consumes cached auth readiness from the provider capability report. A
candidate can therefore trigger incumbent termination even though the same
supervisor tick cannot dispatch it.

The exact-head counterexample used a ready auth probe but
`local_cli_worker_supported=false` and `supports_auto_approve=false`. Dispatch
returned `codex local CLI worker is not ready`; preemption returned `True`.
The assertion `preemption must not kill a worker for a provider that dispatch
refuses` failed with exit code 1.

Run the checked-in reproducer against an isolated checkout of the reviewed
head:

```bash
PYTHONPATH=/path/to/a924-checkout/.orchestrator \
  /path/to/a924-checkout/.venv-pantheon/bin/python \
  docs/deployment/evidence/twelve-loop-gap/SUP-PREEMPTION-DISPATCH-ELIGIBILITY-20260731/repro_provider_readiness_gap.py
```

Required correction:

1. Pass the same in-cycle provider capability report into the preemption
   decision, or extract a shared provider-readiness predicate consumed by both
   paths.
2. Cover adapter support, auto-delivery, local-CLI, auto-approval, and auth
   readiness without treating the incumbent's own slot occupancy as a reason
   that makes all replacement preemption impossible.
3. Add regressions for at least `local_cli_worker_supported=false` and
   `agent_adapters.<lane>.can_auto_deliver=false`; both must make
   `higher_priority_ready_task_exists` false while an urgent lifecycle
   candidate exists.
4. Preserve the five-minute grace and keep legitimate assignment/terminal
   cleanup outside ordinary priority preemption.

The complete command record and live-log readback limitations are in the JSON
manifest. This review does not modify the implementation, config, or runtime.
