# BFF Fanout Concurrency Baseline

Date: 2026-07-01T18:02:20.246Z
Target: https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io
Auth token shape: op-<id>:admin (dev stub-auth shape; not a production secret)
Concurrent routes: /health, /bff/management/evidence, /bff/management/shell-summary, /bff/alerts, /bff/approvals, /bff/jobs
Excluded: /bff/events/stream (long-lived SSE realtime stream, not a bounded request)

## Per-route summary (ms)

| Route | Count | Min | Max | p95 |
|---|---:|---:|---:|---:|
| /health | 5 | 14 | 134 | 134 |
| /bff/management/evidence | 5 | 21 | 78 | 78 |
| /bff/management/shell-summary | 5 | 22 | 78 | 78 |
| /bff/alerts | 5 | 122 | 330 | 330 |
| /bff/approvals | 5 | 25 | 73 | 73 |
| /bff/jobs | 5 | 16 | 67 | 67 |

## Rounds

### Round 1 (wall clock 396 ms)

| Route | Status | ms |
|---|---:|---:|
| /health | 200 | 134 |
| /bff/management/evidence | 200 | 78 |
| /bff/management/shell-summary | 200 | 78 |
| /bff/alerts | 200 | 330 |
| /bff/approvals | 200 | 73 |
| /bff/jobs | 200 | 67 |

### Round 2 (wall clock 154 ms)

| Route | Status | ms |
|---|---:|---:|
| /health | 200 | 25 |
| /bff/management/evidence | 200 | 34 |
| /bff/management/shell-summary | 200 | 41 |
| /bff/alerts | 200 | 151 |
| /bff/approvals | 200 | 38 |
| /bff/jobs | 200 | 37 |

### Round 3 (wall clock 151 ms)

| Route | Status | ms |
|---|---:|---:|
| /health | 200 | 16 |
| /bff/management/evidence | 200 | 25 |
| /bff/management/shell-summary | 200 | 29 |
| /bff/alerts | 200 | 150 |
| /bff/approvals | 200 | 27 |
| /bff/jobs | 200 | 24 |

### Round 4 (wall clock 139 ms)

| Route | Status | ms |
|---|---:|---:|
| /health | 200 | 20 |
| /bff/management/evidence | 200 | 25 |
| /bff/management/shell-summary | 200 | 30 |
| /bff/alerts | 200 | 138 |
| /bff/approvals | 200 | 32 |
| /bff/jobs | 200 | 28 |

### Round 5 (wall clock 123 ms)

| Route | Status | ms |
|---|---:|---:|
| /health | 200 | 14 |
| /bff/management/evidence | 200 | 21 |
| /bff/management/shell-summary | 200 | 22 |
| /bff/alerts | 200 | 122 |
| /bff/approvals | 200 | 25 |
| /bff/jobs | 200 | 16 |
