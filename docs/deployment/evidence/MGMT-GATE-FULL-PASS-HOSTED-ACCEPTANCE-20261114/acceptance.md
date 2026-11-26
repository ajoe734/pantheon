# MGMT-GATE-FULL-PASS-HOSTED-ACCEPTANCE-20261114 — Hosted Management Per-Request Timing Crawl & Acceptance Evidence

## 1. Executive Summary & Single-Deploy Full-Pass Identity (Acceptance 1)

- **Accepted Single-Deploy Full Pass — Hosted Dev Root [`45740904149`](https://github.com/ajoe734/pantheon/actions/runs/45740904149) (FE Gate [`45747260884`](https://github.com/ajoe734/execute-plans/actions/runs/45747260884), FE Switch [`45749895220`](https://github.com/ajoe734/execute-plans/actions/runs/45749895220))**:
  - **Deployed Pair**: BFF `8e67d240e034f4f133ffa926383b584536bc2e35` + FE `38e5337d1f832a8582ce4eaac63cb5febbfb943e` (`releaseCandidateId=923c2a3131e475fcf404e6f5ab4c11db4476eace48885a0648a6fd706c403b19`, `pairId=5b664426555909acda7a5db40b6b742ead1989c634698cc11851ee9932b6d218`, `deploymentState=accepted`, switched at `deployedAt=20261126T040009Z`, stable before and after crawl)
  - **Workflow Conclusions**: Root `45740904149` `completed` (`conclusion=success` at `2026-11-26T04:28:58Z`; `Deploy dev under shared environment lease` = `success`, `Admit and switch exact FE/BFF release` = `success`, `Post-deploy acceptance verdict` = `success`); FE Integration Gate `45747260884` `completed` (`conclusion=success`); FE Deploy `45749895220` `completed` (`conclusion=success`)
  - **Full-Pass Crawl Window (UTC)**: `2026-11-26T03:45:15.379Z` → `2026-11-26T03:52:47.238Z` (`readOnly=true`, `PANTHEON_ACCEPT_CLICK_WRITE_CTAS=0`, `ROUTE_TIMEOUT_MS=45000`, `SETTLE_TIMEOUT_MS=20000`, unloaded Dev VM load average `~0.86–1.42`)
  - **Single-Crawl Outcome**: **`111/111` routes passed (`0` failing routes)** in a single crawl (`result.pass=true`, `result.failures=[]`, `0` route crashes/blanks/navfails, `0` route or discovery data failures, `0` alias direct-render failures, `0` detail-honesty violations, `0` strict-live seed fallback claims, `0` mock/demo success claims, `0` CORS console errors, `0` render-crash console errors, `11/11` live entity IDs resolved, `pinnedTransport.pass=true`, `sessionRbac=pass`, `loadGate=pass`).
  - **Raw Report Digests**: JSON `745531` bytes (SHA-256 `ce70124a8d9a6b2129a7a04514d5846fe567f967589e998029f0a6ece3c45aae`), Markdown `99087` bytes (SHA-256 `7b9a61bfce55710af785e1cc55626f111e303a149eefdf14ef71793704b5c2cb`).
- **Historical Pre-Fix / Load-Contention Context (Retained for Acceptance 3 Traceability)**:
  - **Historical Crawl A — Root [`45684275970`](https://github.com/ajoe734/pantheon/actions/runs/45684275970) (`BFF b3f93d448f44 + FE 38e5337d1f83`)**: `109/111` passed (`2026-11-26T02:37:36.306Z` → `02:46:06.976Z`). Surfaced `/management/evolution-journal` integer `target.version` crash (fixed by `#7877` `BFF-EVO-JOURNAL-VERSION-STRING-20261126` in `8e67d240e034`) and transient `503` on `/management/human-inbox/readiness_blocker%3Apersona%3Apersona-tw-equity` during concurrent Docker build.
  - **Historical Crawl B — Initial Crawl on `8e67d240e034 + 38e5337d1f83` during Concurrent Runner Saturation (`03:05:37.460Z` → `03:15:04.667Z`)**: `107/111` passed while `45747260884` and concurrent worker activity saturated the Dev VM (`load average 12.99` at `03:13Z–03:15Z`), causing settle timeouts on 4 late detail routes (`67`, `101`, `102`, `103`). Rerunning the complete 111-route crawl on the unloaded Dev VM against the same Root `45740904149` pair (`8e67d240e034 + 38e5337d1f83`, which includes `#7858` `BFF-STRATEGY-DETAIL-OVERLAP-READS-20261125` and `#7877` `BFF-EVO-JOURNAL-VERSION-STRING-20261126`) passed **`111/111` routes (`0` failures)** in one single run.

## 2. Gate Checks on Accepted Single-Deploy Full Pass (`8e67d240e034 + 38e5337d1f83`)

| Status | Gate Check | Note |
|---|---|---|
| `pass` | No route crashes, is blank, or fails to navigate. | `count:0` |
| `pass` | Every route and detail resolves auth/loading and completes its data checks. | `routes:0; discovery:0` |
| `pass` | No known alias direct-renders instead of redirecting to its canonical path. | `count:0` |
| `pass` | No detail route shows raw undefined/NaN/Invalid Date. | `count:0` |
| `pass` | No route claims seed fallback armed in strict-live mode. | `count:0` |
| `pass` | No route shows a mock/demo success claim as production truth. | `count:0` |
| `pass` | No CORS console errors on the hosted origin. | `count:0` |
| `pass` | No render-crash console errors. | `count:0` |
| `pass` | Session/RBAC: invalid session cannot read privileged management data. | `Authenticated /bff/me returns operator identity.:pass; Invalid/no-role token is rejected by /bff/me (401/403).:pass; Privileged management read is not served under an invalid session (fails closed).:pass` |
| `pass` | MGMT-LOAD-006/007 release-load-gate manifest reports result.pass=true. | `result.pass:true overall:pass failures:0 missing:0` |
| `warn` | Write-CTA source scan: toast.success() calls are backed by a governed/receipt signal (soft gate). | `ungoverned:25/39 (strict:false)` |

## 3. Prerequisite & Blocker Fix Inclusion in Deployed Pair

| Task ID | Repo | PR | Merge Commit | In `b3f93d448f44` + `38e5337d1f83` | In `8e67d240e034` + `38e5337d1f83` (Root `45740904149`) |
|---|---|---:|---|---|---|
| `BFF-QUARTERLY-RANKING-READ-LATENCY-20261114` | `ajoe734/pantheon` | #7331 | `d7141eb45a60` | yes | yes |
| `BFF-PERSONA-HEALTH-AND-LEAGUE-READS-20261114` | `ajoe734/pantheon` | #7336 | `a9284b68c115` | yes | yes |
| `BFF-DEPLOYMENTS-LIST-STAGE-TRUTH-20261114` | `ajoe734/pantheon` | #7333 | `0f4b0bed66c1` | yes | yes |
| `SOURCE-EVIDENCE-READ-COST-20261114` | `ajoe734/pantheon` | #7344 | `98b87aae226a` | yes | yes |
| `FE-MGMT-DETAIL-NOT-FOUND-AND-AUTH-PROBE-20261114` | `ajoe734/execute-plans` | #1010 | `a47e6eecff4d` | yes | yes |
| `FE-GATE-CRAWLER-ROUTE-ATTRIBUTION-20261114` | `ajoe734/execute-plans` | #1011 | `7a2f02e0bfde` | yes | yes |
| `FE-CRAWL-CANONICAL-READ-ATTRIBUTION-20261114` | `ajoe734/execute-plans` | #1014 | `452d5494b01a` | yes | yes |
| `FE-CRAWL-OPAQUE-DETAIL-ID-20261114` | `ajoe734/execute-plans` | #1015 | `25eb22021027` | yes | yes |
| `FE-MGMT-CRAWL-SHADOW-AND-TRANSPORT-20261125` | `ajoe734/execute-plans` | #1084 | `535556719a11` | yes | yes |
| `BFF-STRATEGIES-DEFAULT-PAGE-BOUNDED-READ-20261125` | `ajoe734/pantheon` | #7816 | `4fbb666ea29a` | yes | yes |
| `BFF-STRATEGY-DETAIL-OVERLAP-READS-20261125` | `ajoe734/pantheon` | #7858 | `4d88a04b9bc7` | yes | yes |
| `BFF-EVO-JOURNAL-VERSION-STRING-20261126` | `ajoe734/pantheon` | #7877 | `8d7ec076e7d7` | no | yes |

## 4. Live Entity Discovery (11/11 Resolved on Accepted Full-Pass Crawl)

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
| `incidents` | `/bff/incidents` | 200 | `agent-c0be28c2c638-0056` | `/management/incidents/agent-c0be28c2c638-0056` |
| `tools` | `/bff/tools` | 200 | `(none)` | `(none)` |
| `mcp` | `/bff/mcp-servers` | 200 | `(none)` | `(none)` |
| `mcp-tools` | `/bff/mcp-tools` | 200 | `(none)` | `(none)` |
| `skills` | `/bff/skills` | 200 | `(none)` | `(none)` |
| `channels` | `/bff/channels` | 200 | `approval` | `/management/channels/approval` |

## 5. Historical Failure Attribution & Resolution Verification in Full Pass (Acceptance 3)

All `111/111` routes passed (`0` failures) in the accepted single-deploy full-pass crawl on Root `45740904149` (`BFF 8e67d240e034 + FE 38e5337d1f83`). The table below retains the named route/request attributions from the earlier pre-fix Crawl A (`45684275970`) and load-contention Crawl B and shows their exact verified measurements in the accepted `111/111` single-crawl full pass:

- **`/management/evolution-journal`** (Historical `Root 45684275970 (BFF b3f93d448f44 + FE 38e5337d1f83)`, historical `status=crash`, `dataPass=False`) — Historical Driving Request: `GET /bff/management/evolution-journal` (HTTP `200`, `7341 ms`). GET /bff/management/evolution-journal (HTTP 200, 7341 ms) emitted integer target.version (6) on pool_version items from #7832, crashing FE 38e5337d1f83 at target.version.toLowerCase(). Fixed by BFF-EVO-JOURNAL-VERSION-STRING-20261126 (PR #7877, 8d7ec076e) and FE-EVO-JOURNAL-VERSION-GUARD-20261126 (PR #1101, 24a9ecc82); verified passing on Root 45740904149 BFF 8e67d240e034 + FE 38e5337d1f83 (status=ok, dataPass=true, GET /bff/management/evolution-journal HTTP 200 in 1295 ms, 0 console errors). **Verified passing in accepted single-deploy full-pass crawl (`status=ok`, `dataPass=true`, slowest read `GET /bff/management/evolution-journal` HTTP `200` in `4402 ms`, total route elapsed `5285 ms`).**
- **`/management/human-inbox/readiness_blocker%3Apersona%3Apersona-tw-equity`** (Historical `Root 45684275970 (BFF b3f93d448f44 + FE 38e5337d1f83)`, historical `status=datafail`, `dataPass=False`) — Historical Driving Request: `GET /bff/management/human-inbox/readiness_blocker%3Apersona%3Apersona-tw-equity` (HTTP `503`, `3893 ms`). GET /bff/management/human-inbox/readiness_blocker%3Apersona%3Apersona-tw-equity (fixture not-found ID, HTTP 503, 3893 ms) calls get_human_inbox(page_size=2000) across all contributors and returns 503 human_inbox_partial_read when any contributor times out if the item is not found. During concurrent Root 45740904149 docker compose build on the Dev VM, one contributor timed out; verified passing on BFF 8e67d240e034 + FE 38e5337d1f83 (status=ok, dataPass=true, GET /bff/management/human-inbox/readiness_blocker%3Apersona%3Apersona-tw-equity HTTP 404 honest not-found in 2006 ms, and direct readback /bff/management/human-inbox HTTP 200 with all 6 surfaces ok). **Verified passing in accepted single-deploy full-pass crawl (`status=ok`, `dataPass=true`, slowest read `GET /bff/management/human-inbox/readiness_blocker%3Apersona%3Apersona-tw-equity` HTTP `404` in `1028 ms`, total route elapsed `1719 ms`).**
- **`/management/strategies/stg_001`** (Historical `Root 45740904149 (BFF 8e67d240e034 + FE 38e5337d1f83) during concurrent self-hosted FE gate run 45747260884`, historical `status=datafail`, `dataPass=False`) — Historical Driving Request: `GET /bff/research-tasks` (HTTP `200`, `8267 ms`). Passed on Root 45684275970 (BFF b3f93d448f44 + FE 38e5337d1f83: status=ok, dataPass=true). During the tail of the 8e67d240e034 crawl (03:13Z-03:15Z), self-hosted runner job 45747260884 started on the Dev VM (load average 12.99) while FE 38e5337d1f83 StrategyDetail eagerly fired 14 concurrent BFF reads on mount and GET /bff/jobs did not finish within the 20s settle window (already addressed on execute-plans dev by FE-STRATEGY-DETAIL-PRIMARY-FIRST-20261125 PR #1094 a013f969130d and pantheon BFF-STRATEGY-DETAIL-OVERLAP-READS-20261125). **Verified passing in accepted single-deploy full-pass crawl (`status=ok`, `dataPass=true`, slowest read `GET /bff/jobs` HTTP `200` in `13244 ms`, total route elapsed `14304 ms`).**
- **`/management/strategies/strat-collector:collector-news-rss:news-cna-202611250274-aspx:bb683a5750b5-9ef1eb2f8462`** (Historical `Root 45740904149 (BFF 8e67d240e034 + FE 38e5337d1f83) during concurrent self-hosted FE gate run 45747260884`, historical `status=datafail`, `dataPass=False`) — Historical Driving Request: `GET /bff/artifacts` (HTTP `200`, `7513 ms`). Passed on Root 45684275970 (BFF b3f93d448f44 + FE 38e5337d1f83: status=ok, dataPass=true). During the tail of the 8e67d240e034 crawl (03:13Z-03:15Z), self-hosted runner job 45747260884 started on the Dev VM (load average 12.99) while FE 38e5337d1f83 StrategyDetail eagerly fired 14 concurrent BFF reads on mount and GET /bff/jobs did not finish within the 20s settle window (already addressed on execute-plans dev by FE-STRATEGY-DETAIL-PRIMARY-FIRST-20261125 PR #1094 a013f969130d and pantheon BFF-STRATEGY-DETAIL-OVERLAP-READS-20261125). **Verified passing in accepted single-deploy full-pass crawl (`status=ok`, `dataPass=true`, slowest read `GET /bff/jobs` HTTP `200` in `12556 ms`, total route elapsed `13115 ms`).**
- **`/management/personas/persona-d57f6d76254c20418a9e`** (Historical `Root 45740904149 (BFF 8e67d240e034 + FE 38e5337d1f83) during concurrent self-hosted FE gate run 45747260884`, historical `status=datafail`, `dataPass=False`) — Historical Driving Request: `GET /bff/audit` (HTTP `200`, `15744 ms`). Passed on Root 45684275970 (BFF b3f93d448f44 + FE 38e5337d1f83: status=ok, dataPass=true). Timed out past the 20s settle window at 03:14Z-03:15Z only while self-hosted runner job 45747260884 (Release candidate 923c2a31...) saturated the Dev VM (load average 12.99). **Verified passing in accepted single-deploy full-pass crawl (`status=ok`, `dataPass=true`, slowest read `GET /bff/strategies` HTTP `200` in `7335 ms`, total route elapsed `7869 ms`).**
- **`/management/capital/hosted-all12-h12-01a11-20261023-9aeb-pool`** (Historical `Root 45740904149 (BFF 8e67d240e034 + FE 38e5337d1f83) during concurrent self-hosted FE gate run 45747260884`, historical `status=datafail`, `dataPass=False`) — Historical Driving Request: `GET /bff/capital-pools/hosted-all12-h12-01a11-20261023-9aeb-pool` (HTTP `None`, `None ms`). Passed on Root 45684275970 (BFF b3f93d448f44 + FE 38e5337d1f83: status=ok, dataPass=true). Timed out past the 20s settle window at 03:14Z-03:15Z only while self-hosted runner job 45747260884 (Release candidate 923c2a31...) saturated the Dev VM (load average 12.99). **Verified passing in accepted single-deploy full-pass crawl (`status=ok`, `dataPass=true`, slowest read `GET /bff/strategies` HTTP `200` in `9571 ms`, total route elapsed `10495 ms`).**
- **Residual Finding Outside Scope (Recorded per AGENTS.md Remediation Plan 2026-11-17)**:
  - `RESIDUAL-ACCEPTANCE-STAGE-VIEWER-APPROVALS-403-20261126` (RC1 WP1.2 / RC6 WP6.5, owning task `ACCEPTANCE-STAGE-POST-SWITCH-CRAWL-20261126`): In Root `45740904149`'s advisory `Post-deploy acceptance verdict` job (`139687776343`, artifact `dev-acceptance-verdict-45740904149-1` ID `15086869630`), `check:management-crawl` (`scripts/dev_acceptance_stage.py`) ran `accept-management-hosted-production.mjs` with `DEV_BFF_DEV_LOGIN_VIEWER_CLIENT_SECRET` (`identity=viewer`) rather than the read-only operator probe credential (`DEV_BFF_DEV_LOGIN_OPERATOR_A_CLIENT_ID`) used by `pantheon-integration-gate.yml`. While `/management/evolution-journal` passed (`status=ok`, `dataPass=true`) in both crawls, `viewer` tokens receive HTTP `403` (`Approval read role required`) on `GET /bff/approvals` (and `/bff/runtimes`), causing `accept-management-hosted-production.mjs` to mark routes querying approvals/runtimes as `api_data_failure` under viewer auth even though all `111/111` routes pass (`0` failures) under operator read-only auth.

## 6. Per-Route Timing Crawl & Slowest Read Table (All 111 Crawled Routes — Single-Deploy Full Pass on Root `45740904149`)

| # | Route | Kind | Full-Pass (`8e67d240e034`) Status / Elapsed | Slowest Route Read (or Shell Fallback) | HTTP | Start..Finish (ms) | Read (ms) | Historical Crawl A (`b3f93d448f44`) Status / Slowest Read (ms) |
|---:|---|---|---|---|---:|---:|---:|---|
| 1 | `/management/performance` | `nav` | `ok` (6143ms) | `GET /bff/management/portfolio-book/holdings` | 200 | 2965..6093 | 3128 | `ok` (`GET /bff/management/portfolio-book/holdings` 200, 4681ms) |
| 2 | `/management/performance?tab=attribution` | `detail-or-alias` | `ok` (4533ms) | `GET /bff/management/performance-attribution` | 200 | 866..4480 | 3614 | `ok` (`GET /bff/management/performance-attribution` 200, 4979ms) |
| 3 | `/management/performance?tab=shadow` | `detail-or-alias` | `ok` (444ms) | `GET /bff/me` | 200 | 45..324 | 279 | `ok` (`GET /bff/me` 200, 548ms) |
| 4 | `/management/performance?tab=shadow&decision=dec_001` | `detail-or-alias` | `ok` (2572ms) | `GET /bff/management/attribution/shadow/dec_001` | 404 | 674..2543 | 1869 | `ok` (`GET /bff/management/attribution/shadow/dec_001` 404, 1242ms) |
| 5 | `/management/rankings` | `nav` | `ok` (5050ms) | `GET /bff/management/quarterly-ranking/recommendations` | 200 | 453..4999 | 4546 | `ok` (`GET /bff/management/quarterly-ranking/recommendations` 200, 6465ms) |
| 6 | `/management/governance-decisions` | `nav` | `ok` (1567ms) | `GET /bff/management/human-inbox` | 200 | 619..1553 | 934 | `ok` (`GET /bff/management/human-inbox` 200, 2758ms) |
| 7 | `/management/cockpit` | `nav` | `ok` (5165ms) | `GET /bff/management/quarterly-ranking` | 200 | 573..5104 | 4531 | `ok` (`GET /bff/management/quarterly-ranking` 200, 8701ms) |
| 8 | `/management/trade-journeys` | `nav` | `ok` (3703ms) | `GET /bff/management/trade-journeys` | 200 | 481..2327 | 1846 | `ok` (`GET /bff/management/trade-journeys` 200, 10699ms) |
| 9 | `/management/persona-fleet` | `nav` | `ok` (1559ms) | `GET /bff/management/persona-fleet` | 200 | 354..1511 | 1157 | `ok` (`GET /bff/management/persona-fleet` 200, 3955ms) |
| 10 | `/management/human-inbox` | `nav` | `ok` (1462ms) | `GET /bff/management/human-inbox` | 200 | 378..1431 | 1053 | `ok` (`GET /bff/management/human-inbox` 200, 1075ms) |
| 11 | `/management/trading-pulse` | `nav` | `ok` (12855ms) | `GET /bff/management/trading-pulse` | 200 | 988..9767 | 8779 | `ok` (`GET /bff/management/trading-pulse` 200, 11061ms) |
| 12 | `/management/evolution-journal` | `nav` | `ok` (5285ms) | `GET /bff/management/evolution-journal` | 200 | 828..5230 | 4402 | `crash` (`GET /bff/management/evolution-journal` 200, 7341ms) |
| 13 | `/management/evidence` | `nav` | `ok` (748ms) | `GET /bff/management/evidence` | 200 | 315..684 | 369 | `ok` (`GET /bff/management/evidence` 200, 767ms) |
| 14 | `/management/persona-intent` | `nav` | `ok` (2200ms) | `GET /bff/management/persona-intent` | 200 | 366..2177 | 1811 | `ok` (`GET /bff/management/persona-intent` 200, 2106ms) |
| 15 | `/management/portfolio-book` | `detail-or-alias` | `ok` (866ms) | `GET /bff/management/portfolio-book/holdings` | 200 | 364..801 | 437 | `ok` (`GET /bff/management/portfolio-book/holdings` 200, 1531ms) |
| 16 | `/management/persona-league` | `detail-or-alias` | `ok` (9089ms) | `GET /bff/management/quarterly-ranking/recommendations` | 200 | 362..9036 | 8674 | `ok` (`GET /bff/management/quarterly-ranking/recommendations` 200, 6816ms) |
| 17 | `/management/quarterly-ranking` | `detail-or-alias` | `ok` (4240ms) | `GET /bff/management/quarterly-ranking/recommendations` | 200 | 816..4181 | 3365 | `ok` (`GET /bff/management/quarterly-ranking/recommendations` 200, 2737ms) |
| 18 | `/management/performance-attribution` | `detail-or-alias` | `ok` (3820ms) | `GET /bff/management/performance-attribution` | 200 | 600..3791 | 3191 | `ok` (`GET /bff/management/performance-attribution` 200, 4634ms) |
| 19 | `/management/readiness/ep5` | `nav` | `ok` (1156ms) | `GET /bff/management/readiness/ep5` | 200 | 360..1120 | 760 | `ok` (`GET /bff/management/readiness/ep5` 200, 634ms) |
| 20 | `/management/readiness/broker-live` | `nav` | `ok` (1031ms) | `GET /bff/management/readiness/broker-live` | 200 | 453..998 | 545 | `ok` (`GET /bff/management/readiness/broker-live` 200, 409ms) |
| 21 | `/management/readiness/capital-binding-live` | `nav` | `ok` (2220ms) | `GET /bff/management/human-inbox` | 200 | 662..2173 | 1511 | `ok` (`GET /bff/management/human-inbox` 200, 1153ms) |
| 22 | `/management/readiness/bff-ha` | `nav` | `ok` (885ms) | `GET /bff/management/readiness/bff-ha` | 200 | 356..833 | 477 | `ok` (`GET /bff/management/readiness/bff-ha` 200, 868ms) |
| 23 | `/management/readiness/strict-publish` | `nav` | `ok` (1359ms) | `GET /bff/management/readiness/strict-publish` | 200 | 634..1333 | 699 | `ok` (`GET /bff/management/readiness/strict-publish` 200, 660ms) |
| 24 | `/management/strategies` | `nav` | `ok` (5799ms) | `GET /bff/strategies` | 200 | 451..5778 | 5327 | `ok` (`GET /bff/strategies` 200, 9110ms) |
| 25 | `/management/alpha-factory` | `nav` | `ok` (13114ms) | `GET /bff/alpha-factory` | 200 | 466..12990 | 12524 | `ok` (`GET /bff/alpha-factory` 200, 7487ms) |
| 26 | `/management/personas` | `nav` | `ok` (8786ms) | `GET /bff/personas` | 200 | 1223..8602 | 7379 | `ok` (`GET /bff/personas` 200, 3759ms) |
| 27 | `/management/capital` | `detail-or-alias` | `ok` (5347ms) | `GET /bff/management/portfolio-book/exposure` | 200 | 726..5289 | 4563 | `ok` (`GET /bff/management/portfolio-book/exposure` 200, 1224ms) |
| 28 | `/management/ranking` | `detail-or-alias` | `ok` (2965ms) | `GET /bff/ranking-formulas` | 200 | 1433..2928 | 1495 | `ok` (`GET /bff/ranking-formulas` 200, 338ms) |
| 29 | `/management/rebalance` | `detail-or-alias` | `ok` (6879ms) | `GET /bff/management/persona-fleet` | 200 | 825..6826 | 6001 | `ok` (`GET /bff/management/persona-fleet` 200, 1802ms) |
| 30 | `/management/evolution` | `nav` | `ok` (6769ms) | `GET /bff/management/evolution-qualification` | 200 | 1790..6705 | 4915 | `ok` (`GET /bff/management/evolution-qualification` 200, 1931ms) |
| 31 | `/management/meetings` | `nav` | `ok` (4343ms) | `GET /bff/management/meetings` | 200 | 1281..4307 | 3026 | `ok` (`GET /bff/management/meetings` 200, 1726ms) |
| 32 | `/management/experiments` | `nav` | `ok` (17134ms) | `GET /bff/research-tasks` | 200 | 1740..17098 | 15358 | `ok` (`GET /bff/research-tasks` 200, 3206ms) |
| 33 | `/management/artifacts` | `nav` | `ok` (13524ms) | `GET /bff/artifacts` | 200 | 1390..6543 | 5153 | `ok` (`GET /bff/artifacts` 200, 2775ms) |
| 34 | `/management/lineage` | `nav` | `ok` (7018ms) | `GET /bff/lineage` | 200 | 842..6916 | 6074 | `ok` (`GET /bff/lineage` 200, 3966ms) |
| 35 | `/management/loops` | `nav` | `ok` (2173ms) | `GET /bff/v5/loop-runs` | 200 | 621..2011 | 1390 | `ok` (`GET /bff/v5/loop-runs` 200, 1460ms) |
| 36 | `/management/deployments` | `nav` | `ok` (2088ms) | `GET /bff/deployments` | 200 | 520..1861 | 1341 | `ok` (`GET /bff/deployments` 200, 2116ms) |
| 37 | `/management/runtimes` | `nav` | `ok` (8422ms) | `GET /bff/management/persona-fleet` | 200 | 463..8117 | 7654 | `ok` (`GET /bff/management/persona-fleet` 200, 2372ms) |
| 38 | `/management/risk` | `nav` | `ok` (8899ms) | `GET /bff/strategies` | 200 | 558..8862 | 8304 | `ok` (`GET /bff/strategies` 200, 11004ms) |
| 39 | `/management/incidents` | `nav` | `ok` (2779ms) | `GET /bff/incidents` | 200 | 512..2701 | 2189 | `ok` (`GET /bff/incidents` 200, 3742ms) |
| 40 | `/management/jobs` | `nav` | `ok` (8578ms) | `GET /bff/jobs` | 200 | 897..8541 | 7644 | `ok` (`GET /bff/jobs` 200, 9033ms) |
| 41 | `/management/alerts` | `nav` | `ok` (2234ms) | `GET /bff/alerts` | 200 | 662..2174 | 1512 | `ok` (`GET /bff/alerts` 200, 1872ms) |
| 42 | `/management/approvals` | `nav` | `ok` (1301ms) | `GET /bff/approvals` | 200 | 816..1283 | 467 | `ok` (`GET /bff/approvals` 200, 521ms) |
| 43 | `/management/governance` | `nav` | `ok` (928ms) | `GET /bff/approvals` | 200 | 399..877 | 478 | `ok` (`GET /bff/approvals` 200, 478ms) |
| 44 | `/management/governance/policies` | `nav` | `ok` (1940ms) | `GET /bff/personas` | 200 | 482..1896 | 1414 | `ok` (`GET /bff/personas` 200, 3644ms) |
| 45 | `/management/governance/permissions` | `nav` | `ok` (957ms) | `GET /bff/management/permissions` | 200 | 491..927 | 436 | `ok` (`GET /bff/management/permissions` 200, 887ms) |
| 46 | `/management/governance/memory` | `nav` | `ok` (2983ms) | `GET /bff/personas` | 200 | 494..2912 | 2418 | `ok` (`GET /bff/personas` 200, 2687ms) |
| 47 | `/management/governance/consult` | `nav` | `ok` (2589ms) | `GET /bff/personas` | 200 | 890..2530 | 1640 | `ok` (`GET /bff/personas` 200, 2104ms) |
| 48 | `/management/knowledge` | `nav` | `ok` (792ms) | `GET /bff/knowledge` | 200 | 445..754 | 309 | `ok` (`GET /bff/knowledge` 200, 431ms) |
| 49 | `/management/postmortems` | `nav` | `ok` (2146ms) | `GET /api/v1/postmortems` | 200 | 805..2058 | 1253 | `ok` (`GET /api/v1/postmortems` 200, 1904ms) |
| 50 | `/management/tools` | `nav` | `ok` (1024ms) | `GET /bff/tools` | 200 | 481..1006 | 525 | `ok` (`GET /bff/tools` 200, 626ms) |
| 51 | `/management/mcp` | `nav` | `ok` (1510ms) | `GET /bff/mcp-servers` | 200 | 481..1480 | 999 | `ok` (`GET /bff/mcp-servers` 200, 370ms) |
| 52 | `/management/skills` | `nav` | `ok` (1175ms) | `GET /bff/skills` | 200 | 493..1114 | 621 | `ok` (`GET /bff/skills` 200, 505ms) |
| 53 | `/management/workflows` | `nav` | `ok` (908ms) | `GET /bff/workflows` | 200 | 502..853 | 351 | `ok` (`GET /bff/workflows` 200, 568ms) |
| 54 | `/management/hooks` | `nav` | `ok` (1165ms) | `GET /bff/hooks` | 200 | 478..1116 | 638 | `ok` (`GET /bff/hooks` 200, 363ms) |
| 55 | `/management/channels` | `nav` | `ok` (1082ms) | `GET /bff/channels` | 200 | 569..1034 | 465 | `ok` (`GET /bff/channels` 200, 340ms) |
| 56 | `/management/llm-provider-auth` | `nav` | `ok` (1462ms) | `GET /bff/assistant/providers` | 200 | 578..1434 | 856 | `ok` (`GET /bff/assistant/providers/usage-summary` 200, 2032ms) |
| 57 | `/management/data-sources` | `nav` | `ok` (6137ms) | `GET /bff/agora/operational-readiness` | 200 | 358..6066 | 5708 | `ok` (`GET /bff/agora/operational-readiness` 200, 8806ms) |
| 58 | `/management/audit` | `nav` | `ok` (826ms) | `GET /bff/audit` | 200 | 479..767 | 288 | `ok` (`GET /bff/audit` 200, 584ms) |
| 59 | `/management/settings` | `nav` | `ok` (560ms) | `GET /bff/auth/readiness` | 200 | 81..357 | 276 | `ok` (`GET /bff/auth/readiness` 200, 708ms) |
| 60 | `/management/loops/execution` | `detail-or-alias` | `ok` (4100ms) | `GET /bff/v5/execution/persona-health` | 200 | 658..3936 | 3278 | `ok` (`GET /bff/v5/execution/persona-health` 200, 6231ms) |
| 61 | `/management/loops/optimization` | `detail-or-alias` | `ok` (1719ms) | `GET /bff/v5/loop-runs` | 200 | 1245..1670 | 425 | `ok` (`GET /bff/v5/loop-runs` 200, 2020ms) |
| 62 | `/management/loops/research` | `detail-or-alias` | `ok` (7899ms) | `GET /bff/management/persona-fleet` | 200 | 561..7830 | 7269 | `ok` (`GET /bff/management/persona-fleet` 200, 1842ms) |
| 63 | `/management/human-inbox/readiness_blocker%3Apersona%3Apersona-tw-equity` | `detail-or-alias` | `ok` (1719ms) | `GET /bff/management/human-inbox/readiness_blocker%3Apersona%3Apersona-tw-equity` | 404 | 652..1680 | 1028 | `datafail` (`GET /bff/management/human-inbox/readiness_blocker%3Apersona%3Apersona-tw-equity` 503, 3893ms) |
| 64 | `/management/evidence/evref-demo-readiness-001` | `detail-or-alias` | `ok` (997ms) | `GET /bff/management/evidence/evref-demo-readiness-001` | 404 | 555..953 | 398 | `ok` (`GET /bff/management/evidence/evref-demo-readiness-001` 404, 2948ms) |
| 65 | `/management/evidence?ref_id=evref-demo-readiness-001` | `detail-or-alias` | `ok` (862ms) | `GET /bff/management/evidence/evref-demo-readiness-001` | 404 | 467..825 | 358 | `ok` (`GET /bff/management/evidence/evref-demo-readiness-001` 404, 914ms) |
| 66 | `/management/persona-intent/trace-001` | `detail-or-alias` | `ok` (2742ms) | `GET /bff/management/persona-intent` | 200 | 496..2721 | 2225 | `ok` (`GET /bff/management/persona-intent` 200, 2072ms) |
| 67 | `/management/strategies/stg_001` | `detail-or-alias` | `ok` (14304ms) | `GET /bff/jobs` | 200 | 1014..14258 | 13244 | `ok` (`GET /bff/jobs` 200, 14084ms) |
| 68 | `/management/personas/per_quant` | `detail-or-alias` | `ok` (6908ms) | `GET /bff/strategies` | 200 | 464..6892 | 6428 | `ok` (`GET /bff/strategies` 200, 7741ms) |
| 69 | `/management/personas/per_quant/onboarding` | `detail-or-alias` | `ok` (1276ms) | `GET /bff/personas/per_quant` | 404 | 482..1230 | 748 | `ok` (`GET /bff/personas/per_quant` 404, 741ms) |
| 70 | `/management/capital/cp_alpha` | `detail-or-alias` | `ok` (1969ms) | `GET /bff/management/persona-fleet` | 200 | 855..1927 | 1072 | `ok` (`GET /bff/management/persona-fleet` 200, 1990ms) |
| 71 | `/management/ranking/formulas` | `detail-or-alias` | `ok` (789ms) | `GET /bff/ranking-formulas` | 200 | 523..758 | 235 | `ok` (`GET /bff/ranking-formulas` 200, 1280ms) |
| 72 | `/management/ranking/formulas/rf_001` | `detail-or-alias` | `ok` (1274ms) | `GET /bff/audit` | 200 | 834..1217 | 383 | `ok` (`GET /bff/audit` 200, 805ms) |
| 73 | `/management/rebalance/rb_q2_2026` | `detail-or-alias` | `ok` (8037ms) | `GET /bff/strategies` | 200 | 1082..7996 | 6914 | `ok` (`GET /bff/strategies` 200, 6836ms) |
| 74 | `/management/evolution/ev_001` | `detail-or-alias` | `ok` (3556ms) | `GET /bff/research-tasks` | 200 | 532..3509 | 2977 | `ok` (`GET /bff/research-tasks` 200, 2908ms) |
| 75 | `/management/meetings/mtg_001` | `detail-or-alias` | `ok` (1171ms) | `GET /bff/agora/capabilities` | 200 | 486..1143 | 657 | `ok` (`GET /bff/management/meetings/mtg_001` 404, 911ms) |
| 76 | `/management/experiments/rx_201` | `detail-or-alias` | `ok` (838ms) | `GET /bff/research-tasks/rx_201` | 404 | 422..774 | 352 | `ok` (`GET /bff/research-tasks/rx_201` 404, 422ms) |
| 77 | `/management/artifacts/art_stg001_v320` | `detail-or-alias` | `ok` (3254ms) | `GET /bff/deployments` | 200 | 494..2918 | 2424 | `ok` (`GET /bff/deployments` 200, 3080ms) |
| 78 | `/management/deployments/dp_001` | `detail-or-alias` | `ok` (1948ms) | `GET /bff/runtimes` | 200 | 604..1779 | 1175 | `ok` (`GET /bff/runtimes` 200, 1429ms) |
| 79 | `/management/incidents/in_021` | `detail-or-alias` | `ok` (6798ms) | `GET /bff/strategies` | 200 | 427..6756 | 6329 | `ok` (`GET /bff/strategies` 200, 7071ms) |
| 80 | `/management/governance/ap_301` | `detail-or-alias` | `ok` (1036ms) | `GET /bff/approvals/ap_301` | 404 | 524..958 | 434 | `ok` (`GET /bff/approvals/ap_301` 404, 916ms) |
| 81 | `/management/governance/policies/rp_quant_v2` | `detail-or-alias` | `ok` (2838ms) | `GET /bff/personas` | 200 | 658..2802 | 2144 | `ok` (`GET /bff/personas` 200, 2747ms) |
| 82 | `/management/tools/tl_market_data` | `detail-or-alias` | `ok` (1185ms) | `GET /bff/tools/tl_market_data` | 404 | 391..1118 | 727 | `ok` (`GET /bff/audit` 200, 443ms) |
| 83 | `/management/mcp/mcp_alpha` | `detail-or-alias` | `ok` (1198ms) | `GET /bff/mcp-servers/mcp_alpha` | 404 | 526..1155 | 629 | `ok` (`GET /bff/mcp-tools` 200, 323ms) |
| 84 | `/management/mcp-tools/mt_001` | `detail-or-alias` | `ok` (1481ms) | `GET /bff/mcp-tools/mt_001` | 404 | 507..1417 | 910 | `ok` (`GET /bff/mcp-tools/mt_001` 404, 406ms) |
| 85 | `/management/skills/sk_macro_brief` | `detail-or-alias` | `ok` (2943ms) | `GET /bff/personas` | 200 | 568..2869 | 2301 | `ok` (`GET /bff/personas` 200, 3551ms) |
| 86 | `/management/channels/ch_slack_alerts` | `detail-or-alias` | `ok` (1057ms) | `GET /bff/audit` | 200 | 573..1025 | 452 | `ok` (`GET /bff/audit` 200, 715ms) |
| 87 | `/management/control-room` | `detail-or-alias` | `ok` (7934ms) | `GET /bff/management/quarterly-ranking` | 200 | 537..7913 | 7376 | `ok` (`GET /bff/management/quarterly-ranking` 200, 5160ms) |
| 88 | `/management/one-ring` | `detail-or-alias` | `ok` (3852ms) | `GET /bff/management/cockpit` | 200 | 452..3817 | 3365 | `ok` (`GET /bff/management/quarterly-ranking` 200, 4548ms) |
| 89 | `/management/overview` | `detail-or-alias` | `ok` (10830ms) | `GET /bff/management/quarterly-ranking` | 200 | 555..10781 | 10226 | `ok` (`GET /bff/management/quarterly-ranking` 200, 10561ms) |
| 90 | `/management/command-center` | `detail-or-alias` | `ok` (7845ms) | `GET /bff/management/quarterly-ranking` | 200 | 1145..7795 | 6650 | `ok` (`GET /bff/management/quarterly-ranking` 200, 4980ms) |
| 91 | `/management/risk-center` | `detail-or-alias` | `ok` (10477ms) | `GET /bff/strategies` | 200 | 626..10452 | 9826 | `ok` (`GET /bff/strategies` 200, 8676ms) |
| 92 | `/management/capital-pools` | `detail-or-alias` | `ok` (3096ms) | `GET /bff/management/persona-fleet` | 200 | 588..3043 | 2455 | `ok` (`GET /bff/management/persona-fleet` 200, 5972ms) |
| 93 | `/management/ranking-formulas` | `detail-or-alias` | `ok` (968ms) | `GET /bff/ranking-formulas` | 200 | 402..922 | 520 | `ok` (`GET /bff/ranking-formulas` 200, 245ms) |
| 94 | `/management/rebalances` | `detail-or-alias` | `ok` (2165ms) | `GET /bff/management/human-inbox` | 200 | 595..2131 | 1536 | `ok` (`GET /bff/management/human-inbox` 200, 1156ms) |
| 95 | `/management/research` | `detail-or-alias` | `ok` (2305ms) | `GET /bff/research-tasks` | 200 | 458..2246 | 1788 | `ok` (`GET /bff/research-tasks` 200, 2166ms) |
| 96 | `/management/deployment` | `detail-or-alias` | `ok` (2111ms) | `GET /bff/deployments` | 200 | 400..1877 | 1477 | `ok` (`GET /bff/deployments` 200, 2477ms) |
| 97 | `/management/capital-pools/cp_alpha` | `detail-or-alias` | `ok` (2562ms) | `GET /bff/management/persona-fleet` | 200 | 802..2492 | 1690 | `ok` (`GET /bff/management/persona-fleet` 200, 1326ms) |
| 98 | `/management/ranking-formulas/rf_001` | `detail-or-alias` | `ok` (1347ms) | `GET /bff/audit` | 200 | 795..1298 | 503 | `ok` (`GET /bff/audit` 200, 298ms) |
| 99 | `/management/rebalances/rb_q2_2026` | `detail-or-alias` | `ok` (7469ms) | `GET /bff/strategies` | 200 | 496..7439 | 6943 | `ok` (`GET /bff/strategies` 200, 6408ms) |
| 100 | `/management/research/rx_201` | `detail-or-alias` | `ok` (1067ms) | `GET /bff/research-tasks/rx_201` | 404 | 524..1054 | 530 | `ok` (`GET /bff/research-tasks/rx_201` 404, 406ms) |
| 101 | `/management/strategies/strat-collector:collector-news-rss:news-cna-202611250274-aspx:bb683a5750b5-9ef1eb2f8462` | `live-id-detail` | `ok` (13115ms) | `GET /bff/jobs` | 200 | 483..13039 | 12556 | `ok` (`GET /bff/jobs` 200, 18193ms) |
| 102 | `/management/personas/persona-d57f6d76254c20418a9e` | `live-id-detail` | `ok` (7869ms) | `GET /bff/strategies` | 200 | 479..7814 | 7335 | `ok` (`GET /bff/strategies` 200, 7982ms) |
| 103 | `/management/capital/hosted-all12-h12-01a11-20261023-9aeb-pool` | `live-id-detail` | `ok` (10495ms) | `GET /bff/strategies` | 200 | 912..10483 | 9571 | `ok` (`GET /bff/strategies` 200, 6287ms) |
| 104 | `/management/ranking/formulas/pm12-quarterly-ranking-formula` | `live-id-detail` | `ok` (889ms) | `GET /bff/audit` | 200 | 432..878 | 446 | `ok` (`GET /bff/audit` 200, 833ms) |
| 105 | `/management/evolution/evp-c0ce26091e8b443495bfa61e3dac34ff` | `live-id-detail` | `ok` (3028ms) | `GET /bff/research-tasks` | 200 | 517..2981 | 2464 | `ok` (`GET /bff/research-tasks` 200, 1717ms) |
| 106 | `/management/meetings/mtg-492d824b116f9ace` | `live-id-detail` | `ok` (1410ms) | `GET /bff/management/meetings/mtg-492d824b116f9ace` | 200 | 567..1375 | 808 | `ok` (`GET /bff/management/meetings/mtg-492d824b116f9ace` 200, 539ms) |
| 107 | `/management/experiments/rtask-20261125-014` | `live-id-detail` | `ok` (2648ms) | `GET /bff/management/persona-fleet` | 200 | 1206..2604 | 1398 | `ok` (`GET /bff/management/persona-fleet` 200, 1086ms) |
| 108 | `/management/artifacts/artifact-persona-pool-039ad01f8bcd2b4aa466-v2` | `live-id-detail` | `ok` (2643ms) | `GET /bff/deployments` | 200 | 427..2474 | 2047 | `ok` (`GET /bff/artifacts/artifact-persona-pool-039ad01f8bcd2b4aa466-v2` 200, 2527ms) |
| 109 | `/management/deployments/plan-persona-pool-039ad01f8bcd2b4aa466-v2` | `live-id-detail` | `ok` (2060ms) | `GET /bff/deployments/plan-persona-pool-039ad01f8bcd2b4aa466-v2` | 200 | 433..2022 | 1589 | `ok` (`GET /bff/deployments/plan-persona-pool-039ad01f8bcd2b4aa466-v2` 200, 1384ms) |
| 110 | `/management/incidents/agent-c0be28c2c638-0056` | `live-id-detail` | `ok` (6685ms) | `GET /bff/strategies` | 200 | 615..6661 | 6046 | `ok` (`GET /bff/strategies` 200, 6799ms) |
| 111 | `/management/channels/approval` | `live-id-detail` | `ok` (1030ms) | `GET /bff/audit` | 200 | 509..993 | 484 | `ok` (`GET /bff/audit` 200, 695ms) |
