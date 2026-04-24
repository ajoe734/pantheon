# APP-003-QLIB-ACTIVATION-001 Review Packet (Sidecar)

**Parent Task**: `APP-003-QLIB-ACTIVATION-001`  
**Parent Owner (at closeout)**: `Codex2`  
**Parent Reviewer (at closeout)**: `Codex`  
**Parent Status**: `done` (archived `2026-04-24T19:34:17Z`)  
**Sidecar Task**: `APP-003-QLIB-ACTIVATION-001-SIDECAR-REVIEW`  
**Sidecar Owner**: `Codex2`  
**Sidecar Reviewer**: `Codex`  
**Helper Kind**: `review_packet`  
**Generated**: `2026-04-24`  
**Mutates canonical**: `no`

> This is a support artifact only. It does not modify L1 policy, canonical
> runtime truth, registry/governance behavior, or the archived parent `done`
> record. It packages a reviewer-facing packet and evidence summary for the
> Qlib activation slice so the assigned reviewer can verify packet accuracy
> without reopening the parent lane.

## 1. Findings First

No blocking findings were identified for this sidecar's scoped purpose:
preparing a truthful review packet and handoff for the already-closed parent
Qlib activation slice.

Non-blocking reviewer notes:

| Severity | Finding | Evidence | Why it does not block |
|---|---|---|---|
| Low | The launch sequence moved quickly (`todo` -> `in_progress` -> `review`) while this packet was being prepared. | `.orchestrator/task-briefs/app_003_qlib_activation_001_sidecar_review.md` captures the dispatch context, and `python3 scripts/ai_status.py show APP-003-QLIB-ACTIVATION-001-SIDECAR-REVIEW` now returns the active sidecar record with `status=review`. | This is sequencing context only; `ai-status.json` remains the durable truth for the current sidecar state. |
| Low | The archived parent handoff into review mentioned `13` tests, while the approved review and archive closeout now record `14` tests after the reviewer-added regression landed. | `ai-task-archive/tasks/APP-003-QLIB-ACTIVATION-001.json` and `docs/reviews/2026-04-24-app-003-qlib-activation-001-codex-review.md`. | This is a sequence/timing detail, not a contradiction in final truth. The final reviewed and archived count is `14`. |

## 2. Source Boundary

This packet uses only task-scoped and directly relevant evidence:

- `AI_COLLABORATION_GUIDE.md`
- `.orchestrator/task-briefs/app_003_qlib_activation_001_sidecar_review.md`
- `ai-status.json`
- `ai-task-archive/tasks/APP-003-QLIB-ACTIVATION-001.json`
- `docs/reviews/2026-04-24-app-003-qlib-activation-001-codex-review.md`
- `support/sidecars/APP-003-QLIB-ACTIVATION-001/APP-003-QLIB-ACTIVATION-001-SIDECAR-ACCEPTANCE.md`
- `integrations/qlib/activation_packet.md`
- `integrations/qlib/integration.md`
- `integrations/qlib/smoke_test.md`
- `services/learning/qlib/ACTIVATION_CRITERIA.md`
- `services/learning/DEFERRED_OSS_ACTIVATION_MAP.md`
- `RESEARCH_BACKEND_MATURITY_MATRIX.md`
- `OSS_INTEGRATION_CHECKLIST.md`
- `services/research/qlib/requirements.txt`
- `services/research/qlib/adapter/qlib_adapter.py`
- `services/research/qlib/smoke_test.py`
- `services/research/qlib/test_adapter.py`

The task brief also named the active planning-session file as relevant
canonical context. This pass checked that file for direct
`APP-003-QLIB-ACTIVATION-001` references and found none, so it does not
materially change this sidecar packet.

Intentionally not reviewed here:

- `current-work.md`
- full `ai-activity-log.jsonl`

Reason: the wake-up instructions explicitly prioritized task-scoped context and
said not to scan the global derived summary or full historical log unless the
task brief required it.

## 3. Current Snapshot

| Item | Current truth | Review implication |
|---|---|---|
| Parent lifecycle | `ai-task-archive/tasks/APP-003-QLIB-ACTIVATION-001.json` records the parent archived as `status=done` / `terminal_outcome=completed` at `2026-04-24T19:34:17Z`, with delivery commit `9ee259fe28a39c9ce3c354fdd0ed4ea264233c62`. | This sidecar must not claim authority to reopen the archived parent. It only summarizes the already-closed state for reviewer intake. |
| Parent closeout truth | The archived parent `next` field says smoke was revalidated on `2026-04-24`, `14` unit tests plus smoke assertions passed, and Qlib remains `smoke-tested` and production-blocked on RS-003 candidate, governed dataset proof, and target StrategySpec binding. | Reviewer should verify the packet preserves that bounded maturity claim and does not upgrade Qlib to production-activated. |
| Parent review path | `docs/reviews/2026-04-24-app-003-qlib-activation-001-codex-review.md` records approval, no blocking findings, the real-backend dataset-contract correction, the new regression test, and refreshed smoke evidence. | The sidecar should summarize the already-approved disposition rather than create a second parent review path. |
| Existing support coverage | `support/sidecars/APP-003-QLIB-ACTIVATION-001/APP-003-QLIB-ACTIVATION-001-SIDECAR-ACCEPTANCE.md` already packages the acceptance read, dependency map, and archival caveats. | This review packet should complement that acceptance artifact by giving the reviewer a compact handoff, not duplicate parent execution work. |
| Sidecar lifecycle | `python3 scripts/ai_status.py show APP-003-QLIB-ACTIVATION-001-SIDECAR-REVIEW` returns the active helper task with owner `Codex2`, reviewer `Codex`, and `status=review`. | The reviewer can now decide whether this support packet is accurate enough to move to `review_approved`; final closure still belongs to the owner. |

## 4. Parent Review Matrix

| Review question | Evidence reviewed | Result |
|---|---|---|
| Does the archived parent still prove the three acceptance criteria without reopening the task? | `ai-task-archive/tasks/APP-003-QLIB-ACTIVATION-001.json` and `support/sidecars/APP-003-QLIB-ACTIVATION-001/APP-003-QLIB-ACTIVATION-001-SIDECAR-ACCEPTANCE.md` | PASS |
| Does the approved parent review still show no blocking findings and explain what changed during review? | `docs/reviews/2026-04-24-app-003-qlib-activation-001-codex-review.md` | PASS |
| Do the activation surfaces still keep Qlib at `smoke-tested` / data-gated rather than production-activated? | `integrations/qlib/activation_packet.md`, `integrations/qlib/integration.md`, `services/learning/qlib/ACTIVATION_CRITERIA.md`, `services/learning/DEFERRED_OSS_ACTIVATION_MAP.md`, `RESEARCH_BACKEND_MATURITY_MATRIX.md`, `OSS_INTEGRATION_CHECKLIST.md` | PASS |
| Do the repo-local implementation surfaces still back the packet's evidence claims? | `services/research/qlib/requirements.txt`, `services/research/qlib/adapter/qlib_adapter.py`, `services/research/qlib/smoke_test.py`, `services/research/qlib/test_adapter.py`, `integrations/qlib/smoke_test.md` | PASS |

## 5. Evidence Summary

### 5.1 Live Parent Truth

| Surface | What it proves | Why it matters |
|---|---|---|
| `ai-task-archive/tasks/APP-003-QLIB-ACTIVATION-001.json` | The parent is archived `done` with delivery commit `9ee259fe28a39c9ce3c354fdd0ed4ea264233c62`, final acceptance list, approved review note, and closeout summary stating Qlib remains `smoke-tested` and data-gated after the `2026-04-24` revalidation. | This is the durable closed truth the sidecar must summarize without re-authorizing or reopening the parent. |
| `docs/reviews/2026-04-24-app-003-qlib-activation-001-codex-review.md` | The reviewer disposition was `approved` with no blocking findings and with explicit note that the real Qlib backend now follows the official dataset contract, a regression test was added, and smoke evidence was refreshed. | It is the parent's canonical review record and remains valid after archival. |
| `support/sidecars/APP-003-QLIB-ACTIVATION-001/APP-003-QLIB-ACTIVATION-001-SIDECAR-ACCEPTANCE.md` | The support-side acceptance packet already consolidates the parent acceptance read, dependency map, verification snapshot, and archival caveats. | It reduces duplication and gives this review packet a stable support artifact to point at. |

### 5.2 Landed Supportable Surfaces

| Surface | Current read | Why it matters |
|---|---|---|
| `integrations/qlib/activation_packet.md` | The first governed LightGBM activation packet exists, names `QlibLightGBMBackend`, keeps `artifact_state=draft`, `deployment_summary.current_stage=none`, and leaves the RS-003 candidate, governed dataset proof, and target StrategySpec binding as open blockers. | This is the reviewer-facing activation truth surface the parent task was closing around. |
| `integrations/qlib/integration.md` | The repo uses `pyqlib==0.9.6`, keeps Qlib behind a governed adapter boundary, and states the baseline is runnable but not production-activated. | It grounds the activation packet in the actual integration boundary. |
| `integrations/qlib/smoke_test.md` | The smoke evidence records `2026-04-24` revalidation with `assertions: OK` and a `Ran 14 tests` summary. | It supports the archive closeout note and reviewer-approved evidence refresh. |
| `services/learning/qlib/ACTIVATION_CRITERIA.md` | The real production gate remains the RS-003 candidate, governed dataset depth (>=50 instruments, >=2 years OHLCV), and supervised-alpha fit target. | It defines why Qlib remains blocked despite the runnable adapter baseline. |
| `OSS_INTEGRATION_CHECKLIST.md`, `services/learning/DEFERRED_OSS_ACTIVATION_MAP.md`, and `RESEARCH_BACKEND_MATURITY_MATRIX.md` | Canonical OSS summaries agree that Qlib is `smoke-tested` / activation-ready rather than live production research path. | Reviewer should confirm the packet does not drift from canonical maturity truth. |
| `services/research/qlib/requirements.txt`, `services/research/qlib/adapter/qlib_adapter.py`, `services/research/qlib/smoke_test.py`, and `services/research/qlib/test_adapter.py` | The repo-local implementation still exposes `QLIB_VERSION_PIN = "0.9.6"`, the governed adapter path, the smoke entrypoint, and the regression-backed test surface. | They are the concrete implementation anchors behind the reviewed and archived claims. |

### 5.3 Repo-Local Verification From This Sidecar Pass

This sidecar did not rerun the Qlib workflow or change runtime code. It
revalidated record and surface alignment only.

| Check | Result |
|---|---|
| `python3 scripts/ai_status.py show APP-003-QLIB-ACTIVATION-001` | archived `done` snapshot confirmed |
| `python3 scripts/ai_status.py show APP-003-QLIB-ACTIVATION-001-SIDECAR-REVIEW` | active sidecar review task confirmed |
| `rg -n 'APP-003-QLIB-ACTIVATION-001' docs/02-architecture/consensus/sessions/phase7-2026-04-18-ep4-ep5-execution-proof/planning-session.json` | no direct references |
| Read archive snapshot, parent review, existing sidecar acceptance packet, and all cited Qlib evidence surfaces | all files present on disk and aligned on `smoke-tested` / data-gated truth |

Review note:

1. This support slice is intentionally narrower than the parent execution lane.
2. Approval of this packet should mean only that the sidecar summary is
   accurate and reviewer-ready.

## 6. What Reviewer Should Reject

| Incorrect move | Why it is wrong |
|---|---|
| Treating this sidecar as authority to reopen the archived parent or re-run the parent's `review_approved -> done` transition | The parent is already archived `done`; this helper has no authority to alter that lifecycle record. |
| Reading the older `13`-test handoff count as the final truth | The final reviewed and archived truth is `14` tests after the review-stage regression was added. |
| Approving a packet that upgrades Qlib from `smoke-tested` to production-ready or production-live | The canonical docs and activation packet still keep the RS-003, governed dataset, and StrategySpec blockers open. |
| Rejecting the packet because it does not introduce new implementation work | This slice is restricted to support artifacts and reviewer handoff only. |

## 7. Reviewer Handoff For Codex

Please verify only these support-side questions:

1. This file faithfully reflects the archived parent `done` state and the
   approved parent review without implying a reopen.
2. The packet uses the existing acceptance sidecar and Qlib evidence surfaces
   as support material rather than inventing new truth.
3. The maturity wording stays precise: Qlib is runnable and `smoke-tested`,
   but still blocked from production activation by RS-003 candidate readiness,
   governed dataset proof, and target StrategySpec binding.
4. If approved, this helper can move to `review_approved`; any mainline
   absorption remains the parent owner's decision, not the sidecar's.

## 8. Recommended Disposition

Approve this sidecar if the packet remains a truthful, support-only wrapper
around the archived parent closeout, approved review, existing acceptance
packet, and current Qlib activation evidence surfaces. Reject only for a
concrete truth mismatch, missing referenced artifact, or sidecar scope
violation.

## 9. Verification Commands

- `python3 scripts/ai_status.py show APP-003-QLIB-ACTIVATION-001`
- `python3 scripts/ai_status.py show APP-003-QLIB-ACTIVATION-001-SIDECAR-REVIEW`
- `rg -n 'APP-003-QLIB-ACTIVATION-001' docs/02-architecture/consensus/sessions/phase7-2026-04-18-ep4-ep5-execution-proof/planning-session.json`
- `find support/sidecars/APP-003-QLIB-ACTIVATION-001 -maxdepth 1 -type f | sort`
- `sed -n '1,220p' ai-task-archive/tasks/APP-003-QLIB-ACTIVATION-001.json`
- `sed -n '1,220p' docs/reviews/2026-04-24-app-003-qlib-activation-001-codex-review.md`
- `sed -n '1,260p' support/sidecars/APP-003-QLIB-ACTIVATION-001/APP-003-QLIB-ACTIVATION-001-SIDECAR-ACCEPTANCE.md`
- `sed -n '1,220p' integrations/qlib/activation_packet.md`
- `sed -n '1,220p' integrations/qlib/integration.md`
- `sed -n '1,220p' integrations/qlib/smoke_test.md`
- `sed -n '1,220p' services/learning/qlib/ACTIVATION_CRITERIA.md`
- `rg -n 'Qlib' OSS_INTEGRATION_CHECKLIST.md RESEARCH_BACKEND_MATURITY_MATRIX.md services/learning/DEFERRED_OSS_ACTIVATION_MAP.md`
- `rg -n 'QLIB_VERSION_PIN|QlibLightGBMBackend|run_qlib_workflow' services/research/qlib/adapter/qlib_adapter.py`
- `rg -n 'pyqlib==0.9.6' services/research/qlib/requirements.txt`
- `rg -n 'Ran 14 tests|assertions: OK|2026-04-24' integrations/qlib/smoke_test.md`

---
*Prepared for the `APP-003-QLIB-ACTIVATION-001-SIDECAR-REVIEW` support slice.
This file is support-only and does not modify canonical truth.*
