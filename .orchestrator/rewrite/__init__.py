"""Parallel rewrite package for the orchestrator supervisor.

New architecture from docs/02-architecture/SUPERVISOR_REWRITE_PLAN.md is built
here in isolation. Each module is first proven behaviour-equivalent to the
incumbent via a shadow validator (read real config/state, compute the new
decision, diff against the old one) BEFORE any cutover, one phase at a time
behind a flag, with the legacy path always one flag away.

Cutover status (SUPERVISOR_REWRITE_PLAN.md §4 migration table):
  * concurrency.max_parallel   — Phase 1b: LIVE. supervisor.agent_dispatch_capacity
                                 routes through it by default; legacy path via
                                 ready_dispatcher.use_rewrite_concurrency=false.
  * concurrency.account_limit  — Phase 1b (account cap): LIVE. supervisor.
                                 quota_group_concurrency_limit routes its cap
                                 lookup through it. Live config uses one explicit
                                 provider.account key; concurrency cap lookups
                                 no longer fan out across identity aliases;
                                 old aliases remain read-compatible only. Same
                                 use_rewrite_concurrency flag.
  * task_machine.dispatch_*    — Phase 3b: LIVE. supervisor.dispatch_priority_for_task
                                 routes through it by default (configured status
                                 sets translated to canonical lifecycle states
                                 first); legacy via
                                 ready_dispatcher.use_rewrite_dispatch_reason=false.
  * shadow.py                  — the pre-cutover equivalence oracle (imports the
                                 incumbent supervisor only for comparison, never
                                 imported BY it, so the live import stays acyclic).
  * task_state_store.py        — Phase 6 shadow storage bridge: owner-only,
                                 hash-chained full-state journal plus replay/parity
                                 verification. ai-status.json remains authoritative.

Post-cutover, shadow.py compares the live functions against the rewrite modules
and therefore agrees trivially; the standing behaviour-preservation guarantee is
carried by rewrite/test_cutover.py, which pins the rewrite path against the
legacy path (flag off) across a config matrix.
"""
