# MGMT-OPS-003 GAP-001 Focus Pagination BFF Handoff

| Field | Value |
|---|---|
| Parent task | `MGMT-OPS-003-GAP-001-FOCUS-PAGINATION-FIX` |
| Parent owner / reviewer | `Codex` / `Antigravity` |
| Sidecar task | `MGMT-OPS-003-GAP-001-FOCUS-PAGINATION-FIX-SIDECAR-BFF-HANDOFF` |
| Sidecar owner / reviewer | `Codex2` / `Codex` |
| Helper kind | `bff_handoff_packet` |
| Generated | `2026-07-12` |
| Mutates canonical | `false` |

This support-only packet records the existing BFF query boundary and a
frontend composition checklist. It does not change the BFF contract,
`execute-plans`, canonical truth, or the parent task disposition.

## 1. Existing BFF Contract

`GET /bff/management/persona-fleet` already accepts the controls required for
the focused-list journey:

| Query | Existing behavior | Frontend use |
|---|---|---|
| `q` | Optional search/focus input | Send the URL `persona_id` value when a linked journey requests one persona. |
| `page_size` | Integer, default `20`, minimum `1`, maximum `200` | Send an explicit sufficient bounded size for an unfocused Fleet request. |
| `page_token` | Optional continuation token | Preserve normal BFF-authored pagination; do not combine a stale token with a newly focused query. |
| `state`, `health`, `deployment_stage`, `market_scope` | Optional production filters | Preserve operator-selected filters unless the product explicitly resets them. |

The gap is frontend query propagation and request-state handling, not a missing
BFF parameter. The parent should not introduce a client-only identity join or
request a new BFF route merely to find the focused persona.

## 2. Frontend Handoff

- Parse `persona_id` as linked-page focus context, but issue the live BFF
  request with `q=<persona_id>`.
- Clear a prior `page_token` when focus or filters change so an old page cursor
  cannot hide the requested row.
- For an unfocused Fleet request, send an explicit bounded `page_size` large
  enough for the intended production view; do not rely on the BFF default 20.
- Keep `q`, `page_size`, and filter translation in the centralized Management
  BFF adapter/path builder rather than assembling query strings in the page.
- Preserve BFF health, production filtering, and unavailable/degraded states.
  A focused empty result is an honest unresolved focus, not permission to show
  mock or stale fallback data.
- Match the returned row by its BFF-authored persona identity. Do not infer a
  match from display name, list position, runtime, or timestamp.
- Keep semantic links derived from the returned row visible after repeated
  focused reloads; never synthesize missing link identifiers.

## 3. Operator Journey

1. An operator follows a BFF-authored Persona Fleet link containing
   `persona_id` from Portfolio Book or another Management surface.
2. Persona Fleet parses the focus and requests the existing live endpoint with
   `q` plus the applicable production filters and bounded `page_size`.
3. The focused row renders even when that persona would have fallen outside
   the default first 20 records.
4. The operator follows the row's BFF-authored semantic links and returns to
   the same focused Fleet context.
5. Repeated reloads repeat the focused live query and retain the exact row and
   links. An unavailable response or exact-row miss remains visibly unresolved.
6. When focus is removed, Fleet returns to an explicit bounded unfocused
   request and normal BFF pagination.

## 4. Acceptance And Regression Matrix

| Case | Required assertion |
|---|---|
| Focus outside default page | Request includes `q=<persona_id>` and the exact row renders. |
| Focus/filter change | Stale `page_token` is absent from the next request. |
| Unfocused load | Request includes the chosen explicit `page_size` and preserves production filters. |
| Repeated focused reload | Every live request retains `q`; exact identity and semantic links remain stable. |
| Exact focus absent | UI shows unresolved/empty focus without selecting another row or fallback data. |
| BFF unavailable/degraded | UI preserves source health and does not claim a successful focus. |
| URL encoding | Reserved characters in focus/filter values are encoded once by the path builder. |
| Hosted workflow | Browser request evidence shows `q` and `page_size`; linked-page desktop/mobile flow passes against live BFF. |

Unit coverage should exercise the Management path/query builder and Persona
Fleet request behavior. Hosted coverage should inspect the actual browser BFF
request, not only the rendered label.

## 5. Compose Boundary

- Parent owner `Codex` decides whether and how to absorb this checklist into
  the correct-base `execute-plans` implementation.
- The parent branch noted in status was created from the wrong frontend base
  and must not be merged; this packet does not rehabilitate that diff.
- A separately delivered workflow slice provides useful precedent:
  `docs/deployment/evidence/mgmt-ops-003-gap/gap-003/20260711T235934Z/README.md`
  records an existing `persona_id -> q` implementation, live OpenAPI proof,
  desktop/mobile hosted success, and deployed frontend merge
  `a74e58696c900112557b0c748c3f8c69629da106`. The parent must still verify its
  own correct-base scope and acceptance; this evidence is not parent closure.
- No Pantheon BFF/runtime/schema change is requested by this packet. If future
  acceptance requires lookup semantics beyond the existing bounded `q` search,
  route that as a separately owned BFF contract task instead of silently
  changing this sidecar.
- Sidecar reviewer `Codex` reviews only this support artifact. Parent reviewer
  `Antigravity` evaluates the composed parent delivery.

## 6. Verification Notes

Source inspection confirmed the route signature in
`services/control-plane/bff/main.py`: `q`, `page_token`, and `page_size` are
present, with `page_size` bounded to 1 through 200 and defaulting to 20. The
hosted evidence named above confirms the dev OpenAPI exposes `q` and
`page_size` and records a previously deployed focused workflow. No canonical
truth, BFF implementation, frontend source, runtime, registry, or governance
file was changed. `current-work.md` and the full `ai-activity-log.jsonl` were
not scanned.
