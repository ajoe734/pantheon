# EXEC-RUNTIME-TW04-001 Sidecar Review Packet

Date: `2026-04-21`
Sidecar task: `EXEC-RUNTIME-TW04-001-SIDECAR-REVIEW`
Parent task: `EXEC-RUNTIME-TW04-001`
Sidecar owner / reviewer: `Codex2` / `Claude`
Parent owner / reviewer: `Claude` / `Codex`
Helper kind: `review_packet`
Scope: support-only review packet and reviewer handoff; no canonical truth, runtime implementation, or contract docs are modified here

## Parent Status Snapshot

- `ai-status.json` currently records the parent as `review`.
- The latest durable parent handoff says:
  - `TW-04 runtime refresh complete. All 4 routes live at 127.0.0.1:18001, links match front router, 32 contract tests pass. Evidence route gap /telemetry/drawdown/:id documented in .coordination/requests/TW-04-teaching-replay-bff-gap.yaml per review instructions. Needs-runtime status updated to verified-with-gap. Ready for Codex review.`
- This sidecar exists because the support packet named in the task brief did not yet exist. It packages the current review surface so `Claude` can review the sidecar itself and `Codex` can review the parent without re-reading the full TW-04 thread.

## Evidence Chain

### 1. Original review findings were narrow and still matter

The parent review record at `.coordination/reviews/TW-04-teaching-replay-review.md`
established two concrete blockers:

1. the active runtime was stale and did not expose the TW-04 route family over live HTTP
2. at least one emitted `event.evidence_ref.url_pattern` still pointed at the undeployed `/telemetry/drawdown/:id` route family

That review already confirmed the positive baseline:

- local TW-04 contract slice passed with `32` tests
- the front handoff bundle was contract-aligned and replay-clean
- replay/session browser links had been corrected in the current Pantheon workspace

This means the remaining issue was runtime freshness and emitted evidence-route
topology, not front drift.

### 2. Runtime follow-up now confirms the TW-04 route family is live

`.coordination/requests/TW-04-teaching-replay-needs-runtime.yaml` upgrades the
runtime truth materially beyond the original review:

- all four TW-04 endpoints now respond on `http://127.0.0.1:18001`
  - `GET /api/v1/trainer/replay`
  - `GET /api/v1/trainer/replay/{session_id}`
  - `POST /api/v1/trainer/sessions/{session_id}/commit`
  - `POST /api/v1/trainer/sessions/{session_id}/discard`
- `links.replay_detail` and `links.session_detail` now match the mounted front routes
- the contract slice still passes with `32` tests
- degraded / unavailable semantics remain covered by the TW-04 contract suite

Important nuance for review:

- the `needs-runtime` artifact no longer describes a missing TW-04 route family
- it now describes a narrower remaining gap: the active runtime still needs one
  more refresh cycle so the corrected evidence target is what live HTTP serves

### 3. The evidence-route gap is resolved in the workspace, but not yet fully re-observed on the active runtime

`.coordination/requests/TW-04-teaching-replay-bff-gap.yaml` is marked
`resolved` and captures the exact route-topology fix:

- previous emitted target:
  - `/telemetry/drawdown/tel-drawdown-2026-04-18`
- corrected target in the current Pantheon workspace:
  - `/operator/paper-live-drift/runtime-042`

The resolution cites the exact implementation surface:

- `services/control-plane/bff/read_store.py`
- `services/control-plane/bff/test_tw04_teaching_replay_contract.py`
- `.coordination/requests/TW-04-teaching-replay-needs-runtime.yaml`

Direct repo evidence agrees with that resolution:

- `read_store.py` defines `_TW04_DRAWDOWN_EVIDENCE_ROUTE = "/operator/paper-live-drift/runtime-042"`
- the TW-04 contract test asserts the drawdown event now emits that mounted route
- the same test also proves stale persisted TW-04 replay snapshots are backfilled
  to the corrected route on startup instead of preserving the old
  `/telemetry/drawdown/:id` target

Reviewer takeaway:

- workspace truth: fixed
- active runtime truth: one more refresh / reprobe still needed

### 4. The parent task is reviewable, but not ready for `done`

The parent acceptance in `ai-status.json` is:

1. the active operator-bff runtime exposes the TW-04 replay route family over live HTTP
2. browser-facing replay/session links match mounted front routes and evidence targets resolve to deployed owner routes
3. live runtime truth preserves the published degraded and unavailable replay semantics

Current evidence posture:

| Acceptance item | Current evidence | Status |
|---|---|---|
| Live TW-04 route family exposed | `needs-runtime` says all four endpoints are live on port `18001` | PASS |
| Browser-facing links and evidence targets resolve to deployed owner routes | browser links pass; evidence target is fixed in workspace and recorded as resolved in `bff-gap`, but the active runtime still needs a final refresh to serve the corrected target over live HTTP | PARTIAL |
| Degraded/unavailable semantics stay truthful | preserved by the `32`-test contract suite and cited in both runtime artifacts | PASS |

This is the key review posture to preserve:

- the parent is no longer blocked on missing TW-04 routes
- the parent is still not honestly `done` until live HTTP re-observes the corrected evidence target

## Source References

- `.orchestrator/task-briefs/exec_runtime_tw04_001_sidecar_review.md`
- `ai-status.json`
- `.coordination/reviews/TW-04-teaching-replay-review.md`
- `.coordination/requests/TW-04-teaching-replay-needs-runtime.yaml`
- `.coordination/requests/TW-04-teaching-replay-bff-gap.yaml`
- `docs/bff/TW-04-teaching-replay.md`
- `services/control-plane/bff/read_store.py`
- `services/control-plane/bff/test_tw04_teaching_replay_contract.py`

## Reviewer Attention Points

### 1. Keep the three truth layers separate

Do not collapse these into one status:

- original review finding: stale runtime and bad evidence route
- current workspace truth: route family live and evidence-route fix landed
- active runtime truth: still needs final refresh to serve the corrected
  evidence target over live HTTP

### 2. Treat `needs-runtime` as the main live-status artifact

For current review, `.coordination/requests/TW-04-teaching-replay-needs-runtime.yaml`
is the best compact statement of what the runtime now proves and what remains.
The older review packet is still important, but it is no longer the latest live
runtime snapshot.

### 3. The `bff-gap` is now evidence of a bounded fix, not an open request

Reviewer should read `.coordination/requests/TW-04-teaching-replay-bff-gap.yaml`
as a resolved support artifact that documents:

- what the wrong route was
- what the corrected route is
- which files enforce the fix

It should not be reinterpreted as proof that the live runtime has already been
recycled onto that corrected workspace.

## Recommended Review Flow For Claude

1. Confirm this sidecar stayed within support-only scope.
2. Read the parent handoff and acceptance text in `ai-status.json`.
3. Compare `.coordination/reviews/TW-04-teaching-replay-review.md` against
   `.coordination/requests/TW-04-teaching-replay-needs-runtime.yaml` to verify
   the route-family blocker really moved from `fail` to `pass`.
4. Spot-check `services/control-plane/bff/read_store.py` and
   `services/control-plane/bff/test_tw04_teaching_replay_contract.py` against
   `.coordination/requests/TW-04-teaching-replay-bff-gap.yaml` to confirm the
   evidence-route fix exists in repo truth.
5. Approve this sidecar only as a reviewer aid. The parent owner still decides
   whether to keep the parent in `review`, reopen it for the final reprobe, or
   split the last live-runtime refresh into another follow-up.

## Suggested Sidecar Disposition

- Approve this sidecar if it accurately summarizes the current parent evidence
  surface and preserves the runtime-vs-workspace distinction.
- For the parent task, the defensible review posture is:
  - accept that TW-04 route exposure is now live
  - accept that the route-topology defect is fixed in the workspace
  - require one final live HTTP confirmation before any claim that the evidence
    target issue is fully closed on the active runtime

## Sidecar Acceptance Check

- Support artifact created only: yes
- Canonical truth modified: no
- Reviewer handoff ready: yes
