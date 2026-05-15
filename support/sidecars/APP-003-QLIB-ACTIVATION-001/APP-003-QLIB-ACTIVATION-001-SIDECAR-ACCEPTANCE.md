# APP-003-QLIB-ACTIVATION-001 Acceptance Packet

**Sidecar kind:** `acceptance_packet`
**Sidecar task:** `APP-003-QLIB-ACTIVATION-001-SIDECAR-ACCEPTANCE`
**Helper parent:** `APP-003-QLIB-ACTIVATION-001`
**Parent owner:** `Codex2`
**Parent reviewer:** `Codex`
**Parent status:** `done` (`completed`, archived at `2026-04-24T19:34:17Z`)
**Prepared by:** `Codex`
**Reviewer:** `Claude`
**Date:** `2026-04-24`
**Status:** `done` — review_approved by `Claude` (`2026-04-24`); closed by `Codex` (`2026-04-24`)

> Scope constraint: support artifact only. This packet summarizes the archived
> parent closeout, approved review evidence, and localized dependency map for
> the Qlib activation slice without changing L1 canonical truth, runtime code,
> or the archived parent record.

## Executive Summary

The parent task `APP-003-QLIB-ACTIVATION-001` is already archived as `done`
with terminal outcome `completed`. This sidecar does not reopen or re-approve
the parent. It packages the final acceptance read for the Qlib activation slice
so the support helper can be reviewed and closed cleanly.

Repo-local final state:

1. The archived parent snapshot records that the governed Qlib adapter and the
   first governed LightGBM activation packet are committed, with delivery
   commit `9ee259fe28a39c9ce3c354fdd0ed4ea264233c62`.
2. The archived parent `next` summary says smoke was revalidated on
   `2026-04-24`, with `14` unit tests plus smoke assertions passing, while
   Qlib truth remains `smoke-tested` and production-blocked on the RS-003
   candidate, governed dataset proof, and target StrategySpec binding.
3. The approved parent review file
   `docs/reviews/2026-04-24-app-003-qlib-activation-001-codex-review.md`
   records no blocking findings and documents the review-stage correction that
   aligned the real Qlib backend with the official dataset contract, added a
   regression test, and refreshed the smoke evidence.
4. Canonical and support-facing Qlib surfaces agree on the same maturity
   position: runnable governed adapter landed, first activation packet prepared,
   but no production activation claim until the remaining data and strategy
   gates are proven.

Disposition: all three parent acceptance criteria are supported by the
archived parent record, the approved review, and the current repo-local
evidence surfaces. Approval of this sidecar should mean only "this support
packet is accurate and ready for closure."

## Acceptance Read

Parent task acceptance:

1. `Qlib activation criteria are satisfied or truthfully blocked with evidence`
2. `First governed LightGBM alpha activation packet is prepared`
3. `Canonical OSS docs and evidence agree on Qlib activation status`

Current read:

| Criterion | Result | Note |
|---|---|---|
| Qlib activation criteria are satisfied or truthfully blocked with evidence | supported | `services/learning/qlib/ACTIVATION_CRITERIA.md` defines the real activation gates, and `integrations/qlib/activation_packet.md` truthfully marks the RS-003 candidate, governed dataset proof (>=50 instruments, >=2 years OHLCV), and target StrategySpec binding as still blocked instead of overstating activation. |
| First governed LightGBM alpha activation packet is prepared | supported | `integrations/qlib/activation_packet.md` exists, names `QlibLightGBMBackend` as the real activation backend, and records the target governed registry envelope with `artifact_state=draft` and `deployment_summary.current_stage=none`. |
| Canonical OSS docs and evidence agree on Qlib activation status | supported | `OSS_INTEGRATION_CHECKLIST.md`, `services/learning/DEFERRED_OSS_ACTIVATION_MAP.md`, `RESEARCH_BACKEND_MATURITY_MATRIX.md`, and `integrations/qlib/integration.md` all keep Qlib at `smoke-tested` / activation-ready rather than production-activated, and all point to the same RS-003, dataset, and StrategySpec blockers. |

Support-packet caveat:

1. This table is a reviewer aid for the sidecar helper, not a second parent
   acceptance workflow.
2. The canonical parent closeout truth already lives in
   `ai-task-archive/tasks/APP-003-QLIB-ACTIVATION-001.json` plus the approved
   parent review file.

## Evidence Snapshot

- Parent terminal record:
  - `ai-task-archive/tasks/APP-003-QLIB-ACTIVATION-001.json`
  - Records `terminal_status=done`, `terminal_outcome=completed`, archived time
    `2026-04-24T19:34:17Z`, delivery commit
    `9ee259fe28a39c9ce3c354fdd0ed4ea264233c62`, and the final summary that Qlib
    remains `smoke-tested` and data-gated after the 2026-04-24 revalidation.
- Parent approved review:
  - `docs/reviews/2026-04-24-app-003-qlib-activation-001-codex-review.md`
  - Confirms no blocking findings and records the review-stage fix that moved
    the real backend to the official Qlib dataset contract, added a regression
    test, and refreshed smoke evidence.
- Activation truth surface:
  - `integrations/qlib/activation_packet.md`
  - Consolidates the current disposition, remaining gates, first-governed-run
    bundle, and the `artifact_state=draft` / `deployment_summary.current_stage=none`
    registry target.
- Integration evidence:
  - `integrations/qlib/integration.md`
  - Records `pyqlib==0.9.6`, the governed adapter surface, and why the baseline
    counts as runnable but still not production-activated.
- Smoke evidence:
  - `integrations/qlib/smoke_test.md`
  - Records the `2026-04-24` revalidation, `assertions: OK`, and `Ran 14 tests`
    summary used by the parent review and closeout.
- Activation gates:
  - `services/learning/qlib/ACTIVATION_CRITERIA.md`
  - Defines the RS-003 candidate requirement, the >=50 instrument / >=2 year
    governed dataset threshold, and the supervised-learning fit gate.
- Canonical status summaries:
  - `OSS_INTEGRATION_CHECKLIST.md`
  - `services/learning/DEFERRED_OSS_ACTIVATION_MAP.md`
  - `RESEARCH_BACKEND_MATURITY_MATRIX.md`
  - These surfaces all describe Qlib as `smoke-tested` / activation-ready with
    the same remaining blockers.
- Repo-local implementation surfaces:
  - `services/research/qlib/requirements.txt`
  - `services/research/qlib/adapter/qlib_adapter.py`
  - `services/research/qlib/smoke_test.py`
  - `services/research/qlib/test_adapter.py`
  - These are the concrete package pin, adapter, smoke entrypoint, and unit
    regression surfaces behind the parent evidence claims.

## Dependency Map

| Surface | Role in review | Current read |
|---|---|---|
| `ai-task-archive/tasks/APP-003-QLIB-ACTIVATION-001.json` | Parent terminal truth | Durable closed record showing the parent is already `done` and should not be reopened by this sidecar |
| `docs/reviews/2026-04-24-app-003-qlib-activation-001-codex-review.md` | Parent approval evidence | Confirms the review-stage backend correction, regression test addition, and refreshed smoke evidence |
| `integrations/qlib/activation_packet.md` | Parent acceptance anchor | Captures the first governed LightGBM activation bundle and truthfully keeps remaining blockers open |
| `integrations/qlib/integration.md` | Integration evidence surface | States the governed adapter is landed, `pyqlib==0.9.6` is pinned, and the baseline is smoke-tested rather than production-activated |
| `integrations/qlib/smoke_test.md` | Smoke proof summary | Records the `2026-04-24` smoke revalidation, `assertions: OK`, and `14` test summary |
| `services/learning/qlib/ACTIVATION_CRITERIA.md` | Gate definition | Defines the RS-003, dataset-depth, and supervised-alpha fit gates the activation packet must satisfy |
| `OSS_INTEGRATION_CHECKLIST.md` | Canonical OSS row summary | Shows Qlib as `smoke-tested` and still blocked on RS-003 candidate, governed dataset proof, and StrategySpec binding |
| `services/learning/DEFERRED_OSS_ACTIVATION_MAP.md` | Deferred activation dependency map | Repeats the same gate read and names the executable next step as the first governed run through `QlibLightGBMBackend` |
| `RESEARCH_BACKEND_MATURITY_MATRIX.md` | Cross-backend consistency check | Places Qlib in `smoke-tested` / `Activation-Ready`, not on the active production research path |
| `services/research/qlib/requirements.txt` | Dependency pin proof | Confirms the current `pyqlib==0.9.6` pin |
| `services/research/qlib/adapter/qlib_adapter.py` | Runtime adapter surface | Contains `QLIB_VERSION_PIN`, `QlibLightGBMBackend`, and `run_qlib_workflow()` plus governed registry output |
| `services/research/qlib/smoke_test.py` | Replay entrypoint | Provides the smoke path used by the parent review and closeout |
| `services/research/qlib/test_adapter.py` | Regression proof surface | Contains the backend regression coverage that now totals `14` tests |

## Verification Snapshot

This sidecar did not add or modify runtime code. Verification for this support
packet was limited to repo-local record consistency checks:

1. Confirmed `scripts/ai-status.sh show APP-003-QLIB-ACTIVATION-001` resolves
   to the archived parent snapshot with `terminal_status=done`,
   `terminal_outcome=completed`, and delivery commit
   `9ee259fe28a39c9ce3c354fdd0ed4ea264233c62`.
2. Confirmed `scripts/ai-status.sh show APP-003-QLIB-ACTIVATION-001-SIDECAR-ACCEPTANCE`
   resolves to the active support task owned by `Codex` and reviewed by
   `Codex2`.
3. Confirmed the archive snapshot, parent review file, Qlib activation packet,
   Qlib integration evidence, Qlib smoke evidence, canonical OSS summary docs,
   and Qlib repo-local implementation surfaces all exist on disk.
4. Confirmed `services/research/qlib/adapter/qlib_adapter.py` still exposes
   `QLIB_VERSION_PIN = "0.9.6"`, `QlibLightGBMBackend`, `run_qlib_workflow()`,
   and governed registry output using `artifact_state=draft` with
   `deployment_summary.current_stage=none`.
5. Confirmed `services/research/qlib/requirements.txt` still pins
   `pyqlib==0.9.6`, and `integrations/qlib/smoke_test.md` still records the
   `2026-04-24` revalidation with `assertions: OK` and `Ran 14 tests`.

## Known Non-Blocking Observations

1. The task brief that launched this sidecar still reflected the parent at
   `review_approved`, but the durable state changed during this support pass:
   the parent is now archived as `done`, and the archive snapshot outranks the
   older brief snapshot.
2. The archived parent handoff into review mentioned `13` tests at the time of
   review request. The final parent review and archive closeout record `14`
   tests after the reviewer-added regression landed. The final review and
   archived closeout are the authoritative counts for this packet.
3. Approval of this sidecar should be interpreted only as "the support packet
   accurately reflects the archived parent closeout and current dependency
   surfaces," not as authority to change the parent delivery record.

## Reviewer Checklist

Before approving this sidecar, confirm:

1. The packet stays support-only and does not claim to reopen or re-finalize
   `APP-003-QLIB-ACTIVATION-001`.
2. The acceptance read matches the archived parent and approved review: Qlib
   remains `smoke-tested`, the first activation packet is prepared, and the
   remaining blockers are still RS-003 candidate readiness, governed dataset
   proof, and target StrategySpec binding.
3. The dependency map points to the real reviewer-facing surfaces: archive
   snapshot, approved review, activation packet, canonical OSS summaries, and
   the repo-local adapter/smoke/test files.
4. The known timing note about `13` vs `14` tests is treated as historical
   sequence context, not as an unresolved contradiction in final truth.
5. Approval of this sidecar is interpreted only as "the support packet is
   current and accurate."
