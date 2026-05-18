# OSS-STAT-V2-001 Review Packet

**Sidecar task:** `OSS-STAT-V2-001-SIDECAR-REVIEW`
**Parent task:** `OSS-STAT-V2-001`
**Parent title:** `statsmodels production cointegration on TWSE pairs`
**Parent owner:** `Copilot`
**Parent reviewer:** `Codex`
**Sidecar owner:** `Codex2`
**Sidecar reviewer:** `Codex`
**Helper kind:** `review_packet`
**Generated:** `2026-05-18`
**Branch:** `task/OSS-STAT-V2-001-SIDECAR-REVIEW`

> Scope declaration: this is a support-only sidecar packet. It does not edit
> L1 canonical truth, statsmodels runtime code, registry/governance runtime
> behavior, or parent task state. Parent owner decides whether and how to
> absorb this packet into the main review flow.

## 1. Context Snapshot

The requested task brief path,
`.orchestrator/task-briefs/oss_stat_v2_001_sidecar_review.md`, was absent at
packet preparation time. This packet therefore uses the wake-up dispatch,
`AI_COLLABORATION_GUIDE.md`, `ai-status.json`, existing task artifacts, and
focused verification as the task-scoped context.

Current durable state from `ai-status.json`:

- Parent `OSS-STAT-V2-001` exists with `status=todo`, `owner=Copilot`, and
  `reviewer=Codex`.
- Parent artifacts are listed as:
  - `services/research/statsmodels/production_cointegration.py`
  - `services/research/statsmodels/test_production_cointegration.py`
  - `services/research/statsmodels/registry_admission_packet.py`
  - `support/evidence/OSS-STAT-V2-001/admission_packet.json`
- A raw `jq` lookup of `ai-status.json` did not return
  `OSS-STAT-V2-001-SIDECAR-REVIEW`; `scripts/ai-status.sh show` can resolve
  the sidecar as an active `in_progress` supervisor task with
  `owner=Codex2` and `reviewer=Codex`.

This creates a lifecycle gap: the implementation and evidence artifacts are
present and reproducible, but the parent task is still recorded as `todo`.
The parent owner should align task state before asking Codex for the official
parent review.

## 2. Parent Acceptance Verification

| Criterion | Status | Evidence |
|---|---|---|
| `run_production(pair_universe, rolling_window)` returns a `signal_snapshot` with pair metrics | PASS | `run_production()` is implemented in `production_cointegration.py:62`; tests assert `pair_id`, `p_value`, `half_life`, and `spread_zscore` at `test_production_cointegration.py:45`. |
| Operates on MGMT-QLIB-001 TWSE OHLCV materialization | PASS | Manifest binding is enforced at `production_cointegration.py:80` and `:159`; reproduced output uses `dataset:tw-equity-ohlcv-top50-2024-daily`, 50 instruments, and 525 minimum periods. |
| Outputs at least 3 cointegrated pairs with `p_value < 0.05` | PASS | Reproduced output has 10/10 cointegrated pairs; top 3 are `TWSE_0004/TWSE_0044`, `TWSE_0013/TWSE_0027`, and `TWSE_0013/TWSE_0049`. |
| `signal_snapshot` artifact includes checksum and lineage | PASS | Registry projection is built at `production_cointegration.py:392`; tests assert checksum, `artifact_state=draft`, `current_stage=none`, dataset refs, and StrategySpec lineage at `test_production_cointegration.py:70`. |
| `registry_admission_packet.py` emits a valid admission packet | PASS | Packet builder starts at `registry_admission_packet.py:76`; validator starts at `:299`; tests assert `PromotionReadinessPacket.v1`, empty `missing_evidence`, and `can_proceed=true` at `test_production_cointegration.py:106`. |
| `pytest -q` exits 0 | PASS | `python3 -m pytest services/research/statsmodels/test_production_cointegration.py -q` reproduced `7 passed in 8.03s`. |
| No live broker / deployment side effects | PASS | Downstream scope and safety assertions are explicit at `production_cointegration.py:586` and `:601`; admission packet validation rejects registry writes, deployment stage changes, and order routes at `registry_admission_packet.py:328` and `:355`. |

## 3. Reproduced Evidence

Commands run on `2026-05-18` from the task branch:

```bash
python3 -m pytest services/research/statsmodels/test_production_cointegration.py -q
```

Observed result:

```text
7 passed in 8.03s
```

```bash
python3 services/research/statsmodels/production_cointegration.py \
  --created-at 2026-05-17T16:45:00Z \
  > /tmp/oss-stat-v2-001-signal-snapshot-20260517.json
```

Observed summary:

- checksum: `sha256:7f7049632dc13a004e88dfd484832389495c3a2c2172d2035b29ef89d94a0a7b`
- pair count: `10`
- cointegrated pair count: `10`
- best pair: `TWSE_0004/TWSE_0044`
- best p-value: `0.0055868352`
- dataset: `dataset:tw-equity-ohlcv-top50-2024-daily`
- instruments: `50`
- min periods per instrument: `525`
- deployment stage: `none`

```bash
python3 services/research/statsmodels/registry_admission_packet.py \
  --output /tmp/oss-stat-v2-001-admission-packet-20260517.json \
  --created-at 2026-05-17T16:45:00Z
```

Then:

```bash
cmp -s \
  support/evidence/OSS-STAT-V2-001/admission_packet.json \
  /tmp/oss-stat-v2-001-admission-packet-20260517.json
```

Observed result: exit code `0`; the committed admission packet is exactly
reproducible when using its recorded `generated_at`.

## 4. Artifact Evidence Map

| Artifact | Review evidence |
|---|---|
| `services/research/statsmodels/production_cointegration.py` | Defines the fixed TWSE pair universe at line 40, implements `run_production()` at line 62, binds dataset to the MGMT-QLIB-001 manifest at lines 80 and 159, runs Engle-Granger pair checks at line 289, emits the draft `signal_snapshot` registry projection at line 392, and records fail-closed downstream scope at lines 586 and 601. |
| `services/research/statsmodels/registry_admission_packet.py` | Defines required evidence at line 48, builds `PromotionReadinessPacket.v1` at line 76, writes packets at line 271, and validates safety/registry-write constraints at line 299 onward. |
| `services/research/statsmodels/test_production_cointegration.py` | Covers pair metrics, dataset manifest binding, draft registry projection, negative dataset/window cases, admission packet shape, and packet emission across seven tests. |
| `support/evidence/OSS-STAT-V2-001/admission_packet.json` | Reproducible admission packet with `missing_evidence=[]`, `can_proceed=true`, 10 cointegrated pairs, draft artifact state, lineage refs, and no broker/deployment side effects. |
| `support/evidence/MGMT-QLIB-001/dataset_manifest.json` | Provides the production dataset floor: 50 instruments, 2.0096 history years, 504 required daily periods, `dataset_gate_satisfied=true`, and research-only downstream scope. |

## 5. Findings

### Finding 1

**Severity:** lifecycle blocker for parent review, not an implementation failure

`ai-status.json` still records parent `OSS-STAT-V2-001` as `status=todo` with
`next=Assignment created`, while the implementation files and admission packet
already exist and focused verification passes. The parent owner should align
ownership/status and hand off the parent task to Codex before this evidence is
treated as an official parent review request.

### Finding 2

**Severity:** sidecar orchestration gap

The sidecar task brief was absent, and raw `ai-status.json` lookup did not
surface the sidecar even though the status script can resolve it from active
orchestrator state. This packet is therefore intentionally self-contained and
records the context gap rather than silently inventing canonical task history.

### Finding 3

**Severity:** none

No acceptance-blocking issue was found in the focused implementation review.
The production snapshot, MGMT-QLIB-001 manifest binding, admission packet,
lineage, and fail-closed downstream scope all match the parent acceptance
contract.

## 6. Reviewer Handoff

Codex should review this sidecar packet only as support material. Suggested
review checklist:

1. Confirm the packet remains support-only and does not mutate canonical truth.
2. Confirm the reproduced evidence and acceptance table match the current repo.
3. Decide whether the lifecycle gaps in Section 5 need a status-system follow-up.

Parent owner Copilot should decide whether to absorb this packet and advance
`OSS-STAT-V2-001` through the normal lifecycle. A clean parent handoff would
include the four parent artifacts listed in Section 1 and the reproduced
verification commands from Section 3.

## 7. Owner Closeout Finalization

Closeout owner: `Codex2`
Closeout date: `2026-05-18`
Reviewer approval file:
`support/sidecars/OSS-STAT-V2-001/OSS-STAT-V2-001-SIDECAR-REVIEW-CODEX-NOTE.md`

The sidecar packet was approved by `Codex` as support-only. During owner
closeout, `AI_NAME=Codex2 ./scripts/ai-status.sh show
OSS-STAT-V2-001-SIDECAR-REVIEW` resolved this sidecar as
`review_approved`, and `AI_NAME=Codex2 ./scripts/ai-status.sh show
OSS-STAT-V2-001` resolved the parent from archive as `done` with terminal
outcome `completed`. The lifecycle gap recorded in Section 5 is therefore
retained as preparation-time context, not a current closeout blocker.

Focused closeout verification rerun from
`task/OSS-STAT-V2-001-SIDECAR-REVIEW`:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/research/statsmodels/test_production_cointegration.py -q
```

Observed result: `7 passed in 5.59s`.

```bash
git diff --check origin/dev...HEAD
```

Observed result: exit code `0`.

```bash
git diff --name-only origin/dev...HEAD
```

Observed result: the branch diff is limited to:

- `support/sidecars/OSS-STAT-V2-001/OSS-STAT-V2-001-SIDECAR-REVIEW.md`
- `support/sidecars/OSS-STAT-V2-001/OSS-STAT-V2-001-SIDECAR-REVIEW-CODEX-NOTE.md`

No L1 canonical truth, statsmodels runtime, registry/governance runtime, or
parent task implementation file is changed by this sidecar closeout.
