# Round 16 — Broad query-parameter fuzz (500-hunt + injection)

**Date:** 2026-06-15
**Depth/breadth step:** Round 3 fuzzed path params, Round 4 fuzzed bodies,
Round 15 fuzzed pagination params. Round 16 fuzzes **every declared query
parameter** across the whole GET surface with malformed and injection-style
values — the last untested input channel.

## Hypotheses

- H1: no query parameter, given a malformed/injection value, causes a 500
  (unhandled exception).
- H2: injection-style values (`'"><script>`, `{{7*7}}`, `' OR '1'='1`,
  path-traversal) are treated as opaque data — not reflected unsanitized nor
  evaluated.

## Method

1. Enumerate every `(param-free GET path, query-param)` pair from the live
   OpenAPI.
2. For each, send a battery of malformed/injection payloads (XSS, SSTI, SQLi,
   traversal, huge numbers, NaN/Infinity, oversized strings, wrong types).
3. Flag any 5xx; inspect reflected responses for unsanitized echo / evaluation.

## Pass criteria

- H1: zero 5xx across the query-param surface; any 500 is root-caused and fixed
  via the dev workflow.
- H2: no payload is evaluated or reflected unsanitized.
