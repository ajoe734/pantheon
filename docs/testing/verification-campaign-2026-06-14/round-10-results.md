# Round 10 — Results

**Executed:** 2026-06-14 (UTC). **Method:** in-process audit of `app.routes` vs
`app.openapi()`.

## H1 — hidden routes: PASS

5 routes carry `include_in_schema=False`, all benign framework/infra:

| Route | Endpoint | Nature |
|---|---|---|
| `GET /docs` | `swagger_ui_html` | Swagger UI (framework) |
| `GET /docs/oauth2-redirect` | `swagger_ui_redirect` | Swagger oauth redirect |
| `GET /redoc` | `redoc_html` | ReDoc UI (framework) |
| `GET /openapi.json` | `openapi` | the spec itself |
| `/bff/management/performance-attribution/by-strategy` | `..._options` | CORS `OPTIONS` preflight (no GET/POST) |

**No hidden state-mutating business endpoint exists.** There is no undocumented
shadow API.

## H2 — spec completeness: PASS

Zero in-schema routes are missing from `openapi()["paths"]`. The documented
contract equals the live (in-schema) surface.

## Net

H1/H2 **PASS** — the OpenAPI faithfully represents the served surface in both
directions: Round 2 showed documented routes are reachable (modulo the one
shadowing bug, fixed); Round 10 shows nothing is served behind the spec's back.
The contract surface is complete and honest.
