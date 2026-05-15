# TW-03 Before/After Compare — QA Status

## Build

- `npm run build`: PASS (no TypeScript errors, no missing imports)

## Contract Coverage

| Requirement | Status |
|---|---|
| GET /api/v1/trainer/sessions/{session_id}/preview wired | PASS |
| POST /api/v1/trainer/sessions/{session_id}/preview wired | PASS |
| No raw fetch in components | PASS — uses tw03PreviewApi only |
| No demo providers imported | PASS |
| Compare header: session_id, status, eval_id, snapshot fields | PASS |
| Metric delta panels from metric_delta[] | PASS |
| Warning hierarchy rail from warnings[] in backend order | PASS |
| Control diff panel from control_diff[] | PASS |
| Refresh CTA visible only when canRefreshPreview=true | PASS |
| POST called with refresh_mode="manual" only | PASS |
| Polling only GET preview with eval_id while status=pending | PASS |
| Polling uses exactly poll_interval_ms | PASS |
| Polling stops on status resolve / degraded / unavailable / deadline | PASS |
| preview_unavailable rendered as degraded copy, not loading | PASS |
| BFF gap alert on missing required fields | PASS |
| Surface ok: normal rendering | PASS |
| Surface stale: staleness banner, canRefreshPreview governs CTA | PASS |
| Surface degraded: degradation banner, refresh suppressed | PASS |
| Surface unavailable: metric panels and refresh suppressed | PASS |

## Known Limitations

- End-to-end testing against live BFF routes was not performed in this pass; the
  implementation follows the published TW-03 BFF contract and example payloads.
- If the live payload diverges from the synced contract, the bff-gap alert will fire
  in production as designed.
