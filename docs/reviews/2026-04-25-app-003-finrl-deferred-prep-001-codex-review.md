# APP-003-FINRL-DEFERRED-PREP-001 Review

Date: 2026-04-25  
Reviewer: Codex  
Outcome: approved after reviewer-side correction of entrypoint gate drift

## Review Summary

- Initial review found two implementation drifts against the handoff:
  `smoke_test.py` did not accept `--enable-deferred-prep`, and `worker.py`
  did not enforce `PANTHEON_FINRL_PREP_ENABLED=1`.
- Added `DeferredPrepGate` to make the prep lane explicitly non-default at both
  entrypoints.
- Extended unit coverage for the gate and corrected the handoff note to the
  implemented adapter name: `GovernedFinRLPolicyAdapter`.
- Canonical docs still preserve the same truth: FinRL remains
  `criteria-defined`, outputs remain `artifact_state=draft`, and the RL gate
  stays closed.

## Verification

- `python3 -m pytest services/research/finrl/test_adapter.py -q`
  Result: `14 passed`
- `python3 services/research/finrl/smoke_test.py --enable-deferred-prep`
  Result: pass, emits draft `rl_policy` artifact summary with gate still
  `closed`
- `python3 services/research/finrl/smoke_test.py`
  Result: exits non-zero with explicit gate message
- `python3 services/research/finrl/worker.py`
  Result: exits non-zero with explicit gate message
- `PANTHEON_FINRL_PREP_ENABLED=1 python3 services/research/finrl/worker.py`
  Result: pass, emits draft registry summary with `deployment_stage=none`

## Approval Basis

- Deferred-prep scope remains repo-local and non-activating.
- The non-default gate is now enforced in code rather than only described in
  docs.
- No default production backend or RL activation state changed.
