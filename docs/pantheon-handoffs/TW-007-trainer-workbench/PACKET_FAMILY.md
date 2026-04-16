# TW-007 Trainer Workbench — Canonical Packet Family

## Header

- Packet family ID: `TW-007`
- Workbench: Trainer Workbench
- Phase origin: `BP5-WB-007`
- Lovable readiness: **not ready** — all four modules require net-new BFF routes and canonical session-mutation or compare contracts; Lovable handoff must not open until the BFF prerequisites listed below are satisfied
- Recommended wave: Wave 3 — after Operator Console (Waves 1–2), Persona Workbench (Waves 1–2), and Governance / Evolution workbench packetization are settled
- Owner: Codex
- Reviewer: Claude

---

## Objective

Turn the current demo-grade Trainer shell into a real BFF-backed teaching workflow. Give operators one coherent surface for starting training sessions, adjusting control parameters, reviewing before/after comparisons, and replaying session history. All data authority and mutation authority must come from the Pantheon BFF — no client-side session synthesis, no locally derived control state, no mock preview results.

---

## Existing Pantheon Support (pre-conditions)

Before any Trainer Workbench module can be packetized, the following canonical artifacts must be treated as known truth:

| Artifact | Location | What it defines |
|---|---|---|
| `PERSONA_RUNTIME_MODEL.md` | L1 policy | Persona identity and session lifecycle; `session_type=trainer` must stay canonical; `SessionPersona.metadata.training.*` fields |
| `Pantheon_API_Service_Contract_設計版.md` §5.x | L3 design docs | `POST /api/v1/trainer/sessions`, `GET /api/v1/trainer/sessions/:id`, `POST /api/v1/trainer/sessions/:id/message`, `POST /api/v1/trainer/sessions/:id/patch` — named as design intent only; not canonical BFF truth |
| `Pantheon_資料表_Schema_設計版.md` | L3 design docs | `TrainingSession`, `TeachingEvent`, and control-state schema direction — not yet promoted to canonical BFF contract |
| Persona Management composed screen (`PKT-004`) and `PS-05` teaching-history surface | Persona Workbench packet family | Read-only teaching-session lists exist at `/api/v1/operator/persona-management/{persona_id}` and `/api/v1/personas/{persona_id}/teaching`; these are Persona drilldown evidence only — they do not constitute a Trainer-owned workflow |
| Demo-grade Trainer shell | `front-ai-trading-system` | Preview and backtest-refresh scaffolding only; cannot be treated as authoritative until canonical packet families exist |

The existing teaching-history read surfaces are evidence inputs. They do **not** define a Trainer-owned dialog surface, a parameter patch path, a compare payload, or a replay contract. Those are the gaps this packet family addresses.

---

## Module Inventory

| Module ID | Module name | Screen / surface scope | Lovable readiness | Wave order |
|---|---|---|---|---|
| `TW-01` | Teaching Dialog | start session, show transcript, send coaching messages, display session status and actor context | not ready | Wave 3 — 1st |
| `TW-02` | Parameter Controls | inspect current control state, edit control patches, surface validation or warning feedback | not ready | Wave 3 — 2nd |
| `TW-03` | Before/After Compare | preview metrics, warnings, control-state diff, and rapid-eval result summary | not ready | Wave 3 — 3rd |
| `TW-04` | Teaching Replay | teaching-session history, ordered event replay, commit or discard evidence, and replay entrypoint | not ready | Wave 3 — 4th |

---

## TW-01 Teaching Dialog

### Surface scope

- **Session start**: a form that creates a new training session against a known persona. Fields anchored to the L3 API contract design intent: `persona_id` (target persona identity), `session_type` (must be `trainer`), `objective` (free-text coaching goal), and optional `context_refs` (typed context array: `{type, id}`). Submission target is `POST /api/v1/trainer/sessions`. The BFF must return `session_id` and `status: active`.
- **Transcript panel**: chronological list of coaching events for the active session. Each event row shows `event_id`, `actor` (`operator` or `persona`), `message_body`, `emitted_at`, and an optional `outcome_signal` label. Transcript events must come from `GET /api/v1/trainer/sessions/:id` — do not construct transcript state from local message history.
- **Coaching message composer**: text input that submits a new coaching message to the active session via `POST /api/v1/trainer/sessions/:id/message`. The message composer must be disabled when session `status` is not `active`. The BFF must echo the composed event back into the transcript without client-side insertion.
- **Session status and actor context**: a status header showing `session_id`, `persona_id`, `status` (`active | paused | completed | abandoned`), `started_at`, and the target persona's display name and current role context. Actor context is BFF-resolved — do not derive persona display state from client-side persona cache.
- **Session list**: a paginated list of past and active training sessions for a given persona, filterable by `status`. Each row shows `session_id`, `status`, `persona_id`, `started_at`, and a message count badge. Source: query filter on `GET /api/v1/trainer/sessions?persona_id={id}`.
- **Degradation**: when `meta.surfaces.trainer_dialog` is `degraded` or `unavailable`, show the canonical non-dismissable degradation banner inherited from `PKT-005`. Never show "no sessions" as authoritative when the surface is degraded — a false empty state during an active training session is a data-integrity risk.

### Backend gaps

| Route or contract | Status | Notes |
|---|---|---|
| `POST /api/v1/trainer/sessions` | **missing** | create route; body: `persona_id`, `session_type=trainer`, `objective`, `context_refs[]`; must return `session_id`, `status: active`, `started_at` |
| `GET /api/v1/trainer/sessions/:id` | **missing** | session detail and transcript read route; must return `session_id`, `persona_id`, `status`, `started_at`, `events[]` (ordered transcript), and `meta.surfaces.trainer_dialog` |
| `GET /api/v1/trainer/sessions` | **missing** | session list route; must support `persona_id`, `status`, `page_token`, `page_size`; must include `meta.surfaces.trainer_dialog` |
| `POST /api/v1/trainer/sessions/:id/message` | **missing** | coaching message submission; body: `message_body`; must be rejected when session `status != active`; BFF must echo the `TeachingEvent` back into the session transcript |
| Trainer session lifecycle contract | **missing** | `active → paused → completed | abandoned` states; transition semantics (who may pause, complete, or abandon a session); `persona_id` binding constraints; must be promoted from L3 design intent to canonical BFF truth |
| `TeachingEvent` schema (TW-01 subset) | **missing** | for the dialog surface: `event_id`, `session_id`, `actor` (`operator | persona`), `message_body`, `emitted_at`, `sequence_number`, optional `outcome_signal`; append-only ordering guarantee via `sequence_number` |

### Packetization prerequisite

The trainer-session lifecycle (`active → paused → completed | abandoned`), the `TeachingEvent` schema (at minimum the dialog event fields), and the transcript read contract must be promoted from L3 design intent to canonical BFF truth before a dialog shell can be packet-defined. Depends on persona identity (`PERSONA_RUNTIME_MODEL.md`) and `session_type=trainer` remaining canonical.

### Lovable readiness gate

`false` — all six rows above must be resolved and field shapes locked before a screen spec or example payload can be created.

---

## TW-02 Parameter Controls

### Surface scope

- **Control state panel**: displays the current mutable control parameters for an active training session. The control-state object is BFF-provided and includes at minimum: `control_id`, `parameter_key`, `current_value`, `allowed_range` (min/max or allowed set), `unit`, and a `last_modified_at` timestamp. Do not construct the control state from any client-side session cache.
- **Patch editor**: an edit form that allows the operator to adjust one or more control parameters within their `allowed_range`. The patch payload targets `POST /api/v1/trainer/sessions/:id/patch`. Each patch is a structured delta: `[{parameter_key, proposed_value}]`. The patch CTA must be disabled when session `status != active`.
- **Validation and warning feedback**: the BFF must return a synchronous validation response before applying the patch. The response must include `valid: bool`, `warnings: [{parameter_key, warning_code, message}]`, and `applied: bool`. If `valid = false`, the editor highlights the offending parameter without applying changes. If `valid = true` but warnings are present, a warning banner is shown before a confirmation CTA appears.
- **Control-state diff preview**: after a successful patch, the panel shows the before/after delta inline — `previous_value` vs. `new_value` — for each changed parameter. This diff is rendered from the BFF patch response, not derived client-side.
- **Degradation**: when `meta.surfaces.trainer_controls` is `degraded`, show the last-known control state with a staleness banner. When `unavailable`, show the canonical unavailable banner. The patch CTA must be hidden whenever the surface is `degraded` or `unavailable` — do not allow mutations against a stale control state.

### Backend gaps

| Route or contract | Status | Notes |
|---|---|---|
| `GET /api/v1/trainer/sessions/:id/controls` | **missing** | control-state read route; must return the full mutable control-state object with `allowed_range` per parameter; must include `meta.surfaces.trainer_controls` |
| `POST /api/v1/trainer/sessions/:id/patch` | **missing** | control patch route; body: `patches: [{parameter_key, proposed_value}]`; must return `valid`, `warnings[]`, `applied`, `updated_controls[]` (full updated state); must be rejected when session `status != active` |
| Control-state schema | **missing** | canonical definition of `ControlParameter` object: `control_id`, `parameter_key`, `current_value`, `allowed_range`, `unit`, `last_modified_at`; must be promoted from L3 design intent to BFF contract |
| Patch validation contract | **missing** | synchronous validation semantics before application; `warning_code` taxonomy; `applied: false` path when `valid = false`; must not silently clip values to `allowed_range` — rejection must be explicit |
| Patch diff response shape | **missing** | `updated_controls[]` in the patch response must include `previous_value` so the UI can render an inline before/after diff without a separate read call |

### Packetization prerequisite

The control-patch payload, the `ControlParameter` schema, the validation and warning contract, and the control-state diff semantics must be defined as canonical BFF truth before a parameter panel can be packet-defined. Depends on `TW-01` establishing the active session context and `session_id` identity.

### Lovable readiness gate

`false` — all five rows above must be resolved and field shapes locked before a screen spec can be opened.

---

## TW-03 Before/After Compare

### Surface scope

- **Metric panels**: side-by-side or before/after display of the key performance metrics produced by a rapid-eval of the current session state. The compare surface reads from a dedicated preview or rapid-eval route — it must not derive metric values from raw control-state diffs or client-side simulations.
- **Warning hierarchy**: tiered warning display drawn from the BFF preview response. Warning levels must be BFF-defined (at minimum `critical`, `high`, `medium`, `informational`). Do not derive warning severity client-side.
- **Control-state diff view**: a structured display of which parameters changed between the baseline session state and the patched candidate state. The diff is the `patches[]` applied so far in the session, rendered using the `previous_value` / `new_value` shape from the `TW-02` patch response.
- **Rapid-eval result summary**: a top-level summary card drawn from the preview response: `eval_id`, `status` (`complete | pending | failed`), `baseline_snapshot_at`, `candidate_snapshot_at`, `metric_delta[]`, `warning_count_by_level`, and a `preview_quality` indicator.
- **`preview_unavailable` degraded state**: when the BFF returns `preview_unavailable` or `meta.surfaces.trainer_preview` is `unavailable`, the compare panel must display a canonical degraded-preview message (not a blank panel). The degraded copy must name the surface and explain that the rapid-eval is temporarily unavailable, without surfacing internal error codes.
- **Refresh**: a manual refresh CTA that re-triggers the rapid-eval via the preview route. Refresh must be disabled while a previous eval `status = pending`. The CTA is absent when `preview_unavailable`.

### Backend gaps

| Route or contract | Status | Notes |
|---|---|---|
| Preview / rapid-eval route | **missing** | `POST /api/v1/trainer/sessions/:id/preview` or `GET /api/v1/trainer/sessions/:id/preview`; must return `eval_id`, `status`, `baseline_snapshot_at`, `candidate_snapshot_at`, `metric_delta[]`, `warnings[]` (with `level`), `preview_quality`, and `meta.surfaces.trainer_preview` |
| Preview response contract | **missing** | `metric_delta[]` shape: `{metric_key, baseline_value, candidate_value, delta, delta_pct, unit}`; `warnings[]` shape: `{warning_id, level, parameter_key, message}`; `preview_unavailable` semantics must be an explicit contract state, not a missing route response |
| `preview_unavailable` degraded contract | **missing** | when the rapid-eval infrastructure is unavailable, the BFF must return a structured `preview_unavailable` payload (not a 5xx); the UI may not mask this as a loading state |
| Async eval status polling (if applicable) | **missing** | if `status = pending` on the preview response, the polling interval, max wait, and timeout semantics must be defined before the UI implements a polling loop; the BFF must not leave eval status permanently `pending` |
| `meta.surfaces.trainer_preview` | **missing** | staleness signal for the compare surface; must be included in every preview response; controls the canonical degradation banner via `PKT-005` |

### Packetization prerequisite

The preview or rapid-eval response contract (metric taxonomy, warning shape, `preview_unavailable` semantics), and the `meta.surfaces.trainer_preview` staleness signal must be locked as canonical BFF truth before a compare surface can be packet-defined. Depends on `TW-02` producing a patchable candidate state (the preview evaluates the session after patches are applied).

### Lovable readiness gate

`false` — all five rows above must be resolved and field shapes locked before a screen spec can be opened.

---

## TW-04 Teaching Replay

### Surface scope

- **Session history list**: paginated list of completed training sessions for a given persona. Each row shows `session_id`, `status` (`completed | abandoned`), `persona_id`, `started_at`, `ended_at`, and an event count. Source: `GET /api/v1/trainer/sessions?persona_id={id}&status=completed`. This list is distinct from the Persona Management teaching-history surface — it is Trainer-owned and must expose the full `TeachingEvent` schema for replay navigation.
- **Ordered event timeline**: chronological replay display of all `TeachingEvent` records for a selected completed session. Each event shows `event_id`, `actor`, `event_type` (`message`, `control_patch`, `preview_trigger`, `outcome_signal`, `commit`, `discard`), `body` or summary, `emitted_at`, and any `evidence_ref`. Events must be ordered by `sequence_number` — do not sort client-side.
- **Evidence drawer**: expandable per-event panel for events that carry an `evidence_ref`. The evidence link is BFF-resolved (not a raw ref that the client must look up). Evidence types include telemetry snapshots, lineage edges, compare results, and persona capability records.
- **Replay action copy**: explicit commit or discard controls for sessions that produced a confirmed candidate state and are eligible for promotion. `commit` confirms the patched state as the new baseline. `discard` abandons the candidate and resets to the prior baseline. Both commands target `POST /api/v1/trainer/sessions/:id/commit` and `POST /api/v1/trainer/sessions/:id/discard`. Each CTA is visible only when `allowedActions.canCommit` or `allowedActions.canDiscard` is present and truthy in the session response.
- **Replay cursor**: a step-through control for navigating events one-by-one in chronological order. Replay cursor state is ephemeral client state — the event sequence itself comes from the BFF. Pause, resume, and jump-to-event controls are bounded by the `sequence_number` range.
- **Degradation**: when `meta.surfaces.trainer_replay` is `degraded`, show the last-known event list with a staleness banner. When `unavailable`, show the canonical unavailable banner. Commit and discard CTAs must be hidden whenever the surface is degraded or unavailable — do not allow session promotion against a stale event history.

### Backend gaps

| Route or contract | Status | Notes |
|---|---|---|
| Standalone Trainer replay read route | **missing** | `GET /api/v1/trainer/sessions/:id/events` or equivalent; must return all `TeachingEvent` records ordered by `sequence_number`; must include `meta.surfaces.trainer_replay` and `allowedActions.canCommit`, `allowedActions.canDiscard` |
| `TeachingEvent` schema (full) | **missing** | extends the TW-01 dialog-event subset to include: `event_type` (`message | control_patch | preview_trigger | outcome_signal | commit | discard`), `evidence_ref` (nullable, BFF-pre-resolved link), `patch_delta` (for `control_patch` events: `{parameter_key, previous_value, new_value}`), `eval_ref` (for `preview_trigger` events: `eval_id`); append-only guarantee must be enforced by `sequence_number` |
| BFF-resolved evidence links | **missing** | when a `TeachingEvent` carries an `evidence_ref`, the BFF must resolve it to a typed canonical link (`{type, id, display_label, url_pattern}`) before serving the event — the client must not construct evidence navigation from raw ref identifiers |
| Commit contract | **missing** | `POST /api/v1/trainer/sessions/:id/commit`; must be gated by `allowedActions.canCommit`; must not be invocable when session `status != completed`; must return the updated `status` and a `committed_at` timestamp |
| Discard contract | **missing** | `POST /api/v1/trainer/sessions/:id/discard`; must be gated by `allowedActions.canDiscard`; must not be invocable when session `status != completed`; must return the updated `status` and a `discarded_at` timestamp |
| Before/after artifact refs | **missing** | the commit response (or a separate `GET /api/v1/trainer/sessions/:id/artifacts`) must expose `before_artifact_ref` (baseline snapshot before the session) and `after_artifact_ref` (committed candidate snapshot) so downstream review surfaces can compare them without re-querying the event log |

### Packetization prerequisite

The full `TeachingEvent` schema (including `control_patch`, `preview_trigger`, `commit`, and `discard` event types), the BFF-resolved evidence link contract, the commit and discard write paths with `allowedActions` gating, and the before/after artifact refs must all be defined as canonical BFF truth before a replay surface can be packet-defined. Depends on `TW-01` transcript events and `TW-03` Before/After Compare evidence being stable and addressable.

### Lovable readiness gate

`false` — all six rows above must be resolved and field shapes locked before a screen spec can be opened.

---

## Backend Gap Matrix

Each row is scoped to one or more modules. A module advances to Lovable-ready when all rows assigned to that module (and its upstream prerequisite modules) are resolved. See the Promotion Criteria section for the per-module gate definition.

| Route or contract | Module(s) | Gap type | Blocking what |
|---|---|---|---|
| `POST /api/v1/trainer/sessions` | TW-01 | missing write route | session creation; entire TW-01 dialog shell and all downstream module identity |
| `GET /api/v1/trainer/sessions/:id` | TW-01, TW-04 | missing read route | session detail, transcript panel, and replay history identity |
| `GET /api/v1/trainer/sessions` | TW-01, TW-04 | missing read route | session list in dialog and replay history |
| `POST /api/v1/trainer/sessions/:id/message` | TW-01 | missing write route | coaching message composer; blocked when `status != active` |
| Trainer session lifecycle contract | TW-01, TW-02, TW-03, TW-04 | missing lifecycle contract | `active → paused → completed | abandoned` state machine; blocks all four modules because session `status` governs all write-path CTAs |
| `TeachingEvent` schema (TW-01 subset) | TW-01 | missing object contract | dialog transcript ordering, append-only guarantee, `sequence_number` |
| `GET /api/v1/trainer/sessions/:id/controls` | TW-02 | missing read route | entire Parameter Controls module |
| `POST /api/v1/trainer/sessions/:id/patch` | TW-02 | missing write route | control patch CTA; gated by `status = active`; blocks TW-03 (compare evaluates patched candidate) |
| Control-state schema | TW-02 | missing object contract | `ControlParameter` object; `allowed_range`; blocks patch editor and validation display |
| Patch validation contract | TW-02 | missing contract | synchronous `valid / warnings[]` response; blocks warning feedback in editor and TW-03 compare |
| Patch diff response shape | TW-02, TW-03 | missing contract | `previous_value` in `updated_controls[]`; blocks inline control-state diff in TW-03 compare panel |
| Preview / rapid-eval route | TW-03 | missing route | entire Before/After Compare module |
| Preview response contract | TW-03 | missing object contract | `metric_delta[]`, `warnings[]` with severity levels, `preview_quality`; blocks metric panels and warning hierarchy |
| `preview_unavailable` degraded contract | TW-03 | missing contract | degraded-state copy for compare panel; must be explicit BFF contract state, not a 5xx |
| Async eval status polling semantics | TW-03 | missing contract | polling interval, max wait, timeout; blocks UI from implementing a safe polling loop |
| `meta.surfaces.trainer_preview` | TW-03 | missing staleness signal | degradation banner wiring for compare surface |
| Standalone Trainer replay read route | TW-04 | missing read route | entire Teaching Replay module; distinct from Persona Management teaching-history |
| `TeachingEvent` schema (full) | TW-04 | missing object contract | all event types including `control_patch`, `preview_trigger`, `commit`, `discard`; `evidence_ref` inclusion; blocks event timeline and evidence drawer |
| BFF-resolved evidence links | TW-04 | missing BFF-side resolution | evidence drawer per event; client must not resolve evidence from raw refs |
| Commit contract | TW-04 | missing write route | commit CTA; gated by `allowedActions.canCommit`; `status = completed` precondition |
| Discard contract | TW-04 | missing write route | discard CTA; gated by `allowedActions.canDiscard`; `status = completed` precondition |
| Before/after artifact refs | TW-04 | missing contract | downstream artifact comparison after commit; must not require replaying the full event log |

---

## Internal Ordering and Dependency Chain

| Position | Module | Why this order | Upstream dependency within workbench |
|---|---|---|---|
| Wave 3 — 1st | `TW-01 Teaching Dialog` | establishes the trainer-session entity, `session_id`, transcript event contract, and lifecycle contract that every later module references; the transcript is the evidentiary backbone of the entire workbench | none — can start when Wave 3 opens |
| Wave 3 — 2nd | `TW-02 Parameter Controls` | patch semantics only make sense after a concrete training session and current control state exist; the patch validation and diff response feed directly into the compare surface | `TW-01`: `session_id`, `status = active`, and transcript event ordering contract |
| Wave 3 — 3rd | `TW-03 Before/After Compare` | preview and rapid-eval outputs compare a candidate patch against a known session state; the compare surface is only meaningful after patches exist to compare | `TW-02`: control-patch payload, `updated_controls[]` with `previous_value`, and patch validation contract; `TW-01`: session state for the baseline snapshot |
| Wave 3 — 4th | `TW-04 Teaching Replay` | replay is only honest once session events, compare artifacts, commit and discard semantics, and before/after artifact refs are stable and addressable; the replay surface is the durable record of the complete teaching arc | `TW-01`: session identity and transcript events; `TW-03`: before/after compare evidence refs. The full replay-grade `TeachingEvent` schema remains `TW-04` scope. |

---

## Promotion Criteria

A Trainer Workbench module moves from **not ready** to **ready** (and may be handed to Lovable) when all of the following are true:

1. All BFF routes listed in that module's Backend Gaps table are implemented and have agreed field shapes.
2. The module's `meta.surfaces.*` staleness signal is defined and wired through to the canonical degradation banner (`PKT-005`).
3. All `allowedActions` authority signals for that module are backend-shaped and documented.
4. An example payload JSON exists for the module's primary read surface.
5. All upstream prerequisite modules are already Lovable-ready (per the dependency chain above).

No Trainer Workbench module should be handed to Lovable before its own criteria and all upstream criteria are met.

---

## Cross-Cutting Rules

### No client-side session synthesis

The BFF must not leave the client to infer training session state from raw event stream counts or elapsed time. All session lifecycle state flows from:

1. `SessionPersona` objects (canonical session state from `PERSONA_RUNTIME_MODEL.md`)
2. `status` and `allowedActions` fields on every Trainer read response
3. BFF-shaped `meta.surfaces.*` staleness signals
4. `TeachingEvent` records with `sequence_number` ordering guarantees

### No client-side metric derivation

The compare surface must never derive metric deltas, warning severity, or eval quality from:
- Raw control-state parameter values
- Local simulation or backtest results
- Cached prior-session metrics

All metric and warning data must come from the BFF preview or rapid-eval route.

### Write-path authority model

All Trainer Workbench write actions must follow the `allowedActions` authority pattern established by `PKT-001` and `F-042`:

- `canCommit` and `canDiscard` in the session response gate the commit and discard CTAs.
- Patch, message, commit, and discard routes must all be rejected by the BFF when the session `status` does not permit that action — the UI must not rely on local status checks as the sole guard.
- When `status != active`, the message composer and patch editor are disabled. When `status != completed`, commit and discard CTAs are hidden.

### Degradation banner inheritance

All four modules must inherit the canonical degradation banner from `PKT-005`. The banner must be non-dismissable. Individual surface staleness states (`meta.surfaces.trainer_dialog`, `meta.surfaces.trainer_controls`, `meta.surfaces.trainer_preview`, `meta.surfaces.trainer_replay`) must be passed through from the BFF — never derived locally.

### `preview_unavailable` is not a loading state

When the rapid-eval infrastructure is temporarily unavailable, the BFF must return a structured `preview_unavailable` contract response rather than a 503 or a stalled `status: pending`. The UI must render explicit degraded-preview copy, not a spinner that never resolves.

### Relationship to existing Persona teaching surfaces

`GET /api/v1/personas/{persona_id}/teaching` and the `PS-05` teaching-history drilldown inside Persona Management remain canonical for read-only teaching evidence. This packet family does **not** replace or fork those surfaces. The Trainer Workbench adds:

- `TW-01`: session-mutation path and coaching message flow (not covered by Persona teaching history)
- `TW-02`: control-patch path (entirely absent from Persona surfaces)
- `TW-03`: preview and compare surface (absent from Persona surfaces)
- `TW-04`: replay with commit/discard evidence (Persona surfaces expose list only, not ordered events or artifact refs)

---

## Separation Rules

When authoring packet language for these modules:

- Put dialog shell copy, control widget labels, compare layout copy, replay timeline copy, and degraded-state wording in `Missing screen-spec work`.
- Put absent trainer mutation routes, control-state schema, preview or rapid-eval contracts, `TeachingEvent` schema, replay event-stream route, commit/discard write paths, and before/after artifact refs in `Backend or contract dependencies`.

---

## Canonical References

- Backlog source: `docs/02-architecture/consensus/sessions/phase3-2026-04-14-pantheon-console-loop/pantheon-console-workbench-backlog.md` (Trainer Workbench section)
- L1 policy basis: `PERSONA_RUNTIME_MODEL.md` — persona identity, `session_type=trainer`, and session lifecycle semantics
- L3 design intent: `Pantheon_API_Service_Contract_設計版.md`, `Pantheon_資料表_Schema_設計版.md` — Trainer route names and schema shapes as design direction only; not canonical BFF truth
- Existing read-only evidence: `GET /api/v1/operator/persona-management/{persona_id}` and `GET /api/v1/personas/{persona_id}/teaching` (Persona Management and `PS-05` drilldown — not Trainer-owned)
- Degradation substrate: `PKT-005` degradation banner and SSE substrate must be inherited by all four modules
- Write-path authority precedent: `PKT-001 Governance Review Queue` and `F-042 Promotion Review` define the `allowedActions` and `POST /api/v1/operator/commands` patterns; Trainer write paths must follow the same authority-signal model
- Handoff directory: `docs/pantheon-handoffs/TW-007-trainer-workbench/`
- Dependent services: `BP5-SVC-014` (persona platform and consultation read surfaces — done), `BP5-SVC-009` (telemetry ingest service — done)
