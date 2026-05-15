# OSS-IMPL-002 Sidecar Acceptance Packet

Last updated: 2026-04-17
Owner: Codex2
Reviewer: Codex
Parent task: `OSS-IMPL-002`
Helper kind: `acceptance_packet`
Status: aligned with parent `review_approved` state and corrected blocker/evidence summary

## Scope and boundary

This packet is a sidecar support artifact for `OSS-IMPL-002` only.

- Purpose: give the reviewer a fresh acceptance snapshot, dependency map, and evidence summary for the QuantLib governed adapter lane.
- Allowed scope: support material only.
- Not in scope: changing L1 canonical truth, parent task implementation, runtime behavior, or checklist semantics.

## Parent task snapshot

From `ai-status.json` on 2026-04-17 after the parent reviewer gate:

- Parent task: `OSS-IMPL-002`
- Title: `Implement QuantLib governed adapter with smoke test`
- Owner: `Codex2`
- Reviewer: `Codex`
- Status: `review_approved`
- Owner-facing next: `Supervisor resumed OSS-IMPL-002 for finalize after successful dispatch.`
- Recorded review basis: parent review notes state that stub evidence was rerun, a pinned QuantLib-installed live rerun was also checked outside this default workspace, and the default workspace still keeps the real-backend test skipped for CI safety.

## Dependency map

```text
phase6 planning-session.json
  -> materializes OSS-IMPL-002
     -> implementation artifacts
        -> services/research/quantlib/adapter/quantlib_adapter.py
        -> services/research/quantlib/smoke_test.py
        -> services/research/quantlib/test_adapter.py
        -> OSS_INTEGRATION_CHECKLIST.md
     -> execution evidence
        -> python3 services/research/quantlib/smoke_test.py
        -> python3 -m pytest services/research/quantlib/test_adapter.py -q
        -> TestQuantLibBackend.test_american_option_greeks_follow_governed_scaling
     -> support context
        -> services/research/quantlib/ACTIVATION_CRITERIA.md
```

Operational dependencies inside the slice:

- Package pin: `QuantLib-Python==1.18` in `services/research/quantlib/requirements.txt`
- Default verification path: `StubQuantLibBackend`
- Real backend gate: `PANTHEON_QUANTLIB_BACKEND=real`
- Governed output invariants:
  - `artifact_family=pricing_report`
  - `framework=quantlib`
  - `registry_entry.artifact_state=draft`
  - `registry_entry.deployment_summary.current_stage=none`
  - `governance.direct_live_influence=false`
  - `governance.lean_consumption=research_only_not_direct_action`

## Acceptance checklist

| Acceptance item | Status | Evidence |
|---|---|---|
| QuantLib governed adapter exists in the planned location | PASS | `services/research/quantlib/adapter/quantlib_adapter.py` implements `GovernedQuantLibInputAdapter`, `StubQuantLibBackend`, `QuantLibBackend`, and `run_quantlib_workflow()` |
| Smoke test emits a governed draft artifact | PASS | `python3 services/research/quantlib/smoke_test.py` passed on 2026-04-17 and printed `artifact_family: pricing_report`, `artifact_state: draft`, `deployment_stage: none`, `direct_influence: False` |
| American-option vega/rho scaling defect is now covered explicitly | MIXED | The prior reviewer reopen cited mis-scaling at `services/research/quantlib/adapter/quantlib_adapter.py:288-312`. Current code divides American-path `rho` by `100.0`, adds `TestQuantLibBackend.test_american_option_greeks_follow_governed_scaling`, and the parent task has since been approved with additional pinned live-rerun evidence recorded in `ai-status.json`. However, the live rerun in this default workspace still skipped that QuantLib-backed test because QuantLib is unavailable locally. |
| Unit tests pass in the current workspace | PASS WITH CAVEAT | `python3 -m pytest services/research/quantlib/test_adapter.py -q` returned `17 passed, 1 skipped in 0.16s` on 2026-04-17. The skipped test is the real QuantLib American-path coverage noted above. |
| OSS checklist advanced to `smoke-tested` | PASS | `OSS_INTEGRATION_CHECKLIST.md` records QuantLib as `smoke-tested` with the same smoke-test and pytest commands |

## Fresh evidence

Verification rerun in the current workspace on 2026-04-17:

```text
$ python3 services/research/quantlib/smoke_test.py
OSS-IMPL-002 QuantLib smoke test complete
  artifact_family:    pricing_report
  framework:          quantlib
  artifact_state:     draft
  deployment_stage:   none
  direct_influence:   False
  lean_consumption:   research_only_not_direct_action
  option_count:       1
  bond_count:         1
  assertions: OK

$ python3 -m pytest services/research/quantlib/test_adapter.py -q
..........s.......
17 passed, 1 skipped in 0.16s
```

Implementation details relevant to review:

- `smoke_test.py` builds a governed snapshot with one option and one bond, then asserts the non-executable registry envelope.
- `quantlib_adapter.py` keeps stub verification as the default path and only selects the real backend when `PANTHEON_QUANTLIB_BACKEND=real`.
- `test_adapter.py` now also contains `TestQuantLibBackend.test_american_option_greeks_follow_governed_scaling`, which compares the American-path `vega` and `rho` outputs against governed scaling expectations when QuantLib is installed.

## Reviewer notes

1. The earlier version of this packet was stale because it only recorded stub smoke/unit evidence and therefore understated the reopened American-option blocker.
2. The current repo state is different from that stale packet: the parent task now records that the American-path scaling fix landed, the targeted QuantLibBackend test was added, and the parent reviewer already advanced `OSS-IMPL-002` to `review_approved`.
3. This workspace still does not provide live real-backend execution evidence; the new QuantLib-backed regression test skipped locally. This packet therefore preserves the distinction between local stub/default-workspace evidence and the broader parent review basis captured in `ai-status.json`.
4. There is also support-doc drift in `services/research/quantlib/ACTIVATION_CRITERIA.md`: Gate 1 still lists `worker.py` and `examples/pricing_dataset_sample.json` as preconditions for `smoke-tested`, but those files are not part of the materialized parent task and are not present in `services/research/quantlib/`.

## Handoff recommendation

Reviewer and parent owner can use this packet as a corrected acceptance snapshot:

- adapter implementation is present
- stub smoke test rerun passes with governed draft artifact output
- pytest rerun now reports `17 passed, 1 skipped`, not the earlier `17 passed`
- the reopened American-option vega/rho issue is no longer ignored in the packet; local reproduction remains partial because the real QuantLib regression test skipped locally, while the parent review approval relied on extra live-rerun evidence noted in `ai-status.json`

This sidecar is not a replacement for the parent review record. It now serves as an accurate support packet for owner closeout and later audit: the local workspace evidence is preserved as-is, the skipped real-backend gap is explicit, and the packet no longer claims that local evidence alone carried the parent task to approval.
