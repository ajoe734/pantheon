# AUTO-IMPL-TW04-001 BFF Handoff Packet

**Sidecar kind:** `bff_handoff_packet`  
**Parent task:** `AUTO-IMPL-TW04-001` - Implement TW-04 teaching replay route family  
**Parent owner:** `Codex2`  
**Parent reviewer:** `Codex`  
**Parent status:** `review` target after this handoff  
**Date:** `2026-04-20`  
**Mutates canonical:** `no`

> Support artifact only. This packet does not change canonical truth. It
> summarizes the current implementation state and the executable evidence for
> reviewer pickup.

---

## 1. Current Conclusion

- The TW-04 replay route family is live in the BFF.
- `GET /api/v1/trainer/replay` and `GET /api/v1/trainer/replay/{session_id}`
  return backend-owned replay truth.
- `POST /api/v1/trainer/sessions/{session_id}/commit` and
  `POST /api/v1/trainer/sessions/{session_id}/discard` enforce the published
  authority model and append backend-recorded replay events.
- The previously reported contract gaps for `allowedActions.canReplay`,
  replay-surface truth, and candidate snapshot guard semantics are now closed.

## 2. Implemented Contract Areas

| Area | Current repo truth |
|---|---|
| Replay list route | `services/control-plane/bff/main.py` exposes `GET /api/v1/trainer/replay` backed by `ReadSurfaceStore.list_trainer_replays(...)` |
| Replay detail route | `services/control-plane/bff/main.py` exposes `GET /api/v1/trainer/replay/{session_id}` backed by `ReadSurfaceStore.get_trainer_replay(...)` |
| Commit path | `services/control-plane/bff/main.py` exposes `POST /api/v1/trainer/sessions/{session_id}/commit` and routes successful decisions through `ReadSurfaceStore.commit_trainer_replay(...)` |
| Discard path | `services/control-plane/bff/main.py` exposes `POST /api/v1/trainer/sessions/{session_id}/discard` and routes successful decisions through `ReadSurfaceStore.discard_trainer_replay(...)` |
| Replay-grade payloads | `services/control-plane/bff/read_store.py` projects replay list rows, replay detail payloads, replay-grade `TeachingEvent` entries, `replay_resolution`, artifacts, and replay-specific `allowedActions` |
| Evidence and artifact refs | Replay detail returns resolved `evidence_ref` objects and before/candidate/after artifact refs in the published TW-04 shape |
| Degradation truth | replay list/detail share `_tw04_replay_surface_state(...)`; stored `degraded` overrides are preserved and `local_snapshot` maps to `stale` |

## 3. Review Findings Closed

| Earlier finding | Current resolution |
|---|---|
| `allowedActions.canReplay` missing from list rows | list projection now includes `canReplay` and keeps it tied to replay-route availability |
| Replay list surface ignored stored degraded overrides | list aggregation now respects stored replay surface overrides before projecting `meta.surfaces.trainer_replay` |
| Commit/discard validated the wrong guard identity | route guard now validates the replayable `candidate_snapshot_at` extracted from the latest `preview_trigger` event |

## 4. Executable Evidence

Command rerun:

```bash
pytest -q services/control-plane/bff/test_tw04_teaching_replay_contract.py
```

Result:

```text
32 passed, 7 warnings in 2.82s
```

Warning note:

- The warnings are the pre-existing `datetime.utcnow()` deprecation warnings
  from `services/control-plane/bff/read_store.py:55`. They do not block TW-04
  contract acceptance.

## 5. Reviewer Focus

- Confirm the replay list/detail/commit/discard contract remains aligned with
  `docs/bff/TW-04-teaching-replay.md`.
- Spot-check that `meta.surfaces.trainer_replay` truth is shared consistently
  between list and detail projections.
- Verify that commit/discard reject mismatched
  `expected_candidate_snapshot_at` and only permit action when
  `allowedActions` allows it.

## 6. References

- `docs/bff/TW-04-teaching-replay.md`
- `docs/screens/TW-04-teaching-replay.md`
- `docs/examples/TW-04-teaching-replay.json`
- `docs/reviews/2026-04-20-auto-impl-tw04-001-codex-review.md`
- `services/control-plane/bff/main.py`
- `services/control-plane/bff/read_store.py`
- `services/control-plane/bff/test_tw04_teaching_replay_contract.py`
