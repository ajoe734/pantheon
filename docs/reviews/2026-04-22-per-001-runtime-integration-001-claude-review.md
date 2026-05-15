# Review: PER-001-RUNTIME-INTEGRATION-001 — Replace persona router and web placeholder runtime behavior

**Reviewer:** Claude
**Date:** 2026-04-22
**Decision:** APPROVED

## Review Basis

The four artifact files
(`services/control-plane/persona/main.py`,
`services/control-plane/router/main.py`,
`services/channels/web/main.py`,
`integrations/openclaw/adapter/gateway_runtime.py`)
remain uncommitted in the working tree of `codex/2026-04-21-exec-sync` at
`HEAD=c45c133`, so the GitHub PR review path keeps skipping. I reviewed the
delta locally with `git diff cf67644 -- <artifact>` against the base
`cf67644 BP5-OSS-002: realize OpenClaw gateway runtime adapter`.

## Acceptance Criteria Verification

### 1. Persona service stops returning the static not ready stub in the normal path — PASS

The prior `respond()` node unconditionally returned
`"[system not ready — upstream schemas not locked]"`. The new
`/invoke` path (`services/control-plane/persona/main.py:387`) calls
`_invoke_openclaw()`, which runs `runtime.agent_turn(...)` on the pinned
OpenClaw gateway and returns the extracted assistant text on success with
`RuntimeStatus(mode="openclaw", gateway_ready=True)`. When the transport
raises `OpenClawGatewayTransportError`, the response is an explicit
`"[persona runtime degraded] intent=<intent>; no governed tool execution
was attempted. <reason>"` surrogate, and the stored session is marked
`degraded`; on a later successful turn the same session is re-activated.
The `/invoke` code path no longer contains a static placeholder branch.

### 2. Router no longer relies on local placeholder classification as authoritative behavior — PASS

`/route` (`services/control-plane/router/main.py:214`) now calls the
persona `/classify` endpoint before any side-effectful invoke and only
falls back to `_classify_intent_local(...)` in the `except` branch with
`intent_source="router.degraded_fallback"` and
`routing_mode="degraded_surrogate"`. The health surface advertises
`classification_owner="persona"` and
`fallback_classifier_mode="degraded_only"`
(`services/control-plane/router/main.py:197`). Permission evaluation is
still placed before the persona `/invoke` call, so the deny-before-invoke
invariant for `execution.signal` and similar high-risk intents still
holds. The returned `routing_mode` follows the runtime-reported mode
from persona when invoke succeeds or degrades.

### 3. Web stream path is backed by truthful runtime events or an explicit degraded contract — PASS

`/stream/{session_id}` (`services/channels/web/main.py:98`) now emits
three truthful SSE events: a `session` event with
`stream_mode="status_only"`, a `router_health` event carrying either the
live router `/health` payload or `status="unavailable"` plus a reason on
failure, and a `notice` event with `streaming="disabled"` and an explicit
reason string. The previous single
`data: [stream not yet implemented ...]` placeholder is gone.
`/chat` also forwards router metadata (`intent_source`, `routing_mode`,
`session_status`) and, on router failure, surfaces
`routing_mode="degraded_surrogate"` and `session_status="degraded"`
instead of a fake success.

## Adapter Truthfulness Boundary

The persona degraded contract only holds if the gateway adapter refuses
to fabricate a successful payload. The updated `agent_turn` in
`integrations/openclaw/adapter/gateway_runtime.py` returns
`{"text": stdout}` when stdout is non-JSON instead of coercing a
structured success, and transport/auth failures raise
`OpenClawGatewayTransportError` with Pantheon-owned error codes
(`UPSTREAM_UNAVAILABLE`, `CONNECTION_REFUSED`, `AUTH_UNAVAILABLE`,
`TIMEOUT`). That preserves the truthfulness surface relied on by
persona's degraded branch.

## Evidence (Rerun)

| Verification | Result |
|---|---|
| `PYTHONPATH=. python3 -m pytest services/control-plane/persona/test_main.py -q` | 7 passed, 3 subtests passed |
| `python3 -m pytest services/control-plane/router/test_main.py -q` (from service dir) | 7 passed |
| `python3 -m pytest services/channels/web/test_main.py -q` (from service dir) | 3 passed |
| `ast.parse` of all four artifact files | 4 files ok |

Note: `py_compile` through the default bytecode cache path fails because
the persona `__pycache__` directory on this workstation is root-owned; I
substituted an `ast.parse` sanity check, which is equivalent for
surface-level syntax verification.

Not rerun: no live authenticated gateway probe. The statement that the
pinned gateway is real and the agent loop degrades truthfully when auth
is missing remains inherited parent evidence from the handoff, not
verified by this review.

## Non-Blocking Reviewer Caveats

1. Persona `/classify` still returns `classifier="persona.local_surrogate"`
   with a keyword-based intent map. The acceptance target is scoped to
   the router-authority shift, not to upgrading persona's classifier
   itself, so this is not a blocker.
2. The web SSE surface is intentionally `status_only` with
   `streaming="disabled"`. This is the "explicit degraded contract" path
   allowed by acceptance #3; it must not be read as evidence that
   incremental token streaming exists.
3. The router's `classify` block uses a broad `except Exception`. This
   is loose but fails closed into the degraded fallback, which is
   truthful; acceptable for this slice.
4. `sessions_overview()` was added to the adapter but is not yet wired
   into any caller. Not blocking; flag for the next integration slice.
5. The four artifact files are uncommitted on
   `codex/2026-04-21-exec-sync` and the branch is not pushed to origin.
   This review is therefore against the working-tree delta, not against
   a persisted commit. The owner should capture these changes as a
   commit and, when appropriate, push the branch before declaring the
   task fully closed.

## Decision

APPROVED. All three acceptance targets are satisfied by the current
working-tree implementation, and the targeted evidence I independently
reran matches the owner's handoff summary. Returning to owner Codex for
finalization.
