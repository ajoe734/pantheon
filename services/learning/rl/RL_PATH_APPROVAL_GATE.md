# RL Path Approval Gate

**Task**: BP5-OSS-004
**Owner**: Codex2
**Reviewer**: Claude
**Scope**: Define the formal approval checkpoint that must be passed before any active FinRL, RLlib, or Ray Tune training/activation lane opens
**Status**: Done — review approved by Claude 2026-04-16
**Last Updated**: 2026-04-29

---

## Purpose

This document turns the deferred RL path from a prose-only caution into a concrete governance
checkpoint.

No FinRL, RLlib, or Ray Tune active training lane, registry/governance-writing adapter, production
dispatch, paper/canary/live runtime path, or capital-bound execution may begin until this gate is
explicitly approved.

Dormant repo-local preparation is allowed before approval only when it is fail-closed: explicit
prep gates default off, offline/mock execution only, draft/none output envelopes, no registry or
governance writes, no networked production backend, and no paper/live execution path. The
RLlib/Ray Tune scaffold in `services/research/rllib` is in this dormant category.

This gate exists because `services/learning/rl/PATH_DEFINITION.md` already defines when RL is
justified, but the repo previously lacked one canonical approval packet naming:

- the exact evidence package required to open the RL lane
- who reviews it
- what decision states are allowed
- what follow-on work becomes permitted after approval

## Current Session Decision

As of **2026-04-17**, the accepted Phase 6 human gate records an explicit **defer** decision for
RL work in the immediate next wave:

- **Next-wave outcome**: keep the RL path `closed`; do not open `approved_for_adapter_work`
- **Reason**: Qlib has only reached the repo-side smoke baseline and does not yet satisfy the
  supervised-alpha exhaustion proof or the required **3 months** of stable approved evaluation
  history
- **Re-entry gate**: reopen this approval packet only after Qlib reaches
  `artifact_state=approved`, accumulates at least **3 months** of stable evaluation evidence for
  the target strategy family, and the rest of the evidence package in this document is complete
- **First executable RL lane once reopened**: `FinRL`, not `RLlib`, because the first approved RL
  implementation should minimize scope by proving the governed **single-agent** policy-output path
  before opening the broader RLlib + Ray Tune train/eval lane

This closes the previous ambiguous state where RL had criteria but no explicit yes/no decision in
the session record. The current answer is: **defer RL for this wave; reopen later with FinRL
first if the gate is approved**.

## Execution-Ready Slice

`EXEC-OSS-RL-001` turns the accepted defer decision into a reviewable execution slice without
opening RL implementation work early.

### Current Slice Decision

- Keep the RL path `closed` for the current wave
- Treat the first future implementation lane as **FinRL single-agent policy-output mapping**
- Keep `RLlib + Ray Tune` as a follow-on lane only after FinRL proves the governed `rl_policy`
  output path end-to-end

### What This Slice Explicitly Prepares

If the gate is later reopened, the first task materialized from that approval should be limited to:

1. a governed `FinRL` adapter that emits a canonical `rl_policy` artifact envelope
2. one reproducible smoke run for a single-agent exit-timing or position-sizing use case
3. a control-plane / downstream-consumer check proving the resulting artifact can enter the normal
   registry path as `artifact_state=draft`

### What This Slice Explicitly Does Not Open

- no active `RLlib` train/eval lane yet
- no active `Ray Tune` search-output lane yet
- no paper or live deployment semantics
- no broader RL architecture expansion beyond the already accepted gate and path documents

### Re-entry Evidence Packet To Assemble Later

When Qlib satisfies the plateau requirement, the reopen packet should be assembled in this order:

1. supervised-alpha exhaustion proof from the governed Qlib path
2. sequential-decision justification for the chosen RL problem
3. reproducible dataset / split package for the target universe
4. FinRL environment + reward sketch aligned to `ENV_CONTRACT.md`
5. downstream registry-consumer mapping for the first `rl_policy` artifact

Only after that packet is complete may reviewers consider changing the gate outcome from `closed`
to `approved_for_adapter_work`.

---

## Gate Outcome

The RL path is either:

- `closed`: default state; RL implementation remains deferred
- `approved_for_adapter_work`: FinRL / RLlib production-capable governed adapter and smoke-path work may begin
- `approved_for_training`: a specific approved adapter path may run governed training/eval loops
- `rejected`: RL remains deferred and the evidence package was judged insufficient

`approved_for_adapter_work` does not automatically permit live or paper deployment. It only opens
the implementation lane for the governed RL research path.

---

## Required Evidence Package

An approval request must include all of the following evidence.

### 1. Supervised Alpha Exhaustion Proof

Show that the Qlib supervised path is no longer the best next investment.

Required evidence:

- a governed Qlib artifact at `artifact_state=approved`
- at least 3 months of stable evaluation history on the target strategy family
- a written comparison showing that further supervised feature/model iterations are plateauing
- the baseline metric set used for comparison: Sharpe or IR, drawdown, turnover, and regime stability

Without this evidence, the RL path stays closed.

### 2. Sequential Decision Justification

Show that the problem is actually sequential.

Required evidence:

- a problem statement that cannot be reduced to static signal scoring
- the decision cadence and action space
- why action sequencing matters more than one-shot prediction
- why Qlib, DSPy, or TRL would not solve the same problem more directly

Accepted examples:

- exit timing
- dynamic position sizing
- stateful liquidation or rebalance policy

Rejected examples:

- feature ranking
- cross-sectional alpha scoring
- preference learning from operator edits alone

### 3. Historical Data Readiness

Show that the training environment can be governed and reproduced.

Required evidence:

- at least 2 years of intraday OHLCV for the target universe
- order fills or an equivalent execution-quality simulation source
- a dataset reference or lineage package that can be used by the future adapter
- confirmation that train/validation/test splits are time-based and reproducible

### 4. Environment and Reward Readiness

Show that the RL problem can be instantiated as a governed environment.

Required evidence:

- chosen framework path: `FinRL` or `RLlib` (with `Ray Tune` if search is needed)
- environment shape consistent with `services/learning/rl/ENV_CONTRACT.md`
- reward function definition with explicit penalties and success metrics
- hard safety constraints: leverage, position size, slippage, and drawdown controls

### 5. Governance and Consumer Readiness

Show that the resulting artifacts will have a legitimate downstream path.

Required evidence:

- target registry artifact shape using canonical `artifact_state`
- intended downstream consumer or evaluator path
- named owner for the implementation lane
- rollback and freeze implications aligned with `EVOLUTION_REVIEW_AND_THRESHOLDS.md`

---

## Review Owners

The approval packet must be reviewed by:

1. `Copilot` as RL path owner and proposer of the implementation lane
2. `Codex` or `Codex2` for contract and integration-boundary review
3. `Claude` for governance and execution-plane review when the request reaches approval

If the proposal would unlock live-adjacent execution semantics, it must also be escalated through
the higher-risk governance route described in `EVOLUTION_REVIEW_AND_THRESHOLDS.md`.

---

## Decision Rules

Approve the RL path only when all of the following are true:

1. The evidence package is complete.
2. Qlib is proven insufficient for the target problem.
3. The problem is genuinely sequential.
4. The dataset and environment can be reproduced without bypassing registry and lineage rules.
5. A governed adapter plan exists for the chosen framework.

Reject or keep the gate closed when any of the following are true:

1. Qlib has not yet been activated or has not plateaued.
2. The proposal relies on vague claims like "RL might do better".
3. The environment contract or reward function is underspecified.
4. Historical data or execution-quality simulation inputs are not governed.
5. No downstream consumer or artifact contract is identified.

---

## What Approval Unlocks

The repo may contain dormant prep-only scaffolds before approval if they meet the fail-closed rules
above. If the gate reaches `approved_for_adapter_work`, the following activation work may be
materialized:

- FinRL governed single-agent policy-output adapter as the **first executable RL lane**
- RLlib governed training/eval adapter
- Ray Tune governed search-output adapter
- one smoke path proving artifact creation under canonical registry metadata

The first smoke target after a future approval is:

- a reproducible `FinRL` single-agent exit-timing or position-sizing smoke run that emits a
  registry-ready `rl_policy` artifact envelope with canonical `artifact_state=draft` and
  `deployment_summary.current_stage=none`

Only after that single-agent proof exists should the broader `RLlib + Ray Tune` lane be opened for
governed training/eval and search-output work.

If the gate reaches `approved_for_training`, the following may additionally proceed:

- limited governed train/eval runs on the approved dataset and environment package
- packaging candidate RL artifacts for registry admission as `draft`

Approval does not bypass:

- registry admission
- deployment-stage review
- rollback requirements
- evolution freeze or incident thresholds

---

## Canonical References

- `services/learning/rl/PATH_DEFINITION.md`
- `services/learning/rl/ENV_CONTRACT.md`
- `services/learning/DEFERRED_OSS_ACTIVATION_MAP.md`
- `OSS_INTEGRATION_CHECKLIST.md`
- `EVOLUTION_REVIEW_AND_THRESHOLDS.md`
