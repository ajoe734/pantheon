# BFF Fanout Concurrency Baseline

Date: 2026-07-01T06:18:50.255Z
Target: https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io
Auth token shape: op-<id>:admin (dev stub-auth shape; not a production secret)
Concurrent routes: /health, /bff/management/evidence, /bff/alerts, /bff/approvals, /bff/jobs
Excluded: /bff/events/stream (long-lived SSE realtime stream, not a bounded request)

## Per-route summary (ms)

| Route | Count | Min | Max | p95 |
|---|---:|---:|---:|---:|
| /health | 5 | 45 | 1328 | 1328 |
| /bff/management/evidence | 5 | 386 | 1423 | 1423 |
| /bff/alerts | 5 | 493 | 1513 | 1513 |
| /bff/approvals | 5 | 491 | 1537 | 1537 |
| /bff/jobs | 5 | 387 | 1538 | 1538 |

## Rounds

### Round 1 (wall clock 2157 ms)

| Route | Status | ms |
|---|---:|---:|
| /health | 200 | 955 |
| /bff/management/evidence | 200 | 386 |
| /bff/alerts | 200 | 1513 |
| /bff/approvals | 200 | 1537 |
| /bff/jobs | 200 | 1538 |

### Round 2 (wall clock 1431 ms)

| Route | Status | ms |
|---|---:|---:|
| /health | 200 | 1328 |
| /bff/management/evidence | 200 | 1423 |
| /bff/alerts | 200 | 1323 |
| /bff/approvals | 200 | 1418 |
| /bff/jobs | 200 | 1419 |

### Round 3 (wall clock 692 ms)

| Route | Status | ms |
|---|---:|---:|
| /health | 200 | 45 |
| /bff/management/evidence | 200 | 656 |
| /bff/alerts | 200 | 648 |
| /bff/approvals | 200 | 650 |
| /bff/jobs | 200 | 646 |

### Round 4 (wall clock 876 ms)

| Route | Status | ms |
|---|---:|---:|
| /health | 200 | 832 |
| /bff/management/evidence | 200 | 835 |
| /bff/alerts | 200 | 834 |
| /bff/approvals | 200 | 834 |
| /bff/jobs | 200 | 872 |

### Round 5 (wall clock 497 ms)

| Route | Status | ms |
|---|---:|---:|
| /health | 200 | 486 |
| /bff/management/evidence | 200 | 496 |
| /bff/alerts | 200 | 493 |
| /bff/approvals | 200 | 491 |
| /bff/jobs | 200 | 387 |
