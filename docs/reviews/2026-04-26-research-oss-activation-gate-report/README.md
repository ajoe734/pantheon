# Research / OSS Activation Gate Report

Status: `activation_gates_blocked`

Generated on `2026-04-26` with:

```bash
python3 scripts/run_research_activation_gates.py --as-of 2026-04-26 --output-dir docs/reviews/2026-04-26-research-oss-activation-gate-report
```

This packet proves the current repo has executable gate validation for the
remaining research / OSS activation rows. It does not promote rows by assertion:
each row must provide the external evidence named in
`research-oss-activation-gate-report.json` before production activation can be
claimed.

Current outcome:

- `Qlib`: repo baseline ready; production activation blocked on RS-003,
  governed data depth, StrategySpec binding, and first activation-run archive.
- `TRL`: repo baseline ready; production activation blocked on FB-002 volume,
  preference-pair volume, imitation artifact, baseline metrics, downstream
  consumer, and first DPO archive.
- `RL stack`: prep baseline ready; production activation blocked by the RL gate,
  Qlib approval + 90-day stability, sequential justification, intraday/order-fill
  data, and FinRL-first proof.
- `W&B`: prep baseline ready; re-entry blocked until at least `2026-05-15` and
  still missing operator, state-migration, SDK, network, and activation-smoke
  evidence.

Forward execution guidance is captured in:

- `docs/reviews/2026-04-26-research-oss-activation-forward-plan.md`

To validate future evidence, copy `evidence-template.json`, replace the false/0
values with real archived evidence facts, and run:

```bash
python3 scripts/run_research_activation_gates.py \
  --as-of <YYYY-MM-DD> \
  --evidence-json <filled-evidence-json> \
  --output-dir docs/reviews/<timestamp>-research-oss-activation-gate-report
```
