# OSS-003-DOC-SYNC-001 Acceptance and Dependency Map (Sidecar)

**Parent Task**: `OSS-003-DOC-SYNC-001` - Reconcile OSS maturity docs with current Qlib TRL and OpenClaw evidence  
**Parent Owner**: `Claude`  
**Parent Reviewer**: `Gemini`  
**Parent Status**: `todo`  
**Sidecar Task**: `OSS-003-DOC-SYNC-001-SIDECAR-ACCEPTANCE`  
**Sidecar Owner**: `Codex`  
**Sidecar Reviewer**: `Claude`  
**Helper Kind**: `acceptance_packet`  
**Generated**: `2026-04-22`

> This is a support artifact only. It does not modify canonical truth, L1 policy
> documents, or core runtime / registry / governance implementations. It
> packages the current evidence anchors, document-drift map, and review boundary
> for `OSS-003-DOC-SYNC-001`.

---

## 1. Executive Summary

`OSS-003-DOC-SYNC-001` exists because the repo's OSS maturity summaries no
longer agree on the current state of `OpenClaw`, `Qlib`, and `TRL`.

Current repo truth is already stronger than some summary docs claim:

1. `OSS_INTEGRATION_CHECKLIST.md` already records `OpenClaw` as `governed`,
   `Qlib` as `smoke-tested`, and `TRL` as `smoke-tested`.
2. `integrations/openclaw/integration.md` already records the realized
   OpenClaw gateway adapter and real upstream gateway smoke path.
3. `integrations/qlib/integration.md` already records the governed Qlib adapter,
   `pyqlib==0.9.6`, and runnable smoke coverage.
4. `integrations/trl/integration.md` already records the governed TRL DPO
   adapter and runnable smoke coverage.

The main remaining drift is in summary documents that still describe missing
pins, missing adapters, or pre-smoke status:

1. `RESEARCH_BACKEND_MATURITY_MATRIX.md` still understates all three rows.
2. `services/learning/DEFERRED_OSS_ACTIVATION_MAP.md` still understates the
   `Qlib` row and still cites `pyqlib==0.9.1`.

This packet gives the parent owner a safe reconciliation target:

1. update the stale summary docs
2. preserve the stronger evidence anchors already landed elsewhere
3. keep the maturity claim at `governed` for `OpenClaw` and `smoke-tested` for
   `Qlib` / `TRL`, without overclaiming production activation

---

## 2. Source References

| Source | Why it matters |
|---|---|
| `ai-status.json` | Confirms parent/sidecar ownership and the parent acceptance targets. |
| `docs/reviews/2026-04-22-full-blueprint-gap-execution-packet.md` | Execution-origin packet that explicitly calls out the OSS maturity doc drift. |
| `OSS_INTEGRATION_CHECKLIST.md` | Current checklist anchor for the repo-wide OSS status table. |
| `RESEARCH_BACKEND_MATURITY_MATRIX.md` | Main stale summary surface that still understates OpenClaw, Qlib, and TRL maturity. |
| `services/learning/DEFERRED_OSS_ACTIVATION_MAP.md` | Follow-on activation map; Qlib portions are stale, TRL portions are mostly current. |
| `integrations/openclaw/integration.md` | Current evidence anchor for OpenClaw pin, adapter realization, and real gateway smoke. |
| `integrations/qlib/integration.md` | Current evidence anchor for Qlib pin, governed adapter, and smoke-tested baseline. |
| `integrations/trl/integration.md` | Current evidence anchor for TRL pin, governed adapter, and smoke-tested baseline. |
| `services/research/qlib/requirements.txt` | Confirms the current `pyqlib==0.9.6` pin. |
| `services/learning/trl/requirements.txt` | Confirms current TRL package pin and shows the remaining runtime-data gates are not code-gate blockers. |
| `integrations/openclaw/adapter/`, `services/research/qlib/adapter/`, `services/learning/trl/adapter/` | Repo-local implementation footprints that back the maturity claims. |

---

## 3. Repo-Current Truth Snapshot

| Truth item | Repo evidence | Implication for parent task |
|---|---|---|
| `OpenClaw` is no longer merely "adapter-started" | `OSS_INTEGRATION_CHECKLIST.md` marks it `governed`; `integrations/openclaw/integration.md` records realized runtime control + cron transport + live gateway smoke; adapter files exist under `integrations/openclaw/adapter/`. | The matrix must stop describing OpenClaw as `adapter-started` or as lacking end-to-end smoke closure. |
| `Qlib` is no longer only `criteria-defined` | `OSS_INTEGRATION_CHECKLIST.md` marks it `smoke-tested`; `integrations/qlib/integration.md` records `pyqlib==0.9.6`, `GovernedQlibDataAdapter`, `QlibLightGBMBackend`, `run_qlib_workflow`, smoke test, and unit coverage; adapter files exist under `services/research/qlib/adapter/`. | The matrix and deferred activation map must stop claiming the pin/adapter/smoke path is still missing. |
| `TRL` is no longer only `criteria-defined` | `OSS_INTEGRATION_CHECKLIST.md` marks it `smoke-tested`; `integrations/trl/integration.md` records `GovernedPreferencePairAdapter`, `StubDPOBackend`, `TRLDPOBackend`, `run_trl_dpo_workflow`, and smoke coverage; adapter file exists under `services/learning/trl/adapter/`. | The matrix must stop claiming TRL still lacks version pin, adapter, or smoke evidence. |
| Remaining blockers for `Qlib` and `TRL` are activation/runtime gates, not missing code baselines | `OSS_INTEGRATION_CHECKLIST.md`, `integrations/qlib/integration.md`, `integrations/trl/integration.md`, and `services/learning/DEFERRED_OSS_ACTIVATION_MAP.md §2` all describe remaining gating as production activation or runtime-data thresholds. | Parent edits must preserve the distinction between `smoke-tested` and `production-activated`; they should not downgrade to "missing adapter" and should not upgrade to "production path". |

Inference note:
the parent task is still `todo`, so none of the stale summary surfaces have been
reconciled yet. That inference is based on the current `ai-status.json` state
plus the still-present stale claims in the summary docs listed below.

---

## 4. Document Drift Map

Use this table as the concrete edit map for the parent owner.

| Target document | Stale claim now | Current repo anchor | Required parent action |
|---|---|---|---|
| `RESEARCH_BACKEND_MATURITY_MATRIX.md` - framework table | `OpenClaw` is `adapter-started` / `Activation-Ready`; `Qlib` and `TRL` are `criteria-defined` / `Activation-Ready`. | Checklist + integration docs show `OpenClaw=governed`, `Qlib=smoke-tested`, `TRL=smoke-tested`. | Update the three rows so the matrix agrees with the checklist and evidence docs. |
| `RESEARCH_BACKEND_MATURITY_MATRIX.md` - production mapping and next-activation narrative | Still describes `Qlib` / `TRL` as activation-ready and `OpenClaw` as an unclosed adapter path. | Current evidence distinguishes runnable smoke-tested baselines from later production gates. | Rewrite the narrative so it reflects runnable baselines already landed, while preserving that production activation is still gated. |
| `RESEARCH_BACKEND_MATURITY_MATRIX.md` - inconsistency risk / gap response sections | Says OpenClaw is not smoke-tested end-to-end and says Qlib / TRL still lack runnable adapters and smoke evidence. | OpenClaw gateway smoke passed on `2026-04-17`; Qlib and TRL smoke-tested adapter docs are present. | Remove claims about missing adapters/smoke paths for these three rows and replace them with the remaining real gaps only. |
| `services/learning/DEFERRED_OSS_ACTIVATION_MAP.md` - row summary table | `Qlib` still shown as `criteria-defined`, `pyqlib==0.9.1`, no governed adapter, no smoke path. | Requirements pin is `pyqlib==0.9.6`; governed adapter and smoke path exist. | Update the `Qlib` row to match current repo truth and keep the remaining activation gates only. |
| `services/learning/DEFERRED_OSS_ACTIVATION_MAP.md` - `Qlib` section | Still says Qlib needs the version pin, adapter, and smoke run before leaving `criteria-defined`. | Those code gates are already closed in the repo. | Rewrite the Qlib section so it mirrors the landed adapter/smoke baseline and leaves only data/activation gates. |
| `services/learning/DEFERRED_OSS_ACTIVATION_MAP.md` - `TRL` section | Mostly current already. | TRL integration and checklist agree on `smoke-tested`. | Preserve the TRL row/section as the stronger baseline; do not downgrade it while reconciling other docs. |
| `integrations/openclaw/integration.md`, `integrations/qlib/integration.md`, `integrations/trl/integration.md` | Not the drift source. These are evidence anchors. | They are the current implementation-backed references. | Use them as sources; do not weaken them to match stale summary docs. |

---

## 5. Parent Acceptance Checklist

Use this table to review `OSS-003-DOC-SYNC-001` closure.

| Parent acceptance target | Repo-current baseline | Required closeout evidence from parent owner | Status now |
|---|---|---|---|
| `RESEARCH_BACKEND_MATURITY_MATRIX.md` matches current `Qlib` / `TRL` / `OpenClaw` evidence | It does not currently match. The matrix still understates all three rows. | Update the matrix rows and narrative sections so they agree with the checklist + integration evidence. | PENDING |
| `DEFERRED_OSS_ACTIVATION_MAP.md` no longer understates landed adapters and smoke paths | `TRL` is mostly correct; `Qlib` is still stale. | Update the `Qlib` row/section to the current smoke-tested baseline and keep only the remaining activation gates. | PARTIAL |
| No canonical OSS doc disagrees on the maturity tier for those rows | The checklist and integration docs are ahead of the matrix and Qlib activation map. | After edits, `OpenClaw` must read as `governed`, `Qlib` as `smoke-tested`, and `TRL` as `smoke-tested` across the canonical OSS summary surfaces touched by the parent. | PENDING |

---

## 6. Scope Boundary - What The Parent Must Not Do

`OSS-003-DOC-SYNC-001` is a document-truth reconciliation slice. It should not
turn into a new implementation wave or a silent maturity re-interpretation.

| Out-of-scope move | Why it should be rejected |
|---|---|
| Downgrading `OSS_INTEGRATION_CHECKLIST.md` or the integration evidence docs to match stale summary prose | The stronger evidence-backed documents are already correct and should remain the truth anchors. |
| Upgrading `Qlib` or `TRL` from `smoke-tested` to production-ready / production-path | The remaining blockers are runtime-data or production activation gates, not yet-closed delivery. |
| Expanding the task into FinRL / RLlib / Ray Tune / W&B rewrites beyond incidental wording needed for consistency | The execution packet scoped this task to `Qlib`, `TRL`, and `OpenClaw` drift. |
| Treating this sidecar as authority to change L1 runtime or governance policy | This sidecar is support-only and must stay in acceptance/handoff territory. |

---

## 7. Dependency Map

### 7.1 Upstream Truth Anchors

| Dependency | Where recorded | Status | Relevance |
|---|---|---|---|
| Execution-origin gap record | `docs/reviews/2026-04-22-full-blueprint-gap-execution-packet.md` | COMPLETE | Establishes why `OSS-003-DOC-SYNC-001` exists and keeps the scope on doc drift. |
| Repo-wide OSS checklist truth | `OSS_INTEGRATION_CHECKLIST.md` | COMPLETE | Current maturity-status anchor for all three rows. |
| OpenClaw evidence pack | `integrations/openclaw/integration.md` and `integrations/openclaw/adapter/` | COMPLETE | Supplies the governed adapter realization and smoke-backed OpenClaw baseline. |
| Qlib evidence pack | `integrations/qlib/integration.md`, `services/research/qlib/requirements.txt`, and `services/research/qlib/adapter/` | COMPLETE | Supplies the actual pin, adapter, and smoke-backed Qlib baseline. |
| TRL evidence pack | `integrations/trl/integration.md`, `services/learning/trl/requirements.txt`, and `services/learning/trl/adapter/` | COMPLETE | Supplies the actual pin, adapter, and smoke-backed TRL baseline. |

### 7.2 Downstream Consumers

| Consumer | Current state | Relationship to parent task |
|---|---|---|
| `RESEARCH_BACKEND_MATURITY_MATRIX.md` | Active but stale | This is the main summary surface that must be brought back in line with the current evidence. |
| `services/learning/DEFERRED_OSS_ACTIVATION_MAP.md` | Active and partially stale | Must stop understating the Qlib baseline while preserving TRL's already-correct smoke-tested state. |
| Future OSS review / execution packets | Ongoing | They depend on the summary docs no longer contradicting the stronger checklist and integration anchors. |

### 7.3 Machine vs. Semantic Dependency Note

`ai-status.json` currently shows no machine-readable `depends_on` for the
parent or the sidecar. The dependency map above is therefore semantic and
evidence-based only. It is not a request to mutate task-board dependencies.

---

## 8. Suggested Parent Closeout Bundle

A minimal truthful closeout for `OSS-003-DOC-SYNC-001` should contain:

1. one commit/update that corrects the `OpenClaw`, `Qlib`, and `TRL` rows in
   `RESEARCH_BACKEND_MATURITY_MATRIX.md`
2. one commit/update that corrects the stale `Qlib` row and section in
   `services/learning/DEFERRED_OSS_ACTIVATION_MAP.md`
3. preserved references to `OSS_INTEGRATION_CHECKLIST.md` and the three
   integration evidence docs as the source of truth for the corrected maturity
   tiers
4. wording that keeps the remaining `Qlib` / `TRL` blockers framed as
   activation/runtime gates rather than missing code baselines

If the parent only restates existing evidence elsewhere but leaves the matrix or
Qlib activation map on the old maturity tiers, the task should not be approved.

Residual non-canonical note:
`services/learning/trl/requirements.txt` still contains a compatibility comment
that names `pyqlib 0.9.1`. That comment drift is outside the parent artifact
list and should be treated as optional follow-up hygiene, not as a blocker for
this document-sync slice.

---

## 9. Reviewer Checklist

| Check | Status | Evidence |
|---|---|---|
| Support artifact only | PASS | Only `support/sidecars/OSS-003-DOC-SYNC-001/OSS-003-DOC-SYNC-001-SIDECAR-ACCEPTANCE.md` is added by this sidecar. |
| No canonical truth edited by sidecar | PASS | No L0/L1 policy docs, runtime code, registry code, or canonical OSS docs were modified here. |
| Drift map is anchored to current repo evidence | PASS | Packet cites checklist, integration docs, requirements pins, and adapter paths. |
| Scope boundary preserves correct maturity claims | PASS | Packet keeps `OpenClaw=governed`, `Qlib=smoke-tested`, `TRL=smoke-tested`, without overclaiming production activation. |

---

## 10. Handoff to Reviewer (`Claude`)

This sidecar is ready for review as the acceptance packet for
`OSS-003-DOC-SYNC-001`.

What it gives you:

1. a concrete drift map showing exactly which summary surfaces are stale and
   which evidence anchors are already correct
2. a parent acceptance matrix that distinguishes "doc still stale" from
   "evidence already landed"
3. a scope boundary that prevents the parent task from downgrading correct docs
   or overclaiming production activation

Recommended reviewer stance:

1. approve this sidecar if it accurately reflects the current repo evidence for
   `OpenClaw`, `Qlib`, and `TRL`
2. when executing or reviewing the parent task, require the matrix and Qlib
   activation map to move onto the current maturity tiers
3. reject any parent closeout that still leaves `RESEARCH_BACKEND_MATURITY_MATRIX.md`
   describing `OpenClaw` as `adapter-started` or `Qlib` / `TRL` as only
   `criteria-defined`

---
*Generated by Codex as a sidecar `acceptance_packet` helper for
`OSS-003-DOC-SYNC-001`. This file is a support artifact and does not modify
canonical truth.*
