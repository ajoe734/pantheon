# Management Console Route-Load Baseline — /management/evidence

Date: 2026-07-01T06:18:29.901Z
FE: https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io
FE commit: 18b406d9bb3d
BFF: https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io
Auth token shape: op-<id>:admin (dev stub-auth shape; not a production secret)
Navigation waitUntil: domcontentloaded (never `networkidle` — the shell opens `/bff/events/stream`, a long-lived SSE stream, so `networkidle` never resolves)

## Milestones (ms since navigation start)

| Milestone | ms |
|---|---:|
| domcontentloaded | 2799 |
| shell (#root) attached | 4328 |
| route heading visible | 4508 |
| primary Evidence API (`/bff/management/evidence`) complete | 4485 |
| first row or empty-state visible | 4668 |

## Summary

- non-primary/BFF+FE requests observed before first row: 11
- total BFF/FE requests captured: 11
- used `networkidle` as readiness signal: false
- error: none
- pass: true

## Request waterfall (BFF + FE document/asset requests)

| Start ms | Duration ms | Status | Method | Path | Note |
|---:|---:|---|---|---|---|
| 35 | 89 | 200 | GET | /management/evidence |  |
| 144 | 1210 | 200 | GET | /assets/index-ANtvIHEF.js |  |
| 145 | 13 | 200 | GET | /assets/index-DfsF2g9b.css |  |
| 3715 | 753 | 200 | GET | /bff/me |  |
| 3715 | 770 | 200 | GET | /bff/approvals |  |
| 3715 | 771 | 200 | GET | /bff/alerts |  |
| 3715 | 785 | 200 | GET | /bff/jobs |  |
| 3716 | 694 | 200 | GET | /health |  |
| 3717 | 766 | 200 | GET | /bff/management/evidence |  |
| 3718 | 768 | 200 | GET | /bff/jobs |  |
| 4226 | n/a | n/a | GET | /bff/events/stream | realtime SSE stream; excluded from readiness milestones |

Full JSON: `route-timing-2026-07-01.json`, `request-waterfall-2026-07-01.json`