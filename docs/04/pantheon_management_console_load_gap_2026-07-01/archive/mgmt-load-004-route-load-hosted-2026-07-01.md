# MGMT-LOAD-004 Hosted Route-Load Evidence - 2026-07-01

## Deployment

- Frontend repository: `ajoe734/execute-plans`
- PR: `https://github.com/ajoe734/execute-plans/pull/134`
- PR head: `f28b7272f61bb778927981f787a440a5a9e5e5fc`
- Merge commit on `dev`: `255e60414e0ca36e29c1b2e39f0543d23d2eea80`
- PR integration gate: `https://github.com/ajoe734/execute-plans/actions/runs/28513916762` - success
- Dev FE deploy run: `https://github.com/ajoe734/execute-plans/actions/runs/28514407926` - success
- Hosted deployment manifest: `https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/deployment.json`

`deployment.json` reported:

```json
{
  "app": "execute-plans",
  "environment": "pantheon-dev-fe",
  "deployedAt": "20260701T113337Z",
  "commit": "255e60414e0ca36e29c1b2e39f0543d23d2eea80",
  "sourceRef": "255e60414e0ca36e29c1b2e39f0543d23d2eea80",
  "sourceBranch": "dev",
  "feHost": "https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io",
  "bffHost": "https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io",
  "buildMode": {
    "VITE_BFF_MODE": "live",
    "VITE_BFF_FALLBACK": "strict",
    "VITE_BFF_REAL_WRITES": "false"
  }
}
```

## Hosted Probe

Command:

```sh
PANTHEON_FE_BASE_URL=https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io \
PANTHEON_BFF_BASE_URL=https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io \
PANTHEON_ROUTE_LOAD_PROBE_PATH=/management/evidence \
PANTHEON_ROUTE_LOAD_PRIMARY_API_PATH=/bff/management/evidence \
PANTHEON_LOAD_BASELINE_OUT_DIR=.lovable/audits/mgmt-load-004 \
PANTHEON_PROBE_NOCACHE_SHA=255e60414e0ca36e29c1b2e39f0543d23d2eea80 \
npm run probe:route-load
```

Four additional samples used the same environment with output dirs
`.lovable/audits/mgmt-load-004/run-02` through `run-05`.

The probe waits on content milestones and never uses `networkidle`; the shell's
long-lived `/bff/events/stream` request is recorded but excluded from readiness.

| Sample | Timestamp | domcontentloaded | heading visible | primary Evidence API complete | first row/empty state visible | Requests before first row | Pass |
|---:|---|---:|---:|---:|---:|---:|---|
| 1 | `2026-07-01T11:37:26.210Z` | 131 ms | 513 ms | 616 ms | 673 ms | 70 | true |
| 2 | `2026-07-01T11:38:14.434Z` | 151 ms | 687 ms | 837 ms | 931 ms | 70 | true |
| 3 | `2026-07-01T11:38:16.589Z` | 158 ms | 851 ms | 1131 ms | 1203 ms | 70 | true |
| 4 | `2026-07-01T11:38:18.792Z` | 128 ms | 581 ms | 702 ms | 754 ms | 70 | true |
| 5 | `2026-07-01T11:38:20.573Z` | 129 ms | 570 ms | 682 ms | 733 ms | 70 | true |

Nearest-rank percentiles over the five `first row/empty state visible` samples:

- p75: 931 ms, under the 1.5 s budget.
- p95: 1203 ms, under the 2.5 s budget.

Primary Evidence API completion also stayed within the route-load envelope:
p75 837 ms and p95 1131 ms.

## Result

MGMT-LOAD-004 hosted route-load closeout passed on the Pantheon dev FE host for
the merged execute-plans dev commit `255e60414e0ca36e29c1b2e39f0543d23d2eea80`.
