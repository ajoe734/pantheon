# Round 18 — Remaining input channels: headers + parameterized-route queries

**Date:** 2026-06-15
**Depth/breadth step:** Round 16 fuzzed query params on param-free GETs and
found F12. Round 18 closes the **last two input channels**: request **headers**
(parsed by write handlers) and **query parameters on parameterized (`{id}`) GET
routes**.

## Hypotheses

- H1: malformed write headers (`Idempotency-Key`, `X-MFA-Token`, `X-Dry-Run`,
  `X-Correlation-Id`) never cause a 500 — header parsing is defensive.
- H2: no query parameter on a parameterized GET route causes a 500 (a sibling of
  F12).

## Method

1. Header fuzz: for every param-free POST, send each header with a battery of
   malformed values (huge, special chars, wrong type) + a malformed body
   (mutation-safe); flag 5xx.
2. Query fuzz on `{id}` routes: fill path params with a benign dummy, fuzz each
   declared query param with injection/malformed payloads; flag 5xx.

## Pass criteria

- H1/H2: zero 5xx, or any 500 root-caused and fixed via the dev workflow.
