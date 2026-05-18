# OSS-FINRL-V2-001 Sidecar Review Packet

Task: OSS-FINRL-V2-001-SIDECAR-REVIEW
Parent task: OSS-FINRL-V2-001
Helper kind: review_packet
Owner: Codex
Reviewer: Gemini2
Date: 2026-05-18
Status: ready for reviewer handoff

## Scope Boundary

This sidecar is support-only. It does not modify L1 canonical truth, FinRL
runtime code, registry implementation, deployment governance, or any parent
task evidence artifact.

The packet consolidates review context for the parent owner/reviewer to decide
whether to absorb anything into the mainline closeout record. It is not a new
source of canonical product semantics.

## Context Notes

- The dispatched task-scoped brief path
  `.orchestrator/task-briefs/oss_finrl_v2_001_sidecar_review.md` was not
  present on `origin/dev` at commit `27474111`; the sidecar task board entry was
  therefore created from the dispatch context with `scripts/ai-status.sh`.
- The parent task brief path for `OSS-FINRL-V2-001` was also absent in
  `.orchestrator/task-briefs`; parent acceptance was reconstructed from
  `ai-status.json` and the existing parent review/evidence records.
- Existing parent review records show an initial Codex changes-requested review
  followed by Codex2 approval after upstream FinRL PPO evidence was produced.

## Parent Acceptance Map

| Parent acceptance item | Evidence observed | Sidecar assessment |
|---|---|---|
| `twse_stock_env.py` wraps FinRL `StockTradingEnv` with TWSE OHLCV for 5+ instruments | `support/evidence/OSS-FINRL-V2-001/admission_packet.json` records `twse_stock_env.finrl_available=true`, `num_instruments=5`, `state_space=21`, `action_space=5`, and upstream env `finrl.meta.env_stock_trading.env_stocktrading.StockTradingEnv`. | Satisfied by existing evidence. |
| `production_drl_run.py` trains PPO or DDPG for at least 1000 CPU-only steps | `support/evidence/OSS-FINRL-V2-001/evaluation_summary.json` records `algorithm=ppo`, `device=cpu`, `total_training_steps=1024`, `stock_trading_env_used=true`, and `torch_cuda_available=false`. | Satisfied by existing evidence. |
| Evaluation summary includes sharpe, annual return, and max drawdown | `evaluation_summary.json` records `sharpe=16.39`, `annual_return=0.298749`, and `max_drawdown=0.000794`. | Satisfied by existing evidence. |
| Model artifact carries checksum and `trained_policy_ref` | `support/evidence/OSS-FINRL-V2-001/registry_entry.json` records checksum `sha256:ee6e46da2779c38b7397607af7c3ab83bce5f31920bdc6ace92332fa937353c4` and `trained_policy_ref=research/finrl/offline/twse-ppo-strategy-001/1.0.0/artifact.json`. | Satisfied by existing evidence. |
| Admission packet is valid and remains registry-admission only | `admission_packet.json` records `schema_version=PromotionReadinessPacket.v1`, `can_proceed=true`, `requested_transition=draft_to_candidate`, and `registry_write_performed=false`. | Satisfied as an offline registry review packet. |
| No GPU and no live broker | `admission_packet.json` safety assertions record `cpu_only=true`, `no_gpu=true`, `no_broker_session=true`, `no_order_route=true`, `no_capital_binding=true`, and `deployment_stage_remains_none=true`. | Boundary remains fail-closed. |

## Review History Digest

- `support/reviews/OSS-FINRL-V2-001-review-codex.md` requested changes because
  earlier evidence did not construct the upstream FinRL `StockTradingEnv` and
  still emitted passing evidence while FinRL was unavailable.
- `support/reviews/OSS-FINRL-V2-001-review-codex2.md` approved the later parent
  state at commit `cc52b0ac3e33a6bb4e3d62d69e1ed7fcede1e111`, noting that the
  production path now constructs upstream FinRL `StockTradingEnv` from
  `FinRL==0.3.7` and trains stable-baselines3 PPO on CPU.
- `support/evidence/OSS-FINRL-V2-001/closeout.md` records parent closeout
  evidence and repeats that no registry write, broker session, order route,
  capital binding, GPU requirement, paper deployment, canary deployment, or
  live deployment authority is granted.

## Handoff Recommendation

Reviewer should treat this sidecar as a compact handoff index for
`OSS-FINRL-V2-001`, not as an additional approval gate for the already reviewed
parent implementation.

Suggested reviewer action:

1. Confirm this packet accurately reflects the existing parent review and
   evidence files.
2. If accurate, approve the sidecar and return it for normal owner closeout.
3. If the parent owner wants to preserve the summary, absorb only the evidence
   map or handoff notes into parent-level records; do not promote this sidecar
   into canonical architecture truth.

## Sidecar Verification

Performed by this sidecar:

- Read parent review and closeout records:
  - `support/reviews/OSS-FINRL-V2-001-review-codex.md`
  - `support/reviews/OSS-FINRL-V2-001-review-codex2.md`
  - `support/evidence/OSS-FINRL-V2-001/closeout.md`
- Inspected parent evidence JSON:
  - `support/evidence/OSS-FINRL-V2-001/admission_packet.json`
  - `support/evidence/OSS-FINRL-V2-001/artifact_bundle.json`
  - `support/evidence/OSS-FINRL-V2-001/candidate_packet.json`
  - `support/evidence/OSS-FINRL-V2-001/evaluation_summary.json`
  - `support/evidence/OSS-FINRL-V2-001/registry_entry.json`
- Parsed all parent evidence JSON with `python3 -m json.tool`.
- Ran a focused assertion check that confirmed:
  - `total_training_steps >= 1000`
  - `algorithm=ppo`
  - `device=cpu`
  - `stock_trading_env_used=true`
  - `torch_cuda_available=false`
  - `can_proceed=true`
  - `twse_stock_env.finrl_available=true`
  - `no_broker_session=true`
  - `no_registry_write=true`
  - registry checksum is SHA-256-prefixed and `trained_policy_ref` is present
- Ran `git diff --check -- support/sidecars/OSS-FINRL-V2-001/OSS-FINRL-V2-001-SIDECAR-REVIEW.md`.

Not rerun by this sidecar:

- Parent FinRL training or pytest suites. This sidecar is scoped to support
  packet review; it relies on the existing Codex2 approval and parent closeout
  verification for runtime behavior.
