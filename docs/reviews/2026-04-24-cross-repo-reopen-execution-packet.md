# 2026-04-24 Cross-Repo Reopen Execution Packet

## Purpose

Materialize the reopened execution lane after the 2026-04-24 dual-repo audit
found that Pantheon still reports several modules as loop-complete while the
current `front-ai-trading-system` default branch continues to mount blocked
shells or retains unfinished follow-up work.

This packet is intentionally about truthful reopen and rebaseline work. It is
not a claim that Pantheon BFF routes are missing.

## Reopened Truth

- `EW-04` and `EW-05` are route-live in Pantheon, but the current front
  default branch still mounts `InspirationGraphBlocked` and
  `MutationReviewBlocked`.
- `CW-01`, `CW-03`, and `CW-04` are route-live in Pantheon, but the current
  front default branch still mounts blocked consultation pages for requests,
  committees, and memos.
- `PKT-001` remains in front-owned closeout because the UI still does not
  validate the required `meta.surfaces` key sets fail-closed.
- `PKT-003` remains in front-owned closeout because replayability,
  `meta.staleness` handling, and host-screen SSE reconciliation are still not
  fully aligned with the accepted contract.
- `EP5-002` remains a separate human-gated proof and is not materialized into
  the normal execution queue.

## Lovable Prompts

If Lovable is needed, use these front-repo-local prompts:

- `../front-ai-trading-system/docs/lovable/2026-04-24-reopened-evolution-consultation-realignment-prompt.md`
- `../front-ai-trading-system/docs/lovable/2026-04-24-pkt001-pkt003-followup-prompt.md`

## Materialized Tasks

| Task ID | Purpose | Owner | Reviewer | Status |
|---|---|---|---|---|
| `APP-003-FRONT-REALIGN-EVOLUTION-001` | Reopen EW-04 / EW-05 against the current front default branch | `Codex2` | `Codex` | `todo` |
| `APP-003-FRONT-REALIGN-CONSULTATION-001` | Reopen CW-01 / CW-03 / CW-04 against the current front default branch | `Claude` | `Codex` | `todo` |
| `APP-003-PKT001-CLOSEOUT-002` | Reopen the remaining PKT-001 front-owned fail-closed validation follow-up | `Codex3` | `Codex` | `todo` |
| `APP-003-PKT003-CLOSEOUT-001` | Reopen the remaining PKT-003 replayability / staleness / SSE follow-up | `Codex2` | `Codex3` | `todo` |
| `APP-003-TRUTH-SYNC-004` | Rebaseline backlog, SA, blueprint, and coordination hygiene against the reopened truth | `Codex` | `Codex3` | `todo` |

## Deferred / Not Materialized

- `EP5-002`
  - Reason: canary / live proof remains human-gated and should not be pushed
    into the normal supervisor execution queue.
