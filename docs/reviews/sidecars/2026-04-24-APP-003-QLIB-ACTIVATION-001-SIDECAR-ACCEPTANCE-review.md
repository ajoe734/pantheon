# Review: APP-003-QLIB-ACTIVATION-001-SIDECAR-ACCEPTANCE

- Date: 2026-04-24
- Reviewer: Claude (auto-reassigned from Codex2 after repeated Codex2 worker terminal exits)
- Owner: Codex
- Parent task: APP-003-QLIB-ACTIVATION-001 (already archived `done` in `ai-task-archive/tasks/APP-003-QLIB-ACTIVATION-001.json`)
- Helper kind: acceptance_packet
- Decision: approved

## Scope check (sidecar)

- support artifact only: PASS — the sidecar packet is limited to
  `support/sidecars/APP-003-QLIB-ACTIVATION-001/APP-003-QLIB-ACTIVATION-001-SIDECAR-ACCEPTANCE.md`.
  No L1 canonical, runtime, registry, or governance edits were required or made.
- canonical truth untouched: PASS — the packet summarizes the archived parent
  closeout, the approved parent review, and the local dependency surfaces
  without rewriting the parent archive or any canonical policy document.
- parent lifecycle untouched: PASS — the packet explicitly frames approval as
  "the support packet is accurate and ready for closure," not as authority to
  reopen or re-approve `APP-003-QLIB-ACTIVATION-001`.

## Substantive claim replays

- Parent archive exists and matches the packet summary: PASS —
  `ai-task-archive/tasks/APP-003-QLIB-ACTIVATION-001.json` records
  `terminal_status=done`, `terminal_outcome=completed`, archived at
  `2026-04-24T19:34:17Z`, and delivery commit
  `9ee259fe28a39c9ce3c354fdd0ed4ea264233c62`. The archived `next` summary says
  smoke was revalidated on `2026-04-24` (14 unit tests + smoke assertions OK)
  and Qlib truth remains `smoke-tested` and production-blocked on the RS-003
  candidate, governed dataset proof, and target StrategySpec binding — exactly
  as cited in the packet.
- Parent approved review exists and matches the packet summary: PASS —
  `docs/reviews/2026-04-24-app-003-qlib-activation-001-codex-review.md` records
  `Disposition: approved`, no blocking findings, and documents the
  review-stage correction (real Qlib backend now matches the official
  `LGBModel.fit(dataset)` / `predict(dataset, segment=...)` contract, added
  regression coverage in `test_adapter.py`, and refreshed smoke evidence with
  no stale hard-coded checksum).
- Activation truth surface is accurate: PASS —
  `integrations/qlib/activation_packet.md` carries the
  `artifact_state=draft`, `deployment_summary.current_stage=none`,
  `QlibLightGBMBackend`, and the four-row activation gate read (RS-003 /
  governed dataset / supervised alpha / dependency) that the packet
  summarizes.
- Implementation surface claims are accurate: PASS —
  `services/research/qlib/adapter/qlib_adapter.py` still exposes
  `QLIB_VERSION_PIN = "0.9.6"`, `QlibLightGBMBackend`, `run_qlib_workflow()`,
  and emits registry output with `artifact_state=draft` and
  `deployment_summary.current_stage=none`.
  `services/research/qlib/requirements.txt` still pins `pyqlib==0.9.6`.
- Canonical status summaries agree: PASS —
  `OSS_INTEGRATION_CHECKLIST.md` row 39, `services/learning/DEFERRED_OSS_ACTIVATION_MAP.md` §1,
  and `RESEARCH_BACKEND_MATURITY_MATRIX.md` row 61 all keep Qlib at
  `smoke-tested` / `Activation-Ready` with the same remaining blockers
  (RS-003 candidate readiness, governed dataset proof of ≥50 instruments and
  ≥2 years OHLCV, target StrategySpec binding).
- Live replay of the smoke and unit test surfaces: PASS — this reviewer ran
  `python3 -m unittest discover -s services/research/qlib -p 'test_*.py'`
  (`Ran 14 tests`, `OK`) and `python3 services/research/qlib/smoke_test.py`
  (`assertions: OK`, `registry_id: qlib-alpha-equity-cross-sectional-alpha-1.0.0`,
  `artifact_state=draft`, `deployment_stage=none`).
- Smoke evidence doc is consistent: PASS —
  `integrations/qlib/smoke_test.md` records the `2026-04-24` revalidation,
  `assertions: OK`, `Ran 14 tests`, and explicitly notes the checksum is
  run-specific rather than hard-coded.

## Scope-discipline checks

- Acceptance read stays anchored to the three parent acceptance criteria
  rather than inventing a second parent approval workflow: PASS.
- Dependency map points to real reviewer-facing surfaces only (archive
  snapshot, approved review, activation packet, canonical OSS summaries, and
  repo-local adapter/smoke/test files): PASS.
- Verification snapshot is appropriately bounded to record consistency and
  file-presence checks for a support-only sidecar: PASS.
- The "13 vs 14 tests" timing note is correctly framed as historical sequence
  context (13 at handoff, 14 after reviewer-added regression landed), not as
  an unresolved contradiction: PASS.

## Notes

- The packet header currently says `Status: review_ready` and names
  `Reviewer: Codex2`. After this review is recorded, the live lifecycle truth
  moves to `review_approved` in `ai-status.json` and the reviewer of record is
  Claude (per the 2026-04-24T19:39:22Z auto-reassignment). That is not a
  blocker because the packet consistently defers lifecycle authority to the
  durable state files.
- No packet edits were required to approve this slice. Any owner-side refresh
  of the packet header during final closeout is optional support polish only.

## Decision

Approved. The acceptance packet accurately summarizes the archived
APP-003-QLIB-ACTIVATION-001 closeout, the approved parent review evidence,
the localized dependency map, and the support-only closure boundary. The
sidecar may move to `review_approved`; parent owner remains `Codex2` and the
archived parent task itself is untouched. Owner `Codex` should finalize this
sidecar to `done`.
