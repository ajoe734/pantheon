# Management Console Route-Load Baseline — /management/evidence

Date: 2026-07-01T18:02:18.345Z
FE: https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io
FE commit: cbd833c49edc
BFF: https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io
Auth token shape: op-<id>:admin (dev stub-auth shape; not a production secret)
Navigation waitUntil: domcontentloaded (never `networkidle` — the shell opens `/bff/events/stream`, a long-lived SSE stream, so `networkidle` never resolves)

## Milestones (ms since navigation start)

| Milestone | ms |
|---|---:|
| domcontentloaded | 141 |
| shell (#root) attached | 262 |
| route heading visible | 555 |
| primary Evidence API (`/bff/management/evidence`) complete | 540 |
| first row or empty-state visible | 609 |

## Summary

- non-primary/BFF+FE requests observed before first row: 68
- total BFF/FE requests captured: 68
- used `networkidle` as readiness signal: false
- error: none
- pass: true

## Request waterfall (BFF + FE document/asset requests)

| Start ms | Duration ms | Status | Method | Path | Note |
|---:|---:|---|---|---|---|
| 7 | 32 | 200 | GET | /management/evidence |  |
| 57 | 60 | 200 | GET | /assets/index-Diifq-h-.js |  |
| 61 | 8 | 200 | GET | /assets/index-D_vlMmWJ.css |  |
| 186 | 44 | 200 | GET | /assets/PlatformShell-CzW2QV-F.js |  |
| 187 | 36 | 200 | GET | /assets/chevron-right-B9YeLyb_.js |  |
| 187 | 37 | 200 | GET | /assets/Combination-BVJAWctm.js |  |
| 187 | 37 | 200 | GET | /assets/dropdown-menu-BQ4YvE9I.js |  |
| 187 | 37 | 200 | GET | /assets/check-C7WdCi_Y.js |  |
| 188 | 42 | 200 | GET | /assets/circle-BtTJrFWO.js |  |
| 188 | 43 | 200 | GET | /assets/hooks-DdZgRRjC.js |  |
| 188 | 43 | 200 | GET | /assets/flask-conical-cNlrHAXH.js |  |
| 188 | 43 | 200 | GET | /assets/button-KYrhvRwT.js |  |
| 188 | 43 | 200 | GET | /assets/useTranslation-DBbUQY1j.js |  |
| 188 | 44 | 200 | GET | /assets/runtimeEnv-BGL7V4V0.js |  |
| 198 | 33 | 200 | GET | /assets/activity-CBd2yWHf.js |  |
| 198 | 33 | 200 | GET | /assets/progress-PVg0HGZN.js |  |
| 198 | 38 | 200 | GET | /assets/lists-DuhmXkHV.js |  |
| 198 | 50 | 200 | GET | /assets/seed-C6lLSeUd.js |  |
| 199 | 37 | 200 | GET | /assets/client-CC4pvLHp.js |  |
| 199 | 37 | 200 | GET | /assets/shield-alert-DZ0T29po.js |  |
| 199 | 37 | 200 | GET | /assets/paths-KG4AE-Mq.js |  |
| 199 | 39 | 200 | GET | /assets/index-COJ0bSjv.js |  |
| 199 | 39 | 200 | GET | /assets/tabs-BYYSXmqT.js |  |
| 199 | 39 | 200 | GET | /assets/triangle-alert-BelmTNPN.js |  |
| 199 | 39 | 200 | GET | /assets/dialog-BDHWxKxK.js |  |
| 199 | 39 | 200 | GET | /assets/index-PfZ7SJkh.js |  |
| 199 | 40 | 200 | GET | /assets/clipboard-check-BdLkygoI.js |  |
| 199 | 40 | 200 | GET | /assets/usePermissions-Dafl1bvF.js |  |
| 199 | 40 | 200 | GET | /assets/useLiveList-DyQhbbXT.js |  |
| 199 | 46 | 200 | GET | /assets/index-7iblkpY5.js |  |
| 199 | 47 | 200 | GET | /assets/index-D5U1VxeI.js |  |
| 200 | 45 | 200 | GET | /assets/channels-B1NVCEr3.js |  |
| 200 | 45 | 200 | GET | /assets/refresh-ccw-D_0hpHqV.js |  |
| 200 | 45 | 200 | GET | /assets/database-CSvDg1Nw.js |  |
| 200 | 45 | 200 | GET | /assets/liveTransport-DVjTJLVw.js |  |
| 200 | 45 | 200 | GET | /assets/search-zIe5ce8u.js |  |
| 200 | 45 | 200 | GET | /assets/routePrimaryReady-D7hLp1Lv.js |  |
| 200 | 46 | 200 | GET | /assets/input-BcJAcsym.js |  |
| 200 | 46 | 200 | GET | /assets/textarea-C9TUNfz4.js |  |
| 200 | 46 | 200 | GET | /assets/overlayStore-CCkUsldv.js |  |
| 200 | 46 | 200 | GET | /assets/separator-D-dWTexh.js |  |
| 200 | 49 | 200 | GET | /assets/select-CxlzVGu8.js |  |
| 300 | 8 | 200 | GET | /assets/minus-BSbfz9XY.js |  |
| 300 | 11 | 200 | GET | /assets/git-branch-DF2mTavz.js |  |
| 300 | 14 | 200 | GET | /assets/useAgentPanel-DW2C4GHQ.js |  |
| 300 | 14 | 200 | GET | /assets/ManagementLayout-C6fKmyxr.js |  |
| 300 | 14 | 200 | GET | /assets/scroll-text-VgFKa-jI.js |  |
| 301 | 11 | 200 | GET | /assets/shield-check-0VfK7ipJ.js |  |
| 301 | 13 | 200 | GET | /assets/clock-oDUu17zq.js |  |
| 301 | 13 | 200 | GET | /assets/key-round-DBxwL7Fn.js |  |
| 301 | 13 | 200 | GET | /assets/file-text-D3s-lPGR.js |  |
| 344 | 25 | 200 | GET | /assets/evidence-BayAZ5Nb.js |  |
| 345 | 17 | 200 | GET | /assets/card-CbvCn9HK.js |  |
| 345 | 23 | 200 | GET | /assets/links-DbT5T0iV.js |  |
| 345 | 24 | 200 | GET | /assets/quarterlyRanking-DM0_QwlP.js |  |
| 345 | 24 | 200 | GET | /assets/management-DguVokGq.js |  |
| 345 | 24 | 200 | GET | /assets/OpenClawLlmAuthPanel-BSZp9r5Q.js |  |
| 345 | 26 | 200 | GET | /assets/managementAi-C_QzPWXn.js |  |
| 346 | 23 | 200 | GET | /assets/circle-check--P7q5yHy.js |  |
| 346 | 23 | 200 | GET | /assets/external-link-BDKNTOUh.js |  |
| 346 | 23 | 200 | GET | /assets/useV5Live-BjGJ6eUh.js |  |
| 346 | 23 | 200 | GET | /assets/arrow-up-right-cRgLY0A3.js |  |
| 346 | 25 | 200 | GET | /assets/plus-DqHpcIxy.js |  |
| 404 | 133 | 200 | GET | /bff/me |  |
| 404 | 134 | 200 | GET | /health |  |
| 404 | 141 | 200 | GET | /bff/management/shell-summary |  |
| 406 | 132 | 200 | GET | /bff/management/evidence |  |
| 535 | n/a | n/a | GET | /bff/events/stream | realtime SSE stream; excluded from readiness milestones |

Full JSON: `route-timing-2026-07-01.json`, `request-waterfall-2026-07-01.json`