# PAPER-DRAWDOWN-CURRENT0040-CONFIG-20261021 — current0040 `expected_drawdown` low-risk PAPER policy review

Status: **approved by the operator on 2026-10-23 (Reviewer on Duty, 6.1); see Reviewer decision below.**
Author: Claude. Assigned reviewer: Antigravity2. Machine-readable twin: `evidence.json`.
Path: `EVOLUTION_REVIEW_AND_THRESHOLDS.md` §6.1 low-risk path, Reviewer on Duty (existing; no new policy service, schema, queue or writer).

## Subject and request

Publish `expected_drawdown = 0.0027` for the preserved current paper artifact
`artifact-persona-paper-00400aa279b49774cde4@1.0.0` (strategy `strategy-persona-00400aa279b49774cde4`, symbol `2330.TW`) in
the existing `services/evolution/config/threshold_sweep_baselines.json`, reusing the reviewed, terminal-DONE proposal
`PAPER-DRAWDOWN-BASELINE-PROPOSAL-20261020` (PR #6688). The `0.0303` entry and the disabled PnL placeholder/thresholds
are unchanged. The worker's existing eligibility keeps this PAPER only; no scope field is added.

## Identity proof (bounded read-only audits, supplied receipts)

| Item | Value / SHA-256 |
| --- | --- |
| Registry checksum (current0040) | `sha256:11ede082f82dc2888434621ee436a382963f984d5ff0ee2fc442a8b2c22ddd6a`; matches official runtime compiled bytes |
| Registry receipt `L11-current0040-actual-Registry-owner-readonly-20261020.json` | `7e50a695c7ccdc81daeb78dfd858e76488f1bed55da3aaf618f3cdd7a4cdf05d` |
| Parameter/logic/code equivalence receipt (`...full-parameter-logic-and-code-equivalence-before-policy-20261020.json`) | `662d82006dec16025dd0bd02940120cecb29e7dd8077569966990ca432d024c9` |
| Four-source audit receipt (`...four-source-calibration-and-stable-BFF-target-pre-policy-audit-20261020.json`) | `d765e90e5554f609eee69caff1ac5c9a59d9b3e15e3041915f5d22dd78d8349d` |
| Current runtime receipt (`current-runtime.json`) | `503b0c5466e8737baf6135a4fed7bd2a3920c78ec79003f658810d82da1191bf` |
| Producer input receipt @ 2026-10-21T00:11:25Z | `5ec6d56055c97a47af4a62ee46f0ce06132fb69920d3813b438189d19ec1e3eb` |
| Symbol alias proof (`...exact684e-TW-TWSE-execution-symbol-normalizer...`) | `35496a8684346dfce4ae267a27ede456dd28e9b4b66e45d33ccef53bd7eaae55` |
| Actual BFF roundtrip proof (BFF395179 wholeSUCCESS) | `b14c41be247bd8693e23b74c7ad34b233a6f9f9a2267dc06071974f2b840d5dd` |

All nine parameters (`market=TW`, `symbols=[2330.TW]`, `data_source=source-ingest:paper/persona-bootstrap`, `bar_frequency=1d`,
`lookback_bars=2`, `quantity_type=SHARES`, `order_quantity=1`, `momentum_threshold=0.0`, `zero_momentum_action=SELL`) and the
whole strategy logic (`close_to_close_momentum`, BUY if positive else SELL) are exactly equal to the proposal subject
`artifact-persona-paper-0b4aedc1da04dba924d8`. The four production files are byte-identical between the proposal
calibration commit `e72ae033` and the current `684e63615` (`strategy_artifact.py` `9da2ebb3…`, `performance_telemetry.py`
`664f6aa7…`, `paper_runtime.py` `9d0d7dcc…`, `paper_simulation.py` `ae42327c…`); the current BFF release (`525bb5c9`)
does not touch them. Initial ledger cash is `100000` TWD. Proposal obligations (1)–(3) of its "Artifact identity
lifecycle" are therefore satisfied; obligation (4), the policy decision, is the reviewer section below.

## Basis and limits (preserved, not widened)

- Basis: the original 40-bar (2026-08-18 .. 2026-10-15) dated official TWSE/TPEX market-data-only offline calculation,
  maximum 20-calendar-day drawdown `0.002697706949093271`, rounded outward to one basis point = **0.0027**. No new
  backtest, model, CPU, provider or input fetch was run for this task.
- **Gross** research-calibrated: fills at bar close, no fee, tax, commission or slippage. This is not a net or zero-fee
  policy claim. Scale is 1 share against 100,000 TWD; the value does not transfer to any other sizing.
- Not 20 observed hosted days, not hosted profit, not a new Alpha claim, `learning_eligible=false`.
- The symbol alias proof only shows the existing `2330.TW`→TWSE execution normalizer; it does not prove the runtime
  consumed 40 or 47 bars.
- The 1.25 breach multiple is unchanged (trigger would be 0.003375 on this baseline).

## Producer input state

The existing producer assessment at 2026-10-21T00:11:25Z (no tick, publish or consume invoked) shows for this artifact:
numeric `drawdown=0.0` valid, binding provenance (`rb-2eebe2b7…`) matching, freshness valid, and the **only** gap
`no approved 'expected_drawdown' baseline`. Runtime stage is `paper`.

## Validation (existing code paths only; no new tests)

Loaded the real config via `load_baselines()` / `approved_baseline_value()` and ran the real `evaluate_breaches()` on
the receipt's runtime summary at 2026-10-21T00:11:30Z:

| Case | Result |
| --- | --- |
| New baseline present | `approved_baseline_value` = 0.0027; 0 payloads, 0 diagnostics (drawdown 0.0 → multiple 0, no breach, input no longer a gap) |
| `0.0303` entry | still 0.0303 |
| Baseline removed (missing) | 0 payloads; fail-closed "no approved 'expected_drawdown' baseline" diagnostic |
| `deployment_stage=live` | 0 payloads; "not eligible for the paper threshold sweep" |
| Evaluated 2 days later (stale) | 0 payloads; "no fresh as-of time" fail-closed diagnostic |

No forced real breach and no manually invoked hosted sweep. `pytest` is not installed in this worker environment, so the
existing `services/evolution/test_threshold_sweep_worker.py` suite was not run here; CI is the required gate for it.

## Delivery boundary

Workers have no VM/SSH/runtime/provider authority. After genuine approval and normal merge, Root alone applies the
exact merged config bytes through the existing bind-mount retune/restart of only the existing sweep producer, after the
bounded current BFF release; no image rebuild. Merge or a no-op tick is **not** all12/L11 completion: it still needs
natural producer threshold/sweep/cooldown/downstream observations. All49/all12/TOTAL remain separate unfinished
obligations. No PnL activation, Telemetry change, new Root or second writer.

## Reviewer decision

`approved_by` is intentionally absent and no human decision is claimed by the author. The assigned ReviewerOnDuty
(Antigravity2) must state below, in genuine review evidence, whether this specific **0.0027 gross research-calibrated
baseline and target for `artifact-persona-paper-00400aa279b49774cde4` (PAPER only)** is approved under §6.1, or reject
it with reasons. Generic code approval alone is insufficient for Root activation.

- Decision: APPROVED on 2026-10-23 by the operator (ajoe734; a human acting as Reviewer on Duty under EVOLUTION_REVIEW_AND_THRESHOLDS.md 6.1). Scope exactly as stated in this document: expected_drawdown = 0.0027 gross research-calibrated baseline for artifact-persona-paper-00400aa279b49774cde4@1.0.0 (2330.TW; PAPER only; 1 share against 100000 TWD; breach trigger 1.25x = 0.003375). Not a net-of-cost; live; other-sizing or other-artifact approval. Rationale: the operator accepted the basis and limits stated above. Recorded from the operator's answer in a coordination session on 2026-10-23.
- If rejected: remove the config entry; keep this file as the record of the rejection.
