# Hosted Browser BFF Probe

Date: 2026-05-15T04:56:45.389Z
FE: https://pantheon-dev.lovable.app
Page URL: https://pantheon-dev.lovable.app/management?nocache=e3452cfd43ba
BFF: https://pantheon-lupin-dev-bff.34.81.75.241.sslip.io
Old BFF: https://pantheon-dev-bff.35.236.178.81.sslip.io
nocache: e3452cfd43ba
timeout ms: 90000
navigation waitUntil: domcontentloaded
core waitForResponse paths: /bff/me, /bff/v5/control-room
required core waitForResponse paths: /bff/v5/control-room
optional core waitForResponse paths: /bff/me

## Summary

- contains intended BFF URL: true
- contains old BFF URL: false
- old BFF URL hit count: 0
- required core BFF responses complete: true
- optional core BFF responses observed: false
- request count: 11
- response count: 11
- failed count: 0
- pass: true

## Core BFF responses

| Status | Method | Path | Required | Accepted | URL / Error |
|---:|---|---|---|---|---|
| 0 | GET | /bff/me | false | false | TimeoutError: page.waitForResponse: Timeout 5000ms exceeded while waiting for event "response" |
| 200 | GET | /bff/v5/control-room | true | true | /bff/v5/control-room |

## Bundle fetches

| Status | Source | Fetched |
|---:|---|---|
| 200 | https://pantheon-dev.lovable.app/assets/index-vlevju41.js | https://pantheon-dev.lovable.app/assets/index-vlevju41.js?nocache=e3452cfd43ba |
| 200 | https://pantheon-dev.lovable.app/~flock.js | https://pantheon-dev.lovable.app/~flock.js?nocache=e3452cfd43ba |

## Old URL hits

None

## Responses

| Status | Method | URL |
|---:|---|---|
| 200 | GET | /bff/v5/execution/persona-health |
| 200 | GET | /bff/search?q= |
| 200 | GET | /bff/alerts |
| 200 | GET | /bff/me |
| 200 | GET | /bff/approvals |
| 200 | GET | /health |
| 200 | GET | /bff/v5/control-room |
| 200 | GET | /bff/jobs |
| 200 | GET | /bff/v5/execution/strategy-health |
| 200 | GET | /bff/jobs |
| 200 | GET | /bff/events/stream?lastEventId=MP6G3GON-3 |

## Failed

None

## Console errors

None