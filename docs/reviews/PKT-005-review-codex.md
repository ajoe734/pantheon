# Review Report: PKT-005

**Task ID**: PKT-005  
**Artifact**: `PKT-005-degradation-banner-sse-packet-family` packet set  
**Reviewer**: Codex  
**Date**: 2026-04-14  
**Status**: Changes requested

## Findings

### 1. Blocking: one residual banner rule still tells Lovable that SSE can update banner state

The main SSE packet text now correctly says degradation-banner state is backend-owned and must only change when a fresh BFF `meta` snapshot is received. But the banner screen spec still says the banner updates on every composed-view refresh, including an "SSE snapshot event."

- The residual banner rule still says: "Banner state is updated on every composed view response refresh (poll or SSE snapshot event)." (`docs/screens/PKT-005-degradation-banner.md:74-80`)
- The SSE substrate now says the opposite: SSE events do not carry `meta` snapshots and must not be used to re-derive or update the degradation banner; a significant event should trigger a fresh full fetch instead. (`docs/screens/PKT-005-sse-substrate.md:70-75`)
- The packet-family inheritance rule also now says screens must not re-derive banner state from SSE payloads and that the banner reflects the most recently received `meta` from a full BFF read. (`docs/02-architecture/consensus/sessions/phase3-2026-04-14-pantheon-console-loop/PKT-005-degradation-banner-sse-packet-family.md:95-99`)

Why this blocks approval:
This is still a direct normative conflict inside the packet set. Lovable now has two incompatible instructions for the same cross-cutting primitive: one sentence still implies SSE can refresh banner state, while the SSE substrate and packet-family rules forbid that.

## Resolved Since Last Review

1. Split-read aggregation for `PKT-002` Incident Home is now defined in the BFF contract and referenced from the screen spec.
2. Per-surface `meta.surfaces.*.status` is now aligned to `ok | degraded | unavailable`; `stale` and `partial` are derived banner variants, not surface enums.
3. The main SSE substrate and packet-family inheritance rule now consistently state that SSE payloads do not own degradation-banner state.

## Recommendation

Do not approve `PKT-005` yet.

The next revision should remove or rewrite the remaining `docs/screens/PKT-005-degradation-banner.md:80` sentence so it matches the backend-authority rule already adopted everywhere else in PKT-005. A correct replacement would say the banner updates only when the screen receives a fresh BFF `meta` snapshot, whether from initial load, polling, or an explicit full refetch triggered after an SSE-significant event.

## Re-review approval (2026-04-14)

The residual backend-authority conflict is now resolved across the packet set:

- the banner screen spec now says banner state only updates when the screen receives a fresh BFF `meta` snapshot from initial load, polling, or an explicit full refetch after a significant SSE event; it explicitly forbids using SSE payloads to update banner state directly (`docs/screens/PKT-005-degradation-banner.md:74-80`)
- the SSE substrate keeps the same rule: SSE is incremental UI reconciliation only, while degradation-banner state remains owned by the most recent full BFF read (`docs/screens/PKT-005-sse-substrate.md:70-75`)
- the packet-family inheritance rule still repeats that screens must not re-derive banner state from SSE payloads, so downstream packets inherit one consistent contract instead of two incompatible ones (`docs/02-architecture/consensus/sessions/phase3-2026-04-14-pantheon-console-loop/PKT-005-degradation-banner-sse-packet-family.md:93-99`)
- the downstream handoff bundle also stays aligned: the Lovable UI task and contract-ready response both constrain banner state to the current composed-view response rather than any SSE-owned state (`.coordination/responses/PKT-005-degradation-banner-lovable-ui-task.yaml:13-20`, `.coordination/responses/PKT-005-degradation-banner-contract-ready.yaml:23-30`)

No blocking review findings remain.

### Approval recommendation

`PKT-005` is approved and can move to `review_approved`.
