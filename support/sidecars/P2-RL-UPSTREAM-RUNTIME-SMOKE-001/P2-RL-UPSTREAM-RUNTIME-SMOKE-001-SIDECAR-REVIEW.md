# Sidecar Review Packet: P2-RL-UPSTREAM-RUNTIME-SMOKE-001

Sidecar task: P2-RL-UPSTREAM-RUNTIME-SMOKE-001-SIDECAR-REVIEW
Parent task: P2-RL-UPSTREAM-RUNTIME-SMOKE-001 - FinRL RLlib Ray Tune governed runtime activation smoke
Helper kind: review_packet
Owner: Codex
Reviewer: Claude
Generated: 2026-05-01
Status: Review approved; ready for owner closeout

## Scope

This packet is support-only. It summarizes the parent task's evidence bundle and review posture for the assigned reviewer. It does not modify canonical truth, L1 policy, runtime adapters, registry/governance behavior, or the parent task's review decision.

Primary artifact reviewed:

- `support/evidence/P2-RL-UPSTREAM-RUNTIME-SMOKE-001/`

Additional context read:

- `.orchestrator/task-briefs/p2_rl_upstream_runtime_smoke_001_sidecar_review.md`
- `ai-status.json`
- `support/sidecars/P2-RL-UPSTREAM-RUNTIME-SMOKE-001/P2-RL-UPSTREAM-RUNTIME-SMOKE-001-SIDECAR-ACCEPTANCE.md`
- `services/research/finrl/activation_smoke.py`
- `services/research/rllib/activation_smoke.py`
- `services/research/rllib/ray_tune_activation_smoke.py`
- `OSS_INTEGRATION_CHECKLIST.md` rows for FinRL, RLlib, and Ray Tune

## Parent Evidence Summary

The parent evidence bundle records three activation-ready real-backend attempts. All three attempts used `--enable-activation-ready --backend real`, reached the real upstream import path, and produced explicit dependency/config evidence rather than silently falling back to stub execution.

| Framework | Real backend result | Cause | Stub handoff artifact | Dataset floor | Boundary |
|---|---|---|---|---|---|
| FinRL | `dependency_or_config_error`, `silent_stub_fallback=false` | `ModuleNotFoundError: No module named 'finrl'` | `stub_finrl` artifact bundle, registry entry, candidate packet, evaluator packet | 3 instruments, 30 periods, 27 estimated steps, floor pass | `deployment_stage=none`, `gate_state=closed`, no broker/order/promotion/capital |
| RLlib | `dependency_or_config_error`, `silent_stub_fallback=false` | `ModuleNotFoundError: No module named 'ray'` | `stub_rllib` artifact bundle, registry entry, candidate packet, evaluator packet | 3 instruments, 30 periods, 18 train steps, 9 eval steps, floor pass | `deployment_stage=none`, `gate_state=closed`, no broker/order/promotion/capital |
| Ray Tune | `dependency_or_config_error`, `silent_stub_fallback=false` | `ModuleNotFoundError: No module named 'ray'` | `stub_ray_tune` artifact bundle, registry entry, candidate packet, evaluator packet | 3 instruments, 30 periods, 8 trials, floor pass | `deployment_stage=none`, `gate_state=closed`, no broker/order/promotion/capital |

The combined `activation_evidence_summary.json` reports:

- `all_gates_pass=true`
- `bounded_governed_smoke_or_explicit_error=true`
- `reward_env_dataset_schema_enforced=true`
- `checksums_persisted=true`
- `evaluator_packet_produced=true`
- `no_broker_session=true`
- `no_order_routing=true`
- `no_paper_canary_live_promotion=true`
- `no_capital_binding=true`

## Acceptance Mapping

| Parent acceptance criterion | Evidence posture |
|---|---|
| FinRL, RLlib, and Ray Tune enabled paths run bounded governed train/search smoke or produce explicit upstream dependency/config evidence | Satisfied by the three real-backend attempt files plus stub handoff artifacts. The real attempts fail explicitly on missing upstream packages and set `silent_stub_fallback=false`. |
| Reward environment, dataset, and artifact schemas are enforced with persisted checksums and evaluator packet | Satisfied by dataset evidence files, candidate/evaluator packets, and `manifest.json` checksum coverage. Manifest checksum verification passed for every listed artifact. |
| Outputs remain research artifacts only with no broker session, order routing, paper/canary/live promotion, or capital binding | Satisfied by activation summaries, evaluator packets, candidate packets, and artifact bundles. Registry entries stay `artifact_state=draft` with `deployment_summary.current_stage=none`. |

## Verification Performed

Successful verification:

```bash
jq -r '.checksums | to_entries[] | "\(.value[7:])  support/evidence/P2-RL-UPSTREAM-RUNTIME-SMOKE-001/\(.key)"' support/evidence/P2-RL-UPSTREAM-RUNTIME-SMOKE-001/manifest.json | sha256sum -c -
python3 -m unittest discover -s services/research/finrl -p 'test_*.py'
python3 -m unittest discover -s services/research/rllib -p 'test_*.py'
```

Results:

- Manifest checksum verification: OK for all files listed in `manifest.json`.
- FinRL tests: 16 tests, OK.
- RLlib/Ray Tune tests: 33 tests, OK.

Operator note: running FinRL and RLlib tests together in one `python3 -m unittest ...` process is not a reliable verification shape in this repo because both trees expose a top-level `adapter` package name. Separate discovery runs are the verified result.

## Reviewer Attention Items

1. `OSS_INTEGRATION_CHECKLIST.md` currently says `task P2-RL-UPSTREAM-RUNTIME-SMOKE-001 closed` in the FinRL, RLlib, and Ray Tune rows, while `ai-status.json` still has the parent task in `review`. This sidecar does not change the wording. Reviewer should decide whether that lifecycle wording is acceptable before parent closeout.
2. The parent evidence is not proof of successful real upstream training/search in this local environment. It is proof of real-backend attempt plus explicit missing-package dependency/config evidence, followed by stub-backed handoff artifacts. That matches the parent acceptance only because the criterion allows explicit upstream dependency/config evidence.
3. The activation smoke dataset builders use Python `hash(instrument)` in `services/research/finrl/activation_smoke.py`, `services/research/rllib/activation_smoke.py`, and `services/research/rllib/ray_tune_activation_smoke.py`. Persisted evidence is checksum-verified, but regenerated synthetic metrics may vary unless `PYTHONHASHSEED` is fixed or the harness is changed to a stable hash. This is a reproducibility note for parent review, not a sidecar edit.
4. Safety flags are strongest in activation summaries, evaluator packets, candidate packets, and artifact bundles. Registry entries themselves primarily show `artifact_state=draft` and `deployment_summary.current_stage=none`; they do not independently repeat every no-broker/no-capital flag. If the reviewer expects registry entries to be self-contained safety packets, request a parent follow-up.

## Handoff Recommendation

Hand this packet to Claude as the sidecar reviewer. Suggested parent-review use:

- Treat the evidence bundle as complete for the "explicit dependency/config evidence plus research-only stub handoff" path.
- Do not treat it as real upstream FinRL/RLlib/Ray Tune success.
- Before final parent closeout, reconcile lifecycle wording that says "closed" while the parent task remains in `review`.
- Preserve the closed RL gate boundary: this packet does not authorize paper, canary, live, broker, capital, registry write, or promotion paths.

## Closeout Note

Claude approved this sidecar packet on 2026-05-01 in `support/reviews/P2-RL-UPSTREAM-RUNTIME-SMOKE-001-SIDECAR-REVIEW-claude-review.md`. Closeout remains support-only: no L1 canonical truth, runtime adapter, registry/governance behavior, or parent implementation file is changed by this sidecar.
