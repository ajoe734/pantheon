# Round 30 — Results

**Executed:** 2026-06-15 (UTC). **Method:** AST scan of 635 non-test files.

## H1 — mutable default arguments: PASS

**Zero** functions use a mutable literal default (`[]`, `{}`, `set()`). The
codebase consistently uses `None` + in-body init, or FastAPI's
`Body(default_factory=...)` — no shared-mutable-default state-leak footgun.

## H2 — `assert` in production: PASS (with note)

21 `assert` statements in non-test code, but:

- 17 are in **test-like scripts** (`run_smoke_logic.py`, `reproduce_sse_gap.py`,
  `e2e_fixtures.py`) — smoke/repro/fixture helpers, not request paths.
- 4 are in `services/evolution/main.py` (lines 663–666):
  `assert cooldown_start/cooldown_end/observation_start/observation_end is not
  None`. These are **internal invariants** (post-computation sanity), **not**
  security/input validation, and the service runs `uvicorn …main:app` **without
  `-O`** (asserts active). No security impact even if `-O` were enabled.

**Observation (O7, not changed):** for runtime invariants in a service handler,
explicit `if x is None: raise` is more robust than `assert` (survives `-O`).
Low-priority hardening for the evolution owner; not force-edited (no current
`-O`, not security-relevant).

## Net

H1/H2 **PASS** — no mutable-default footgun, and the only production asserts are
benign non-`-O` internal invariants. No defect.
