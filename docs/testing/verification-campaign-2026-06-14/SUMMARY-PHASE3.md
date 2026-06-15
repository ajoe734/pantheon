# Verification Campaign 2026-06-14 — Phase 3 Summary (rounds 21–35)

Phase 3 broadened beyond the operator BFF to the **whole fleet**, the **edge**,
and **systemic bug classes**, and went deep on security hardening and
datetime-correctness.

## Coverage map

| # | Theme | Verdict |
|---|---|---|
| 21 | Fleet route-resolution audit (21 services) | PASS |
| 22 | Non-BFF fleet input 500-hunt (21 services) | PASS |
| 23 | CORS configuration correctness | PASS |
| 24 | BFF security response headers | **fix F14** |
| 25 | Edge security headers (Caddy FE/BFF) | **fix F15** |
| 26 | Canonical state cross-consistency | PASS |
| 27 | Request body-size limit (DoS) | **fix F16** |
| 28 | Complete fleet audit (5 deferred services) | PASS |
| 29 | Error-handling discipline (bare/broad except) | PASS |
| 30 | Python footguns (mutable defaults, asserts) | PASS |
| 31 | Naive/aware datetime mixing | **fix F17** |
| 32 | Generalize F17 across read_store | **fix F18 (20 sites)** |
| 33 | Fleet-wide aware/naive sort audit | **fix F19** |
| 34 | ZeroDivisionError audit | PASS |
| 35 | Consolidation + summary | PASS |

## Defects found & fixed (via dev workflow)

| ID | Severity | Defect | PR |
|---|---|---|---|
| F14 | low | BFF emitted no security headers (nosniff/frame/referrer) — added SSE-safe middleware | #1667 |
| F15 | low | FE/edge served no security headers + leaked `Server` banner — added to Caddy templates (validated) | #1668 |
| F16 | medium | no request body-size limit (2MB body → 201) — added `request_body max_size 10MB` at edge | #1670 |
| F17 | high | `list_research_analyses` 500 on mixed-tz `run_at` sort (aware vs naive `TypeError`) | #1674 |
| F18 | high | 20 more `_parse_rfc3339 or datetime.min` sort keys in read_store with the same 500 | #1675 |
| F19 | medium | search retriever 500 on score-tie with mixed-tz `updated_at` | #1676 |

## Whole-fleet audits (PASS)

- **Route resolution:** all 26 FastAPI services — 0 shadowed routes, 0 duplicate
  registrations (R21+R28).
- **Input robustness:** all 26 services — 0 input-driven 500s (R22+R28).
- **Static health:** 0 undefined-call symbols, 0 bare-except, 0 mutable
  defaults, reachable divisions guarded.

## Findings recorded for owners (not changed)

- **O6** — 30 broad silent-`except` swallows (intentional defensive patterns; a
  debug log would aid forensics).
- **O7** — 4 internal `assert`s in evolution (prefer explicit `raise`; service
  not run `-O`).
- **O8** — internal optimizer/governance helpers divide by `len(proposals)`
  without an entry guard (ZeroDivision only on unconfirmed empty input).

## New regression tests added (Phase 3)

`test_security_headers.py`, `deploy/caddy/test_caddy_security_headers.py`,
`test_research_analyses_mixed_tz_sort.py`, `test_read_store_sort_key_tz_safe.py`,
`services/search/test_retriever_mixed_tz_sort.py`.

## Deploy state

The Phase-3 code/edge fixes (F14/F16 app+edge, F15/F16 Caddy, F17/F18/F19) are
merged to `dev`; they take **live** effect on the next BFF redeploy +
`sync-caddy.sh` (OPS — same deploy-lag as F2/F3/F5/F12).

## Posture after three phases (35 rounds)

**9 real defects fixed** across the campaign (F2/F3/F5/F8/F9-guard/F12 + F14–F19),
**12 findings attributed to owners**, **14 regression test files added**. The
fleet is route-clean, input-robust, auth/CORS-secure, header-hardened,
DoS-bounded, and datetime-correct; the systemic aware/naive sort-key 500 class
(22 sites) is fully closed. Remaining gaps are upstream build-out (F1) or
owner/ops decisions, all explicitly attributed.
