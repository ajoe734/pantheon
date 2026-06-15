# Pantheon Management Console — BFF Gap & Execution Tasks (2026-06-15)

Source: management-console audit (`docs/04-frontend/management-console-inventory-2026-06-15.md`
+ execute-plans PR #36). Live probe basis: `https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io`
with dev stub auth (`Authorization: Bearer op-dev:admin:mfa`).

## Scope

This gap covers **management-console pages whose BFF endpoint returns 404** — i.e. the
FE page is fully built but there is no backend at all. Making them real requires a BFF
endpoint (this doc) and, separately, FE wiring (a `paths.ts` entry + client read in the
execute-plans repo — tracked as FE follow-up, not in these pantheon tasks).

**Out of scope (NOT a BFF gap):** pages whose endpoint exists but returns empty/degraded
(`strategies`, `approvals`, `interventions`, `sentinel`, `jobs`, `rebalance`, `evolution`,
`experiments`, `readiness/*`, …). Those are empty because of the dev *build* gap (no real
strategy artifact / no signal producer / no market data) and are covered by the existing
DEVLOOP tasks — not here.

## Confirmed missing endpoints (live 404, verified against `services/control-plane/bff/main.py`)

| # | FE page (execute-plans) | Missing BFF endpoint(s) | Priority |
|---|---|---|---|
| 1 | `/management/data-sources` (DataSourceManagement) | `GET /bff/management/data-sources` | **P0** |
| 2 | `/management/alpha-factory` (AlphaFactoryBoard) | `GET /bff/alpha-factory` | P1 |
| 3 | `/management/governance/{permissions,memory,consult,policies}` | `GET /bff/management/permissions`, `GET /bff/management/memory-governance`, `GET /bff/management/consult-rules`, `GET /bff/route-policies` | P1 |
| 4 | `/management/lineage` (LineageExplorer) | `GET /bff/lineage` | P1 |
| 5 | `/management/knowledge` (KnowledgeInbox) | `GET /bff/knowledge` | P2 |
| 6 | `/management/workflows`, `/management/hooks` | `GET /bff/workflows`, `GET /bff/hooks` | P2 |
| 7 | `/management/studios/{formula,skill-sandbox}` | `POST /bff/studios/formula-backtest`, `POST /bff/studios/skill-sandbox/run` | **P3 (deferred)** |

P0–P2 are read endpoints with a known list/detail shape → dispatched as execution tasks
below. P3 (studios) needs a real backtest/skill-execution engine (a much larger lift than a
read endpoint); it is **documented but not auto-dispatched** — the two FE tools stay on mock
until a dedicated design lands.

## Contract rules (apply to every endpoint)

- Return the canonical BFF list envelope used elsewhere in `main.py`:
  `{ "data": [...], "items": [...], "page_info": {"next_page_token": null, "total": N},
  "meta": {"snapshot_at": <iso>, "surfaces": {...}, "total": N} }`. When the underlying store
  is empty, return the explicit degradation envelope (`status: unavailable`, `source: missing`)
  rather than a bare `[]` — match the pattern already used by `/bff/strategies`.
- Respect existing auth/CORS middleware (operator read; same guard as sibling management routes).
- Add the path to `BFF_API_CONTRACT.md` and the OpenAPI surface, and add a contract test under
  `services/control-plane/bff/tests/` (mirror `test_bff_management_cockpit.py`).
- **Merge-conflict guard:** `main.py` is ~47k lines. Prefer implementing each endpoint in a new
  module (e.g. `services/control-plane/bff/console_gap/<feature>.py` exposing an `APIRouter`)
  and wiring it with a single `include_router(...)` line, to keep the `main.py` contention to
  one line per task.

## Execution tasks (EPIC BFFGAP-CONSOLE)

Dispatched via `scripts/dispatch_bff_console_gap_2026-06-15.py` into the live orchestrator
(sprint `2026-06-09-mpos-full-loop-gap-closure`, wave `2026-W25`). All `depends_on=[]`
(independent, ready-now).

| Task ID | Endpoint(s) | Owner | Reviewer |
|---|---|---|---|
| `BFFGAP-DATASOURCES` | `GET /bff/management/data-sources` | Claude | Claude2 |
| `BFFGAP-ALPHAFACTORY` | `GET /bff/alpha-factory` | Claude2 | Claude |
| `BFFGAP-GOVRULES` | permissions + memory-governance + consult-rules + route-policies | Claude | Codex |
| `BFFGAP-LINEAGE` | `GET /bff/lineage` | Claude2 | Codex |
| `BFFGAP-KNOWLEDGE` | `GET /bff/knowledge` | Codex | Claude |
| `BFFGAP-WORKFLOWS-HOOKS` | `GET /bff/workflows` + `GET /bff/hooks` | Codex | Claude2 |

> Note: `Codex2` and `Antigravity` are disabled in the live supervisor `ready_dispatcher`,
> so owners/reviewers were kept on enabled agents (Claude / Claude2 / Codex). Owner load
> stays within caps (Claude 2/5, Claude2 2/3, Codex 2/4).

### Acceptance (per task)
1. Endpoint(s) return 200 with the canonical envelope (real store when present; explicit
   degraded envelope when empty) under dev stub auth.
2. Contract entry + OpenAPI surface updated; contract test added and green.
3. `python3 -m pytest services/control-plane/bff/tests/<new_test>.py` passes.
4. Live probe after deploy: `curl -s -H "Authorization: Bearer op-dev:admin:mfa" \
   https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io<path>` returns 200 (not 404).

## FE follow-up (separate, execute-plans repo — not in these tasks)
For each new endpoint, add a `paths.ts` builder + a client read in `src/lib/bff/client.ts`
and point the page at it (replacing its mock seed). Track on the execute-plans side.
