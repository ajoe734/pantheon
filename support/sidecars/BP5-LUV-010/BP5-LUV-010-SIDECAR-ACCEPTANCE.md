# BP5-LUV-010 Acceptance Packet

**Sidecar kind:** `acceptance_packet`
**Sidecar task:** `BP5-LUV-010-SIDECAR-ACCEPTANCE`
**Helper parent:** `BP5-LUV-010` — Drive `PKT-005` SSE substrate through the Lovable implementation loop
**Parent owner:** `Claude`
**Parent reviewer:** `Codex`
**Prepared by:** `Codex2`
**Reviewer:** `Claude`
**Date:** `2026-04-16`
**Status:** `finalized`

> Scope constraint: support artifact only. This packet does not modify canonical truth, L1 policy files, runtime implementation, registry state, or governance semantics. It packages the acceptance surface for the `BP5-LUV-010` Lovable slice so the assigned reviewer can validate the loop state without re-scanning the full task history.

---

## 1. Purpose

This sidecar packet gives `Claude` a compact acceptance surface for the open parent task `BP5-LUV-010`:

1. restate the parent acceptance criteria against the current PKT-005 SSE substrate evidence
2. map the one formal upstream dependency and the concrete downstream loop artifacts
3. summarize the exact SSE contract and implementation constraints the Lovable loop must preserve
4. provide a reviewer handoff checklist that keeps this slice support-only

---

## 2. Parent Acceptance Criteria Checklist

From active `ai-status.json` and the phase5 planning session:

| # | Criterion | Status | Evidence |
|---|---|---|---|
| 1 | `sse-substrate` completes one full Lovable loop with explicit closure or follow-up | **READY FOR EXECUTION, NOT YET MET** | The loop has been materialized and dispatched via `.coordination/responses/PKT-005-sse-substrate-lovable-ui-task.yaml` and `.coordination/responses/PKT-005-sse-substrate-lovable-prompt.md`, but no `ui-done`, `frontend-feedback`, or blocker handoff artifact exists yet in this repo. |
| 2 | replay, reconnect, heartbeat, and reconciliation semantics remain canonical and shared | **SOURCE-READY / MUST BE PRESERVED** | The semantics are already defined consistently in `docs/screens/PKT-005-sse-substrate.md`, `docs/bff/PKT-005-sse-substrate.md`, `docs/pantheon-handoffs/PKT-005-sse-substrate/FRONTEND_CHANGE_SPEC.md`, and `docs/examples/PKT-005-sse-substrate.json`. |

**Overall verdict:** the parent task is correctly staged for execution, but it is not yet at acceptance. This sidecar packet confirms that the contract surface is coherent and that the remaining work is the actual Lovable loop progression plus formal return artifacts.

### Evidence by current loop stage

| Stage | Evidence present now | Missing before parent can close |
|---|---|---|
| Lovable dispatch | `.coordination/responses/PKT-005-sse-substrate-lovable-ui-task.yaml`, `.coordination/responses/PKT-005-sse-substrate-lovable-prompt.md` | — |
| Contract/spec references | `docs/screens/PKT-005-sse-substrate.md`, `docs/bff/PKT-005-sse-substrate.md`, `docs/pantheon-handoffs/PKT-005-sse-substrate/FRONTEND_CHANGE_SPEC.md`, `docs/examples/PKT-005-sse-substrate.json` | — |
| UI completion handoff | none present yet | `.coordination/requests/PKT-005-sse-substrate-ui-done.yaml` or explicit blocker/gap handoff |
| Pantheon feedback return | none present yet | frontend feedback bundle under `docs/pantheon-feedback/PKT-005-sse-substrate/` if implementation returns |
| Parent review/closeout | parent task still `todo` in active state | reviewer approval and owner closeout after loop return |

---

## 3. Dependency Map

### Upstream dependency

| Dependency | Status | Relevance to `BP5-LUV-010` |
|---|---|---|
| `BP5-SVC-016` — Package the honest service stack into Docker, compose, and smoke topology | `done` | establishes the honest service-stack baseline assumed by the SSE substrate Lovable loop and prevents the UI slice from validating against fallback-only topology |

No unresolved upstream blocker is recorded for this sidecar or for the parent task materialization.

### Loop dependency chain

```text
BP5-SVC-016
  -> BP5-LUV-010 lovable-ui-task dispatch
      -> either:
         a) ui-done handoff
         b) bff-gap handoff
         c) execution blocker / follow-up
      -> Pantheon feedback bundle or explicit follow-up packet
      -> reviewer approval
      -> parent closeout
```

### Downstream artifacts this loop is expected to produce

| Expected artifact | Role |
|---|---|
| `.coordination/requests/PKT-005-sse-substrate-ui-done.yaml` | explicit frontend completion handoff when implementation succeeds |
| `.coordination/requests/PKT-005-sse-substrate-bff-gap.yaml` | required if any event field or live contract surface is missing |
| `docs/pantheon-feedback/PKT-005-sse-substrate/LOVABLE_CHANGE_FEEDBACK.md` | Pantheon review summary for returned frontend work |
| `docs/pantheon-feedback/PKT-005-sse-substrate/API_GAP_REQUESTS.json` | explicit gap report, including `[]` when no open gaps remain |
| `docs/pantheon-feedback/PKT-005-sse-substrate/UI_DECISIONS.md` | decision log for SSE substrate wiring and host-screen integration choices |
| `docs/pantheon-feedback/PKT-005-sse-substrate/QA_STATUS.md` | targeted verification and residual-risk note |

---

## 4. Canonical SSE Rules The Parent Loop Must Preserve

These rules are already source-ready and should be treated as invariant during the Lovable loop.

### 4.1 Streams and endpoints

| Stream | Endpoint | Required note |
|---|---|---|
| Runtime events | `GET /api/v1/runtime/{runtime_id}/events/stream` | BFF does not yet filter server-side by `runtime_id`; client must filter on `event.data.runtime_id` |
| Incident events | `GET /api/v1/incidents/stream` | supports `last_event_id` replay |
| Kill-switch updates | `GET /api/v1/kill-switch/updates` | supports `last_event_id` replay |

### 4.2 Reconnect and replay invariants

| Rule | Required behavior |
|---|---|
| Initial read authority | SSE must not be the first data source; the host screen fetches the composed BFF view first, then applies SSE events incrementally |
| Reconnect backoff | exponential backoff starts at 1 second, doubles, caps at 30 seconds, with jitter |
| Replay cursor | the client stores the last applied `event.id` and reconnects with `?last_event_id=...` |
| Idempotency | replayed or duplicate events must be skipped by `event.id` |
| Heartbeat | `: heartbeat` comments every 30 seconds are ignored and never treated as data events |
| Pre-hydration race | events arriving before the initial composed view is ready must be buffered, then flushed after hydration |

### 4.3 Screen integration constraints

| Area | Constraint |
|---|---|
| Shared client ownership | no component file may create raw `EventSource`; all stream wiring goes through the shared SSE client layer |
| Network ownership | no raw `fetch` or ad hoc client calls in component files; use the existing BFF client only |
| Incident action gating | `kill_switch_activated` disables runtime action buttons immediately on incident surfaces |
| Banner authority boundary | SSE payloads do not own degradation banner state; banner state remains sourced from the latest full BFF `meta` snapshot |
| Delayed realtime note | if no SSE event arrives for 60 seconds where events are expected, show a footer note only; do not escalate this into banner state by itself |
| Missing-field handling | any missing required top-level or `data` field must emit `bff-gap` rather than guessing or silently dropping the defect |

---

## 5. Reviewer-Facing Acceptance Surface

### 5.1 What the reviewer should confirm now

| Check | Expected result |
|---|---|
| Parent acceptance criterion 1 is correctly marked as not yet met | true; no returned `ui-done`, gap handoff, or feedback bundle is present yet |
| Parent acceptance criterion 2 is correctly marked as source-ready | true; the four PKT-005 SSE support docs agree on replay, reconnect, heartbeat, and reconciliation rules |
| The dependency map does not invent extra blockers | true; only `BP5-SVC-016` is the formal upstream dependency in task state |
| This sidecar remains support-only | true; no canonical docs or runtime implementation are edited by this slice |

### 5.2 What should trigger parent-task reopen or blocker status later

| Condition | Required parent-loop response |
|---|---|
| frontend loop returns missing required event fields | emit `.coordination/requests/PKT-005-sse-substrate-bff-gap.yaml` and keep parent open |
| frontend loop can only implement partial SSE wiring | return explicit follow-up or blocker note instead of silently closing |
| returned implementation derives banner state from SSE payloads | reject or reopen; this violates the published boundary |
| returned implementation adds raw `EventSource` or raw fetch calls in component files | reject or reopen; this violates the shared-substrate contract |
| returned implementation omits reconnect replay or idempotent dedupe | reject or reopen; parent acceptance criterion 2 is no longer preserved |

---

## 6. Sidecar Scope Declaration

- No canonical L1 or L2 document was modified by this sidecar
- No runtime, BFF, registry, or governance implementation was modified by this sidecar
- No parent task artifact was edited by this sidecar
- The only artifact produced by this slice is this acceptance packet
- Parent execution, review approval, and closeout remain owned by the `BP5-LUV-010` parent task lifecycle

---

## 7. Reviewer Handoff Notes

**Reviewer:** `Claude`

**What to verify**

1. Confirm the parent acceptance checklist in §2 correctly distinguishes current loop state from source-ready contract state.
2. Confirm the dependency map in §3 matches the task brief and active `ai-status.json`.
3. Confirm the invariant SSE rules in §4 match the current PKT-005 screen spec, BFF contract, frontend change spec, and example payload.
4. Confirm the packet stays support-only and does not rewrite parent-task truth.

**If approved**

Use:

```bash
AI_NAME=Claude python3 scripts/ai_status.py approve BP5-LUV-010-SIDECAR-ACCEPTANCE "Acceptance packet approved; BP5-LUV-010 dependency map, current loop state, and invariant SSE substrate rules are accurately packaged for the parent Lovable loop."
```

**If changes are required**

Use:

```bash
AI_NAME=Claude python3 scripts/ai_status.py reopen BP5-LUV-010-SIDECAR-ACCEPTANCE "Describe the specific acceptance-packet corrections needed."
```
