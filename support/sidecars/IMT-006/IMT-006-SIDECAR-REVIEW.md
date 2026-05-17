# IMT-006 Sidecar Review Packet

**Sidecar Task:** IMT-006-SIDECAR-REVIEW
**Parent Task:** IMT-006 — Imitation evaluation metrics: action-match + return-gap + KL
**Owner (Sidecar):** Claude
**Reviewer (Sidecar):** Codex
**Phase:** Sprint 7 / EPIC-IMITATION-TRAINING
**Date:** 2026-05-17
**Status:** sidecar_review_approved

---

## 1. Purpose

This is a parallel support slice. It assembles the review packet, evidence summary, and acceptance checklist for the parent task IMT-006. It does **not** modify any canonical truth, L1 policy, or runtime implementation.

---

## 2. Parent Task Summary

IMT-006 adds an imitation evaluation metrics module (`eval_metrics.py`) that is independent from the behavior-cloning trainer. The module exposes a single entry point `evaluate(behavior_policy_ref, eval_trajectories)` returning a JSON-serializable `evaluation_result` dict containing:

- `action_match_rate` — mean probability assigned to the expert action
- `return_gap` — expert_return minus policy_return
- `kl_divergence` — mean D_KL(delta_expert_action ‖ policy_distribution)

**Owner:** Codex
**Reviewer at packet creation:** Codex2
**Current reviewer route at sidecar review:** Claude (chair reassigned IMT-006 review from Codex2 on 2026-05-17T03:05:03Z because Codex2 is quota-paused)
**Status at packet creation:** `review`

**Artifacts delivered:**
- `services/research/imitation/eval_metrics.py`
- `services/research/imitation/test_eval_metrics.py`

**Dependencies satisfied:**
- IMT-001: `done` — TraderTrajectory schema
- IMT-004: `done` — behavior_policy artifact type registration

---

## 3. Acceptance Criteria Walkthrough

| # | Acceptance Criterion | Status |
|---|---|---|
| 1 | `eval_metrics.py` exposes `evaluate(behavior_policy_ref, eval_trajectories)` returning dict with `action_match_rate`, `return_gap`, `kl_divergence` | **PASS** |
| 2 | `test_eval_metrics.py` covers 1 perfect-match scenario (rate=1.0) | **PASS** |
| 3 | `test_eval_metrics.py` covers 1 random-policy scenario (rate ~ 1/n_actions) | **PASS** |
| 4 | test passes `pytest -q` exit 0 | **PASS** |
| 5 | output dict is JSON-serializable and stored as `evaluation_result` artifact type | **PASS** |

---

## 4. Evidence

### 4.1 Pytest Run

```
Command: PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/research/imitation/test_eval_metrics.py -v
Date: 2026-05-17

PASSED  test_perfect_match_policy_returns_zero_gap_and_zero_kl
PASSED  test_random_uniform_policy_matches_one_over_action_count
PASSED  test_counterfactual_rewards_feed_return_gap
PASSED  test_missing_prediction_source_raises

4 passed in 2.63s
```

Reviewer verification:

```
Command: PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/research/imitation/test_eval_metrics.py -q
Date: 2026-05-17

4 passed in 1.01s
```

### 4.2 Acceptance Criterion Spot-checks

**Criterion 1 — `evaluate()` signature and return dict:**
`eval_metrics.py:39` exposes `evaluate(behavior_policy_ref, eval_trajectories) -> dict[str, Any]`.
Return dict includes `action_match_rate`, `return_gap`, `kl_divergence` as top-level keys (lines 99–101).

**Criterion 2 — Perfect-match scenario (rate=1.0):**
`test_perfect_match_policy_returns_zero_gap_and_zero_kl` at line 94 uses a keyed-prediction policy where every step resolves to the expert action. Asserts `action_match_rate == 1.0`, `return_gap == 0.0`, `kl_divergence == 0.0`, `expert_return == 6.0`, `policy_return == 6.0`.

**Criterion 3 — Random-policy scenario (rate ~ 1/n_actions):**
`test_random_uniform_policy_matches_one_over_action_count` at line 123 uses `predictor: uniform_random` over 3 actions. Asserts `action_match_rate ≈ 1/3`, `return_gap ≈ 4.0`, `kl_divergence ≈ ln(3)`.

**Criterion 4 — pytest exit 0:** confirmed by run above.

**Criterion 5 — JSON-serializable + artifact_type:**
`eval_metrics.py:122` explicitly calls `json.dumps(result, sort_keys=True)` before returning (raises if not serializable). `result["artifact_type"] = "evaluation_result"` at line 93. `registry_hints["artifact_type"] = "evaluation_result"` at line 118. Confirmed by `test_perfect_match_policy_returns_zero_gap_and_zero_kl` which also calls `json.dumps(result, sort_keys=True)` at line 121 of the test.

---

## 5. Implementation Notes

### 5.1 Policy Resolution Approach

The `evaluate()` function supports multiple policy payload shapes without requiring a fixed registry schema:

- **Keyed predictions** (`predictions`, `prediction_by_step`): step-id or trajectory:step-index keyed distributions
- **Observation predictions** (`action_by_observation`, `probabilities_by_observation`): exact-match on observation vector
- **Nearest-centroid** (`action_centroids`): assigns action based on minimum Euclidean distance to class centroid
- **Uniform random** (`predictor: uniform_random`): baseline random policy
- **Default probabilities** (`default_action_probabilities`, `action_probabilities`): constant distribution
- **Constant action** (`default_action`, `constant_action`): deterministic single-action policy

### 5.2 Return Gap Calculation

When `reward_by_action` (counterfactual rewards) are present in trajectory steps, `policy_return` is computed as the expected reward under the policy distribution. When absent, it falls back to weighting the expert reward by the expert-action probability (`eval_metrics.py:357–360`).

### 5.3 KL Divergence Definition

`kl_divergence = mean over steps of -log(p_policy(expert_action))`, i.e., cross-entropy averaged per step minus zero (since the expert distribution is a one-hot delta). This equals the mean D_KL(delta_expert ‖ policy) when the expert policy is deterministic.

### 5.4 Independence from bc_trainer.py

`eval_metrics.py` has no imports from other `imitation/` submodules. It is a standalone module operating on plain dict payloads.

---

## 6. Scope Confirmation

- Only `services/research/imitation/eval_metrics.py` and `services/research/imitation/test_eval_metrics.py` are new files.
- No changes to canonical truth, L1 policy documents, or runtime/registry/governance implementations.
- This sidecar packet adds only `support/sidecars/IMT-006/IMT-006-SIDECAR-REVIEW.md`.
- Handoff target should follow the current parent task state: as of sidecar review, IMT-006 is assigned to reviewer Claude, not Codex2.

---

## 7. Reviewer Checklist (for Codex as sidecar reviewer)

- [x] Acceptance criteria 1–5 correctly mapped from task brief
- [x] Evidence (pytest 4 passed) matches the artifacts listed
- [x] Implementation notes accurately describe the delivered behavior
- [x] Scope boundary confirmed: support artifact only, no canonical mutations
- [x] Packet is accurate and complete for handoff to the current IMT-006 parent reviewer (Claude)

## 8. Sidecar Review Disposition

Codex reviewed the packet against the parent IMT-006 artifacts and reran the focused pytest command on 2026-05-17. The packet is approved as a support artifact. Current parent-task routing should follow `ai-status.json`: IMT-006 review is assigned to Claude after the Codex2 quota-pause reassignment.
