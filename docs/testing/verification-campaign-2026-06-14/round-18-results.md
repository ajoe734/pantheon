# Round 18 — Results

**Executed:** 2026-06-15 (UTC).

## H1 — header fuzz: PASS

**1,408** requests: every param-free POST × {`X-Dry-Run`, `X-MFA-Token`,
`Idempotency-Key`, `X-Correlation-Id`} × a battery of malformed values (wrong
type, 2k–10k-char strings, special chars, injection-shaped strings), each with a
malformed body (mutation-safe). **0 5xx.** Header parsing is defensive — no
unguarded `int()`/format parse on header values. (Raw CR/LF/NUL are rejected by
the HTTP client before transmission, so header-injection via these values is not
reachable from a conforming client.)

## H2 — parameterized-route query fuzz: PASS

**52** `(parameterized GET path, query-param)` targets, each fuzzed with the same
injection/malformed payload set that found F12 on the param-free surface. **0
5xx.** No sibling of F12 exists on the `{id}`-route query surface.

## Net

H1/H2 **PASS** — the last two input channels (request headers, parameterized-
route query params) are robust. Combined with Rounds 3/4/15/16, the **entire**
BFF input surface (path params, bodies, param-free query, pagination params,
headers, parameterized-route query) has now been fuzzed; the only 500 found
across all of it was F12 (fixed in Round 16).
