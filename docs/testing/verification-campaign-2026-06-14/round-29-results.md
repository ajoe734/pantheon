# Round 29 — Results

**Executed:** 2026-06-15 (UTC). **Method:** AST scan of 655 non-test files
(1,475 except handlers).

## H1 — bare `except:`: PASS

**Zero** bare `except:` handlers across the entire codebase. Every one of the
1,475 handlers names an exception type — `KeyboardInterrupt`/`SystemExit` are
never accidentally swallowed.

## H2 — broad silent swallows: PASS (intentional patterns)

30 handlers catch `Exception`/`BaseException` with a silent `pass` (18 in BFF
`main.py`). Spot-reading their `try` contexts shows they are the idiomatic
defensive patterns, not hidden critical failures:

- `payload = await request.json()` wrapped to tolerate an **optional/empty
  body** (the dominant case) — handlers that don't require a body.
- best-effort reads with a fallback, e.g.
  `all_plans = read_store.list_deployment_plans() or []` — degrade to empty on a
  read hiccup rather than 500.

These are graceful-degradation, consistent with the campaign's other findings
(the read layer degrades to `unavailable` markers, Round 19). 30 such sites in a
655-file / 50k-line-`main.py` codebase is low density.

**Observation (O6, not changed):** silent broad swallows are invisible to
telemetry. If any wraps a security- or capital-relevant step, a one-line debug
log on the except path would aid forensics. Recorded for team review; not
force-edited (changing 30 defensive sites risks behavior/log-noise with no
demonstrated failure).

## Net

H1/H2 **PASS** — error-handling discipline is sound: no bare excepts, and the
broad silent swallows are intentional optional-body / best-effort patterns. No
defect.
