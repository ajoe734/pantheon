# Frontend Browser BFF Probe

Date: 2026-07-02T09:48:16.931Z
FE: https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io
Page URL: https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/agora/trading-room?nocache=aa071d6fbdb5
BFF: https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io
Upstream BFF: https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io
Old BFF: https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io
nocache: aa071d6fbdb5
timeout ms: 90000
navigation waitUntil: domcontentloaded
core waitForResponse paths: /bff/me, /bff/agora/trading-room
required core waitForResponse paths: /bff/agora/trading-room
optional core waitForResponse paths: /bff/me

## Summary

- contains intended BFF URL: true
- contains intended BFF URL in html/bundle: true
- intended BFF runtime request count: 6
- contains old BFF URL: false
- old BFF URL hit count: 0
- required core BFF responses complete: false
- optional core BFF responses observed: true
- request count: 6
- response count: 6
- failed count: 0
- pass: false

## Core BFF responses

| Status | Method | Path | Required | Accepted | URL / Error |
|---:|---|---|---|---|---|
| 200 | GET | /bff/me | false | true | /bff/me |
| 401 | GET | /bff/agora/trading-room | true | false | /bff/agora/trading-room |

## Bundle fetches

| Status | Source | Fetched |
|---:|---|---|
| 200 | https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/assets/index-C1uXbWxP.js | https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/assets/index-C1uXbWxP.js?nocache=aa071d6fbdb5 |

## Old URL hits

None

## Responses

| Status | Method | URL |
|---:|---|---|
| 401 | GET | /bff/agora/trading-room |
| 401 | GET | /bff/agora/trading-room/decision-events |
| 200 | GET | /bff/me |
| 200 | GET | /health |
| 200 | GET | /bff/management/shell-summary |
| 200 | GET | /bff/events/stream?lastEventId=MR3BNBR6-3 |

## Failed

None

## Console errors

- Failed to load resource: the server responded with a status of 401 ()
- Failed to load resource: the server responded with a status of 401 ()