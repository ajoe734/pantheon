# MGMT-GATE-FULL-PASS-HOSTED-ACCEPTANCE-20261114 — Hosted Management Timing Crawl & Acceptance Evidence

## 1. Deployed Pair & Crawl Verdict

- **Hosted Dev Root Run**: [`45684275970`](https://github.com/ajoe734/pantheon/actions/runs/45684275970) (FE Gate [`45690103912`](https://github.com/ajoe734/execute-plans/actions/runs/45690103912))
- **Frontend (`ajoe734/execute-plans`)**: `38e5337d1f832a8582ce4eaac63cb5febbfb943e` (post-crawl: `38e5337d1f832a8582ce4eaac63cb5febbfb943e`)
- **Backend (`ajoe734/pantheon`)**: `b3f93d448f44aa426f3a4848c93df3e5a1ae8aa3` (post-crawl: `b3f93d448f44aa426f3a4848c93df3e5a1ae8aa3`)
- **Pair ID**: `4ee2000dceee2403c4e9b4297374c19e276ce6ebcc4a8060026faced28954d61` (`pair_stable_across_crawl=True`)
- **Crawl Window (UTC)**: `2026-11-26T02:37:36.306Z` → `2026-11-26T02:46:06.976Z`
- **Crawl Harness**: `execute-plans/scripts/accept-management-hosted-production.mjs` @ `38e5337d1f83` (`readOnly=true`, `PANTHEON_ACCEPT_CLICK_WRITE_CTAS=0`, `ROUTE_TIMEOUT_MS=45000`, `SETTLE_TIMEOUT_MS=20000`)
- **Routes Crawled**: `111` (`100` baseline + `11` live-id details)
- **Passing Routes**: `109/111` | **Failing Routes**: `2`
- **Pinned BFF Transport**: `pass=True` (`errors=0`)
- **Overall Harness Result**: `overall=fail` (`pass=False`)
- **Raw Crawl Report Digests**: `management-hosted-acceptance-2026-11-26.json` (`747225` bytes, SHA-256 `eccd11bbde7b20bcb74f74dbd427059b6bebfb580a05d095dcf22e93c6940380`), `management-hosted-acceptance-2026-11-26.md` (`100001` bytes, SHA-256 `44a7cd449a7fc4cb2e6350196ddec7919cf40cc7923d2884fcf8a6407cee2cf9`)

## 2. Prerequisite & Blocker Fix Inclusion in Deployed Pair

| Task ID | Repo | PR | Merge Commit | Deployed Ancestry |
|---|---|---:|---|---|
| `BFF-QUARTERLY-RANKING-READ-LATENCY-20261114` | `ajoe734/pantheon` | #7331 | `d7141eb45a60` | verified |
| `BFF-PERSONA-HEALTH-AND-LEAGUE-READS-20261114` | `ajoe734/pantheon` | #7336 | `a9284b68c115` | verified |
| `BFF-DEPLOYMENTS-LIST-STAGE-TRUTH-20261114` | `ajoe734/pantheon` | #7333 | `0f4b0bed66c1` | verified |
| `SOURCE-EVIDENCE-READ-COST-20261114` | `ajoe734/pantheon` | #7344 | `98b87aae226a` | verified |
| `FE-MGMT-DETAIL-NOT-FOUND-AND-AUTH-PROBE-20261114` | `ajoe734/execute-plans` | #1010 | `a47e6eecff4d` | verified |
| `FE-GATE-CRAWLER-ROUTE-ATTRIBUTION-20261114` | `ajoe734/execute-plans` | #1011 | `7a2f02e0bfde` | verified |
| `FE-CRAWL-CANONICAL-READ-ATTRIBUTION-20261114` | `ajoe734/execute-plans` | #1014 | `452d5494b01a` | verified |
| `FE-CRAWL-OPAQUE-DETAIL-ID-20261114` | `ajoe734/execute-plans` | #1015 | `25eb22021027` | verified |
| `FE-MGMT-CRAWL-SHADOW-AND-TRANSPORT-20261125` | `ajoe734/execute-plans` | #1084 | `535556719a11` | verified |
| `BFF-STRATEGIES-DEFAULT-PAGE-BOUNDED-READ-20261125` | `ajoe734/pantheon` | #7816 | `4fbb666ea29a` | verified |

## 3. Gate Checks & Live Entity Discovery

| Status | Gate Check | Note |
|---|---|---|
| `fail` | No route crashes, is blank, or fails to navigate. | `count:1` |
| `fail` | Every route and detail resolves auth/loading and completes its data checks. | `routes:2; discovery:0` |
| `pass` | No known alias direct-renders instead of redirecting to its canonical path. | `count:0` |
| `pass` | No detail route shows raw undefined/NaN/Invalid Date. | `count:0` |
| `pass` | No route claims seed fallback armed in strict-live mode. | `count:0` |
| `pass` | No route shows a mock/demo success claim as production truth. | `count:0` |
| `pass` | No CORS console errors on the hosted origin. | `count:0` |
| `fail` | No render-crash console errors. | `count:1` |
| `pass` | Session/RBAC: invalid session cannot read privileged management data. | `Authenticated /bff/me returns operator identity.:pass; Invalid/no-role token is rejected by /bff/me (401/403).:pass; Privileged management read is not served under an invalid session (fails closed).:pass` |
| `pass` | MGMT-LOAD-006/007 release-load-gate manifest reports result.pass=true. | `result.pass:true overall:pass failures:0 missing:0` |
| `warn` | Write-CTA source scan: toast.success() calls are backed by a governed/receipt signal (soft gate). | `ungoverned:25/39 (strict:false)` |

| Entity | Discovery Endpoint | HTTP | Live ID | Detail Route |
|---|---|---:|---|---|
| `strategies` | `/bff/strategies` | 200 | `strat-collector:collector-news-rss:news-cna-202611250274-aspx:bb683a5750b5-9ef1eb2f8462` | `/management/strategies/strat-collector:collector-news-rss:news-cna-202611250274-aspx:bb683a5750b5-9ef1eb2f8462` |
| `personas` | `/bff/personas` | 200 | `persona-d57f6d76254c20418a9e` | `/management/personas/persona-d57f6d76254c20418a9e` |
| `capital` | `/bff/capital-pools` | 200 | `hosted-all12-h12-01a11-20261023-9aeb-pool` | `/management/capital/hosted-all12-h12-01a11-20261023-9aeb-pool` |
| `ranking-formulas` | `/bff/ranking-formulas` | 200 | `pm12-quarterly-ranking-formula` | `/management/ranking/formulas/pm12-quarterly-ranking-formula` |
| `rebalance` | `/bff/rebalances` | 200 | `(none)` | `(none)` |
| `evolution` | `/bff/evolution-programs` | 200 | `evp-c0ce26091e8b443495bfa61e3dac34ff` | `/management/evolution/evp-c0ce26091e8b443495bfa61e3dac34ff` |
| `meetings` | `/bff/management/meetings` | 200 | `mtg-492d824b116f9ace` | `/management/meetings/mtg-492d824b116f9ace` |
| `experiments` | `/bff/research-tasks` | 200 | `rtask-20261125-014` | `/management/experiments/rtask-20261125-014` |
| `artifacts` | `/bff/artifacts` | 200 | `artifact-persona-pool-039ad01f8bcd2b4aa466-v2` | `/management/artifacts/artifact-persona-pool-039ad01f8bcd2b4aa466-v2` |
| `deployments` | `/bff/deployments` | 200 | `plan-persona-pool-039ad01f8bcd2b4aa466-v2` | `/management/deployments/plan-persona-pool-039ad01f8bcd2b4aa466-v2` |
| `incidents` | `/bff/incidents` | 200 | `agent-af7e6d72ed59-0006` | `/management/incidents/agent-af7e6d72ed59-0006` |
| `tools` | `/bff/tools` | 200 | `(none)` | `(none)` |
| `mcp` | `/bff/mcp-servers` | 200 | `(none)` | `(none)` |
| `mcp-tools` | `/bff/mcp-tools` | 200 | `(none)` | `(none)` |
| `skills` | `/bff/skills` | 200 | `(none)` | `(none)` |
| `channels` | `/bff/channels` | 200 | `approval` | `/management/channels/approval` |

## 4. Failure Attribution (Acceptance 3)

- **Route**: `/management/evolution-journal` (`status=crash`, `dataPass=False`, `dataReasons=[]`)
  - **Slowest / Driving Read**: `GET /bff/management/evolution-journal` (HTTP `200`, `7341 ms`)
  - **Console / Render Error**: `TypeError: L.target.version.toLowerCase is not a function
    at https://app.dev.mvl-cap.tw/assets/evidence-B91N_jnI.js:5:119748
    at Array.map (<anonymous>)
    at wd (https://app.dev.mvl-cap.tw/assets/evidence-B91N_jnI.js:5:119015)
    `
  - **Attributed Request Timeline**: `GET /bff/me` -> HTTP `200` (`59..272 ms`, attr=`True`), `GET /bff/auth/readiness` -> HTTP `200` (`59..378 ms`, attr=`True`), `GET /bff/me` -> HTTP `200` (`526..770 ms`, attr=`True`), `GET /bff/management/shell-summary` -> HTTP `200` (`526..3778 ms`, attr=`True`), `GET /health` -> HTTP `200` (`532..842 ms`, attr=`True`), `GET /bff/management/evolution-journal` -> HTTP `200` (`532..7873 ms`, attr=`True`), `GET /bff/management/persona-fleet` -> HTTP `200` (`532..3758 ms`, attr=`True`), `GET /bff/events/stream` -> HTTP `200` (`532..None ms`, attr=`True`)
- **Route**: `/management/human-inbox/readiness_blocker%3Apersona%3Apersona-tw-equity` (`status=datafail`, `dataPass=False`, `dataReasons=['rendered_data_error', 'no_completed_route_data', 'api_data_failure']`)
  - **Slowest / Driving Read**: `GET /bff/management/human-inbox/readiness_blocker%3Apersona%3Apersona-tw-equity` (HTTP `503`, `3893 ms`)
  - **Attributed Request Timeline**: `GET /bff/me` -> HTTP `200` (`81..942 ms`, attr=`True`), `GET /bff/auth/readiness` -> HTTP `200` (`81..465 ms`, attr=`True`), `GET /bff/me` -> HTTP `200` (`1216..1652 ms`, attr=`True`), `GET /bff/management/shell-summary` -> HTTP `200` (`1216..8142 ms`, attr=`True`), `GET /health` -> HTTP `200` (`1220..1760 ms`, attr=`True`), `GET /bff/management/human-inbox/readiness_blocker%3Apersona%3Apersona-tw-equity` -> HTTP `503` (`1220..5113 ms`, attr=`True`), `GET /bff/events/stream` -> HTTP `200` (`1233..None ms`, attr=`True`), `GET /health` -> HTTP `200` (`5128..5181 ms`, attr=`True`)

## 5. Per-Route Timing Crawl & Slowest Read Table (All Crawled Routes)

| # | Route | Kind / Source | Status | Data | Elapsed (ms) | Reads | Slowest Route Read (or Shell Fallback) | HTTP | Start..Finish (ms) | Read Duration (ms) |
|---:|---|---|---|---|---:|---:|---|---:|---:|---:|
| 1 | `/management/performance` | `nav` (baseline-or-live-nav) | `ok` | `True` | 6837 | 6 | `GET /bff/management/portfolio-book/holdings` | 200 | 2102..6783 | 4681 |
| 2 | `/management/performance?tab=attribution` | `detail-or-alias` (baseline-or-live-nav) | `ok` | `True` | 6364 | 9 | `GET /bff/management/performance-attribution` | 200 | 1316..6295 | 4979 |
| 3 | `/management/performance?tab=shadow` | `detail-or-alias` (baseline-or-live-nav) | `ok` | `True` | 753 | 5 | `GET /bff/me` | 200 | 52..600 | 548 |
| 4 | `/management/performance?tab=shadow&decision=dec_001` | `detail-or-alias` (baseline-or-live-nav) | `ok` | `True` | 2607 | 6 | `GET /bff/management/attribution/shadow/dec_001` | 404 | 1321..2563 | 1242 |
| 5 | `/management/rankings` | `nav` (baseline-or-live-nav) | `ok` | `True` | 7568 | 7 | `GET /bff/management/quarterly-ranking/recommendations` | 200 | 1047..7512 | 6465 |
| 6 | `/management/governance-decisions` | `nav` (baseline-or-live-nav) | `ok` | `True` | 3307 | 6 | `GET /bff/management/human-inbox` | 200 | 505..3263 | 2758 |
| 7 | `/management/cockpit` | `nav` (baseline-or-live-nav) | `ok` | `True` | 9766 | 15 | `GET /bff/management/quarterly-ranking` | 200 | 1024..9725 | 8701 |
| 8 | `/management/trade-journeys` | `nav` (baseline-or-live-nav) | `ok` | `True` | 17606 | 9 | `GET /bff/management/trade-journeys` | 200 | 6849..17548 | 10699 |
| 9 | `/management/persona-fleet` | `nav` (baseline-or-live-nav) | `ok` | `True` | 4986 | 6 | `GET /bff/management/persona-fleet` | 200 | 943..4898 | 3955 |
| 10 | `/management/human-inbox` | `nav` (baseline-or-live-nav) | `ok` | `True` | 1763 | 6 | `GET /bff/management/human-inbox` | 200 | 644..1719 | 1075 |
| 11 | `/management/trading-pulse` | `nav` (baseline-or-live-nav) | `ok` | `True` | 16333 | 7 | `GET /bff/management/trading-pulse` | 200 | 756..11817 | 11061 |
| 12 | `/management/evolution-journal` | `nav` (baseline-or-live-nav) | `crash` | `False` | 7921 | 7 | `GET /bff/management/evolution-journal` | 200 | 532..7873 | 7341 |
| 13 | `/management/evidence` | `nav` (baseline-or-live-nav) | `ok` | `True` | 1847 | 6 | `GET /bff/management/evidence` | 200 | 994..1761 | 767 |
| 14 | `/management/persona-intent` | `nav` (baseline-or-live-nav) | `ok` | `True` | 2713 | 6 | `GET /bff/management/persona-intent` | 200 | 599..2705 | 2106 |
| 15 | `/management/portfolio-book` | `detail-or-alias` (baseline-or-live-nav) | `ok` | `True` | 2219 | 6 | `GET /bff/management/portfolio-book/holdings` | 200 | 639..2170 | 1531 |
| 16 | `/management/persona-league` | `detail-or-alias` (baseline-or-live-nav) | `ok` | `True` | 7525 | 7 | `GET /bff/management/quarterly-ranking/recommendations` | 200 | 659..7475 | 6816 |
| 17 | `/management/quarterly-ranking` | `detail-or-alias` (baseline-or-live-nav) | `ok` | `True` | 3411 | 8 | `GET /bff/management/quarterly-ranking/recommendations` | 200 | 601..3338 | 2737 |
| 18 | `/management/performance-attribution` | `detail-or-alias` (baseline-or-live-nav) | `ok` | `True` | 5681 | 9 | `GET /bff/management/performance-attribution` | 200 | 1000..5634 | 4634 |
| 19 | `/management/readiness/ep5` | `nav` (baseline-or-live-nav) | `ok` | `True` | 1317 | 6 | `GET /bff/management/readiness/ep5` | 200 | 602..1236 | 634 |
| 20 | `/management/readiness/broker-live` | `nav` (baseline-or-live-nav) | `ok` | `True` | 1276 | 6 | `GET /bff/management/readiness/broker-live` | 200 | 822..1231 | 409 |
| 21 | `/management/readiness/capital-binding-live` | `nav` (baseline-or-live-nav) | `ok` | `True` | 1920 | 9 | `GET /bff/management/human-inbox` | 200 | 720..1873 | 1153 |
| 22 | `/management/readiness/bff-ha` | `nav` (baseline-or-live-nav) | `ok` | `True` | 1550 | 6 | `GET /bff/management/readiness/bff-ha` | 200 | 643..1511 | 868 |
| 23 | `/management/readiness/strict-publish` | `nav` (baseline-or-live-nav) | `ok` | `True` | 1521 | 6 | `GET /bff/management/readiness/strict-publish` | 200 | 820..1480 | 660 |
| 24 | `/management/strategies` | `nav` (baseline-or-live-nav) | `ok` | `True` | 10414 | 6 | `GET /bff/strategies` | 200 | 1240..10350 | 9110 |
| 25 | `/management/alpha-factory` | `nav` (baseline-or-live-nav) | `ok` | `True` | 8244 | 6 | `GET /bff/alpha-factory` | 200 | 708..8195 | 7487 |
| 26 | `/management/personas` | `nav` (baseline-or-live-nav) | `ok` | `True` | 4976 | 6 | `GET /bff/personas` | 200 | 815..4574 | 3759 |
| 27 | `/management/capital` | `detail-or-alias` (baseline-or-live-nav) | `ok` | `True` | 2581 | 6 | `GET /bff/management/portfolio-book/exposure` | 200 | 1249..2473 | 1224 |
| 28 | `/management/ranking` | `detail-or-alias` (baseline-or-live-nav) | `ok` | `True` | 1496 | 6 | `GET /bff/ranking-formulas` | 200 | 1088..1426 | 338 |
| 29 | `/management/rebalance` | `detail-or-alias` (baseline-or-live-nav) | `ok` | `True` | 2664 | 9 | `GET /bff/management/persona-fleet` | 200 | 823..2625 | 1802 |
| 30 | `/management/evolution` | `nav` (baseline-or-live-nav) | `ok` | `True` | 2728 | 7 | `GET /bff/management/evolution-qualification` | 200 | 724..2655 | 1931 |
| 31 | `/management/meetings` | `nav` (baseline-or-live-nav) | `ok` | `True` | 2676 | 10 | `GET /bff/management/meetings` | 200 | 908..2634 | 1726 |
| 32 | `/management/experiments` | `nav` (baseline-or-live-nav) | `ok` | `True` | 4207 | 7 | `GET /bff/research-tasks` | 200 | 920..4126 | 3206 |
| 33 | `/management/artifacts` | `nav` (baseline-or-live-nav) | `ok` | `True` | 9552 | 9 | `GET /bff/artifacts` | 200 | 2448..5223 | 2775 |
| 34 | `/management/lineage` | `nav` (baseline-or-live-nav) | `ok` | `True` | 5006 | 6 | `GET /bff/lineage` | 200 | 830..4796 | 3966 |
| 35 | `/management/loops` | `nav` (baseline-or-live-nav) | `ok` | `True` | 2401 | 7 | `GET /bff/v5/loop-runs` | 200 | 872..2332 | 1460 |
| 36 | `/management/deployments` | `nav` (baseline-or-live-nav) | `ok` | `True` | 3407 | 6 | `GET /bff/deployments` | 200 | 640..2756 | 2116 |
| 37 | `/management/runtimes` | `nav` (baseline-or-live-nav) | `ok` | `True` | 3485 | 7 | `GET /bff/management/persona-fleet` | 200 | 672..3044 | 2372 |
| 38 | `/management/risk` | `nav` (baseline-or-live-nav) | `ok` | `True` | 11807 | 15 | `GET /bff/strategies` | 200 | 749..11753 | 11004 |
| 39 | `/management/incidents` | `nav` (baseline-or-live-nav) | `ok` | `True` | 4861 | 6 | `GET /bff/incidents` | 200 | 1015..4757 | 3742 |
| 40 | `/management/jobs` | `nav` (baseline-or-live-nav) | `ok` | `True` | 9788 | 6 | `GET /bff/jobs` | 200 | 676..9709 | 9033 |
| 41 | `/management/alerts` | `nav` (baseline-or-live-nav) | `ok` | `True` | 2973 | 6 | `GET /bff/alerts` | 200 | 1032..2904 | 1872 |
| 42 | `/management/approvals` | `nav` (baseline-or-live-nav) | `ok` | `True` | 1325 | 6 | `GET /bff/approvals` | 200 | 767..1288 | 521 |
| 43 | `/management/governance` | `nav` (baseline-or-live-nav) | `ok` | `True` | 1243 | 6 | `GET /bff/approvals` | 200 | 718..1196 | 478 |
| 44 | `/management/governance/policies` | `nav` (baseline-or-live-nav) | `ok` | `True` | 4703 | 7 | `GET /bff/personas` | 200 | 999..4643 | 3644 |
| 45 | `/management/governance/permissions` | `nav` (baseline-or-live-nav) | `ok` | `True` | 1518 | 6 | `GET /bff/management/permissions` | 200 | 605..1492 | 887 |
| 46 | `/management/governance/memory` | `nav` (baseline-or-live-nav) | `ok` | `True` | 3452 | 7 | `GET /bff/personas` | 200 | 652..3339 | 2687 |
| 47 | `/management/governance/consult` | `nav` (baseline-or-live-nav) | `ok` | `True` | 2762 | 8 | `GET /bff/personas` | 200 | 558..2662 | 2104 |
| 48 | `/management/knowledge` | `nav` (baseline-or-live-nav) | `ok` | `True` | 1008 | 6 | `GET /bff/knowledge` | 200 | 517..948 | 431 |
| 49 | `/management/postmortems` | `nav` (baseline-or-live-nav) | `ok` | `True` | 2876 | 6 | `GET /api/v1/postmortems` | 200 | 811..2715 | 1904 |
| 50 | `/management/tools` | `nav` (baseline-or-live-nav) | `ok` | `True` | 1291 | 6 | `GET /bff/tools` | 200 | 626..1252 | 626 |
| 51 | `/management/mcp` | `nav` (baseline-or-live-nav) | `ok` | `True` | 1489 | 6 | `GET /bff/mcp-servers` | 200 | 1062..1432 | 370 |
| 52 | `/management/skills` | `nav` (baseline-or-live-nav) | `ok` | `True` | 1244 | 6 | `GET /bff/skills` | 200 | 709..1214 | 505 |
| 53 | `/management/workflows` | `nav` (baseline-or-live-nav) | `ok` | `True` | 1292 | 6 | `GET /bff/workflows` | 200 | 692..1260 | 568 |
| 54 | `/management/hooks` | `nav` (baseline-or-live-nav) | `ok` | `True` | 1089 | 6 | `GET /bff/hooks` | 200 | 658..1021 | 363 |
| 55 | `/management/channels` | `nav` (baseline-or-live-nav) | `ok` | `True` | 1028 | 6 | `GET /bff/channels` | 200 | 624..964 | 340 |
| 56 | `/management/llm-provider-auth` | `nav` (baseline-or-live-nav) | `ok` | `True` | 2702 | 8 | `GET /bff/assistant/providers/usage-summary` | 200 | 624..2656 | 2032 |
| 57 | `/management/data-sources` | `nav` (baseline-or-live-nav) | `ok` | `True` | 9663 | 8 | `GET /bff/agora/operational-readiness` | 200 | 773..9579 | 8806 |
| 58 | `/management/audit` | `nav` (baseline-or-live-nav) | `ok` | `True` | 1231 | 6 | `GET /bff/audit` | 200 | 598..1182 | 584 |
| 59 | `/management/settings` | `nav` (baseline-or-live-nav) | `ok` | `True` | 1009 | 5 | `GET /bff/auth/readiness` | 200 | 81..789 | 708 |
| 60 | `/management/loops/execution` | `detail-or-alias` (baseline-or-live-nav) | `ok` | `True` | 7591 | 7 | `GET /bff/v5/execution/persona-health` | 200 | 1115..7346 | 6231 |
| 61 | `/management/loops/optimization` | `detail-or-alias` (baseline-or-live-nav) | `ok` | `True` | 3155 | 6 | `GET /bff/v5/loop-runs` | 200 | 1083..3103 | 2020 |
| 62 | `/management/loops/research` | `detail-or-alias` (baseline-or-live-nav) | `ok` | `True` | 4418 | 7 | `GET /bff/management/persona-fleet` | 200 | 2529..4371 | 1842 |
| 63 | `/management/human-inbox/readiness_blocker%3Apersona%3Apersona-tw-equity` | `detail-or-alias` (baseline-or-live-nav) | `datafail` | `False` | 20075 | 7 | `GET /bff/management/human-inbox/readiness_blocker%3Apersona%3Apersona-tw-equity` | 503 | 1220..5113 | 3893 |
| 64 | `/management/evidence/evref-demo-readiness-001` | `detail-or-alias` (baseline-or-live-nav) | `ok` | `True` | 4766 | 6 | `GET /bff/management/evidence/evref-demo-readiness-001` | 404 | 1756..4704 | 2948 |
| 65 | `/management/evidence?ref_id=evref-demo-readiness-001` | `detail-or-alias` (baseline-or-live-nav) | `ok` | `True` | 3620 | 6 | `GET /bff/management/evidence/evref-demo-readiness-001` | 404 | 2674..3588 | 914 |
| 66 | `/management/persona-intent/trace-001` | `detail-or-alias` (baseline-or-live-nav) | `ok` | `True` | 2988 | 6 | `GET /bff/management/persona-intent` | 200 | 884..2956 | 2072 |
| 67 | `/management/strategies/stg_001` | `detail-or-alias` (baseline-or-live-nav) | `ok` | `True` | 14778 | 19 | `GET /bff/jobs` | 200 | 650..14734 | 14084 |
| 68 | `/management/personas/per_quant` | `detail-or-alias` (baseline-or-live-nav) | `ok` | `True` | 8420 | 11 | `GET /bff/strategies` | 200 | 626..8367 | 7741 |
| 69 | `/management/personas/per_quant/onboarding` | `detail-or-alias` (baseline-or-live-nav) | `ok` | `True` | 1226 | 6 | `GET /bff/personas/per_quant` | 404 | 461..1202 | 741 |
| 70 | `/management/capital/cp_alpha` | `detail-or-alias` (baseline-or-live-nav) | `ok` | `True` | 3239 | 8 | `GET /bff/management/persona-fleet` | 200 | 1212..3202 | 1990 |
| 71 | `/management/ranking/formulas` | `detail-or-alias` (baseline-or-live-nav) | `ok` | `True` | 2049 | 6 | `GET /bff/ranking-formulas` | 200 | 720..2000 | 1280 |
| 72 | `/management/ranking/formulas/rf_001` | `detail-or-alias` (baseline-or-live-nav) | `ok` | `True` | 1645 | 7 | `GET /bff/audit` | 200 | 791..1596 | 805 |
| 73 | `/management/rebalance/rb_q2_2026` | `detail-or-alias` (baseline-or-live-nav) | `ok` | `True` | 7916 | 9 | `GET /bff/strategies` | 200 | 1040..7876 | 6836 |
| 74 | `/management/evolution/ev_001` | `detail-or-alias` (baseline-or-live-nav) | `ok` | `True` | 3442 | 11 | `GET /bff/research-tasks` | 200 | 515..3423 | 2908 |
| 75 | `/management/meetings/mtg_001` | `detail-or-alias` (baseline-or-live-nav) | `ok` | `True` | 1544 | 8 | `GET /bff/management/meetings/mtg_001` | 404 | 586..1497 | 911 |
| 76 | `/management/experiments/rx_201` | `detail-or-alias` (baseline-or-live-nav) | `ok` | `True` | 930 | 6 | `GET /bff/research-tasks/rx_201` | 404 | 496..918 | 422 |
| 77 | `/management/artifacts/art_stg001_v320` | `detail-or-alias` (baseline-or-live-nav) | `ok` | `True` | 3823 | 8 | `GET /bff/deployments` | 200 | 506..3586 | 3080 |
| 78 | `/management/deployments/dp_001` | `detail-or-alias` (baseline-or-live-nav) | `ok` | `True` | 2686 | 9 | `GET /bff/runtimes` | 200 | 1082..2511 | 1429 |
| 79 | `/management/incidents/in_021` | `detail-or-alias` (baseline-or-live-nav) | `ok` | `True` | 7617 | 9 | `GET /bff/strategies` | 200 | 492..7563 | 7071 |
| 80 | `/management/governance/ap_301` | `detail-or-alias` (baseline-or-live-nav) | `ok` | `True` | 1536 | 7 | `GET /bff/approvals/ap_301` | 404 | 589..1505 | 916 |
| 81 | `/management/governance/policies/rp_quant_v2` | `detail-or-alias` (baseline-or-live-nav) | `ok` | `True` | 3447 | 7 | `GET /bff/personas` | 200 | 634..3381 | 2747 |
| 82 | `/management/tools/tl_market_data` | `detail-or-alias` (baseline-or-live-nav) | `ok` | `True` | 1032 | 7 | `GET /bff/audit` | 200 | 517..960 | 443 |
| 83 | `/management/mcp/mcp_alpha` | `detail-or-alias` (baseline-or-live-nav) | `ok` | `True` | 970 | 7 | `GET /bff/mcp-tools` | 200 | 584..907 | 323 |
| 84 | `/management/mcp-tools/mt_001` | `detail-or-alias` (baseline-or-live-nav) | `ok` | `True` | 994 | 6 | `GET /bff/mcp-tools/mt_001` | 404 | 573..979 | 406 |
| 85 | `/management/skills/sk_macro_brief` | `detail-or-alias` (baseline-or-live-nav) | `ok` | `True` | 4382 | 8 | `GET /bff/personas` | 200 | 739..4290 | 3551 |
| 86 | `/management/channels/ch_slack_alerts` | `detail-or-alias` (baseline-or-live-nav) | `ok` | `True` | 1237 | 7 | `GET /bff/audit` | 200 | 461..1176 | 715 |
| 87 | `/management/control-room` | `detail-or-alias` (baseline-or-live-nav) | `ok` | `True` | 5758 | 15 | `GET /bff/management/quarterly-ranking` | 200 | 537..5697 | 5160 |
| 88 | `/management/one-ring` | `detail-or-alias` (baseline-or-live-nav) | `ok` | `True` | 5142 | 15 | `GET /bff/management/quarterly-ranking` | 200 | 534..5082 | 4548 |
| 89 | `/management/overview` | `detail-or-alias` (baseline-or-live-nav) | `ok` | `True` | 11157 | 15 | `GET /bff/management/quarterly-ranking` | 200 | 552..11113 | 10561 |
| 90 | `/management/command-center` | `detail-or-alias` (baseline-or-live-nav) | `ok` | `True` | 5699 | 15 | `GET /bff/management/quarterly-ranking` | 200 | 678..5658 | 4980 |
| 91 | `/management/risk-center` | `detail-or-alias` (baseline-or-live-nav) | `ok` | `True` | 9184 | 15 | `GET /bff/strategies` | 200 | 469..9145 | 8676 |
| 92 | `/management/capital-pools` | `detail-or-alias` (baseline-or-live-nav) | `ok` | `True` | 6572 | 9 | `GET /bff/management/persona-fleet` | 200 | 543..6515 | 5972 |
| 93 | `/management/ranking-formulas` | `detail-or-alias` (baseline-or-live-nav) | `ok` | `True` | 939 | 6 | `GET /bff/ranking-formulas` | 200 | 658..903 | 245 |
| 94 | `/management/rebalances` | `detail-or-alias` (baseline-or-live-nav) | `ok` | `True` | 1873 | 9 | `GET /bff/management/human-inbox` | 200 | 692..1848 | 1156 |
| 95 | `/management/research` | `detail-or-alias` (baseline-or-live-nav) | `ok` | `True` | 2716 | 7 | `GET /bff/research-tasks` | 200 | 500..2666 | 2166 |
| 96 | `/management/deployment` | `detail-or-alias` (baseline-or-live-nav) | `ok` | `True` | 3682 | 6 | `GET /bff/deployments` | 200 | 697..3174 | 2477 |
| 97 | `/management/capital-pools/cp_alpha` | `detail-or-alias` (baseline-or-live-nav) | `ok` | `True` | 2676 | 8 | `GET /bff/management/persona-fleet` | 200 | 1281..2607 | 1326 |
| 98 | `/management/ranking-formulas/rf_001` | `detail-or-alias` (baseline-or-live-nav) | `ok` | `True` | 901 | 7 | `GET /bff/audit` | 200 | 555..853 | 298 |
| 99 | `/management/rebalances/rb_q2_2026` | `detail-or-alias` (baseline-or-live-nav) | `ok` | `True` | 7035 | 9 | `GET /bff/strategies` | 200 | 610..7018 | 6408 |
| 100 | `/management/research/rx_201` | `detail-or-alias` (baseline-or-live-nav) | `ok` | `True` | 939 | 6 | `GET /bff/research-tasks/rx_201` | 404 | 517..923 | 406 |
| 101 | `/management/strategies/strat-collector:collector-news-rss:news-cna-202611250274-aspx:bb683a5750b5-9ef1eb2f8462` | `live-id-detail` (live-id:strategies) | `ok` | `True` | 18792 | 19 | `GET /bff/jobs` | 200 | 559..18752 | 18193 |
| 102 | `/management/personas/persona-d57f6d76254c20418a9e` | `live-id-detail` (live-id:personas) | `ok` | `True` | 8700 | 11 | `GET /bff/strategies` | 200 | 672..8654 | 7982 |
| 103 | `/management/capital/hosted-all12-h12-01a11-20261023-9aeb-pool` | `live-id-detail` (live-id:capital) | `ok` | `True` | 7456 | 10 | `GET /bff/strategies` | 200 | 1150..7437 | 6287 |
| 104 | `/management/ranking/formulas/pm12-quarterly-ranking-formula` | `live-id-detail` (live-id:ranking-formulas) | `ok` | `True` | 1435 | 7 | `GET /bff/audit` | 200 | 549..1382 | 833 |
| 105 | `/management/evolution/evp-c0ce26091e8b443495bfa61e3dac34ff` | `live-id-detail` (live-id:evolution) | `ok` | `True` | 2349 | 11 | `GET /bff/research-tasks` | 200 | 594..2311 | 1717 |
| 106 | `/management/meetings/mtg-492d824b116f9ace` | `live-id-detail` (live-id:meetings) | `ok` | `True` | 986 | 8 | `GET /bff/management/meetings/mtg-492d824b116f9ace` | 200 | 401..940 | 539 |
| 107 | `/management/experiments/rtask-20261125-014` | `live-id-detail` (live-id:experiments) | `ok` | `True` | 2023 | 8 | `GET /bff/management/persona-fleet` | 200 | 890..1976 | 1086 |
| 108 | `/management/artifacts/artifact-persona-pool-039ad01f8bcd2b4aa466-v2` | `live-id-detail` (live-id:artifacts) | `ok` | `True` | 3788 | 8 | `GET /bff/artifacts/artifact-persona-pool-039ad01f8bcd2b4aa466-v2` | 200 | 965..3492 | 2527 |
| 109 | `/management/deployments/plan-persona-pool-039ad01f8bcd2b4aa466-v2` | `live-id-detail` (live-id:deployments) | `ok` | `True` | 2321 | 9 | `GET /bff/deployments/plan-persona-pool-039ad01f8bcd2b4aa466-v2` | 200 | 884..2268 | 1384 |
| 110 | `/management/incidents/agent-af7e6d72ed59-0006` | `live-id-detail` (live-id:incidents) | `ok` | `True` | 7235 | 9 | `GET /bff/strategies` | 200 | 421..7220 | 6799 |
| 111 | `/management/channels/approval` | `live-id-detail` (live-id:channels) | `ok` | `True` | 1187 | 7 | `GET /bff/audit` | 200 | 434..1129 | 695 |
