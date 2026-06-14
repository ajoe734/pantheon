# Round 9 — Results

**Executed:** 2026-06-14 (UTC). **Method:** in-process audit of the BFF
`app.routes` table (branched off `origin/dev`, which includes the Round 2 fix).

## H1 — static-vs-param shadowing: PASS

Zero static routes are shadowed by an earlier `{param}` route. This confirms
`incidents/stream` (Round 2) was the **only** instance of that bug class — the
generalized sweep finds no others.

## H3 — duplicate operationIds: PASS

Zero duplicate operationIds in the generated OpenAPI.

## H2 — duplicate (method, path) registrations: PASS (with F9)

Four routes have more than one registered handler. In **every** case the
first-match winner is the dedicated/intended handler; the dead registrations are
generic aliases/stubs:

| Route | Winner (served) | Dead (shadowed) |
|---|---|---|
| `GET /bff/events/stream` | `stream_bff_events` | `bff_events_stream_alias`, `sem_final_generic_read_alias` |
| `GET /bff/management/persona-fleet` | `bff_management_persona_fleet` | `bff_management_fleet` |
| `GET /bff/management/persona-league` | `bff_management_persona_league` | `bff_persona_league` |
| `POST /bff/ranking-formulas` | `bff_create_ranking_formula_facade` | `sem_final_generic_create_alias` |

### F9 — duplicate registrations are a latent hazard (currently benign)

Live behavior is correct today (the dedicated handler wins, verified in earlier
rounds: `/bff/events/stream` serves SSE; the others return 200). But the served
handler depends purely on registration order — a future reorder (or a new
generic-alias decorator registered earlier) could silently let a stub shadow the
real handler, regressing the endpoint with no test failure.

## Fix (F9 — guard test, no behavior change)

Added `services/control-plane/bff/test_route_resolution_no_shadowing.py`
(2 passed):
- `test_duplicate_routes_resolve_to_intended_handler` — locks the intended
  winner for all four duplicated routes.
- `test_no_static_route_shadowed_by_earlier_param_route` — fails if any static
  route is shadowed by an earlier `{param}` route (regression guard for the
  Round 2 bug class, now covering the whole surface).

No handler code changed: the duplicates are dead aliases whose removal is
hygiene best left to the owning teams; the guard test prevents the hazard from
becoming an active bug.

## Net

H1/H3 **PASS**, H2 **PASS** (all duplicates resolve correctly). The whole BFF
route surface resolves to intended handlers; the latent duplicate-registration
hazard (F9) is now locked by a regression guard.
