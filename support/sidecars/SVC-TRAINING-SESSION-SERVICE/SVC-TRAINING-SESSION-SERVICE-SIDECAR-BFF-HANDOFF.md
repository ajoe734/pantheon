# SVC-TRAINING-SESSION-SERVICE BFF and Frontend Handoff Packet

**Sidecar Task ID**: `SVC-TRAINING-SESSION-SERVICE-SIDECAR-BFF-HANDOFF`
**Parent Task**: `SVC-TRAINING-SESSION-SERVICE`
**Parent Owner**: `Codex2`
**Parent Reviewer**: `Claude`
**Sidecar Owner**: `Codex2`
**Sidecar Reviewer**: `Copilot`
**Helper Kind**: `bff_handoff_packet`
**Generated**: 2026-04-28
**Last Refresh**: 2026-04-28T17:50:00Z
**Mutates Canonical**: `no`

This is a support artifact only. It does not update canonical truth, L1 policy,
core contracts, runtime/registry/governance implementation, BFF implementation,
frontend code, or compose wiring. The parent owner decides whether and how to
absorb this packet into the main training-session service materialization
slice.

---

## 1. Scope Snapshot

`SVC-TRAINING-SESSION-SERVICE` is the deployable service-wrapper slice for the
Trainer Workbench. The parent acceptance target is:

- expose training-session lifecycle, append-only teaching event, control patch,
  preview, replay, and health APIs;
- add Dockerfile, durable storage, and compose wiring;
- move BFF trainer surfaces to an explicit service URL or truthfully report
  unavailable;
- cover append-only replay, control patch, preview, and compose config with
  tests;
- avoid formal imitation-training activation in this slice.

The current repo has live BFF Trainer Workbench route families for TW-01 through
TW-04, but no `services/training-session/` deployable wrapper and no root
compose service for it. Parent work should therefore be framed as "promote the
existing BFF-backed Trainer Workbench data path into an explicit
training-session HTTP service boundary" rather than "design a new browser
contract."

---

## 2. Current Implementation Snapshot

| Area | Current fact | Evidence |
|---|---|---|
| Training-session service wrapper | No `services/training-session/`, `services/training_session/`, or `services/trainer/` service directory exists in this checkout. | filesystem check |
| Root compose | No training-session service block or BFF training-session service URL env is present. | `docker-compose.yml`, `rg PANTHEON_.*TRAIN` |
| BFF public routes | BFF mounts TW-01 through TW-04 routes: session create/list/detail/message, controls read/patch, preview read/refresh, replay list/detail, commit, and discard. | `services/control-plane/bff/main.py` |
| BFF session storage path | Trainer session lifecycle and message append currently use `teaching_sessions` through `PANTHEON_BFF_TEACHING_SESSION_STORE` or local snapshot fallback. | `services/control-plane/bff/read_store.py` |
| BFF controls path | Controls read/patch use `trainer_controls` through `PANTHEON_BFF_TRAINER_CONTROL_STORE` or local fallback. | `services/control-plane/bff/read_store.py` |
| BFF preview path | Preview read/refresh uses `trainer_previews` through `PANTHEON_BFF_TRAINER_PREVIEW_STORE` or local fallback. | `services/control-plane/bff/read_store.py` |
| BFF replay path | Replay list/detail/commit/discard use `trainer_replays` through `PANTHEON_BFF_TRAINER_REPLAY_STORE` or local fallback. | `services/control-plane/bff/read_store.py` |
| Frontend contract | Existing frontend materials require browser calls to Pantheon BFF only and prohibit local synthesis, Persona teaching-history substitution, direct backend calls, and raw fetches outside the BFF client. | `docs/pantheon-handoffs/TW-007-trainer-workbench/PACKET_FAMILY.md`, `docs/pantheon-handoffs/TW-01-teaching-dialog/FRONTEND_CHANGE_SPEC.md`, `docs/pantheon-handoffs/TW-04-teaching-replay/FRONTEND_CHANGE_SPEC.md` |

---

## 3. Activation Target for Parent Owner

Suggested service boundary shape for review, not canonical truth:

| Boundary | Proposed normal-path target |
|---|---|
| Compose service | `training-session-svc` built from a new `services/training-session/Dockerfile` |
| Health | `GET /health` initially, with future compatibility for the platform `/healthz`/`/livez`/`/readyz` unification task |
| Internal service port | Pick one explicit container port, for example `8080`, and use it consistently in Dockerfile, compose, BFF env, and smoke tests |
| Durable storage | A dedicated data dir or volume, for example `TRAINING_SESSION_DATA_DIR=/data/training-session` |
| BFF env | New explicit URL such as `PANTHEON_TRAINING_SESSION_SERVICE_URL=http://training-session-svc:8080` |
| Browser boundary | Frontend continues to call only `operator-bff`; browser must not call `training-session-svc` directly |
| Fallback | Existing BFF JSON store/local fallback paths remain fenced as migration/test behavior, not the normal single-VM service-backed path |
| Non-goal | Do not activate production imitation training, RL training, or external learning frameworks from this service wrapper |

The BFF should continue owning the public `/api/v1/trainer/...` response
envelopes, `allowedActions`, degradation metadata, pagination, frontend links,
and browser-facing error mapping.

---

## 4. BFF Query Gap Matrix

| BFF route / flow | Current implementation path | Service API readiness | Activation gap |
|---|---|---|---|
| `POST /api/v1/trainer/sessions` | BFF validates `persona_id`, `session_type=trainer`, `objective`, and `context_refs`, verifies the persona, then writes a generated session to `teaching_sessions`. | No training-session HTTP service exists. | Add service create-session API and BFF client projection while preserving the TW-01 response shape and persona validation boundary. |
| `GET /api/v1/trainer/sessions` | BFF lists `teaching_sessions`, filters by `persona_id` and `status`, sorts by last event or start time, and paginates. | No service list/query API exists. | Service must support persona/status filters or return enough ordered data for BFF to preserve pagination and `meta.surfaces.trainer_dialog`. |
| `GET /api/v1/trainer/sessions/{session_id}` | BFF reads one `teaching_sessions` record and projects actor context, summary, ordered events, and links. | No service detail API exists. | Service should return append-only event stream and lifecycle state; BFF should keep projection ownership for actor context and browser links unless parent explicitly accepts service-owned fields. |
| `POST /api/v1/trainer/sessions/{session_id}/message` | BFF gates on active status and `allowedActions.canSendMessage`, then appends an operator event with the next sequence number. | No append-event API exists. | Service must own append-only event sequence allocation/idempotency; BFF should stop mutating shared JSON directly on the normal path. |
| `GET /api/v1/trainer/sessions/{session_id}/controls` | BFF reads `trainer_controls`, combines it with session status, and returns `TrainerControlState`. | No controls service API exists. | Service needs controls read API or an accepted shared-store contract; missing controls must map to degraded/unavailable instead of false authority. |
| `POST /api/v1/trainer/sessions/{session_id}/patch` | BFF validates patch payload, enforces active status and `allowedActions.canPatchControls`, applies validation, mutates controls, and returns diff. | No control patch service API exists. | Parent must decide whether validation/diff semantics move into service or remain BFF-owned over a service patch primitive. Either way, BFF public TW-02 contract must not drift. |
| `GET /api/v1/trainer/sessions/{session_id}/preview` | BFF reads `trainer_previews`, returns preview status or constructs structured `preview_unavailable`. | No preview service API exists. | Service must expose preview read state or BFF must keep structured unavailable behavior when preview backing is absent. Do not turn preview absence into generic `5xx`. |
| `POST /api/v1/trainer/sessions/{session_id}/preview` | BFF gates on `allowedActions.canRefreshPreview`, active/paused session status, and pending eval reuse, then mutates preview store. | No refresh/eval API exists. | Service needs refresh API with duplicate-pending semantics or BFF must retain this orchestration with explicit service-backed persistence. |
| `GET /api/v1/trainer/replay` | BFF reads `trainer_replays`, filters terminal sessions by persona/status, computes list CTA summaries, and returns surface state. | No replay list API exists. | Service must expose replayable sessions and stored surface state; BFF keeps list envelope and pagination. |
| `GET /api/v1/trainer/replay/{session_id}` | BFF reads replay detail, orders replay-grade events, resolves replay resolution/artifact/evidence fields. | No replay detail API exists. | Service should own ordered replay event history and decision state; BFF should preserve browser-facing `links` and field names. |
| `POST /api/v1/trainer/sessions/{session_id}/commit` | BFF verifies completed status, `allowedActions.canCommit`, and `expected_candidate_snapshot_at`, then appends a commit event and updates replay resolution/artifacts. | No replay decision API exists. | Service must atomically check candidate snapshot, append decision event, update replay resolution, and return the current decision view. |
| `POST /api/v1/trainer/sessions/{session_id}/discard` | Same as commit, but records discard and leaves no `after_artifact_ref`. | No replay decision API exists. | Same atomic decision API requirement as commit; do not leave commit/discard as BFF JSON writes on the activated normal path. |
| Degraded/unavailable behavior | BFF can return degraded, stale, unavailable, `preview_unavailable`, 404, 409, or 503 depending on route and store state. | No network-failure mapping exists. | Parent must map service unreachable, store missing, stale service data, and object-not-found distinctly. Empty lists are authoritative only when the service-backed surface is fresh/ok. |

---

## 5. Operator Journey Handoff

### 5.1 Normal Trainer Journey After Activation

1. Operator opens a Trainer Workbench screen; frontend calls the published
   `/api/v1/trainer/...` BFF route.
2. BFF authenticates the operator, validates request params/body, and checks
   persona/session authority where it already does today.
3. BFF calls `training-session-svc` through the explicit service URL.
4. `training-session-svc` owns durable training-session records, append-only
   event sequence allocation, controls persistence, preview state, replay
   records, commit/discard decision events, and health.
5. BFF projects the service response into the existing TW-01/TW-02/TW-03/TW-04
   browser contract, including `allowedActions`, links, pagination, and
   `meta.surfaces.*`.
6. Frontend renders from BFF response fields only and follows returned links;
   it does not call the service directly or synthesize trainer state locally.

### 5.2 Degraded or Migration Journey

1. If `training-session-svc` is unreachable, BFF should return the existing
   route-specific degraded/unavailable semantics rather than authoritative empty
   state.
2. If a BFF JSON-store fallback remains during migration, responses must expose
   truthful source/staleness metadata and must not claim service-backed
   freshness.
3. Preview absence should remain a structured `preview_unavailable` branch for
   TW-03 where the route contract requires a success body.
4. Commit/discard and control-patch CTAs must be suppressed whenever the backing
   service surface is degraded or unavailable.

---

## 6. Frontend Handoff Materials

This sidecar does not create a new Lovable task. Existing Trainer Workbench
frontend materials remain valid because service activation is behind the BFF.

| Screen / flow | Frontend contract material | Notes |
|---|---|---|
| Trainer Workbench packet family | `docs/pantheon-handoffs/TW-007-trainer-workbench/PACKET_FAMILY.md` | TW-01 through TW-04 are route-live BFF-backed modules; service activation must not reopen browser contract shape by default. |
| Teaching Dialog | `docs/bff/TW-01-teaching-dialog.md`, `docs/pantheon-handoffs/TW-01-teaching-dialog/FRONTEND_CHANGE_SPEC.md` | Keep create/list/detail/message on BFF `/api/v1/trainer/sessions`. |
| Parameter Controls | `docs/bff/TW-02-parameter-controls.md`, `docs/pantheon-handoffs/TW-02-parameter-controls/FRONTEND_CHANGE_SPEC.md` | Keep control patch authority on BFF `allowedActions.canPatchControls`. |
| Before/After Compare | `docs/bff/TW-03-before-after-compare.md`, `docs/pantheon-handoffs/TW-03-before-after-compare/FRONTEND_CHANGE_SPEC.md` | Preserve structured `preview_unavailable`, warning hierarchy, and polling contract. |
| Teaching Replay | `docs/bff/TW-04-teaching-replay.md`, `docs/pantheon-handoffs/TW-04-teaching-replay/FRONTEND_CHANGE_SPEC.md` | Preserve replay list/detail, ordered events, evidence refs, and commit/discard authority. |

Frontend implementation constraints:

- Use `operator-bff` only.
- Do not add browser calls to `training-session-svc`.
- Do not substitute `/api/v1/personas/{persona_id}/teaching` for Trainer
  session or replay routes.
- Preserve backend ordering by `sequence_number`; do not re-sort events by
  timestamp.
- Use `allowedActions` as the only CTA authority source.
- Treat missing required fields as a BFF gap.
- Treat `meta.surfaces.trainer_dialog`, `trainer_controls.state`,
  `trainer_preview`, and `trainer_replay` as authoritative degradation signals.

---

## 7. Minimal Smoke Requests for Parent QA

Training-session service health after compose activation:

```http
GET /health
Host: training-session-svc
```

BFF session create:

```http
POST /api/v1/trainer/sessions
Authorization: Bearer op-42:operator
Content-Type: application/json

{
  "persona_id": "persona-alpha",
  "session_type": "trainer",
  "objective": "Tune risk controls for the next paper canary window.",
  "context_refs": [
    { "type": "deployment_plan", "id": "plan-F-042" }
  ]
}
```

BFF lifecycle and append checks:

```http
GET /api/v1/trainer/sessions?persona_id=persona-alpha&page_size=10
Authorization: Bearer op-42:operator
```

```http
POST /api/v1/trainer/sessions/{session_id}/message
Authorization: Bearer op-42:operator
Content-Type: application/json

{
  "message_body": "Reduce max leverage before previewing the candidate state."
}
```

BFF controls and preview checks:

```http
GET /api/v1/trainer/sessions/{session_id}/controls
Authorization: Bearer op-42:operator
```

```http
POST /api/v1/trainer/sessions/{session_id}/preview
Authorization: Bearer op-42:operator
Content-Type: application/json

{
  "refresh_mode": "manual"
}
```

BFF replay decision checks:

```http
GET /api/v1/trainer/replay?persona_id=persona-alpha&status=completed
Authorization: Bearer op-42:operator
```

```http
POST /api/v1/trainer/sessions/{session_id}/commit
Authorization: Bearer op-42:operator
Content-Type: application/json

{
  "expected_candidate_snapshot_at": "2026-04-28T17:45:00Z",
  "note": "Operator reviewed replay evidence and accepted the candidate."
}
```

Suggested focused verification commands for the parent owner:

```bash
python3 -m pytest -q \
  services/control-plane/bff/test_tw01_teaching_dialog_contract.py \
  services/control-plane/bff/test_tw02_parameter_controls_contract.py \
  services/control-plane/bff/test_tw03_before_after_compare_contract.py \
  services/control-plane/bff/test_tw04_teaching_replay_contract.py
docker compose config --quiet
```

After implementation, add an HTTP service-wrapper smoke that starts
`training-session-svc`, exercises session create/event append/control
patch/preview/replay decision through the service, then proves the BFF normal
path uses the explicit service URL.

---

## 8. Verification Evidence

Structural checks performed for this packet:

- Confirmed no `services/training-session/`, `services/training_session/`, or
  `services/trainer/` service directory exists.
- Confirmed no `PANTHEON_*TRAIN*` compose/service URL wiring for a
  training-session backend is present.
- Confirmed BFF TW-01 through TW-04 routes are mounted under
  `/api/v1/trainer/...`.
- Confirmed current BFF storage path uses `teaching_sessions`,
  `trainer_controls`, `trainer_previews`, and `trainer_replays` service-store or
  local fallback datasets.
- Confirmed frontend handoff materials already require BFF-only calls and BFF
  gap handoff on field drift.

Focused verification run by this sidecar:

```bash
python3 -m pytest -q \
  services/control-plane/bff/test_tw01_teaching_dialog_contract.py \
  services/control-plane/bff/test_tw02_parameter_controls_contract.py \
  services/control-plane/bff/test_tw03_before_after_compare_contract.py \
  services/control-plane/bff/test_tw04_teaching_replay_contract.py
```

Result: `48 passed, 8 warnings in 5.12s`.

Warnings were existing `datetime.utcnow()` deprecation warnings from
`services/control-plane/bff/read_store.py`; no contract failures were observed.
