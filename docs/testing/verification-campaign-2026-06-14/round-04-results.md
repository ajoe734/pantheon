# Round 4 — Results

**Executed:** 2026-06-14 (UTC). **Target:** dev BFF
`https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io`. 64 param-free POST
paths.

## Input-robustness sweep (mutation-safe)

| Body sent | Status distribution | 5xx |
|---|---|---|
| malformed JSON `{ this is : not valid json` | 400×6, 422×58 | **0** |
| array `[]` | 400×6, 422×58 | **0** |
| string `"x"` | 400×6, 422×58 | **0** |
| number `123` | 400×6, 422×58 | **0** |
| `null` | 200×2, 201×3, 202×1, 400×19, 401×1, 403×1, 410×1, 422×36 | **0** |

- **H1 PASS / H2 PASS.** No 500 under any malformed or type-confused body. The
  write surface deserializes input defensively across all 64 endpoints.

## Error-envelope consistency

Sampled 401 (no auth), 404 (unknown path), 405 (wrong method), 422 (missing
param). **H3 PASS** — all four carry the canonical envelope
`{error:{code,i18nKey,message,retryable,userActionable,details}, meta:{correlationId}}`.
(Minor note: 405 reports `code:"VALIDATION_FAILED"` with message "Method Not
Allowed" rather than a dedicated `METHOD_NOT_ALLOWED` code — cosmetic, not a
defect.)

## Observations (intended scaffolding — not fixed)

- **O1 — `null` body accepted (201) by generic create aliases.** `POST
  /bff/artifacts`, `/bff/research-experiments`, `/bff/ranking-formulas` are
  served by `sem_final_generic_create_alias`, a **stateless stub**
  (`Body(default_factory=dict)`) that echoes back a generated id and **does not
  persist**. Verified: GET of a never-created id returns `200` with
  `status:"degraded", readSurface.source:"missing"` — i.e. the POST writes
  nothing and the GET-by-id is also a degraded-surface stub. The two ids
  "created" during probing (`7377fcdc8650`, `c54777bd73bf`) are **not**
  persisted; no cleanup required.
- **O2 — generic GET-by-id stubs return `200 degraded` for unknown ids**
  (rather than 404). This explains the "21×200 on unknown id" bucket in
  Round 3. These are placeholder surfaces awaiting real backends.

These are deliberate placeholders, consistent with the dev build-gap state
(the same upstream gap as Round 1's F1). Forcing strict body/404 behavior on
stubs could break FE flows that lean on the lenient placeholders, so **no code
change** is made; recorded for the team's stub-hardening backlog.

## Net

H1/H2/H3 **PASS** — the write surface is robust against bad input and error
responses are uniform. No runtime defect found this round; two intended-stub
behaviors documented (O1, O2). This is a clean verification result, not a fix
round.
