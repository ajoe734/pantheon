# Round 23 — Results

**Executed:** 2026-06-15 (UTC). **Target:** live dev BFF.

## H1/H2 — origin allowlist & bypass resistance: PASS

Live `OPTIONS` preflight, `Access-Control-Allow-Origin` (AAO) in the response:

| Origin | AAO |
|---|---|
| `https://<dev-fe>` (exact allowed) | **reflected** (204) |
| `https://evil.example.com` | none (400) |
| `http://localhost:3000` | none (400) |
| `null` | none (400) |
| `https://evil.<dev-fe>` (subdomain prefix) | none |
| `https://<dev-fe>.attacker.com` (suffix) | none |
| `https://<dev-fe>:1234` (port) | none |
| `http://<dev-fe>` (scheme downgrade) | none |

Exact-match only — no reflection of arbitrary origins, and no bypass via
subdomain, suffix, port, scheme, or `null`.

## H2 — preview regex anchoring: PASS

`_LOVABLE_PREVIEW_ORIGIN_REGEX` is passed to Starlette's `CORSMiddleware` as
`allow_origin_regex`. Although the regex string itself is not `^…$`-anchored,
**Starlette 1.0.0 matches with `self.allow_origin_regex.fullmatch(origin)`**
(`starlette/middleware/cors.py:102`), which requires the whole origin to match —
so a `…lovable.app.evil.com` suffix cannot bypass it.

## H3 — credentials: PASS (with benign note)

`Access-Control-Allow-Credentials: true` is present, but only the exact allowed
origin also receives `Access-Control-Allow-Origin`. Per the CORS spec a browser
honors credentials only when **both** headers are present and the origin
matches, so credentials are not usable from any non-allowlisted origin.

**Note (benign):** rejected-origin preflights still emit
`allow-credentials: true` (without `allow-origin`). Harmless (the browser blocks
without a matching `allow-origin`), but cosmetically it could be omitted on the
reject path.

Production-strict mode (`_is_production_strict_mode`) additionally filters the
dev-only and preview origins out of the allowlist (code path; not live-testable
from dev).

## Net

H1–H3 **PASS** — the CORS policy is a correct exact-match allowlist with
`fullmatch` regex handling and no reflection/bypass; credentials are safe. No
defect.
