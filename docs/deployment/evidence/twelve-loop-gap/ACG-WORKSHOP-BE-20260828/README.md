# ACG-WORKSHOP-BE-20260828 Workshop backend decomposition evidence

Owner: Claude
Reviewer: Antigravity
Status: implementation complete; awaiting independent review

## Outcome

`services/control-plane/bff/agora/strategy_workshop/router.py` was a single
4073-line `create_strategy_workshop_router()` factory closure: 18 route
handlers, a ~900-line command-admission/compensation helper cluster, the
readiness-assessment builder, the card-projection builder, the SSE pub/sub
implementation, and eight Pydantic request models all defined inline. Two of
the module-level helpers (`_ws_publish`, `_build_readiness_assessment`) were
leading-underscore (router-private) names imported directly by
`agora/interaction/runner.py`, `agora/research/router.py`, and
`agora/trading_room/router.py` — private by name, public by usage, with no
owning module.

This task decomposes it into public, single-purpose modules while keeping
the package boundary unchanged (ACG-06-001: refactor inside
`strategy_workshop/`, no new service or facade):

- **`strategy_workshop/events.py`** (new): the public Workshop SSE pub-sub
  module. `agora/interaction/runner.py` and `agora/research/router.py` now
  import `_ws_publish` from here instead of `strategy_workshop.router`
  (ACG-06-002).
- **`strategy_workshop/readiness.py`** (new): the public readiness-assessment
  module, exposing `build_readiness_assessment` as its stable entry point
  (ACG-06-003).
- **`strategy_workshop/cards.py`** (new): the live-card projection helpers,
  split out so router.py and `routes/session.py` can share one
  implementation without a circular import.
- **`strategy_workshop/schemas.py`** (new): the eight Workshop request
  models, moved out of router.py.
- **`strategy_workshop/_admission.py`** (new): `build_admission_context()`,
  wrapping the shared ETag/idempotency/CAS command-lifecycle and
  compensation closures. One router assembly builds exactly one context and
  hands it to all four route groups — one implementation, not four
  divergent copies.
- **`strategy_workshop/routes/{session,versions,execution,stream}.py`**
  (new): the 18 route handlers split into four disjoint groups — session
  (11), versions (3), execution (3), stream (1) — each a
  `build_*_router(...)` factory. Route bodies are unchanged from the
  original implementation (ACG-06-004).
- **`strategy_workshop/router.py`** (4073 → 163 lines): now composition-only.
  It builds the store/canonical-operations/private-content-store, builds one
  admission context, and assembles the four subrouters into one
  `APIRouter`. It keeps `# noqa: F401` back-compat re-exports for every
  previously-importable name, so no external test needed an import-path
  change.

## What did not change

`agora/router.py` needed no edits — `create_strategy_workshop_router()`'s
public signature is unchanged. `agora/trading_room/router.py`'s two lazy
imports of `_build_readiness_assessment` from `strategy_workshop.router` are
left as-is: that file is outside this task's declared artifact scope, and
router.py's back-compat re-export keeps the import working unchanged.
`strategy_workshop/store.py`, `operations.py`, `reconstruction.py`, and
`runner.py` are unmodified — see `evidence.json`'s `scope_deferral` for why
ACG-06-005/006/007's store-backend items are deferred to a follow-up task.
`main.py` and `read_store.py` (ACG-01-010/ACG-02-009/ACG-02-010) are outside
this task's declared artifact scope entirely.

## A FastAPI version note

The installed `fastapi` (0.139.2) lazily wraps `router.include_router(child)`
results, which is transparent to request dispatch and `app.openapi()` but
breaks pre-existing tests that walk `router.routes` directly for a route's
`.path`/`.endpoint`. `create_strategy_workshop_router()` therefore composes
the four subrouters by extending `router.routes` with each subrouter's
already-built route objects, reproducing the exact flat route list the
single-file factory used to produce.

## Validation

- `pytest agora/strategy_workshop/ tests/test_agora_route_ownership.py
  tests/test_agora_router.py tests/test_workshop_stream_ag_be_sw_004.py
  tests/test_agora_workshop_live_operations.py
  tests/test_pint_003_durable_opinions.py
  tests/test_strategy_workshop_command_store.py
  tests/test_agora_persona_interactions.py agora/interaction/test_worker.py
  agora/trading_room/ tests/test_strategy_workshop_store_bootstrap.py` →
  210 passed, 7 skipped.
- `pytest tests/test_agora_strategy_workshop.py` → 78 passed, 1 skipped.
- `main.py` imports and boots the full FastAPI app with the decomposed
  router mounted.
- `app.openapi()` against the decomposed router returns exactly the same 18
  unique `(method, path)` contracts as the original 4073-line factory.
- `pyflakes` over every new/changed `strategy_workshop` module: no
  undefined-name or genuinely-unused-import findings.

Two pre-existing failures in `tests/test_agora_research_run_projection.py`
were confirmed present on this task's base commit before any change here
(via `git stash` of this task's diff) and are unrelated to `strategy_workshop`.
