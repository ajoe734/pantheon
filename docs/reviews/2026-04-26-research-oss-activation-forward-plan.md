# 2026-04-26 Research / OSS Activation Forward Plan

## Bottom Line

The remaining research / OSS activation gap is not missing repo-local baseline code. The repo now
has smoke-tested or deferred-prep paths plus an executable verifier. The next work is evidence
collection and gate-controlled activation:

1. Activate `Qlib` first, because it is the first missing production research problem type:
   supervised alpha discovery.
2. Activate `TRL` second, once the governed feedback store can prove enough runtime volume and a
   downstream consumer.
3. Keep the RL stack closed until Qlib is approved and stable for at least 90 days; reopen with
   `FinRL` first, then consider `RLlib` / `Ray Tune`.
4. Keep W&B as an optional backend until the MLflow-history, operator-preference, SDK, network,
   and canonical-state gates all clear.

The command that must arbitrate any future promotion is:

```bash
python3 scripts/run_research_activation_gates.py \
  --as-of <YYYY-MM-DD> \
  --evidence-json <filled-evidence-json> \
  --output-dir docs/reviews/<timestamp>-research-oss-activation-gate-report
```

## Current Verification

Verified locally on 2026-04-26:

```bash
python3 scripts/run_research_activation_gates.py --as-of 2026-04-26 --output-dir /tmp/pantheon-research-oss-gate-check
```

Result: `activation_gates_blocked`.

```bash
python3 -m pytest scripts/test_run_research_activation_gates.py -q
```

Result: `4 passed`.

## Activation Queue

| Order | Lane | Current Truth | Next Action | Promotion Blocker |
|---|---|---|---|---|
| 1 | Qlib | `smoke-tested`, repo baseline ready | Assemble the first governed supervised-alpha evidence packet and run a target-universe LightGBM activation | Needs RS-003 candidate, >=50 instruments, >=2 years governed OHLCV, StrategySpec binding, archived activation run |
| 2 | TRL | `smoke-tested`, repo baseline ready | Assemble the first governed DPO evidence packet once FB-002 has enough runtime volume | Needs >=200 FB-002 events, >=100 valid pairs, >=2 strategy families, approve/edit/reject coverage, approved imitation artifact, baseline metrics, downstream consumer, archived DPO run |
| 3 | RL stack | deferred-prep only, gate closed | Do nothing production-facing until the RL reopen packet passes; reopen with FinRL first | Needs Qlib approved + 90 stable days, sequential justification, intraday/order-fill dataset, RL approval, FinRL first-lane proof |
| 4 | W&B | deferred-prep only, re-entry blocked | Prepare only a reopen packet after 2026-05-15 and after operator/infrastructure evidence exists | Needs MLflow 30-day history, operator preference, adapter review, canonical-state migration, SDK compatibility, network readiness, real activation smoke |

## Qlib Next Slice

Goal: convert Qlib from smoke-tested baseline to the first production-activated supervised-alpha
lane.

Work to do:

1. Select one RS-003 candidate StrategySpec whose alpha objective is static supervised scoring,
   not sequential decision-making.
2. Bind that StrategySpec to a governed dataset reference with at least 50 instruments and 2 years
   of OHLCV history.
3. Run the governed Qlib LightGBM workflow against that target universe, not the toy smoke sample.
4. Archive the activation run and registry artifact envelope with canonical
   `artifact_state=draft` and `deployment_summary.current_stage=none`.
5. Fill the `qlib` section of the evidence JSON:

```json
{
  "qlib": {
    "rs003_candidate_passed": true,
    "dataset_instruments": 50,
    "dataset_years": 2.0,
    "strategy_spec_binding": true,
    "activation_run_archived": true,
    "evidence_refs": []
  }
}
```

Done means `scripts/run_research_activation_gates.py` returns Qlib as
`production_activated` without weakening any threshold.

## TRL Next Slice

Goal: convert TRL from smoke-tested baseline to the first production-activated preference-learning
lane.

Work to do:

1. Query FB-002 for governed approve/edit/reject events with complete actor, artifact, strategy
   family, and promotion metadata.
2. Build and archive at least 100 valid preference pairs across at least 2 strategy families.
3. Point to an approved LP-002 imitation artifact.
4. Train or attach a baseline preference model whose metrics clear the gate.
5. Identify one governed downstream consumer, preferably EV-001 or LP-001; RL should not be used
   as the first TRL consumer while the RL gate is closed.
6. Run and archive the first production DPO packet.
7. Fill the `trl` section of the evidence JSON:

```json
{
  "trl": {
    "feedback_events": 200,
    "preference_pairs": 100,
    "strategy_families": 2,
    "action_types": ["approve", "edit", "reject"],
    "imitation_approved_artifact": true,
    "baseline_model_metrics_pass": true,
    "downstream_consumer_ready": true,
    "activation_run_archived": true,
    "evidence_refs": []
  }
}
```

Done means `scripts/run_research_activation_gates.py` returns TRL as
`production_activated` and the registry artifact remains non-execution-stage
(`deployment_summary.current_stage=none`).

## RL Reopen Slice

Goal: keep the RL stack deferred until it has a real reason to exist.

Do not promote FinRL, RLlib, or Ray Tune based on deferred-prep smoke. The next valid RL task is a
reopen packet, not implementation.

Reopen packet requirements:

1. Qlib artifact is approved.
2. Qlib has at least 90 days of stable evaluation evidence for the target strategy family.
3. The target problem is genuinely sequential: exit timing, position sizing, liquidation, or
   stateful rebalancing.
4. A governed intraday OHLCV + order-fill or execution-quality simulation dataset exists.
5. The RL approval gate is accepted.
6. The first governed lane is FinRL; RLlib / Ray Tune stay follow-on.

## W&B Reopen Slice

Goal: avoid treating W&B as additive capability while MLflow already covers the production
registry path.

Do not add an SDK-backed W&B production implementation until every re-entry condition is true.
The earliest date gate is 2026-05-15 because MLflow became governed on 2026-04-15.

Reopen packet requirements:

1. MLflow has 30 consecutive governed days with no critical incident.
2. A human operator preference for W&B is filed with a concrete workflow reason.
3. Adapter generalization is review-closed.
4. Canonical `artifact_state` / derived `deployment_stage` migration is complete.
5. W&B SDK pin and compatibility proof are archived.
6. Network or self-hosted infrastructure readiness is archived.
7. A real W&B activation smoke is archived.

## What Not To Do

- Do not count deferred-prep smoke as production activation.
- Do not open RL before Qlib is approved and stable for 90 days.
- Do not make W&B the default experiment backend.
- Do not promote any row by editing `RESEARCH_BACKEND_MATURITY_MATRIX.md`; promote only from a
  passing evidence JSON plus verifier report.
