# Sidecar Acceptance Packet: P2-TRL-RUNTIME-DATA-ACTIVATION-001

**Sidecar ID**: P2-TRL-RUNTIME-DATA-ACTIVATION-001-SIDECAR-ACCEPTANCE
**Parent Task**: P2-TRL-RUNTIME-DATA-ACTIVATION-001 — TRL runtime-data activation and real DPO smoke
**Sidecar Kind**: acceptance_packet
**Owner**: Claude
**Reviewer**: Codex2
**Parent Owner**: Codex2
**Parent Reviewer**: Codex
**Date Prepared**: 2026-05-01
**Status**: ready for reviewer handoff

---

## Purpose

This packet is a parallel support artifact for P2-TRL-RUNTIME-DATA-ACTIVATION-001. It does three things:

1. Consolidates the acceptance checklist the parent owner must satisfy before closing the parent task.
2. Maps every declared dependency — both satisfied and still open — so the parent owner can begin without re-reading the full document chain.
3. Provides a structured handoff surface for Codex2 (parent reviewer) to cross-check the parent task evidence bundle when it arrives.

This packet does **not** modify canonical truth files, registry state, governance docs, or the parent task's own implementation artifacts. It is a support artifact only.

---

## 1. Parent Task Summary

**Goal**: Advance TRL from `smoke-tested / runtime-gated` to a confirmed runtime-data activation: connect the FB-002 preference-pair runtime feed, execute a real TRL DPO backend run on bounded test data (or record an explicit install/config failure — not a silent stub pass), and produce a model artifact with checksum, an evaluator packet, and a registry candidate handoff.

**Hard boundaries** (must not be crossed by the parent task):
- No direct governance write from the TRL path.
- No order-capable route opening; `deployment_summary.current_stage` must remain `none`.
- No paper/canary/live deployment stage promotion.
- No raw secrets in any produced artifact or log.
- No broker, LEAN, or capital-binding route added.

---

## 2. Parent Task Acceptance Criteria (verbatim from ai-status.json)

| # | Criterion | Evidence required |
|---|---|---|
| AC-1 | TRL reads governed FB-002 preference pairs and records volume/quality gate evidence | FB-002 evidence snapshot: event count ≥200, strategy-family coverage ≥2, all three action types, pair-construction summary with dedup and linkage check, operator count |
| AC-2 | Real TRL DPO backend runs with bounded test data **or** fails with explicit dependency/config evidence — not a silent stub | Either: TRL backend run log showing real DPO execution with `TRLDPOBackend`, bounded pair count, and metrics; OR: explicit error trace proving install/config failure rather than silent stub fallback |
| AC-3 | Model artifact checksum, evaluator packet, and registry candidate handoff are produced without direct governance write or order routing | Artifact envelope with `artifact_type=model_artifact`, `artifact_state=draft` or `candidate`, `deployment_summary.current_stage=none`, `sha256:` checksum, governed storage path; plus evaluator packet citing the artifact ID; plus registry handoff packet with no governance write or order route |

All three criteria must be satisfied simultaneously. Partial satisfaction of AC-1 while AC-2 shows only a silent stub pass is **not acceptable** — that is the specific failure mode that P2-OSS-ACTIVATE-001 flagged.

---

## 3. Dependency Map

### 3.1 Satisfied dependencies

| Dependency | Status | Evidence location |
|---|---|---|
| `trl>=0.8.0,<0.10.0` version pin | Satisfied | `services/learning/trl/requirements.txt`; compatibility verified against DSPy v2.4.5, imitation v1.0.1, MLflow 3.10.1, pyqlib 0.9.6 |
| `GovernedPreferencePairAdapter` implementation | Satisfied | `services/learning/trl/adapter/trl_adapter.py` |
| `StubDPOBackend` (CI smoke) | Satisfied | same file; 29 unit tests pass |
| `TRLDPOBackend` (real upstream) implementation | Satisfied — activatable | same file; distilbert-base-uncased base; requires `trl` package install and model hub access |
| `run_trl_dpo_workflow()` entrypoint | Satisfied | same file; emits `artifact_state=draft`, `deployment_summary.current_stage=none` |
| Pre-activation preflight scaffold | Satisfied | `services/learning/trl/preflight.py`; non-writing; reports FB-002 event volume, preference-pair volume, imitation-artifact readiness, downstream-consumer readiness |
| Smoke test and unit coverage baseline | Satisfied | `services/learning/trl/smoke_test.py` (5 synthetic pairs, stub backend); 29 unit tests; revalidated 2026-04-29 |
| Activation packet (runtime-gated) | Prepared | `integrations/trl/activation_packet.md`; defines the required evidence bundle; does not claim production activation |
| P2-OSS-ACTIVATE-001 (OSS production data posture) | Done | `services/learning/OSS_ACTIVATION_NOTES.md`; TRL gate defined as `P2-TRL-RUNTIME-DATA-ACTIVATION-001` |
| Worker env gate | Satisfied | Worker requires `PANTHEON_TRL_ACTIVATION_READY_ENABLED=1`; gate is explicit and not bypassed by parent task scope |

### 3.2 Open runtime-data dependencies (cannot be pre-staged)

| Dependency | Current read | What the parent task must prove |
|---|---|---|
| FB-002 feedback event volume: ≥200 governed events spanning ≥2 strategy families and all three action types | Not yet proven by repo-local evidence | Attach a governed FB-002 evidence snapshot with event counts, strategy-family split, and action distribution; cite the query window |
| Preference-pair volume: ≥100 valid pairs constructable from those events | Not yet proven; smoke baseline proves only 5 synthetic pairs | Attach pair-construction summary: valid pairs after governance filtering, dedup rule applied, artifact-linkage completeness, operator count |
| LP-002 imitation baseline active with `artifact_state=approved` artifacts | Not yet proven by live registry evidence | Cite the approved LP-002 registry artifact ID(s) and the evidence that keeps them active; run `services/learning/trl/preflight.py` and attach the imitation-readiness section of the report |
| Baseline preference-model performance before DPO activation | Not yet attached; smoke stub gives only `accuracy: 0.6` | Attach baseline experiment evidence (logistic or GBT) with holdout accuracy ≥0.65 and AUC-ROC ≥0.70, source dataset window, and strategy-family coverage |
| Downstream consumer readiness: EV-001, LP-005, or LP-001 | Not yet proven as an active runtime consumer | Cite one concrete consumer lane, the consuming contract or worker path, and readiness evidence for that lane |

### 3.3 Dependency-chain summary

```
P2-OSS-ACTIVATE-001 (done)
  └── P2-TRL-RUNTIME-DATA-ACTIVATION-001 (todo → in_progress under Codex2)
        ├── Repo-local adapter gates: CLOSED (AC gates are data, not code)
        ├── FB-002 event volume gate:        OPEN (runtime data)
        ├── Preference-pair volume gate:     OPEN (runtime data)
        ├── LP-002 imitation gate:           OPEN (runtime registry state)
        ├── Baseline-model gate:             OPEN (runtime experiment)
        └── Downstream consumer gate:        OPEN (runtime consumer readiness)
```

---

## 4. Pre-flight Checklist for Parent Owner (Codex2)

Before invoking `TRLDPOBackend` or producing a registry handoff, run these steps in order:

1. **Confirm env gate**: Verify `PANTHEON_TRL_ACTIVATION_READY_ENABLED=1` is set in the execution environment.
2. **Run stub smoke baseline** to confirm adapter is still intact:
   ```bash
   python3 services/learning/trl/smoke_test.py
   # Expect: assertions: OK, artifact_state=draft, deployment_stage=none, direct_live_influence=false
   python3 -m unittest discover -s services/learning/trl -p 'test_*.py'
   # Expect: 29 tests passed
   ```
3. **Run preflight** against runtime evidence:
   ```bash
   python3 services/learning/trl/preflight.py
   # Attach the full preflight output to the task evidence; check imitation-artifact and FB-002 sections
   ```
4. **Assemble FB-002 evidence snapshot** (AC-1): event count, strategy-family split, approve/edit/reject distribution, query window.
5. **Assemble preference-pair dataset summary** (AC-1): valid pairs, dedup rule, linkage completeness, operator count.
6. **Cite imitation prerequisite proof** (AC-1): approved LP-002 registry artifact IDs, activation date, active stage proof.
7. **Attach baseline-model evidence** (AC-1): model type, holdout accuracy, AUC-ROC, dataset window.
8. **Cite downstream consumer proof** (AC-1): which consumer lane, what contract path, why it is ready.
9. **Invoke TRL backend** with bounded test data (AC-2):
   ```bash
   python3 services/learning/trl/smoke_test.py --backend trl
   # If install/config fails: record the full error trace; do NOT fall back to stub silently
   ```
10. **Verify artifact output** (AC-3): `artifact_state=draft` or `candidate`, `deployment_summary.current_stage=none`, `sha256:` checksum, governed storage path under `learning/trl/`.
11. **Produce evaluator packet** citing the artifact ID (AC-3).
12. **Produce registry candidate handoff packet** (AC-3): no governance write, no order route.

---

## 5. Reviewer Gate Summary (for Codex2)

When the parent task (P2-TRL-RUNTIME-DATA-ACTIVATION-001) arrives for review, Codex2 should verify:

| Review check | Pass condition |
|---|---|
| FB-002 evidence snapshot attached | Event count ≥200, ≥2 strategy families, all three action types documented |
| Preference-pair summary attached | ≥100 valid pairs, governance filters applied, dedup and linkage documented |
| Imitation prerequisite cited | Active LP-002 `artifact_state=approved` artifact(s) named; preflight imitation gate passed |
| Baseline-model evidence attached | Logistic or GBT baseline, holdout accuracy ≥0.65, AUC-ROC ≥0.70 |
| Downstream consumer named | One concrete consumer lane and contract path cited |
| Backend run log attached | Real `TRLDPOBackend` output OR explicit install/config error trace; not a silent stub pass |
| Artifact envelope correct | `artifact_state=draft` or `candidate`, `deployment_summary.current_stage=none`, `sha256:` checksum |
| Evaluator packet present | References produced artifact ID |
| Registry handoff present | `artifact_state=draft` or `candidate`, no governance write, no order route |
| No canonical truth modified | Gate docs, policy files, and L1 docs are unchanged by the parent task |
| No raw secrets in artifacts or logs | Credential IDs only; no API keys, tokens, or passwords |
| Worker env gate preserved | `PANTHEON_TRL_ACTIVATION_READY_ENABLED=1` remains required; no bypass |

---

## 6. Evidence File References

All of the following were read and are current as of 2026-05-01:

| File | Role |
|---|---|
| `services/learning/trl/ACTIVATION_CRITERIA.md` | Gate doc (OSS-003 approved); primary entry criteria for TRL activation |
| `services/learning/trl/adapter/trl_adapter.py` | Canonical adapter implementation |
| `services/learning/trl/preflight.py` | Non-writing pre-activation preflight scaffold |
| `services/learning/trl/smoke_test.py` | Stub smoke path (revalidated 2026-04-29) |
| `integrations/trl/integration.md` | Smoke-tested adapter baseline record |
| `integrations/trl/activation_packet.md` | Runtime-gated activation evidence bundle definition |
| `integrations/trl/governance.md` | Governance overlay for TRL artifacts |
| `integrations/trl/smoke_test.md` | Smoke procedure record |
| `services/learning/trl/PREFERENCE_LEARNING_CONTRACT.md` | LP-004 preference-pair construction contract |
| `services/learning/trl/WORKFLOW_DEFINITION.md` | LP-004 workflow design |
| `services/learning/trl/EV-001_INTEGRATION.md` | EV-001 downstream consumer integration shape |
| `services/learning/DEFERRED_OSS_ACTIVATION_MAP.md` | OSS activation row status and prerequisite chain |
| `services/learning/OSS_ACTIVATION_NOTES.md` | P2-OSS-ACTIVATE-001 evidence; TRL component posture |
| `OSS_INTEGRATION_CHECKLIST.md` | Per-row checklist; TRL row at `smoke-tested` |

---

## 7. Disposition

This packet is complete and ready for Codex2 review.

The parent task P2-TRL-RUNTIME-DATA-ACTIVATION-001 remains in `todo` status until Codex2 starts a fresh implementation run. When the parent task produces its evidence bundle, the checklist in §4 and the reviewer gate in §5 above provide the structured surface for review and acceptance.

The sidecar task (P2-TRL-RUNTIME-DATA-ACTIVATION-001-SIDECAR-ACCEPTANCE) will proceed to `review` → `review_approved` → `done` independently of the parent task timeline.

**Handoff instruction**: After this packet is reviewed and approved by Codex2, the sidecar closes. The acceptance packet remains as a support artifact for the parent task. Absorption into the main line is at the parent owner's (Codex2's) discretion.
