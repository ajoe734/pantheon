# Frontend Browser BFF Probe

Date: 2026-07-07T15:57:46.011Z
FE: https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io
Page URL: https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/agora/trading-room?nocache=4a4f256e0bc1
BFF: https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io
Upstream BFF: https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io
Old BFF: https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io
nocache: 4a4f256e0bc1
timeout ms: 90000
navigation waitUntil: domcontentloaded
core waitForResponse paths: /bff/me, /bff/agora/trading-room, /bff/agora/trading-room/decision-events
required core waitForResponse paths: /bff/agora/trading-room, /bff/agora/trading-room/decision-events
optional core waitForResponse paths: /bff/me

## Summary

- contains intended BFF URL: true
- contains intended BFF URL in html/bundle: true
- intended BFF runtime request count: 4
- contains old BFF URL: false
- old BFF URL hit count: 0
- required core BFF responses complete: true
- optional core BFF responses observed: false
- request count: 4
- response count: 4
- failed count: 0
- pass: true

## Core BFF responses

| Status | Method | Path | Required | Accepted | URL / Error |
|---:|---|---|---|---|---|
| 0 | GET | /bff/me | false | false | TimeoutError: page.waitForResponse: Timeout 5000ms exceeded while waiting for event "response" |
| 200 | GET | /bff/agora/trading-room | true | true | /bff/agora/trading-room |
| 200 | GET | /bff/agora/trading-room/decision-events | true | true | /bff/agora/trading-room/decision-events |

## Bundle fetches

| Status | Source | Fetched |
|---:|---|---|
| 200 | https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/assets/index-CUsZS2eG.js | https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/assets/index-CUsZS2eG.js?nocache=4a4f256e0bc1 |

## Old URL hits

None

## Responses

| Status | Method | URL |
|---:|---|---|
| 200 | GET | /bff/events/stream |
| 200 | GET | /bff/agora/trading-room/decision-events |
| 200 | GET | /bff/agora/trading-room |
| 201 | POST | /bff/agora/strategies/full003-live-1783268175-13279b/trading-room/proposals |

## Failed

None

## Console errors

None