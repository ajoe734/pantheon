# QLIB-ACT-001 Review Packet (Sidecar)

**Parent Task:** `QLIB-ACT-001`  
**Parent Owner:** `Claude`  
**Parent Reviewer:** `Codex2`  
**Parent Status at packet prep:** `review`  
**Sidecar Task:** `QLIB-ACT-001-SIDECAR-REVIEW`  
**Sidecar Owner:** `Codex`  
**Sidecar Reviewer:** `Claude`  
**Helper Kind:** `review_packet`  
**Prepared:** `2026-05-12`  
**Mutates canonical:** `no`

> Scope constraint: support artifact only. This packet does not modify L1
> policy, registry truth, runtime implementation, Qlib adapter behavior, or
> governance semantics. It packages the reviewer-facing evidence surface for
> the parent `QLIB-ACT-001` StrategySpec so the parent owner can decide what,
> if anything, to absorb before or during Codex2 review.

## 1. Findings First

No blocking sidecar-scope findings were identified. The parent artifacts are
present, the parent delivery commit is task-scoped, and the core acceptance
surface for an RS-003 draft StrategySpec is reviewable.

Reviewer attention items for the parent owner:

| Severity | Finding | Evidence | Suggested reviewer action |
|---|---|---|---|
| Low | A few status phrases in the parent artifacts can be read as stale after the handoff entered `review`. | `integrations/qlib/activation_packet.md` still says `QLIB-ACT-001 pending handoff` / `handoff pending`, while active state shows `QLIB-ACT-001` is already in `review` and awaiting `Codex2`. | Claude can either treat this as harmless wording or patch the parent artifact before Codex2 approval. |
| Low | One checklist item names `Codex reviewer approval` while the durable reviewer is `Codex2`. | `services/registry/strategy-specs/qlib-tw-cross-sectional-alpha-v1.md` section 10 vs file header and `ai-status.json` task state. | If exact agent naming matters for approval records, align the wording to `Codex2`; no evidence suggests the assigned reviewer changed. |

These notes are not sidecar blockers because this helper is not approving the
parent. They are included so the parent owner and Codex2 reviewer do not have
to rediscover the wording issues.

## 2. Source Boundary

This packet used only task-scoped or directly referenced material:

- `AI_COLLABORATION_GUIDE.md`
- `.orchestrator/task-briefs/qlib_act_001_sidecar_review.md`
- `.orchestrator/skills/task-closeout-finalization.md`
- `ai-status.json`
- `docs/02-architecture/consensus/sessions/phase6-2026-05-01-pantheon-p0-paper-loop/planning-session.json`
- `services/registry/strategy-specs/qlib-tw-cross-sectional-alpha-v1.md`
- `integrations/qlib/activation_packet.md`
- delivery commit `f4b32a7b0e774f677e8e77e915f9da7597693f47`

Intentionally not reviewed:

- `current-work.md`
- full `ai-activity-log.jsonl`

Reason: the wake-up instructions explicitly restricted the initial context to
task-scoped files and said not to scan global derived summaries or the full
activity log unless the task brief required it.

The task brief listed the phase6 planning session as relevant context. This
pass checked its top-level session metadata and searched for direct
`QLIB-ACT-001`, `RS-003`, `LightGBM`, and `qlib` references; no direct matches
were found, so it does not materially change this sidecar packet.

## 3. Current Parent Snapshot

| Item | Current read |
|---|---|
| Parent lifecycle | `QLIB-ACT-001` is in `review`, owned by `Claude`, reviewed by `Codex2`. |
| Parent delivery commit | `f4b32a7b0e774f677e8e77e915f9da7597693f47` (`QLIB-ACT-001: finalize RS-003 baseline StrategySpec for TW cross-sectional equity alpha`). |
| Files changed by parent commit | `services/registry/strategy-specs/qlib-tw-cross-sectional-alpha-v1.md`, `integrations/qlib/activation_packet.md`. |
| Registry ID issued | `qlib-tw-cross-sectional-alpha-spec-v1`. |
| Artifact state | `draft`. |
| Deployment stage | `none`. |
| Parent handoff state | Active handoff from `Claude` to `Codex2` is pending reviewer action. |

This sidecar does not move the parent task, does not approve the parent task,
and does not request registry admission.

## 4. Parent Acceptance Matrix

| Parent acceptance criterion | Evidence surface | Sidecar read |
|---|---|---|
| StrategySpec problem statement defined | `services/registry/strategy-specs/qlib-tw-cross-sectional-alpha-v1.md` section 1 | PASS: supervised cross-sectional TW equity alpha prediction is defined as scoring-only, not an order-routing policy. |
| Universe defined | StrategySpec section 2 | PASS: TWSE listed + TPEx listed equities, common-stock filters, target top turnover universe, and minimum floors of >=50 instruments / >=2 years / daily frequency are stated. |
| Label and horizon defined | StrategySpec sections 3 and 5 | PASS: 5 trading-day forward return is primary, cross-sectional z-score normalization is stated, 1-day IC evaluation is secondary, and time-ordered splits are defined. |
| Evaluation metric defined | StrategySpec section 7 | PASS: IC, IR, annualized return, Sharpe, max drawdown, turnover, and feature stability are specified; test IC gate is >=0.03 and test Sharpe must be >=80% of validation Sharpe. |
| Why supervised LightGBM vs RL explicit | StrategySpec section 6.2 | PASS: the document distinguishes prediction from sequential decision-making and defers RL until the supervised path is exhausted and LP-005 entry criteria are met. |
| Why not TRL explicit | StrategySpec section 6.3 | PASS: TRL is rejected because the task has market-observable forward returns, not human preference labels. |
| RS-003 replication gate evidence attached or referenced | StrategySpec section 8 and activation packet sections 3 / 3.0 | PASS WITH PENDING DOWNSTREAM: StrategySpec is the candidate submission surface; governed dataset proof and LightGBM activation run are correctly left to QLIB-ACT-002 and QLIB-ACT-003. |
| Candidate registry artifact ID issued | StrategySpec section 9 and activation packet section 3.0 | PASS: `qlib-tw-cross-sectional-alpha-spec-v1` is the draft candidate registry ID and downstream reference key. |
| No production registry write before review approval | StrategySpec sections 8-11 and activation packet sections 1 / 4 / 6 | PASS: packet keeps registry write authority constrained to `registry_service_only`, requests no production write, and preserves review/admission gates. |
| `artifact_state=draft` preserved | StrategySpec header and section 9; activation packet sections 3 / 4 | PASS: both parent artifacts keep draft state. |
| `deployment_summary.current_stage=none` preserved | StrategySpec header and section 9; activation packet sections 1 / 3 / 4 | PASS: both parent artifacts keep deployment stage `none`. |

## 5. Evidence Summary

### 5.1 StrategySpec Evidence

`services/registry/strategy-specs/qlib-tw-cross-sectional-alpha-v1.md` is the
primary parent deliverable. It currently provides:

- Registry identity: `strategy_spec`, registry ID
  `qlib-tw-cross-sectional-alpha-spec-v1`, strategy ID
  `tw-cross-sectional-equity-alpha`, version `1.0.0`.
- Governance state: `Artifact State: draft`, `Deployment Stage: none`,
  owner `Claude`, reviewer `Codex2`.
- Problem framing: supervised score generation from point-in-time OHLCV,
  explicitly not buy/sell/hold execution.
- Universe and data floors: TWSE + TPEx listed common equities, >=50
  instruments, >=2 years daily history, >=504 trading days.
- Label: 5-day forward return z-scored within the cross-section, with PIT
  guards and a 1-day IC evaluation alternative.
- Baseline model: LightGBM `LGBModel` through `pyqlib==0.9.6`, with concrete
  hyperparameters and 13 OHLCV-derived features.
- Gate conditions: test IC >=0.03, test Sharpe >=80% of validation Sharpe,
  pending governed dataset proof and activation run.

### 5.2 Activation Packet Evidence

`integrations/qlib/activation_packet.md` provides the integration-facing
activation surface and currently confirms:

- Qlib remains blocked from production registry admission, paper/canary/live
  deployment, broker sessions, capital binding, and order routing.
- The first governed LightGBM activation bundle is prepared only as a
  reviewable `draft -> candidate` handoff path.
- Production dataset proof still belongs to QLIB-ACT-002 and must satisfy
  provider entitlement, freshness, PIT, durable storage, audit, and no-order
  controls.
- The governed LightGBM run still belongs to QLIB-ACT-003 and must cite
  `qlib-tw-cross-sectional-alpha-spec-v1`.
- The packet preserves `artifact_state=draft`,
  `deployment_summary.current_stage=none`, and `registry_service_only`.

### 5.3 Delivery Commit Evidence

`git show --name-status --format=fuller --no-renames f4b32a7b` shows a
task-scoped parent commit with only two modified files:

- `integrations/qlib/activation_packet.md`
- `services/registry/strategy-specs/qlib-tw-cross-sectional-alpha-v1.md`

The commit body records the acceptance summary, no-production-write boundary,
verification note, `LLM-Agent: Claude`, `Task-ID: QLIB-ACT-001`, and
`Reviewer: Codex2`.

## 6. Remaining Parent Gaps

These are expected downstream gaps, not failures of QLIB-ACT-001:

| Gap | Owning future surface | Current sidecar read |
|---|---|---|
| Governed TWSE/TPEx dataset proof with >=50 instruments and >=2 years OHLCV | `QLIB-ACT-002` | Pending; correctly not claimed by parent StrategySpec. |
| First governed LightGBM activation run with IC / Sharpe evidence | `QLIB-ACT-003` | Pending; correctly not claimed by parent StrategySpec. |
| Registry admission / artifact state promotion | Reviewer and registry admission flow after downstream evidence | Pending; no production registry write occurs here. |

## 7. What Reviewer Should Reject

Reject this sidecar packet if it is interpreted as any of the following:

| Incorrect interpretation | Why it is wrong |
|---|---|
| Parent approval of `QLIB-ACT-001` | Only `Codex2` is the assigned parent reviewer. This sidecar reviewer is `Claude`, and this helper only packages review support. |
| Registry write authorization | The parent artifacts are still draft and non-writing; registry admission is downstream. |
| Production activation readiness | QLIB-ACT-002 and QLIB-ACT-003 remain pending. |
| Permission to edit L1 policy or runtime behavior | This sidecar is support-only and made no runtime, registry, governance, or canonical policy edits. |

## 8. Handoff For Claude

Please review this support packet only for accuracy and usefulness:

1. It should faithfully summarize the parent StrategySpec and activation packet.
2. It should preserve the distinction between QLIB-ACT-001 StrategySpec review
   and the later QLIB-ACT-002 / QLIB-ACT-003 evidence gates.
3. It should not imply parent approval, registry admission, deployment stage
   movement, or production activation.
4. If the low-severity wording notes are worth absorbing, make that decision in
   the parent lane or leave them for Codex2 parent review.

Recommended sidecar disposition: approve if this packet is accurate as a
support-only evidence summary and reviewer handoff.

## 9. Verification Commands Run

- `AI_NAME=Codex ./scripts/ai-status.sh show QLIB-ACT-001-SIDECAR-REVIEW`
- `sed -n '1,280p' services/registry/strategy-specs/qlib-tw-cross-sectional-alpha-v1.md`
- `sed -n '1,280p' integrations/qlib/activation_packet.md`
- `git show --stat --oneline --decorate --no-renames f4b32a7b`
- `git show --name-status --format=fuller --no-renames f4b32a7b`
- `sed -n '1,160p' docs/02-architecture/consensus/sessions/phase6-2026-05-01-pantheon-p0-paper-loop/planning-session.json`
- `rg -n "QLIB-ACT-001|RS-003|LightGBM|qlib" docs/02-architecture/consensus/sessions/phase6-2026-05-01-pantheon-p0-paper-loop/planning-session.json`

## 10. Closeout Addendum

Claude reviewed and approved this sidecar packet on 2026-05-12. The review
record is preserved at
`support/sidecars/QLIB-ACT-001/review-qlib-act-001-sidecar-review-claude.md`.

The approval confirms that this packet is support-only, that it accurately
summarizes the parent `QLIB-ACT-001` evidence surface, and that it does not
modify canonical truth, runtime, registry, or governance implementation. The
two low-severity wording findings listed above were subsequently addressed in
the parent closeout commit `60cb3c11`.

---

Prepared for `QLIB-ACT-001-SIDECAR-REVIEW`. This file is support-only and does
not modify canonical truth.
