# MGMT-GAP-001 Closeout - Route IA Cleanup - 2026-06-30

Status: done

This closeout records the implementation and hosted verification for
`MGMT-GAP-001`, the first management-console production gap item from the
2026-06-30 re-audit.

## Implementation

| Item | Evidence |
|---|---|
| Implementation repo | `ajoe734/execute-plans` |
| PR | `https://github.com/ajoe734/execute-plans/pull/120` |
| Implementation commit | `806f53fe5e9ac6e0e909621ba0c13b775679adc7` |
| Merge commit on FE `dev` | `6218e67d4119bcfc663681935d2a98e5af73e55a` |
| PR integration gate | `https://github.com/ajoe734/execute-plans/actions/runs/28451953181` |
| Dev integration gate | `https://github.com/ajoe734/execute-plans/actions/runs/28452500411` |
| Dev deploy | `https://github.com/ajoe734/execute-plans/actions/runs/28452499928` |

Implemented behavior:

- `/management/control-room-legacy` redirects to `/management/cockpit`.
- `/management/deployment` redirects to `/management/deployments`.
- `/management/deployment/:id` redirects to `/management/deployments/:id` while
  preserving search/hash.
- Formula Studio, Skill Sandbox, and loop subpages were removed from primary
  management nav exposure while their direct routes remain mounted for
  compatibility and later deep-production work.

## Hosted Deployment Evidence

Hosted FE `/deployment.json` after merge:

```json
{
  "app": "execute-plans",
  "environment": "pantheon-dev-fe",
  "deployedAt": "20260630T143840Z",
  "commit": "6218e67d4119bcfc663681935d2a98e5af73e55a",
  "sourceRef": "6218e67d4119bcfc663681935d2a98e5af73e55a",
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

BFF health after FE deploy:

| Field | Value |
|---|---|
| URL | `https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/healthz` |
| status | `ok` |
| live | `true` |
| ready | `true` |
| version | `0.2.0` |

## Hosted Browser Probe

Hosted browser probe against
`https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io` passed:

| Probe | Result |
|---|---|
| `/management/control-room-legacy` | redirects to `/management/cockpit` |
| `/management/deployment` | redirects to `/management/deployments` |
| `/management/deployment/dep-9?tab=events` | redirects to `/management/deployments/dep-9?tab=events` |
| Primary nav | `53` links after route IA cleanup |
| Demoted nav entries absent | `/management/studios/formula`, `/management/studios/skill-sandbox`, `/management/loops/research`, `/management/loops/execution`, `/management/loops/optimization` |
| Loop overview | `/management/loops` remains visible |

## Local And CI Validation

Local validation in the FE worktree passed:

- `npm ci`
- `npm run build`
- `npm run lint` with existing warnings only and `0` errors
- `npx playwright test e2e/20-management-route-ia.spec.ts --project=chromium --workers=1`
- `npx playwright test e2e/02-control-room.spec.ts e2e/18-perf.spec.ts e2e/19-management-persona-100-flows.spec.ts --project=chromium --workers=1`

CI validation passed:

- PR integration gate passed for PR #120.
- Dev push integration gate run `28452500411` passed.
- Dev deploy run `28452499928` passed and published commit
  `6218e67d4119bcfc663681935d2a98e5af73e55a`.

## Residual Notes

`MGMT-GAP-001` is closed for the route/IA slice only. The overall management
console gap remains open until `MGMT-GAP-002` through `MGMT-GAP-007` are closed.

Direct studio and loop subpage routes remain mounted for compatibility. They are
not primary-nav production surfaces after this change and must be handled by
`MGMT-GAP-005` and `MGMT-GAP-006` before final production closeout.
