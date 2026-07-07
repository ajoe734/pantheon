# Hosted Browser BFF Probe

Date: 2026-07-07T15:55:42.817Z
FE: https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io
BFF: https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io
Target: https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/agora/trading-room

## Summary

- contains intended BFF URL: true
- contains old BFF URL: false
- old BFF URL hit count: 0
- request count: 4
- response count: 4
- failed count: 0
- trading room request count: 1
- unauthorized/forbidden response count: 0
- generic-only Trading Room failure: false
- cache policy ok: true
- pass: true

## Deployment

- deployment status: 200
- deployment id: 4a4f256e0bc14c99820b7406de44822b6b1cbe2c
- deployment keys: app, bffHost, buildMode, commit, deployedAt, environment, feHost, sourceBranch, sourceRef
- deployment Cache-Control: no-store, no-cache, must-revalidate, max-age=0


## Cache headers

| Resource | Status | Cache-Control | ETag | Last-Modified |
|---|---:|---|---|---|
| shell /agora/trading-room | 200 | no-store, no-cache, must-revalidate, max-age=0 | "tht9mezt" | Tue, 07 Jul 2026 15:27:50 GMT |
| deployment.json | 200 | no-store, no-cache, must-revalidate, max-age=0 |  |  |
| asset https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/assets/index-CUsZS2eG.js | 200 | public, max-age=31536000, immutable | "tht9mejczg" | Tue, 07 Jul 2026 15:27:50 GMT |



## Trading Room error diagnostics

None


## Responses

| Status | Method | URL | Request ID | Correlation ID |
|---:|---|---|---|---|
| 200 | GET | /bff/events/stream |  |  |
| 200 | GET | /bff/agora/trading-room/decision-events |  |  |
| 200 | GET | /bff/agora/trading-room |  |  |
| 201 | POST | /bff/agora/strategies/full003-live-1783268175-13279b/trading-room/proposals |  |  |

## Failed

None

## Console errors

None