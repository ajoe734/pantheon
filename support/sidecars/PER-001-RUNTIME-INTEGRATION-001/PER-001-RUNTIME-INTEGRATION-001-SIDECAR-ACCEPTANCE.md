# PER-001-RUNTIME-INTEGRATION-001 Acceptance and Dependency Map (Sidecar)

**Parent Task**: `PER-001-RUNTIME-INTEGRATION-001` - Replace persona router and web placeholder runtime behavior
**Parent Owner**: `Codex`
**Parent Reviewer**: `Claude`
**Parent Status**: `done` (archived `2026-04-22T15:15:03Z`, commit `156f94d`)
**Sidecar Task**: `PER-001-RUNTIME-INTEGRATION-001-SIDECAR-ACCEPTANCE`
**Sidecar Owner**: `Codex`
**Sidecar Reviewer**: `Claude`
**Helper Kind**: `acceptance_packet`
**Created**: `2026-04-22`
**Last Refreshed**: `2026-04-23` (post-review-approved lifecycle sync for `Claude` handback; narrow verification unchanged)
**Mutates canonical**: `no`

> This is a support artifact only. It does not modify L1 policy, canonical
> runtime truth, registry/governance behavior, or the parent task's execution
> record. It packages the reviewer-facing acceptance matrix, dependency map,
> and verification summary for `PER-001-RUNTIME-INTEGRATION-001`.

## 1. Executive Summary

`PER-001-RUNTIME-INTEGRATION-001` exists to close the repo-local runtime
placeholder gap identified in the full-blueprint execution packet. The parent
task's job is not to invent a new persona architecture; it is to replace the
obvious stub paths in the persona, router, and web channel services with the
already-ratified OpenClaw-backed runtime path, plus explicit degraded behavior
when the upstream runtime is unavailable.

Current repo evidence shows that the normal-path placeholder behavior has been
replaced at three surfaces:

1. the persona service now probes and invokes the OpenClaw gateway adapter,
   and returns an explicit degraded surrogate response only when the runtime
   transport fails.
2. the router now treats persona classification as the authoritative path and
   keeps the local classifier only as a degraded fallback when persona
   classify is unavailable.
3. the web channel now forwards router metadata on `/chat` and exposes a
   truthful status-only SSE contract on `/stream/{session_id}` instead of a
   fake streaming placeholder.

This sidecar re-verified the current targeted test coverage on `2026-04-23`:

1. `python3 -m pytest services/control-plane/persona/test_main.py -q`
   -> `7 passed, 3 subtests passed`
2. `python3 -m pytest services/control-plane/router/test_main.py -q`
   -> `7 passed`
3. `python3 -m pytest services/channels/web/test_main.py -q`
   -> `3 passed`
4. syntax validation for the touched runtime files succeeded via
   `PYTHONPYCACHEPREFIX="$(mktemp -d)" python3 -m py_compile ...` for 4 files,
   so the check stayed support-only and avoided repo-local `__pycache__`
   writes

The same narrow suite was rerun again during the current
`review_ready_dispatch`, with the same `7 + 7 + 3 + py_compile` outcome, so
this packet remains current without reopening the archived parent task.

Lifecycle note:
the execution-origin packet still lists the earlier materialization-time
owner/reviewer for `PER-001-RUNTIME-INTEGRATION-001`. Current lifecycle truth
comes from the archived parent snapshot at
`ai-task-archive/tasks/PER-001-RUNTIME-INTEGRATION-001.json`, which records
the parent as closed by `Codex` after `Claude` review approval, plus the
active sidecar entry in `ai-status.json`, which records this sidecar as owned
by `Codex`, reviewed by `Claude`, and now in `review_approved` awaiting owner
finalization.

## 2. Source References

| Source | Why it matters |
|---|---|
| `ai-status.json` | Durable truth for the active sidecar owner/reviewer/status and the reviewer-approved return from `Claude` back to the owner for finalization. |
| `ai-task-archive/tasks/PER-001-RUNTIME-INTEGRATION-001.json` | Durable truth for the archived parent acceptance targets, parent closeout timestamp, reviewer notes, and delivery metadata. |
| `.orchestrator/task-briefs/per_001_runtime_integration_001_sidecar_acceptance.md` | Confirms the sidecar is support-only and limited to an acceptance packet. |
| `docs/reviews/2026-04-22-full-blueprint-gap-execution-packet.md` | Execution-origin packet that materialized the repo-local runtime/control-plane closeout gap. |
| `OPENCLAW_RUNTIME_CONTRACT.md` | L1 boundary that keeps OpenClaw as the external runtime substrate and Pantheon as the adapter/policy owner. |
| `PERSONA_RUNTIME_MODEL.md` | L1 boundary that defines persona as registry object + session object + runtime instance, rather than a static stub process. |
| `services/control-plane/persona/main.py` | Parent implementation surface for runtime probe, OpenClaw invocation, and degraded surrogate response. |
| `services/control-plane/router/main.py` | Parent implementation surface for persona-owned classify and degraded-only local fallback. |
| `services/channels/web/main.py` | Parent implementation surface for truthful `/chat` forwarding and status-only SSE events. |
| `integrations/openclaw/adapter/gateway_runtime.py` | Adapter boundary that preserves truthful handling when the gateway returns plain text or transport errors. |
| `services/control-plane/persona/test_main.py` | Verifies persona health, classify, invoke success, degraded behavior, and degraded-session recovery. |
| `services/control-plane/router/test_main.py` | Verifies persona-first classify, degraded fallback, permission gate ordering, and 503 behavior when persona fails. |
| `services/channels/web/test_main.py` | Verifies router metadata passthrough and truthful SSE status events. |
| `docs/02-architecture/consensus/sessions/phase7-2026-04-18-ep4-ep5-execution-proof/planning-session.json` | Accepted phase-7 planning provenance. The session objective is to materialize stable EP4 execution proof from existing evidence, so this sidecar treats `PER-001` as execution closeout support, not a new canonical runtime redesign. |

## 3. Repo-Current Truth Snapshot

| Truth item | Repo evidence | Implication for review |
|---|---|---|
| Parent runtime-integration slice is already closed | `ai-task-archive/tasks/PER-001-RUNTIME-INTEGRATION-001.json` records terminal status `done` at `2026-04-22T15:15:03Z` on commit `156f94d4d3f9c386fd656777072d23c2eea1ef77`. | This sidecar should be reviewed as a support artifact aligned to a closed parent, not as a gate that can silently rewrite parent lifecycle truth. |
| OpenClaw remains the runtime substrate, with Pantheon owning adapter/policy mapping | `OPENCLAW_RUNTIME_CONTRACT.md` says OpenClaw is the external runtime substrate and the adapter owns persona/session/tool mapping. | The parent should be reviewed as a runtime integration slice, not as a rewrite of the canonical runtime contract. |
| Persona is now a session-bound runtime path rather than a static local stub | `services/control-plane/persona/main.py` now probes the gateway, invokes `runtime.agent_turn(...)`, emits `RuntimeStatus(mode=\"openclaw\")` on success, and emits `[persona runtime degraded] ... no governed tool execution was attempted` on transport failure. | The parent closes the old "not ready" stub behavior on the normal invoke path while keeping degraded semantics explicit. |
| Router no longer treats the local classifier as the normal authoritative behavior | `services/control-plane/router/main.py` advertises `classification_owner=\"persona\"`, posts to `/classify` before permission evaluation, and only falls back to `_classify_intent_local(...)` after classify errors with `intent_source=\"router.degraded_fallback\"`. | Approval should focus on whether persona classify is the normal path and local classify is truly degraded-only. |
| Web channel now exposes truthful chat/stream metadata instead of placeholder streaming text | `services/channels/web/main.py` forwards router metadata on `/chat`, marks router failures as `routing_mode=\"degraded_surrogate\"`, and emits `session`, `router_health`, and `notice` SSE events with `streaming=\"disabled\"` rather than fake token streaming. | The parent only needs truthful runtime/availability signaling for the web path; it does not need to implement incremental token streaming. |
| The adapter preserves truthful behavior when OpenClaw returns non-JSON stdout | `integrations/openclaw/adapter/gateway_runtime.py` preserves plain-text stdout as `{ \"text\": stdout }` instead of fabricating a structured success payload. | Reviewer should read degraded/surrogate handling as an explicit truth-preserving design, not as an unfinished placeholder. |
| Current targeted coverage matches the parent closeout summary | Sidecar rerun: persona tests `7 passed, 3 subtests passed`; router tests `7 passed`; web tests `3 passed`; temporary `py_compile` validation succeeded for 4 files. | The reviewer has fresh repo-local evidence that the current acceptance surfaces still hold. |

Inference note:
this sidecar did not rerun a live authenticated gateway probe. The statement in
the archived parent snapshot that the pinned gateway is real and degrades
truthfully when gateway auth is missing remains inherited parent-task evidence,
not a new live probe executed by this sidecar.

## 4. Parent Acceptance Checklist

Use this table to confirm the closed parent
`PER-001-RUNTIME-INTEGRATION-001` still matches the active implementation and
test surfaces.

| Parent acceptance target | Verification | Status now |
|---|---|---|
| Persona service stops returning the static not-ready stub in the normal path | `services/control-plane/persona/main.py` now routes invoke requests through `_invoke_openclaw`, returns `RuntimeStatus(mode=\"openclaw\")` on success, and returns an explicit degraded surrogate only when the OpenClaw transport fails. `services/control-plane/persona/test_main.py` re-verified both success and degraded paths. | PASS |
| Router no longer relies on local placeholder classification as authoritative behavior | `services/control-plane/router/main.py` calls persona `/classify` before permission evaluation and only uses `_classify_intent_local` when classify is unavailable, tagging the result as `router.degraded_fallback`. `services/control-plane/router/test_main.py` re-verified both the persona-authoritative path and degraded fallback path. | PASS |
| Web stream path is backed by truthful runtime events or an explicit degraded contract | `services/channels/web/main.py` emits status-only SSE events (`session`, `router_health`, `notice`) and explicitly reports `streaming=\"disabled\"` instead of placeholder token output. `services/channels/web/test_main.py` re-verified that the stream contains truthful router metadata and no fake placeholder string. | PASS |

## 5. Dependency Map

### 5.1 Upstream Truth Anchors

| Dependency | Where recorded | Status | Relevance |
|---|---|---|---|
| Execution-origin runtime gap record | `docs/reviews/2026-04-22-full-blueprint-gap-execution-packet.md` | COMPLETE | Establishes why this parent exists and keeps the scope on replacing repo-local runtime placeholders. |
| Canonical runtime boundary | `OPENCLAW_RUNTIME_CONTRACT.md` | COMPLETE | Ensures the parent integrates with the OpenClaw substrate through the adapter boundary instead of redefining ownership. |
| Canonical persona runtime model | `PERSONA_RUNTIME_MODEL.md` | COMPLETE | Ensures the parent behavior remains session/runtime-based rather than collapsing back into a static local persona stub. |
| OpenClaw adapter runtime bridge | `integrations/openclaw/adapter/gateway_runtime.py` | COMPLETE | Supplies the actual gateway transport/invocation behavior the persona service depends on. |
| Parent implementation surfaces | `services/control-plane/persona/main.py`, `services/control-plane/router/main.py`, `services/channels/web/main.py` | COMPLETE | These files hold the behavioral closure for the three acceptance targets. |
| Accepted planning provenance | phase-7 `planning-session.json` | COMPLETE | Confirms this execution wave is materialized from accepted planning, not a fresh runtime architecture fork. |

### 5.2 Behavioral Evidence Dependencies

| Evidence | Current state | Relationship to parent task |
|---|---|---|
| `services/control-plane/persona/test_main.py` | Re-verified on 2026-04-23 | Confirms health metadata, classify behavior, degraded invoke behavior, and recovery to active session state. |
| `services/control-plane/router/test_main.py` | Re-verified on 2026-04-23 | Confirms persona-first classify, degraded fallback, permission ordering, and unavailable/HTTP-error handling. |
| `services/channels/web/test_main.py` | Re-verified on 2026-04-23 | Confirms router metadata passthrough and truthful SSE event content. |
| Temporary-target `py_compile` validation | Re-verified on 2026-04-23 via temporary `PYTHONPYCACHEPREFIX` cache dir | Confirms the touched runtime surfaces still parse cleanly without writing repo-local `__pycache__` artifacts. |

### 5.3 Downstream Consumers

| Consumer | Current state | Relationship to parent task |
|---|---|---|
| `Claude` review of this sidecar packet | COMPLETE | Reviewer approved the packet on `2026-04-23`, confirming the support-only boundary and the repo-current acceptance surfaces. |
| Future runtime/control-plane follow-ups | Ongoing | They depend on the repo no longer pretending the persona/router/web path is complete while still serving obvious placeholders. |
| Parent-owner final closeout | COMPLETE | The parent already finalized on `2026-04-22T15:15:03Z`; this sidecar now serves only as reviewer-facing support material. |
| Sidecar-owner final closeout | READY FOR FINALIZATION | The owner can now archive the sidecar as `done` without changing parent execution truth. |

### 5.4 Machine vs. Semantic Dependency Note

`ai-status.json` currently shows no machine-readable `depends_on` for the
parent or the sidecar. The dependency map above is therefore semantic only. It
is a review aid, not a request to mutate task-board dependencies.

## 6. Scope Boundary - What Reviewer Should Reject

| Problematic move | Why it is wrong |
|---|---|
| Treating this parent as successful only if full token streaming is implemented in the web channel | The acceptance target only requires truthful runtime events or an explicit degraded contract. The current status-only SSE surface is intentionally bounded and explicit about `streaming=\"disabled\"`. |
| Rejecting the parent because degraded behavior still exists | Degraded behavior is required when the upstream runtime is unavailable. The failure mode is acceptable if it is explicit, truthful, and avoids fabricating governed execution. |
| Treating `classifier=\"persona.local_surrogate\"` in the persona classify response as proof that the router still relies on its own placeholder classifier | The authoritative boundary moved to the persona service. Router-local classification is now degraded-only, which is the relevant acceptance condition. |
| Using this sidecar or the parent to reinterpret L1 runtime ownership, binding semantics, or governance truth | `OPENCLAW_RUNTIME_CONTRACT.md` and `PERSONA_RUNTIME_MODEL.md` remain the canonical truth. This slice is implementation closeout against that truth, not a contract rewrite. |
| Expanding review into capital binding, deployment authority, or live execution semantics | The execution packet scoped `PER-001` to repo-local persona/router/web runtime behavior only. |

## 7. Suggested Reviewer Checks

| Check | Status | Evidence |
|---|---|---|
| Support artifact only | PASS | This sidecar adds only `support/sidecars/PER-001-RUNTIME-INTEGRATION-001/PER-001-RUNTIME-INTEGRATION-001-SIDECAR-ACCEPTANCE.md`. |
| No canonical runtime/policy edits by sidecar | PASS | No L1 docs, runtime code, registry files, or governance files were modified here. |
| Parent acceptance targets mapped to active implementation + tests | PASS | Sections 3 and 4 tie each acceptance target to the current code path and rerun tests. |
| Degraded semantics are distinguished from fake-placeholder behavior | PASS | Sections 3, 4, and 6 make explicit that truthful degradation is acceptable while silent stub behavior is not. |

## 8. Review Closeout and Owner Finalization

This sidecar was reviewed and approved by `Claude` on `2026-04-23` as the
acceptance packet aligned to the already-closed parent
`PER-001-RUNTIME-INTEGRATION-001`.

What remains true at finalization:

1. the packet still gives a direct acceptance matrix against the parent's
   three runtime-closure targets
2. the fresh `2026-04-23` targeted verification results for persona, router,
   web, and syntax validation remain the bounded evidence set for this sidecar
3. the lifecycle boundary still keeps this artifact tied to the archived
   parent closeout rather than reopening the parent runtime slice

Owner finalization stance:

1. archive this sidecar as `done` without reopening the archived parent task
   or any canonical runtime truth
2. preserve the reviewer boundary that treats explicit degraded behavior as
   acceptable and fake placeholder behavior as the real regression
3. keep any later runtime/control-plane changes in separate follow-up slices
   rather than expanding this support packet

---
*Generated by Codex as a sidecar `acceptance_packet` helper for
`PER-001-RUNTIME-INTEGRATION-001`. This file is a support artifact and does
not modify canonical truth.*
