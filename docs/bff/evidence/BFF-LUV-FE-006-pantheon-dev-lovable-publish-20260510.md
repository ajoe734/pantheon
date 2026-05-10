# BFF-LUV-FE-006 — pantheon-dev Lovable Publish Verification

Date: 2026-05-10T05:41:24Z
Reviewer: Codex
Correct hosted URL: `https://pantheon-dev.lovable.app/`
Old URL incorrectly probed earlier: `https://pantheon-ai-system-front-dev.lovable.app/`

## Verdict

`https://pantheon-dev.lovable.app/` is published and wired to the intended lupin
dev BFF:

`https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io`

The earlier Lovable deploy blocker is superseded because it probed the obsolete
front-ai-platform/front-ai-trading-system dev URL instead of the current
execute-plans dev URL.

## Hosted Bundle Probe

Command class: hosted HTML + JS asset probe against `pantheon-dev.lovable.app`.

```text
probed_at=2026-05-10T05:41:24Z
deployment_id=b944ef3a-5ac7-4f7d-999c-550776971377
asset=/assets/index-hGWC2E4H.js
contains_lupin_bff=yes
contains_old_dev_bff=no
bundle_bff_urls=
https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io
```

## BFF Health And Discovery

Target: `https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io`

```text
/health 200 bytes=93
/docs 200 bytes=1020
/openapi.json 200 bytes=344125
/bff/events/stream 200 bytes=248
```

Health body:

```json
{"status":"ok","service":"operator-bff","version":"0.2.0","timestamp":"2026-05-10T05:41:24Z"}
```

## Anonymous Route Registration Probe

This rechecked the FE BFF Contract v1 path catalog that previously showed 39
anonymous `404` responses. Current lupin dev result:

```text
probed_at=2026-05-10T05:42:04Z
counts={"200": 1, "401": 42}
```

Interpretation:

- `200`: public route is reachable (`GET /bff/events/stream`).
- `401`: route is registered and protected by auth.
- `404`: no route missing in this anonymous registration pass.

Representative rows:

```text
401 GET  /bff/me
401 POST /bff/auth/refresh
401 POST /bff/logout
401 POST /bff/actions/strategies/strategy-dev/promote
401 GET  /bff/strategies
401 GET  /bff/personas
401 GET  /bff/capital-pools
401 GET  /bff/approvals
401 POST /bff/approvals/approval-dev/decide
401 GET  /bff/mcp-servers
401 GET  /bff/agora/signals
401 GET  /bff/v5/loop-runs
401 GET  /bff/v5/interventions
401 POST /bff/v5/interventions/intervention-dev/decide
401 GET  /bff/v5/execution/persona-health
200 GET  /bff/events/stream
```

## Authenticated Smoke Status

An authenticated live smoke was already recorded earlier in this task packet:

- `docs/bff/evidence/BFF-LUV-AUTHED-LIVE-001-live-smoke-20260510T024935Z.json`
- Result: `37/37` passed against the same lupin dev BFF target.

Attempting to rerun it from the current shell at 2026-05-10T05:40Z was blocked
because neither `PANTHEON_BFF_SMOKE_BEARER_TOKEN` nor
`PANTHEON_BFF_SMOKE_JWT_SECRET` is present in this worker environment. No secret
or bearer token was written into evidence.

## Browser Network Probe And CORS Fix

Reviewer reran a real headless Chromium network probe from
`https://pantheon-dev.lovable.app/` after installing temporary Playwright
browsers outside the repo at `/tmp/pw-browsers`.

Pre-fix result at 2026-05-10T05:49:16Z:

```text
page_url=https://pantheon-dev.lovable.app/management
bff_url_origins=["https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io"]
request_count=5
response_count=0
contains_lupin_bff=true
contains_old_dev_bff=false
console_error_sample=Access to fetch ... from origin 'https://pantheon-dev.lovable.app' has been blocked by CORS policy
```

Root cause: the running dev BFF container allowed only the legacy Lovable dev
origin:

```text
PANTHEON_BFF_CORS_ORIGINS=https://pantheon-ai-system-front-dev.lovable.app
```

Fix committed and pushed:

```text
45bf6873 BFF-LUV-FE-006: allow execute-plans Lovable dev CORS
```

Dev BFF restart:

```text
host=pantheon-lupin-dev
repo=/home/lupin/code/pantheon
head=45bf6873
PANTHEON_BFF_CORS_ORIGINS=https://pantheon-ai-system-front-dev.lovable.app,https://pantheon-dev.lovable.app
docker compose -p pantheon -f docker-compose.yml up -d --build operator-bff
health-ok
{"status":"ok","service":"operator-bff","version":"0.2.0","timestamp":"2026-05-10T06:00:11Z"}
```

Post-fix CORS preflight:

```text
OPTIONS /health
Origin: https://pantheon-dev.lovable.app
HTTP/2 200
access-control-allow-origin: https://pantheon-dev.lovable.app
```

Post-fix browser network probe at 2026-05-10T06:00:59Z:

```json
{
  "page_url": "https://pantheon-dev.lovable.app/management",
  "bff_url_origins": [
    "https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io"
  ],
  "request_count": 5,
  "response_count": 5,
  "failed_count": 0,
  "contains_lupin_bff": true,
  "contains_old_dev_bff": false,
  "responses": [
    {"method": "GET", "status": 200, "url": "/health"},
    {"method": "GET", "status": 401, "url": "/bff/v5/execution/strategy-health"},
    {"method": "GET", "status": 401, "url": "/bff/v5/control-room"},
    {"method": "GET", "status": 401, "url": "/bff/v5/execution/persona-health"},
    {"method": "GET", "status": 200, "url": "/bff/events/stream"}
  ]
}
```

The `401` responses are the expected anonymous auth gate. They prove the browser
request reached the registered BFF route and was no longer blocked by CORS.

## Acceptance Impact

| Criterion | Status |
|---|---|
| dev deployment completed from recorded execute-plans publish | Met for corrected URL `https://pantheon-dev.lovable.app/` |
| deployed frontend bundle points to intended BFF | Met |
| old BFF URL removed from hosted bundle | Met |
| route catalog no longer has anonymous 404 gaps | Met |
| full browser network E2E from this shell | Met after CORS fix and dev BFF restart; 5 BFF requests, 5 responses, 0 failed |
