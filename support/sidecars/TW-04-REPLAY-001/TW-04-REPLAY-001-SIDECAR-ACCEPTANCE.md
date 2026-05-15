# TW-04 Teaching Replay Acceptance Review Packet (Sidecar)

**Parent Task**: `TW-04-REPLAY-001` - Publish Teaching Replay event model and commit-discard contract
**Parent Owner**: `Codex`
**Parent Reviewer**: `Claude`
**Parent Status**: `review_approved`
**Sidecar Task**: `TW-04-REPLAY-001-SIDECAR-ACCEPTANCE`
**Sidecar Owner**: `Claude`
**Sidecar Reviewer**: `Codex`
**Helper Kind**: `acceptance_packet`
**Updated**: `2026-04-19`

> This is a support artifact only. It does not modify canonical truth, L1 policy
> documents, or runtime / registry / governance implementations. It summarizes
> the repo-visible acceptance state of the parent `TW-04` slice and the
> dependency closure that makes the replay contract reviewable.

---

## 1. Executive Summary

`TW-04-REPLAY-001` is no longer a gap-identification task. The canonical replay
contract pack is now published and the parent task is in `review`.

Repo-visible publication state:

1. `docs/bff/TW-04-teaching-replay.md` now defines the Trainer-owned replay
   list/detail routes, replay-grade `TeachingEvent` schema, BFF-resolved
   `evidence_ref`, commit/discard write paths, and before/candidate/after
   artifact-ref semantics.
2. `docs/screens/TW-04-teaching-replay.md` and
   `docs/examples/TW-04-teaching-replay.json` now provide the aligned screen
   contract and example payload expected by downstream consumers.
3. `docs/pantheon-handoffs/TW-007-trainer-workbench/PACKET_FAMILY.md` and
   `docs/lovable/PANTHEON_FRONTEND_SA.md` now reflect `TW-04` as
   `contract-published — pending BFF implementation`, which closes the design
   gap while truthfully preserving the remaining runtime blocker.

This packet therefore serves as an acceptance review companion for the parent
task: it crosswalks the recorded parent acceptance criteria to the now-published
artifacts, confirms upstream dependency closure, and isolates the remaining
non-goal: live BFF implementation is still pending and was not claimed by this
contract-publishing slice.

---

## 2. Source References

| Source | Why it matters now |
|---|---|
| `ai-status.json` | Canonical task board; confirms the parent task is in `review`, records the three acceptance criteria, and shows the direct dependency on `TW-03-COMPARE-001` |
| `docs/bff/TW-04-teaching-replay.md` | Canonical BFF truth for the replay list/detail routes, replay-grade `TeachingEvent` schema, BFF-resolved evidence links, commit/discard write paths, and artifact-ref semantics |
| `docs/screens/TW-04-teaching-replay.md` | Screen-level contract aligned to the published replay BFF slice |
| `docs/examples/TW-04-teaching-replay.json` | Example payload proving the published field shape for replay list/detail and commit/discard flows |
| `docs/reviews/2026-04-19-architecture-team-input-gap-matrix.md` §E4 | Records the six original `TW-04` gaps and the minimum contract fields the parent task needed to publish |
| `docs/pantheon-handoffs/TW-007-trainer-workbench/PACKET_FAMILY.md` §TW-04 | Now reflects `TW-04` as contract-published and pending BFF, proving the family-level readiness state changed from blocked-gap to published-contract |
| `docs/lovable/PANTHEON_FRONTEND_SA.md` | Confirms the frontend gating statement now points to the published replay contract pack and still truthfully blocks live UI work on BFF availability |
| `docs/bff/TW-01-teaching-dialog.md` | Published upstream dialog-safe `TeachingEvent` subset and session lifecycle identity that `TW-04` extends |
| `docs/bff/TW-02-parameter-controls.md` | Published upstream diff semantics that `TW-04` reuses for `patch_delta[]` |
| `docs/bff/TW-03-before-after-compare.md` | Published upstream compare identity (`eval_id`, snapshot timestamps) reused by replay `eval_ref` |

---

## 3. Parent Acceptance Crosswalk

Parent acceptance criteria recorded in `ai-status.json`:

| Parent acceptance criterion | Published evidence | Review result |
|---|---|---|
| `replay route and full event schema are published` | `docs/bff/TW-04-teaching-replay.md` publishes `GET /api/v1/trainer/replay` and `GET /api/v1/trainer/replay/{session_id}` plus replay-grade `TeachingEvent` fields and event invariants; `docs/examples/TW-04-teaching-replay.json` shows all six event types (`message`, `control_patch`, `preview_trigger`, `outcome_signal`, `commit`, `discard`) in the intended payload family | PASS |
| `evidence links are BFF resolved` | `docs/bff/TW-04-teaching-replay.md` locks `evidence_ref` as `{type, id, display_label, url_pattern}` and explicitly forbids client-side reconstruction; `docs/examples/TW-04-teaching-replay.json` shows typed compare and telemetry link objects | PASS |
| `commit and discard authority are explicit` | `docs/bff/TW-04-teaching-replay.md` publishes `POST /api/v1/trainer/sessions/{session_id}/commit` and `/discard`, `allowedActions.canCommit` / `allowedActions.canDiscard`, `expected_candidate_snapshot_at`, `replay_resolution`, and before/candidate/after artifact refs; `PACKET_FAMILY.md` and `PANTHEON_FRONTEND_SA.md` now both point to this published contract pack | PASS |

What changed relative to the original gap matrix:

| Original gap from §E4 | Current repo truth |
|---|---|
| Standalone replay read route missing | Published in `docs/bff/TW-04-teaching-replay.md` |
| Full `TeachingEvent` replay schema missing | Published in `docs/bff/TW-04-teaching-replay.md` and exercised in `docs/examples/TW-04-teaching-replay.json` |
| BFF-resolved evidence links missing | Published in `docs/bff/TW-04-teaching-replay.md` |
| Commit contract missing | Published in `docs/bff/TW-04-teaching-replay.md` |
| Discard contract missing | Published in `docs/bff/TW-04-teaching-replay.md` |
| Before/after artifact refs missing | Published in `docs/bff/TW-04-teaching-replay.md` and example payload |

---

## 4. Dependency Closure Map

### 4.1 Direct dependency recorded on the task board

| Task ID | Status | Why it mattered | Current conclusion |
|---|---|---|---|
| `TW-03-COMPARE-001` | `done` | `TW-04` replay `preview_trigger` events depend on stable compare identity and snapshot timestamps | Satisfied; `TW-04` now reuses `eval_ref.eval_id`, `baseline_snapshot_at`, and `candidate_snapshot_at` from the published compare contract |

### 4.2 Upstream trainer-module prerequisites reused by the published replay contract

| Upstream module | Published artifact | Reused by `TW-04` |
|---|---|---|
| `TW-01 Teaching Dialog` | `docs/bff/TW-01-teaching-dialog.md` | session identity, lifecycle truth, append-only ordering, and dialog-safe `TeachingEvent` subset |
| `TW-02 Parameter Controls` | `docs/bff/TW-02-parameter-controls.md` | `patch_delta[]` row semantics with `parameter_key`, `previous_value`, `new_value` |
| `TW-03 Before/After Compare` | `docs/bff/TW-03-before-after-compare.md` | preview evidence identity and candidate-snapshot timestamps referenced by replay `eval_ref` |

### 4.3 Downstream readiness after publication

| Surface / record | Current truthful state | Meaning |
|---|---|---|
| `docs/pantheon-handoffs/TW-007-trainer-workbench/PACKET_FAMILY.md` §TW-04 | `contract-published — pending BFF implementation` | Contract design work for `TW-04` is complete; live backend implementation is the remaining blocker |
| `docs/lovable/PANTHEON_FRONTEND_SA.md` §14.3.5 | `contract-published` with explicit pending-BFF gate | Frontend can rely on the canonical route and payload shape, but may only ship blocked placeholders until the BFF is live |
| Trainer Replay UI routes (`/trainer/replay`, `/trainer/replay/:session_id`) | pending-bff placeholder only | This remains truthful and is not a failure of the parent contract-publishing slice |

---

## 5. Contract Areas Confirmed Published

The parent task needed to convert the six original gaps into canonical contract
truth. The published replay pack now covers:

| Contract area | Published location |
|---|---|
| Replay list route | `docs/bff/TW-04-teaching-replay.md` - `GET /api/v1/trainer/replay` |
| Replay detail route | `docs/bff/TW-04-teaching-replay.md` - `GET /api/v1/trainer/replay/{session_id}` |
| Replay-grade `TeachingEvent` schema | `docs/bff/TW-04-teaching-replay.md` - `TeachingEvent` and event invariants |
| BFF-resolved `evidence_ref` | `docs/bff/TW-04-teaching-replay.md` - evidence link object contract |
| `patch_delta[]` shape aligned to TW-02 | `docs/bff/TW-04-teaching-replay.md` + `docs/bff/TW-02-parameter-controls.md` |
| `eval_ref` aligned to TW-03 | `docs/bff/TW-04-teaching-replay.md` + `docs/bff/TW-03-before-after-compare.md` |
| Commit route and gating | `docs/bff/TW-04-teaching-replay.md` - commit route, request guard, authority rules |
| Discard route and gating | `docs/bff/TW-04-teaching-replay.md` - discard route, request guard, authority rules |
| Before/candidate/after artifact refs | `docs/bff/TW-04-teaching-replay.md` + `docs/examples/TW-04-teaching-replay.json` |
| Replay staleness / degradation semantics | `docs/bff/TW-04-teaching-replay.md`, `PACKET_FAMILY.md`, `PANTHEON_FRONTEND_SA.md` |

---

## 6. Remaining Truthful Non-Goals

This sidecar should not be read as proof that the live BFF implementation
exists. The parent slice published canonical contract truth; it did not claim
runtime delivery.

Still intentionally out of scope for `TW-04-REPLAY-001`:

1. Live Pantheon BFF routes serving the replay contract in production.
2. UI promotion from pending-BFF placeholder to active replay page.
3. Any L1 policy rewrite, registry mutation, or runtime/governance behavior
   outside the published replay contract.

---

## 7. Reviewer Checklist

| Check | Status | Evidence |
|---|---|---|
| Support artifact only | PASS | Only `support/sidecars/TW-04-REPLAY-001/TW-04-REPLAY-001-SIDECAR-ACCEPTANCE.md` is touched by this sidecar |
| No canonical truth edited by the sidecar | PASS | Canonical replay docs live under the parent task; this sidecar only summarizes acceptance evidence |
| Parent acceptance criteria are mapped to repo-visible artifacts | PASS | Section 3 ties each recorded criterion to the published BFF contract and example payload |
| Dependency closure matches current repo truth | PASS | `TW-03-COMPARE-001` is done and `TW-01` through `TW-03` published artifacts are reused by `TW-04` |
| Packet family / frontend SA status matches the current repo | PASS | Both now show `TW-04` as `contract-published — pending BFF implementation` rather than missing-contract |
| Remaining blocker is stated honestly | PASS | Packet distinguishes contract publication from live BFF implementation |

---

## 8. Handoff to Sidecar Owner (`Claude`)

Reviewer conclusion:

1. This sidecar packet is now aligned to the current repo state and can support
   review/closure of `TW-04-REPLAY-001-SIDECAR-ACCEPTANCE`.
2. The parent `TW-04-REPLAY-001` slice has repo-visible evidence for all three
   recorded acceptance criteria and is correctly positioned for reviewer
   judgment on the canonical contract pack itself.
3. The truthful remaining blocker is downstream runtime work: the published
   replay routes and commit/discard semantics still need live BFF
   implementation before UI work can move past pending-BFF placeholders.

Recommended disposition for this sidecar task: `review_approved`.

---

*Updated by Codex as a sidecar `acceptance_packet` reviewer support artifact for `TW-04-REPLAY-001`. This file is a support artifact and does not modify canonical truth.*
