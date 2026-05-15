# EXEC-FRONT-RW04-001 Sidecar Review Packet

Date: `2026-04-21`
Sidecar task: `EXEC-FRONT-RW04-001-SIDECAR-REVIEW`
Parent task: `EXEC-FRONT-RW04-001`
Owner: `Codex2`
Reviewer: `Codex`
Scope: support-only review packet and reviewer handoff; no canonical or runtime implementation changes

## Parent Status Snapshot

- `ai-status.json` records the parent task as `review`.
- Current parent owner / reviewer: `Codex` / `Copilot`.
- Current parent review artifact: `.coordination/reviews/RW-04-experiment-launch-review.md`.
- Current returned handoff: `.coordination/requests/RW-04-experiment-launch-ui-done.yaml`.
- Current Pantheon frontend follow-up: `.coordination/responses/RW-04-experiment-launch-frontend-feedback.yaml`.
- Current parent disposition: the front slice is broadly wired to the published RW-04 contract, but the loop is still blocked and not ready for closeout.

## What This Sidecar Is For

- This packet does not reopen or reinterpret RW-04 contract truth.
- It compresses the already-recorded review state into a reviewer-ready support artifact so `Codex` can validate the sidecar without re-scanning the whole parent execution thread.
- The durable parent truth remains the parent task entry plus `.coordination/reviews/RW-04-experiment-launch-review.md`.

## Review Arc Summary

1. `EXEC-REBASE-RW04-001` published the route-live coordination bundle and frontend handoff packet for RW-04.
2. `EXEC-FRONT-RW04-001` then returned `.coordination/requests/RW-04-experiment-launch-ui-done.yaml`, claiming launch/history/detail wiring, durable polling, backend-owned cancel gating, and sibling front verification (`eslint`, `tsc --noEmit`, `npm run build`).
3. Pantheon review at `.coordination/reviews/RW-04-experiment-launch-review.md` confirmed the broad implementation direction, but kept the loop blocked on replay cleanliness, stale live runtime, and concrete front-owned error/pagination defects.
4. This sidecar packages that blocked-review state into a compact reviewer handoff for the assigned sidecar reviewer.

## What Is Already Good

- The reviewed front workspace mounts the requested RW-04 routes:
  - `/research/experiments`
  - `/research/experiments/launch`
  - `/research/experiments/:experiment_id`
- The returned UI routes RW-04 traffic through shared BFF client helpers rather than component-local raw fetch calls.
- The implementation renders backend-owned progress, warnings, failure payload, artifact ids, and cancel visibility from Pantheon truth rather than synthesizing local status.
- The recorded review confirms local Pantheon contract coverage still passes:
  - `python3 -m pytest -q services/control-plane/bff/test_rw04_experiment_launch_contract.py`
  - result recorded in the review: `21 passed`
- The `ui-done` handoff records sibling front static verification success:
  - targeted `eslint`
  - `npx tsc --noEmit`
  - `npm run build`

## Blocking Facts Preventing Closure

### 1. The returned handoff is not replay-clean and the required frontend feedback bundle is still absent

- `.coordination/requests/RW-04-experiment-launch-ui-done.yaml` points at `source_commit: 93a4b58891031442133a6966d0354ae216a80b72`.
- The recorded review says that immutable commit does not actually contain the reviewed RW-04 implementation files or the required front-owned feedback artifacts.
- The reviewed front files still live only in the sibling front working tree, so Pantheon cannot reconstruct the reviewed state from one Git-visible commit.
- Impact: the parent loop cannot close truthfully even before runtime refresh, because the published evidence chain is not replayable.

### 2. Live HTTP acceptance is still blocked because the active operator-bff runtime is stale for the entire RW-04 route family

- The recorded review says local contract verification passes in the Pantheon workspace, but the active runtime at `http://127.0.0.1:18001` still returns `404` for RW-04 experiment endpoints.
- The same review records that live `openapi.json` exposes no `/api/v1/experiments*` or `/api/v1/research*` path family.
- Pantheon already emitted the runtime follow-up request referenced by the `ui-done` handoff:
  - `.coordination/requests/RW-04-experiment-launch-needs-runtime.yaml`
- Impact: queued, running, degraded, unavailable, and terminal behavior cannot be honestly validated over live HTTP yet.

### 3. The front detail path still collapses contract-defined object-level 404s into a route-not-live placeholder

- The review records that `ExperimentRunHelpers.ts` treats any `404` `BffError` as route absence.
- Pantheon contract coverage for RW-04 explicitly distinguishes route availability from object-missing detail failures.
- Impact: a missing `experiment_id` can be rendered as "routes not yet live" instead of truthful `OBJECT_NOT_FOUND` behavior.

### 4. The launch screen can stop polling after the first failed durable detail read and hide the actual failure

- The recorded review says the retry timer is only armed when an initial detail snapshot exists.
- If the first post-launch detail read fails before the first durable snapshot arrives, the screen can remain in a passive waiting state without surfacing the real error or continuing to poll.
- Impact: this breaks the claimed durable polling flow after a successful launch receipt.

### 5. The history paginator keeps stale page tokens when the `ticket_id` filter changes

- The review records that the `ticket_id` filter clears `page_token` but not the paginator backstack.
- Impact: `Previous` can reuse a page token from a different filter context and send the operator into the wrong history slice.

## Evidence Crosswalk

- `ai-status.json`
  Current parent/sidecar ownership, lifecycle state, and assigned reviewer truth.
- `.coordination/reviews/RW-04-experiment-launch-review.md`
  Canonical review record for the current blocked disposition, findings, and verification evidence.
- `.coordination/requests/RW-04-experiment-launch-ui-done.yaml`
  Returned UI handoff describing changed files, claimed verification, and Pantheon follow-up requests.
- `.coordination/responses/RW-04-experiment-launch-frontend-feedback.yaml`
  Pantheon-issued frontend follow-up packet that records the blocked disposition and required front-owned republish / behavior fixes.
- `.coordination/requests/RW-04-experiment-launch-needs-runtime.yaml`
  Existing runtime follow-up proving the stale live HTTP environment is already tracked as a Pantheon-owned blocker.
- `.coordination/responses/RW-04-experiment-launch-contract-ready.yaml`
  Published contract-ready packet for the live RW-04 route family.
- `.coordination/responses/RW-04-experiment-launch-lovable-ui-task.yaml`
  Frontend task constraints and required completion artifacts.
- `docs/pantheon-handoffs/RW-04-experiment-launch/FRONTEND_CHANGE_SPEC.md`
  Frontend implementation guardrails, async state-machine rules, and stop-and-escalate path.
- `services/control-plane/bff/test_rw04_experiment_launch_contract.py`
  Pantheon contract verification referenced by the review.

## Reviewer Handoff For Codex

- Use `.coordination/reviews/RW-04-experiment-launch-review.md` as the authoritative parent review record.
- Treat this sidecar file as a compact intake packet only.
- Recommended sidecar disposition: approve this packet if it accurately summarizes the already-recorded blocked parent review state.
- Recommended parent posture remains unchanged: keep `EXEC-FRONT-RW04-001` blocked from closeout until the front repo republishes a replay-clean evidence bundle, the recorded front-owned defects are corrected, and RW-04 is revalidated against a refreshed live runtime.

## Sidecar Acceptance Check

- Support artifact created only: yes
- Canonical truth modified: no
- Reviewer handoff ready: yes
