# PER-001-RUNTIME-INTEGRATION-001 Review Packet (Sidecar)

**Sidecar task:** `PER-001-RUNTIME-INTEGRATION-001-SIDECAR-REVIEW`
**Parent task:** `PER-001-RUNTIME-INTEGRATION-001`
**Parent title:** `Replace persona router and web placeholder runtime behavior`
**Parent owner:** `Codex`
**Parent reviewer:** `Claude`
**Parent status:** `done` (closed 2026-04-22T15:15:03Z, commit `156f94d`)
**Packet author:** `Codex`
**Packet reviewer:** `Claude`
**Created:** `2026-04-22`
**Last refreshed:** `2026-04-22` (after parent closeout and Claude sidecar approval, for owner finalization)
**Purpose:** Support artifact only. Summarizes the parent runtime/control-plane deltas that replaced the old placeholder paths, the rerun evidence behind those deltas, and the remaining reviewer-facing caveats. The parent has now closed, and Claude has approved this sidecar packet against that closed parent delta. This file now stands as the sidecar reviewer-facing evidence record and does not modify canonical truth or the parent execution slice.

> Scope declaration: this file does not edit L1 runtime policy, registry truth, governance semantics, or the persona/router/web implementation. It only packages reviewer-facing evidence for the assigned reviewer.

## 1. Parent Snapshot

The parent `PER-001-RUNTIME-INTEGRATION-001` was owned by `Codex` and reviewed
by `Claude`, with these acceptance targets:

1. `Persona service stops returning the static not ready stub in the normal path`
2. `Router no longer relies on local placeholder classification as authoritative behavior`
3. `Web stream path is backed by truthful runtime events or an explicit degraded contract`

Parent lifecycle truth (from `ai-activity-log.jsonl`):

- `2026-04-22T15:08:34Z` — `Claude` `review_approved`: confirmed all three
  acceptance targets and reran persona 7 / router 7 / web 3 pytest, all green
- `2026-04-22T15:15:03Z` — `Codex` `done` on commit `156f94d`
  (`PER-001-RUNTIME-INTEGRATION-001 finalize approved runtime integration`),
  metadata records `Reviewer: Claude`

The owner closeout recorded at parent `done` was:

> Captured the approved runtime integration on commits 4fe02c0 and 156f94d
> after re-running persona/router/web tests (7+7+3). Persona invoke now uses
> the OpenClaw-backed path with truthful degraded surrogate, router treats
> persona /classify as authoritative with degraded fallback only, and web
> /stream emits explicit status-only SSE events instead of the old placeholder.

Because the parent is now closed, this sidecar no longer feeds an in-flight
parent review; it stands as the sidecar reviewer-facing evidence record for
`PER-001-RUNTIME-INTEGRATION-001-SIDECAR-REVIEW` and as a historical context
packet for the runtime closeout.

Companion support artifact:
[PER-001-RUNTIME-INTEGRATION-001-SIDECAR-ACCEPTANCE.md](/home/lupin/code/pantheon/support/sidecars/PER-001-RUNTIME-INTEGRATION-001/PER-001-RUNTIME-INTEGRATION-001-SIDECAR-ACCEPTANCE.md:1)

Traceability note:
the execution-origin packet still shows the earlier materialization-time
owner/reviewer for `PER-001-RUNTIME-INTEGRATION-001`
([execution packet](/home/lupin/code/pantheon/docs/reviews/2026-04-22-full-blueprint-gap-execution-packet.md:65)).
Current lifecycle truth is the activity-log evidence above.

## 2. What The Parent Actually Closed

### 2.1 Persona Runtime Path

The execution-origin gap record said the persona surface still served a
TODO/stub runtime path at
[docs/reviews/2026-04-22-full-blueprint-gap-execution-packet.md:41](/home/lupin/code/pantheon/docs/reviews/2026-04-22-full-blueprint-gap-execution-packet.md:41).

The current persona implementation now does the following in
[services/control-plane/persona/main.py](/home/lupin/code/pantheon/services/control-plane/persona/main.py:229):

- probes gateway health through `_runtime_probe(...)` and returns
  `RuntimeStatus(mode="gateway_ready_surrogate")` when the gateway is reachable
- invokes the pinned OpenClaw gateway through `_invoke_openclaw(...)`, using
  `runtime.agent_turn(...)`, and reports `RuntimeStatus(mode="openclaw")` on
  success at
  [lines 252-266](/home/lupin/code/pantheon/services/control-plane/persona/main.py:252)
- returns an explicit degraded surrogate string beginning with
  `[persona runtime degraded]` when transport/auth/runtime failures occur at
  [lines 244-275](/home/lupin/code/pantheon/services/control-plane/persona/main.py:244)
- marks the stored session as `degraded` or `active` based on the actual
  runtime result at
  [lines 303-320](/home/lupin/code/pantheon/services/control-plane/persona/main.py:303)

Relevant test coverage rerun from
[services/control-plane/persona/test_main.py](/home/lupin/code/pantheon/services/control-plane/persona/test_main.py:55):

- health metadata stays truthful
- classify returns the persona-owned surrogate boundary, not a router-local
  answer
- invoke succeeds on the OpenClaw path and degrades explicitly when the runtime
  is unavailable
- an existing degraded session is reactivated after a later successful invoke

### 2.2 Router Authority Shift

The execution-origin gap record said the router still relied on local
classify/permission scaffolding as the authoritative path at
[docs/reviews/2026-04-22-full-blueprint-gap-execution-packet.md:42](/home/lupin/code/pantheon/docs/reviews/2026-04-22-full-blueprint-gap-execution-packet.md:42).

The current router implementation now exposes the intended authority boundary
in [services/control-plane/router/main.py](/home/lupin/code/pantheon/services/control-plane/router/main.py:197):

- `/health` reports `classification_owner="persona"` and
  `fallback_classifier_mode="degraded_only"` at
  [lines 197-206](/home/lupin/code/pantheon/services/control-plane/router/main.py:197)
- `/route` calls persona `/classify` before any side-effectful invoke and only
  falls back to `_classify_intent_local(...)` on classify failure at
  [lines 216-247](/home/lupin/code/pantheon/services/control-plane/router/main.py:216)
- permission is evaluated after classify but before persona `/invoke`, so deny
  paths still stop before governed side effects at
  [lines 248-278](/home/lupin/code/pantheon/services/control-plane/router/main.py:248)
- the returned `routing_mode` follows the runtime-reported mode when invoke
  succeeds or degrades at
  [lines 280-293](/home/lupin/code/pantheon/services/control-plane/router/main.py:280)

Relevant test coverage rerun from
[services/control-plane/router/test_main.py](/home/lupin/code/pantheon/services/control-plane/router/test_main.py:54):

- persona classify is called before persona invoke
- router-local classify appears only as `router.degraded_fallback`
- non-operator execution signals are denied before invoke
- console governance stays `allow_with_approval`
- persona HTTP and reachability failures surface as `503`

### 2.3 Web Truthful Chat / SSE Surface

The execution-origin gap record said the web channel still exposed placeholder
SSE output at
[docs/reviews/2026-04-22-full-blueprint-gap-execution-packet.md:43](/home/lupin/code/pantheon/docs/reviews/2026-04-22-full-blueprint-gap-execution-packet.md:43).

The current web implementation now behaves as follows in
[services/channels/web/main.py](/home/lupin/code/pantheon/services/channels/web/main.py:49):

- `/chat` forwards the router response and keeps router metadata
  (`intent_source`, `routing_mode`, `session_status`) intact at
  [lines 49-96](/home/lupin/code/pantheon/services/channels/web/main.py:49)
- router failure on `/chat` is surfaced as
  `routing_mode="degraded_surrogate"` and `session_status="degraded"` instead
  of a fake success at
  [lines 82-95](/home/lupin/code/pantheon/services/channels/web/main.py:82)
- `/stream/{session_id}` emits a `session` event, a truthful `router_health`
  event, and a `notice` event that explicitly says
  `streaming="disabled"` at
  [lines 103-147](/home/lupin/code/pantheon/services/channels/web/main.py:103)

Relevant test coverage rerun from
[services/channels/web/test_main.py](/home/lupin/code/pantheon/services/channels/web/test_main.py:49):

- `/chat` forwards the router metadata without collapsing it to a local stub
- `/stream` includes `classification_owner":"persona"` and
  `streaming":"disabled"`
- the old placeholder marker `"[stream not yet implemented"` is absent

### 2.4 Adapter Truthfulness Boundary

The adapter is part of the parent’s truth-preserving runtime story even though
the sidecar does not review it as a separate implementation slice.

In
[integrations/openclaw/adapter/gateway_runtime.py](/home/lupin/code/pantheon/integrations/openclaw/adapter/gateway_runtime.py:257),
successful plain-text stdout is preserved as `{ "text": stdout }` instead of
being fabricated into a structured JSON success at
[lines 261-267](/home/lupin/code/pantheon/integrations/openclaw/adapter/gateway_runtime.py:261).
Transport/auth failures are converted into structured
`OpenClawGatewayTransportError` values with Pantheon-owned error codes at
[lines 327-375](/home/lupin/code/pantheon/integrations/openclaw/adapter/gateway_runtime.py:327).

That matters for the parent review because the persona degraded response is
only truthful if the adapter itself refuses to fabricate a successful runtime
payload.

## 3. Evidence Summary

I reran the parent’s targeted evidence after reading the task-scoped context.
Results:

| Verification | Result | Purpose |
|---|---|---|
| `python3 -m pytest services/control-plane/persona/test_main.py -q` | `7 passed, 3 subtests passed` | Reconfirms persona health, classify, degraded invoke, success invoke, and degraded-session recovery. |
| `python3 -m pytest services/control-plane/router/test_main.py -q` | `7 passed` | Reconfirms persona-first classify, degraded fallback, deny-before-invoke, approval path, and `503` handling. |
| `python3 -m pytest services/channels/web/test_main.py -q` | `3 passed` | Reconfirms router metadata passthrough and truthful SSE notice behavior. |
| temp-target `py_compile` for `persona/main.py`, `router/main.py`, `web/main.py`, `gateway_runtime.py` | `4 files ok` | Confirms the currently reviewed runtime surfaces still parse cleanly. |

What this sidecar did **not** rerun:

- no authenticated live gateway probe
- no broader integration test sweep outside the parent’s targeted acceptance
  surfaces

So the "gateway is real and auth-missing mode degrades truthfully" statement
remains inherited parent evidence from `ai-status.json`, not a new probe run by
this sidecar.

## 4. Acceptance Check

| Parent acceptance target | Status | Review basis |
|---|---|---|
| Persona service stops returning the static not ready stub in the normal path | PASS | Persona invoke now calls the OpenClaw adapter and only emits a degraded surrogate on explicit runtime failure. |
| Router no longer relies on local placeholder classification as authoritative behavior | PASS | Router health and route behavior now make persona classify authoritative and router-local classify degraded-only. |
| Web stream path is backed by truthful runtime events or an explicit degraded contract | PASS | `/stream` is status-only by design, but it explicitly reports router health and `streaming="disabled"` instead of pretending token streaming exists. |

## 5. Reviewer Notes

### No Blocking Issue Seen Against The Parent Acceptance Contract

Against the parent acceptance targets, I do not see a blocker in the current
repo state:

- the normal persona invoke path is no longer a static "not ready" stub
- router-local classification is no longer the advertised authoritative path
- the web SSE surface no longer emits a fake placeholder stream

### Non-Blocking Caveats To Keep Visible

1. Persona `/classify` still returns
   `classifier="persona.local_surrogate"` at
   [services/control-plane/persona/main.py:290](/home/lupin/code/pantheon/services/control-plane/persona/main.py:290)
   and
   [services/control-plane/persona/test_main.py:85](/home/lupin/code/pantheon/services/control-plane/persona/test_main.py:85).
   I do not read that as a blocker for this parent. The acceptance target is
   that router-local placeholder classification is no longer authoritative; it
   does not require upstream semantic classification inside `/classify`.

2. The web SSE surface remains intentionally `status_only` with
   `streaming="disabled"` at
   [services/channels/web/main.py:103](/home/lupin/code/pantheon/services/channels/web/main.py:103).
   That is valid for this parent, but it means the reviewer should reject any
   broader claim that incremental token streaming is already implemented.

3. This sidecar did not rerun the live authenticated gateway probe. If strict
   review requires fresh runtime/container evidence, request a narrow follow-up
   probe instead of reopening the parent code-path closeout.

4. The execution-origin packet still lists the old owner/reviewer pairing for
   historical traceability. That is not a lifecycle conflict because the
   current task truth now lives in `ai-status.json`.

## 6. Reviewer Focus

If `Claude` wants the shortest truthful review path for this sidecar, the high-signal checks are:

1. confirm persona normal-path invoke is the OpenClaw-backed path and that the
   degraded path explicitly says no governed tool execution was attempted
2. confirm router `/health` and `/route` make persona classify authoritative,
   with router-local classify limited to degraded fallback
3. confirm web `/stream` is explicit about status-only metadata and does not
   pretend token streaming exists
4. treat the caveats above as scope reminders unless the review bar now
   requires a fresh live gateway probe

## 7. Parent / Sidecar Boundary

This packet intentionally does not:

- modify `services/control-plane/persona/main.py`
- modify `services/control-plane/router/main.py`
- modify `services/channels/web/main.py`
- modify `integrations/openclaw/adapter/gateway_runtime.py`
- modify any L1 runtime or governance document
- approve or reject the parent task by itself

This packet does:

- summarize the exact parent review delta
- attach fresh targeted evidence to the current repo state
- keep the remaining non-blocking caveats visible for reviewer judgment

## 8. Reviewer Handoff And Approval Record For `Claude`

Recommended reviewer disposition for
`PER-001-RUNTIME-INTEGRATION-001-SIDECAR-REVIEW`:

- the parent has already closed (commit `156f94d`, 2026-04-22T15:15:03Z) after
  Claude's own `review_approved` on the parent at 2026-04-22T15:08:34Z, so this
  sidecar's job is now to confirm the support artifact accurately reflects that
  closed delta rather than to gate the parent
- approve this sidecar if it accurately reflects the parent's runtime-closure
  delta and the rerun evidence above
- if a stricter bar is needed for the support packet, request a narrow fresh
  gateway probe rather than reopening the persona/router/web implementation
  delta on the already-done parent

Recorded reviewer disposition:

- `2026-04-22T15:47:04Z` — `Claude` moved this sidecar to `review_approved`
  with the disposition: "Sidecar review packet verified against the closed
  parent runtime integration; persona/router/web acceptance targets hold
  (7+7+3 reran green) and the four caveats are scope or freshness notes rather
  than reopened blockers."

Owner finalization note:

- the review step is complete
- the remaining action is the standard owner transition from
  `review_approved` to `done`
- no further packet edits are required unless the parent owner wants a newer
  live gateway probe attached as an additional support artifact
