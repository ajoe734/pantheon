# Round 4 — Write-surface input robustness & error-envelope consistency

**Date:** 2026-06-14
**Depth/breadth step:** Rounds 2–3 covered the GET surface. Round 4 moves to the
**write surface** (POST) and to **cross-cutting error behavior**. It is the
first round to exercise mutation endpoints — done in a **mutation-safe** way:
only bodies that cannot deserialize into a valid create (malformed JSON, wrong
JSON types) are sent, so no real resource can be created.

## Why this round (not a duplicate)

No prior doc fuzzes the write surface's input validation or audits error-
envelope uniformity. The BFF write-gap work (`pantheon_bff_write_gap_*`,
05-28) enumerated *missing* write endpoints; this round verifies the *existing*
write endpoints fail gracefully on bad input and that all error responses share
one canonical envelope.

## Hypotheses

- H1: every param-free POST returns a clean 4xx (400/422) — never a 500 — for
  malformed JSON.
- H2: type-confused valid JSON (array/string/number where an object is
  expected) also yields a clean 4xx, never a 500.
- H3 (envelope): all error responses (401/404/405/422) conform to the canonical
  envelope `{error:{code,i18nKey,message,retryable,userActionable,details},
  meta:{correlationId}}`.

## Method (mutation-safe)

1. Enumerate param-free POST paths (64) from live OpenAPI.
2. Probe each with: malformed JSON, then `[]`, `"x"`, `123`, `null`.
   None of these can populate a create model, so persistence is not triggered.
3. Classify status codes; reproduce/inspect any 5xx.
4. Sample one representative of each error class (401/404/405/422) and diff the
   envelope shape.

## Pass criteria

- H1/H2: zero 5xx across the POST surface under bad input.
- H3: every sampled error response carries the canonical envelope.
- Any genuine defect fixed via the dev workflow; intended-stub behavior
  recorded as an observation, not force-fixed.
