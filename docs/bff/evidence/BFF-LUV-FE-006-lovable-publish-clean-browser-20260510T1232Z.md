# BFF-LUV-FE-006 Lovable Publish Clean Browser Verification

Date: 2026-05-10T12:32Z

Target frontend:

- `https://pantheon-dev.lovable.app`

Target BFF:

- `https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io`

## Hosted Bundle Probe

The Lovable hosted deployment changed from the stale bundle that previously
missed the dev browser auth fallback.

```text
deployment_id=d018dd4f-feec-4094-878e-a0ad7c23d4c8
asset=assets/index-020E3hRu.js
new_dev_token_present=yes
lupin_bff_present=yes
old_asset=no
```

## BFF Auth/CORS Probe

```text
GET /bff/me with Authorization: Bearer pantheon-dev-browser:reviewer
status=200
access-control-allow-origin=https://pantheon-dev.lovable.app
environment.auth_mode=stub
environment.strict_auth=false
```

## Clean Browser Control Room Probe

No localStorage/sessionStorage token was pre-seeded. The hosted bundle supplied
the dev browser fallback token.

```text
url=https://pantheon-dev.lovable.app/management/control-room
banner=null
bffResponseCount=5
non2xx=[]
failureCount=0
consoleErrorCount=0

200 GET /bff/v5/execution/strategy-health
200 GET /health
200 GET /bff/v5/control-room
200 GET /bff/v5/execution/persona-health
200 GET /bff/events/stream
```

## Clean Browser Route Crawl

No localStorage/sessionStorage token was pre-seeded.

Routes covered: 31 management/agora routes.

```text
badRoutes=0
totalBffResponses=86
totalNon2xx=0
totalFailures=0
ignoredSseAborts=30
```

Ignored SSE aborts are expected when the single browser page navigates between
routes and closes the previous `GET /bff/events/stream` connection.

Covered route set:

```text
/management/control-room
/management/loops/research
/management/loops/execution
/management/loops/optimization
/management/sentinel
/management/interventions
/management/strategies
/management/personas
/management/capital
/management/ranking/formulas
/management/rebalance
/management/evolution
/management/experiments
/management/artifacts
/management/deployments
/management/runtimes
/management/jobs
/management/alerts
/management/incidents
/management/audit
/management/approvals
/management/tools
/management/mcp
/management/skills
/management/channels
/agora
/agora/signals
/agora/ask
/agora/journal
/agora/insights
/agora/channels
```

## Verdict

`https://pantheon-dev.lovable.app` is now published with the execute-plans dev
BFF auth fallback. The previously observed `LIVE BFF UNAVAILABLE strict:
HTTP 401` banner is resolved for the clean-browser control-room path, and the
main route crawl shows no BFF HTTP failures.
