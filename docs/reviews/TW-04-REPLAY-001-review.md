# TW-04-REPLAY-001 Review

**Reviewer**: Claude
**Task**: TW-04-REPLAY-001 — Publish Teaching Replay event model and commit-discard contract
**Owner**: Codex
**Review date**: 2026-04-19
**Outcome**: APPROVED

---

## Acceptance Criteria Verification

### 1. replay route and full event schema are published — PASS

- List route `GET /api/v1/trainer/replay` and detail route `GET /api/v1/trainer/replay/{session_id}` are both defined with complete field contracts.
- All six `event_type` values (`message`, `control_patch`, `preview_trigger`, `outcome_signal`, `commit`, `discard`) are declared with required field invariants for each type.
- `patch_delta[]` shape `{parameter_key, previous_value, new_value}` correctly inherits the TW-02 diff semantics.
- `eval_ref` on `preview_trigger` events correctly references TW-03 `eval_id` with `baseline_snapshot_at` and `candidate_snapshot_at`.
- `event_summary` (event count, first/last sequence numbers, latest outcome signal) is present for header and cursor bounds.
- `replay_resolution` object with four states (`pending_decision`, `committed`, `discarded`, `not_applicable`) and their mutual invariants is correctly specified.
- `meta.surfaces.trainer_replay` surface-health signal is defined on every route response.
- Route choice (`/api/v1/trainer/replay` family) is a clean, distinct replay surface — an improvement on the `sessions/:id/events` sketch in the sidecar, equivalent in intent.

### 2. evidence links are BFF resolved — PASS

- `evidence_ref` is defined as `{type, id, display_label, url_pattern}` — a fully resolved typed link, not a raw identifier.
- Explicit invariant: the client must not synthesize evidence routing from raw IDs, storage paths, or event metadata.
- The example JSON demonstrates all four canonical evidence-link types (`telemetry`, `compare_result`, `lineage_edge`, `persona_capability`) with correctly resolved objects.
- The non-goals section re-states the client prohibition against reconstructing evidence links from raw event refs or artifact IDs.

### 3. commit and discard authority are explicit — PASS

- Both `POST /api/v1/trainer/sessions/{session_id}/commit` and `POST /api/v1/trainer/sessions/{session_id}/discard` are defined.
- `allowedActions.canCommit` and `allowedActions.canDiscard` are the sole CTA authority signals; all conditions that must force them to `false` are enumerated (non-completed status, non-`pending_decision` replay state, degraded/unavailable surface, missing candidate artifact, stale snapshot).
- `expected_candidate_snapshot_at` guard on both write routes prevents stale or double-commit races.
- After a successful commit the BFF appends a `commit` TeachingEvent, advances `replay_resolution.state` to `"committed"`, populates `after_artifact_ref`, and sets both `allowedActions` to `false`.
- After a successful discard the BFF appends a `discard` TeachingEvent, advances `replay_resolution.state` to `"discarded"`, leaves `after_artifact_ref` null, and sets both `allowedActions` to `false`.
- Terminal states (`committed`, `discarded`) are correctly irreversible.
- Before/after/candidate artifact refs are present on commit response and on detail response.

## Additional Observations

- Degradation rules for `stale`, `degraded`, and `unavailable` surface states correctly reference the PKT-005 shared substrate and suppress CTAs appropriately.
- Replay cursor design (ephemeral UI state, bounds from `event_summary.first/last_sequence_number`) correctly offloads ordering truth to the backend.
- The `replay_resolution.state = "not_applicable"` case for abandoned or candidate-less sessions is correctly specified and avoids misleading `pending_decision` for sessions that never produced a candidate.
- No canonical L1 policy documents were modified — this is a BFF contract publication only, which is the correct scope.

## Sidecar Crosswalk

All ten pending checks from the sidecar acceptance packet (§3) are now satisfied by the published contract:

| Sidecar check | Status |
|---|---|
| Replay read route defined | PASS |
| Full `TeachingEvent` schema (all 6 types) locked | PASS |
| `evidence_ref` BFF-resolved (typed canonical link) | PASS |
| `patch_delta` aligned to TW-02 diff shape | PASS |
| `eval_ref` references TW-03 `eval_id` | PASS |
| Commit route and gating published | PASS |
| Discard route and gating published | PASS |
| `allowedActions.canCommit` / `canDiscard` declared | PASS |
| Before/after artifact refs published | PASS |
| `meta.surfaces.trainer_replay` staleness signal defined | PASS |

---

Disposition: **APPROVED** — all three parent acceptance criteria are met and the contract is consistent with the sidecar checklist and upstream TW-01/TW-02/TW-03 field shapes.
