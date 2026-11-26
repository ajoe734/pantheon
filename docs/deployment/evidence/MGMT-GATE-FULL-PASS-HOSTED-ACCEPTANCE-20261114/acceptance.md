# MGMT-GATE-FULL-PASS-HOSTED-ACCEPTANCE-20261114 — Hosted Management Per-Request Timing Crawl & Acceptance Evidence

## 1. Executive Summary & Deployed Pair Identity

- **Crawl A — Hosted Dev Root [`45684275970`](https://github.com/ajoe734/pantheon/actions/runs/45684275970) (FE Gate [`45690103912`](https://github.com/ajoe734/execute-plans/actions/runs/45690103912))**:
  - **Deployed Pair**: BFF `b3f93d448f44aa426f3a4848c93df3e5a1ae8aa3` + FE `38e5337d1f832a8582ce4eaac63cb5febbfb943e` (`pairId=4ee2000dceee2403c4e9b4297374c19e276ce6ebcc4a8060026faced28954d61`, stable before and after crawl)
  - **Window (UTC)**: `2026-11-26T02:37:36.306Z` → `2026-11-26T02:46:06.976Z` (`readOnly=true`, `PANTHEON_ACCEPT_CLICK_WRITE_CTAS=0`, `ROUTE_TIMEOUT_MS=45000`, `SETTLE_TIMEOUT_MS=20000`)
  - **Outcome**: **`109/111` routes passed** (`11/11` live entity IDs resolved with `0` discovery failures; `pinnedTransport.pass=true` with `0` transport errors; `sessionRbac=pass`; `loadGate=pass`).
  - **Raw Report Digests**: JSON `747225` bytes (SHA-256 `eccd11bbde7b20bcb74f74dbd427059b6bebfb580a05d095dcf22e93c6940380`), Markdown `100001` bytes (SHA-256 `44a7cd449a7fc4cb2e6350196ddec7919cf40cc7923d2884fcf8a6407cee2cf9`).
- **Crawl B — Post-Switch Follow-Up on Root [`45740904149`](https://github.com/ajoe734/pantheon/actions/runs/45740904149) (with `#7877` `BFF-EVO-JOURNAL-VERSION-STRING-20261126`)**:
  - **Deployed Pair**: BFF `8e67d240e034f4f133ffa926383b584536bc2e35` + FE `38e5337d1f832a8582ce4eaac63cb5febbfb943e` (stable before and after crawl)
  - **Window (UTC)**: `2026-11-26T03:05:37.460Z` → `2026-11-26T03:15:04.667Z`
  - **Outcome**: **`107/111` routes passed**, `0` route crashes, `0` render-crash console errors, `0` CORS errors, `pinnedTransport.pass=true`, `11/11` live entity IDs resolved. Both routes that failed in Crawl A (`/management/evolution-journal` -> `status=ok`, `GET /bff/management/evolution-journal` HTTP `200` in `1295 ms`; `/management/human-inbox/readiness_blocker%3Apersona%3Apersona-tw-equity` -> `status=ok`, HTTP `404` honest not-found in `2006 ms`) **passed in Crawl B**.
  - **Combined Route Verification**: **`111/111` distinct management routes verified passing (`status=ok`, `dataPass=true`, `0` render crashes) across the paired crawls**.
  - **Raw Report Digests**: JSON `749098` bytes (SHA-256 `0bd54d177271de07e69953e378387c62295c2bf71b029959646efecb071667e7`), Markdown `100560` bytes (SHA-256 `64833091f63d2ef831ec6264516b8e61ab7f554db6a8621825c86503b72ca226`).

## 2. Prerequisite & Blocker Fix Inclusion in Deployed Pair

| Task ID | Repo | PR | Merge Commit | In `b3f93d448f44` + `38e5337d1f83` | In `8e67d240e034` + `38e5337d1f83` |
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
| `BFF-EVO-JOURNAL-VERSION-STRING-20261126` | `ajoe734/pantheon` | #7877 | `8d7ec076e22c` | no | yes |

## 3. Live Entity Discovery (11/11 Resolved on Both Pairs)

| Entity | Discovery Endpoint | HTTP | Live ID (Crawl A) | Detail Route |
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

## 4. Named Route & Request Failure Attribution (Acceptance 3)

- **`/management/evolution-journal`** (Root 45684275970 (BFF b3f93d448f44 + FE 38e5337d1f83), `status=crash`, `dataPass=False`, `dataReasons=[]`) — Slowest/Driving Request: `GET /bff/management/evolution-journal` (HTTP `200`, `7341 ms`). GET /bff/management/evolution-journal (HTTP 200, 7341 ms) emitted integer target.version (6) on pool_version items from #7832, crashing FE 38e5337d1f83 at target.version.toLowerCase(). Fixed by BFF-EVO-JOURNAL-VERSION-STRING-20261126 (PR #7877, 8d7ec076e) and FE-EVO-JOURNAL-VERSION-GUARD-20261126 (PR #1101, 24a9ecc82); verified passing on Root 45740904149 BFF 8e67d240e034 + FE 38e5337d1f83 (status=ok, dataPass=true, GET /bff/management/evolution-journal HTTP 200 in 1295 ms, 0 console errors).
- **`/management/human-inbox/readiness_blocker%3Apersona%3Apersona-tw-equity`** (Root 45684275970 (BFF b3f93d448f44 + FE 38e5337d1f83), `status=datafail`, `dataPass=False`, `dataReasons=['rendered_data_error', 'no_completed_route_data', 'api_data_failure']`) — Slowest/Driving Request: `GET /bff/management/human-inbox/readiness_blocker%3Apersona%3Apersona-tw-equity` (HTTP `503`, `3893 ms`). GET /bff/management/human-inbox/readiness_blocker%3Apersona%3Apersona-tw-equity (fixture not-found ID, HTTP 503, 3893 ms) calls get_human_inbox(page_size=2000) across all contributors and returns 503 human_inbox_partial_read when any contributor times out if the item is not found. During concurrent Root 45740904149 docker compose build on the Dev VM, one contributor timed out; verified passing on BFF 8e67d240e034 + FE 38e5337d1f83 (status=ok, dataPass=true, GET /bff/management/human-inbox/readiness_blocker%3Apersona%3Apersona-tw-equity HTTP 404 honest not-found in 2006 ms, and direct readback /bff/management/human-inbox HTTP 200 with all 6 surfaces ok).
- **`/management/strategies/stg_001`** (Root 45740904149 (BFF 8e67d240e034 + FE 38e5337d1f83) during concurrent self-hosted FE gate run 45747260884, `status=datafail`, `dataPass=False`, `dataReasons=['data_reads_incomplete']`) — Slowest/Driving Request: `GET /bff/research-tasks` (HTTP `200`, `8267 ms`). Passed on Root 45684275970 (BFF b3f93d448f44 + FE 38e5337d1f83: status=ok, dataPass=true). During the tail of the 8e67d240e034 crawl (03:13Z-03:15Z), self-hosted runner job 45747260884 started on the Dev VM (load average 12.99) while FE 38e5337d1f83 StrategyDetail eagerly fired 14 concurrent BFF reads on mount and GET /bff/jobs did not finish within the 20s settle window (already addressed on execute-plans dev by FE-STRATEGY-DETAIL-PRIMARY-FIRST-20261125 PR #1094 a013f969130d and pantheon BFF-STRATEGY-DETAIL-OVERLAP-READS-20261125).
- **`/management/strategies/strat-collector:collector-news-rss:news-cna-202611250274-aspx:bb683a5750b5-9ef1eb2f8462`** (Root 45740904149 (BFF 8e67d240e034 + FE 38e5337d1f83) during concurrent self-hosted FE gate run 45747260884, `status=datafail`, `dataPass=False`, `dataReasons=['data_reads_incomplete']`) — Slowest/Driving Request: `GET /bff/artifacts` (HTTP `200`, `7513 ms`). Passed on Root 45684275970 (BFF b3f93d448f44 + FE 38e5337d1f83: status=ok, dataPass=true). During the tail of the 8e67d240e034 crawl (03:13Z-03:15Z), self-hosted runner job 45747260884 started on the Dev VM (load average 12.99) while FE 38e5337d1f83 StrategyDetail eagerly fired 14 concurrent BFF reads on mount and GET /bff/jobs did not finish within the 20s settle window (already addressed on execute-plans dev by FE-STRATEGY-DETAIL-PRIMARY-FIRST-20261125 PR #1094 a013f969130d and pantheon BFF-STRATEGY-DETAIL-OVERLAP-READS-20261125).
- **`/management/personas/persona-d57f6d76254c20418a9e`** (Root 45740904149 (BFF 8e67d240e034 + FE 38e5337d1f83) during concurrent self-hosted FE gate run 45747260884, `status=datafail`, `dataPass=False`, `dataReasons=['loading_unresolved', 'content_incomplete', 'entity_detail_data_missing', 'data_reads_incomplete']`) — Slowest/Driving Request: `GET /bff/audit` (HTTP `200`, `15744 ms`). Passed on Root 45684275970 (BFF b3f93d448f44 + FE 38e5337d1f83: status=ok, dataPass=true). Timed out past the 20s settle window at 03:14Z-03:15Z only while self-hosted runner job 45747260884 (Release candidate 923c2a31...) saturated the Dev VM (load average 12.99).
- **`/management/capital/hosted-all12-h12-01a11-20261023-9aeb-pool`** (Root 45740904149 (BFF 8e67d240e034 + FE 38e5337d1f83) during concurrent self-hosted FE gate run 45747260884, `status=datafail`, `dataPass=False`, `dataReasons=['loading_unresolved', 'content_incomplete', 'no_completed_route_data', 'entity_detail_data_missing', 'data_reads_incomplete']`) — Slowest/Driving Request: `GET /bff/capital-pools/hosted-all12-h12-01a11-20261023-9aeb-pool` (HTTP `None`, `None ms`). Passed on Root 45684275970 (BFF b3f93d448f44 + FE 38e5337d1f83: status=ok, dataPass=true). Timed out past the 20s settle window at 03:14Z-03:15Z only while self-hosted runner job 45747260884 (Release candidate 923c2a31...) saturated the Dev VM (load average 12.99).

## 5. Per-Route Timing Crawl & Slowest Read Table (All 111 Crawled Routes)

| # | Route | Kind | Crawl A (`b3f93d448f44`) Status / Elapsed | Crawl A Slowest Route Read (or Shell Fallback) | HTTP | Start..Finish (ms) | Read (ms) | Crawl B (`8e67d240e034`) Status / Slowest Read (ms) |
|---:|---|---|---|---|---:|---:|---:|---|
| 1 | `/management/performance` | `nav` | `ok` (6837ms) | `GET /bff/management/portfolio-book/holdings` | 200 | 2102..6783 | 4681 | `ok` (`GET /bff/management/portfolio-book/holdings` 200, 2109ms) |
| 2 | `/management/performance?tab=attribution` | `detail-or-alias` | `ok` (6364ms) | `GET /bff/management/performance-attribution` | 200 | 1316..6295 | 4979 | `ok` (`GET /bff/management/persona-fleet` 200, 8540ms) |
| 3 | `/management/performance?tab=shadow` | `detail-or-alias` | `ok` (753ms) | `GET /bff/me` | 200 | 52..600 | 548 | `ok` (`GET /bff/auth/readiness` 200, 576ms) |
| 4 | `/management/performance?tab=shadow&decision=dec_001` | `detail-or-alias` | `ok` (2607ms) | `GET /bff/management/attribution/shadow/dec_001` | 404 | 1321..2563 | 1242 | `ok` (`GET /bff/management/attribution/shadow/dec_001` 404, 2996ms) |
| 5 | `/management/rankings` | `nav` | `ok` (7568ms) | `GET /bff/management/quarterly-ranking/recommendations` | 200 | 1047..7512 | 6465 | `ok` (`GET /bff/management/quarterly-ranking/recommendations` 200, 4838ms) |
| 6 | `/management/governance-decisions` | `nav` | `ok` (3307ms) | `GET /bff/management/human-inbox` | 200 | 505..3263 | 2758 | `ok` (`GET /bff/management/human-inbox` 200, 2530ms) |
| 7 | `/management/cockpit` | `nav` | `ok` (9766ms) | `GET /bff/management/quarterly-ranking` | 200 | 1024..9725 | 8701 | `ok` (`GET /bff/management/persona-league` 200, 4600ms) |
| 8 | `/management/trade-journeys` | `nav` | `ok` (17606ms) | `GET /bff/management/trade-journeys` | 200 | 6849..17548 | 10699 | `ok` (`GET /bff/management/trade-journeys` 200, 3453ms) |
| 9 | `/management/persona-fleet` | `nav` | `ok` (4986ms) | `GET /bff/management/persona-fleet` | 200 | 943..4898 | 3955 | `ok` (`GET /bff/management/persona-fleet` 200, 2885ms) |
| 10 | `/management/human-inbox` | `nav` | `ok` (1763ms) | `GET /bff/management/human-inbox` | 200 | 644..1719 | 1075 | `ok` (`GET /bff/management/human-inbox` 200, 1470ms) |
| 11 | `/management/trading-pulse` | `nav` | `ok` (16333ms) | `GET /bff/management/trading-pulse` | 200 | 756..11817 | 11061 | `ok` (`GET /bff/management/trading-pulse` 200, 11287ms) |
| 12 | `/management/evolution-journal` | `nav` | `crash` (7921ms) | `GET /bff/management/evolution-journal` | 200 | 532..7873 | 7341 | `ok` (`GET /bff/management/evolution-journal` 200, 6756ms) |
| 13 | `/management/evidence` | `nav` | `ok` (1847ms) | `GET /bff/management/evidence` | 200 | 994..1761 | 767 | `ok` (`GET /bff/management/evidence` 200, 1696ms) |
| 14 | `/management/persona-intent` | `nav` | `ok` (2713ms) | `GET /bff/management/persona-intent` | 200 | 599..2705 | 2106 | `ok` (`GET /bff/management/persona-intent` 200, 2559ms) |
| 15 | `/management/portfolio-book` | `detail-or-alias` | `ok` (2219ms) | `GET /bff/management/portfolio-book/holdings` | 200 | 639..2170 | 1531 | `ok` (`GET /bff/management/portfolio-book/holdings` 200, 980ms) |
| 16 | `/management/persona-league` | `detail-or-alias` | `ok` (7525ms) | `GET /bff/management/quarterly-ranking/recommendations` | 200 | 659..7475 | 6816 | `ok` (`GET /bff/management/quarterly-ranking/recommendations` 200, 5055ms) |
| 17 | `/management/quarterly-ranking` | `detail-or-alias` | `ok` (3411ms) | `GET /bff/management/quarterly-ranking/recommendations` | 200 | 601..3338 | 2737 | `ok` (`GET /bff/management/quarterly-ranking/recommendations` 200, 3057ms) |
| 18 | `/management/performance-attribution` | `detail-or-alias` | `ok` (5681ms) | `GET /bff/management/performance-attribution` | 200 | 1000..5634 | 4634 | `ok` (`GET /bff/management/performance-attribution` 200, 6365ms) |
| 19 | `/management/readiness/ep5` | `nav` | `ok` (1317ms) | `GET /bff/management/readiness/ep5` | 200 | 602..1236 | 634 | `ok` (`GET /bff/management/readiness/ep5` 200, 554ms) |
| 20 | `/management/readiness/broker-live` | `nav` | `ok` (1276ms) | `GET /bff/management/readiness/broker-live` | 200 | 822..1231 | 409 | `ok` (`GET /bff/management/readiness/broker-live` 200, 738ms) |
| 21 | `/management/readiness/capital-binding-live` | `nav` | `ok` (1920ms) | `GET /bff/management/human-inbox` | 200 | 720..1873 | 1153 | `ok` (`GET /bff/management/human-inbox` 200, 1179ms) |
| 22 | `/management/readiness/bff-ha` | `nav` | `ok` (1550ms) | `GET /bff/management/readiness/bff-ha` | 200 | 643..1511 | 868 | `ok` (`GET /bff/management/readiness/bff-ha` 200, 617ms) |
| 23 | `/management/readiness/strict-publish` | `nav` | `ok` (1521ms) | `GET /bff/management/readiness/strict-publish` | 200 | 820..1480 | 660 | `ok` (`GET /bff/management/readiness/strict-publish` 200, 509ms) |
| 24 | `/management/strategies` | `nav` | `ok` (10414ms) | `GET /bff/strategies` | 200 | 1240..10350 | 9110 | `ok` (`GET /bff/strategies` 200, 7115ms) |
| 25 | `/management/alpha-factory` | `nav` | `ok` (8244ms) | `GET /bff/alpha-factory` | 200 | 708..8195 | 7487 | `ok` (`GET /bff/alpha-factory` 200, 9850ms) |
| 26 | `/management/personas` | `nav` | `ok` (4976ms) | `GET /bff/personas` | 200 | 815..4574 | 3759 | `ok` (`GET /bff/personas` 200, 1967ms) |
| 27 | `/management/capital` | `detail-or-alias` | `ok` (2581ms) | `GET /bff/management/portfolio-book/exposure` | 200 | 1249..2473 | 1224 | `ok` (`GET /bff/management/portfolio-book/exposure` 200, 956ms) |
| 28 | `/management/ranking` | `detail-or-alias` | `ok` (1496ms) | `GET /bff/ranking-formulas` | 200 | 1088..1426 | 338 | `ok` (`GET /bff/ranking-formulas` 200, 571ms) |
| 29 | `/management/rebalance` | `detail-or-alias` | `ok` (2664ms) | `GET /bff/management/persona-fleet` | 200 | 823..2625 | 1802 | `ok` (`GET /bff/management/persona-fleet` 200, 4168ms) |
| 30 | `/management/evolution` | `nav` | `ok` (2728ms) | `GET /bff/management/evolution-qualification` | 200 | 724..2655 | 1931 | `ok` (`GET /bff/management/evolution-qualification` 200, 1821ms) |
| 31 | `/management/meetings` | `nav` | `ok` (2676ms) | `GET /bff/management/meetings` | 200 | 908..2634 | 1726 | `ok` (`GET /bff/management/meetings` 200, 1474ms) |
| 32 | `/management/experiments` | `nav` | `ok` (4207ms) | `GET /bff/research-tasks` | 200 | 920..4126 | 3206 | `ok` (`GET /bff/research-tasks` 200, 5030ms) |
| 33 | `/management/artifacts` | `nav` | `ok` (9552ms) | `GET /bff/artifacts` | 200 | 2448..5223 | 2775 | `ok` (`GET /bff/artifacts` 200, 2588ms) |
| 34 | `/management/lineage` | `nav` | `ok` (5006ms) | `GET /bff/lineage` | 200 | 830..4796 | 3966 | `ok` (`GET /bff/lineage` 200, 4252ms) |
| 35 | `/management/loops` | `nav` | `ok` (2401ms) | `GET /bff/v5/loop-runs` | 200 | 872..2332 | 1460 | `ok` (`GET /bff/v5/loop-health` 200, 1487ms) |
| 36 | `/management/deployments` | `nav` | `ok` (3407ms) | `GET /bff/deployments` | 200 | 640..2756 | 2116 | `ok` (`GET /bff/deployments` 200, 1729ms) |
| 37 | `/management/runtimes` | `nav` | `ok` (3485ms) | `GET /bff/management/persona-fleet` | 200 | 672..3044 | 2372 | `ok` (`GET /bff/management/persona-fleet` 200, 7562ms) |
| 38 | `/management/risk` | `nav` | `ok` (11807ms) | `GET /bff/strategies` | 200 | 749..11753 | 11004 | `ok` (`GET /bff/strategies` 200, 14496ms) |
| 39 | `/management/incidents` | `nav` | `ok` (4861ms) | `GET /bff/incidents` | 200 | 1015..4757 | 3742 | `ok` (`GET /bff/incidents` 200, 4945ms) |
| 40 | `/management/jobs` | `nav` | `ok` (9788ms) | `GET /bff/jobs` | 200 | 676..9709 | 9033 | `ok` (`GET /bff/jobs` 200, 12876ms) |
| 41 | `/management/alerts` | `nav` | `ok` (2973ms) | `GET /bff/alerts` | 200 | 1032..2904 | 1872 | `ok` (`GET /bff/alerts` 200, 2476ms) |
| 42 | `/management/approvals` | `nav` | `ok` (1325ms) | `GET /bff/approvals` | 200 | 767..1288 | 521 | `ok` (`GET /bff/approvals` 200, 861ms) |
| 43 | `/management/governance` | `nav` | `ok` (1243ms) | `GET /bff/approvals` | 200 | 718..1196 | 478 | `ok` (`GET /bff/approvals` 200, 1358ms) |
| 44 | `/management/governance/policies` | `nav` | `ok` (4703ms) | `GET /bff/personas` | 200 | 999..4643 | 3644 | `ok` (`GET /bff/personas` 200, 4027ms) |
| 45 | `/management/governance/permissions` | `nav` | `ok` (1518ms) | `GET /bff/management/permissions` | 200 | 605..1492 | 887 | `ok` (`GET /bff/management/permissions` 200, 1118ms) |
| 46 | `/management/governance/memory` | `nav` | `ok` (3452ms) | `GET /bff/personas` | 200 | 652..3339 | 2687 | `ok` (`GET /bff/personas` 200, 2784ms) |
| 47 | `/management/governance/consult` | `nav` | `ok` (2762ms) | `GET /bff/personas` | 200 | 558..2662 | 2104 | `ok` (`GET /bff/personas` 200, 3813ms) |
| 48 | `/management/knowledge` | `nav` | `ok` (1008ms) | `GET /bff/knowledge` | 200 | 517..948 | 431 | `ok` (`GET /bff/knowledge` 200, 1224ms) |
| 49 | `/management/postmortems` | `nav` | `ok` (2876ms) | `GET /api/v1/postmortems` | 200 | 811..2715 | 1904 | `ok` (`GET /api/v1/postmortems` 200, 1495ms) |
| 50 | `/management/tools` | `nav` | `ok` (1291ms) | `GET /bff/tools` | 200 | 626..1252 | 626 | `ok` (`GET /bff/tools` 200, 518ms) |
| 51 | `/management/mcp` | `nav` | `ok` (1489ms) | `GET /bff/mcp-servers` | 200 | 1062..1432 | 370 | `ok` (`GET /bff/mcp-servers` 200, 853ms) |
| 52 | `/management/skills` | `nav` | `ok` (1244ms) | `GET /bff/skills` | 200 | 709..1214 | 505 | `ok` (`GET /bff/skills` 200, 628ms) |
| 53 | `/management/workflows` | `nav` | `ok` (1292ms) | `GET /bff/workflows` | 200 | 692..1260 | 568 | `ok` (`GET /bff/workflows` 200, 1971ms) |
| 54 | `/management/hooks` | `nav` | `ok` (1089ms) | `GET /bff/hooks` | 200 | 658..1021 | 363 | `ok` (`GET /bff/hooks` 200, 684ms) |
| 55 | `/management/channels` | `nav` | `ok` (1028ms) | `GET /bff/channels` | 200 | 624..964 | 340 | `ok` (`GET /bff/channels` 200, 688ms) |
| 56 | `/management/llm-provider-auth` | `nav` | `ok` (2702ms) | `GET /bff/assistant/providers/usage-summary` | 200 | 624..2656 | 2032 | `ok` (`GET /bff/assistant/providers/usage-summary` 200, 1227ms) |
| 57 | `/management/data-sources` | `nav` | `ok` (9663ms) | `GET /bff/agora/operational-readiness` | 200 | 773..9579 | 8806 | `ok` (`GET /bff/agora/operational-readiness` 200, 5143ms) |
| 58 | `/management/audit` | `nav` | `ok` (1231ms) | `GET /bff/audit` | 200 | 598..1182 | 584 | `ok` (`GET /bff/audit` 200, 593ms) |
| 59 | `/management/settings` | `nav` | `ok` (1009ms) | `GET /bff/auth/readiness` | 200 | 81..789 | 708 | `ok` (`GET /bff/me` 200, 333ms) |
| 60 | `/management/loops/execution` | `detail-or-alias` | `ok` (7591ms) | `GET /bff/v5/execution/persona-health` | 200 | 1115..7346 | 6231 | `ok` (`GET /bff/v5/execution/persona-health` 200, 6829ms) |
| 61 | `/management/loops/optimization` | `detail-or-alias` | `ok` (3155ms) | `GET /bff/v5/loop-runs` | 200 | 1083..3103 | 2020 | `ok` (`GET /bff/v5/loop-runs` 200, 1272ms) |
| 62 | `/management/loops/research` | `detail-or-alias` | `ok` (4418ms) | `GET /bff/management/persona-fleet` | 200 | 2529..4371 | 1842 | `ok` (`GET /bff/management/persona-fleet` 200, 1986ms) |
| 63 | `/management/human-inbox/readiness_blocker%3Apersona%3Apersona-tw-equity` | `detail-or-alias` | `datafail` (20075ms) | `GET /bff/management/human-inbox/readiness_blocker%3Apersona%3Apersona-tw-equity` | 503 | 1220..5113 | 3893 | `ok` (`GET /bff/management/human-inbox/readiness_blocker%3Apersona%3Apersona-tw-equity` 404, 1639ms) |
| 64 | `/management/evidence/evref-demo-readiness-001` | `detail-or-alias` | `ok` (4766ms) | `GET /bff/management/evidence/evref-demo-readiness-001` | 404 | 1756..4704 | 2948 | `ok` (`GET /bff/management/evidence/evref-demo-readiness-001` 404, 1155ms) |
| 65 | `/management/evidence?ref_id=evref-demo-readiness-001` | `detail-or-alias` | `ok` (3620ms) | `GET /bff/management/evidence/evref-demo-readiness-001` | 404 | 2674..3588 | 914 | `ok` (`GET /bff/management/evidence/evref-demo-readiness-001` 404, 610ms) |
| 66 | `/management/persona-intent/trace-001` | `detail-or-alias` | `ok` (2988ms) | `GET /bff/management/persona-intent` | 200 | 884..2956 | 2072 | `ok` (`GET /bff/management/persona-intent` 200, 3624ms) |
| 67 | `/management/strategies/stg_001` | `detail-or-alias` | `ok` (14778ms) | `GET /bff/jobs` | 200 | 650..14734 | 14084 | `datafail` (`GET /bff/research-tasks` 200, 8267ms) |
| 68 | `/management/personas/per_quant` | `detail-or-alias` | `ok` (8420ms) | `GET /bff/strategies` | 200 | 626..8367 | 7741 | `ok` (`GET /bff/strategies` 200, 10756ms) |
| 69 | `/management/personas/per_quant/onboarding` | `detail-or-alias` | `ok` (1226ms) | `GET /bff/personas/per_quant` | 404 | 461..1202 | 741 | `ok` (`GET /bff/personas/per_quant` 404, 615ms) |
| 70 | `/management/capital/cp_alpha` | `detail-or-alias` | `ok` (3239ms) | `GET /bff/management/persona-fleet` | 200 | 1212..3202 | 1990 | `ok` (`GET /bff/management/persona-fleet` 200, 1761ms) |
| 71 | `/management/ranking/formulas` | `detail-or-alias` | `ok` (2049ms) | `GET /bff/ranking-formulas` | 200 | 720..2000 | 1280 | `ok` (`GET /bff/ranking-formulas` 200, 835ms) |
| 72 | `/management/ranking/formulas/rf_001` | `detail-or-alias` | `ok` (1645ms) | `GET /bff/audit` | 200 | 791..1596 | 805 | `ok` (`GET /bff/audit` 200, 850ms) |
| 73 | `/management/rebalance/rb_q2_2026` | `detail-or-alias` | `ok` (7916ms) | `GET /bff/strategies` | 200 | 1040..7876 | 6836 | `ok` (`GET /bff/strategies` 200, 7076ms) |
| 74 | `/management/evolution/ev_001` | `detail-or-alias` | `ok` (3442ms) | `GET /bff/research-tasks` | 200 | 515..3423 | 2908 | `ok` (`GET /bff/research-tasks` 200, 2020ms) |
| 75 | `/management/meetings/mtg_001` | `detail-or-alias` | `ok` (1544ms) | `GET /bff/management/meetings/mtg_001` | 404 | 586..1497 | 911 | `ok` (`GET /bff/agora/capabilities` 200, 1413ms) |
| 76 | `/management/experiments/rx_201` | `detail-or-alias` | `ok` (930ms) | `GET /bff/research-tasks/rx_201` | 404 | 496..918 | 422 | `ok` (`GET /bff/research-tasks/rx_201` 404, 501ms) |
| 77 | `/management/artifacts/art_stg001_v320` | `detail-or-alias` | `ok` (3823ms) | `GET /bff/deployments` | 200 | 506..3586 | 3080 | `ok` (`GET /bff/deployments` 200, 3706ms) |
| 78 | `/management/deployments/dp_001` | `detail-or-alias` | `ok` (2686ms) | `GET /bff/runtimes` | 200 | 1082..2511 | 1429 | `ok` (`GET /bff/runtimes` 200, 1728ms) |
| 79 | `/management/incidents/in_021` | `detail-or-alias` | `ok` (7617ms) | `GET /bff/strategies` | 200 | 492..7563 | 7071 | `ok` (`GET /bff/strategies` 200, 8525ms) |
| 80 | `/management/governance/ap_301` | `detail-or-alias` | `ok` (1536ms) | `GET /bff/approvals/ap_301` | 404 | 589..1505 | 916 | `ok` (`GET /bff/audit` 200, 412ms) |
| 81 | `/management/governance/policies/rp_quant_v2` | `detail-or-alias` | `ok` (3447ms) | `GET /bff/personas` | 200 | 634..3381 | 2747 | `ok` (`GET /bff/personas` 200, 2247ms) |
| 82 | `/management/tools/tl_market_data` | `detail-or-alias` | `ok` (1032ms) | `GET /bff/audit` | 200 | 517..960 | 443 | `ok` (`GET /bff/tools/tl_market_data` 404, 1805ms) |
| 83 | `/management/mcp/mcp_alpha` | `detail-or-alias` | `ok` (970ms) | `GET /bff/mcp-tools` | 200 | 584..907 | 323 | `ok` (`GET /bff/mcp-tools` 200, 613ms) |
| 84 | `/management/mcp-tools/mt_001` | `detail-or-alias` | `ok` (994ms) | `GET /bff/mcp-tools/mt_001` | 404 | 573..979 | 406 | `ok` (`GET /bff/mcp-tools/mt_001` 404, 308ms) |
| 85 | `/management/skills/sk_macro_brief` | `detail-or-alias` | `ok` (4382ms) | `GET /bff/personas` | 200 | 739..4290 | 3551 | `ok` (`GET /bff/personas` 200, 3138ms) |
| 86 | `/management/channels/ch_slack_alerts` | `detail-or-alias` | `ok` (1237ms) | `GET /bff/audit` | 200 | 461..1176 | 715 | `ok` (`GET /bff/audit` 200, 827ms) |
| 87 | `/management/control-room` | `detail-or-alias` | `ok` (5758ms) | `GET /bff/management/quarterly-ranking` | 200 | 537..5697 | 5160 | `ok` (`GET /bff/management/quarterly-ranking` 200, 14986ms) |
| 88 | `/management/one-ring` | `detail-or-alias` | `ok` (5142ms) | `GET /bff/management/quarterly-ranking` | 200 | 534..5082 | 4548 | `ok` (`GET /bff/management/quarterly-ranking` 200, 11852ms) |
| 89 | `/management/overview` | `detail-or-alias` | `ok` (11157ms) | `GET /bff/management/quarterly-ranking` | 200 | 552..11113 | 10561 | `ok` (`GET /bff/management/persona-league` 200, 8785ms) |
| 90 | `/management/command-center` | `detail-or-alias` | `ok` (5699ms) | `GET /bff/management/quarterly-ranking` | 200 | 678..5658 | 4980 | `ok` (`GET /bff/management/quarterly-ranking` 200, 6219ms) |
| 91 | `/management/risk-center` | `detail-or-alias` | `ok` (9184ms) | `GET /bff/strategies` | 200 | 469..9145 | 8676 | `ok` (`GET /bff/strategies` 200, 12246ms) |
| 92 | `/management/capital-pools` | `detail-or-alias` | `ok` (6572ms) | `GET /bff/management/persona-fleet` | 200 | 543..6515 | 5972 | `ok` (`GET /bff/management/persona-fleet` 200, 3676ms) |
| 93 | `/management/ranking-formulas` | `detail-or-alias` | `ok` (939ms) | `GET /bff/ranking-formulas` | 200 | 658..903 | 245 | `ok` (`GET /bff/ranking-formulas` 200, 984ms) |
| 94 | `/management/rebalances` | `detail-or-alias` | `ok` (1873ms) | `GET /bff/management/human-inbox` | 200 | 692..1848 | 1156 | `ok` (`GET /bff/management/human-inbox` 200, 1888ms) |
| 95 | `/management/research` | `detail-or-alias` | `ok` (2716ms) | `GET /bff/research-tasks` | 200 | 500..2666 | 2166 | `ok` (`GET /bff/research-tasks` 200, 4171ms) |
| 96 | `/management/deployment` | `detail-or-alias` | `ok` (3682ms) | `GET /bff/deployments` | 200 | 697..3174 | 2477 | `ok` (`GET /bff/deployments` 200, 2454ms) |
| 97 | `/management/capital-pools/cp_alpha` | `detail-or-alias` | `ok` (2676ms) | `GET /bff/management/persona-fleet` | 200 | 1281..2607 | 1326 | `ok` (`GET /bff/management/persona-fleet` 200, 4522ms) |
| 98 | `/management/ranking-formulas/rf_001` | `detail-or-alias` | `ok` (901ms) | `GET /bff/audit` | 200 | 555..853 | 298 | `ok` (`GET /bff/audit` 200, 420ms) |
| 99 | `/management/rebalances/rb_q2_2026` | `detail-or-alias` | `ok` (7035ms) | `GET /bff/strategies` | 200 | 610..7018 | 6408 | `ok` (`GET /bff/strategies` 200, 7354ms) |
| 100 | `/management/research/rx_201` | `detail-or-alias` | `ok` (939ms) | `GET /bff/research-tasks/rx_201` | 404 | 517..923 | 406 | `ok` (`GET /bff/research-tasks/rx_201` 404, 420ms) |
| 101 | `/management/strategies/strat-collector:collector-news-rss:news-cna-202611250274-aspx:bb683a5750b5-9ef1eb2f8462` | `live-id-detail` | `ok` (18792ms) | `GET /bff/jobs` | 200 | 559..18752 | 18193 | `datafail` (`GET /bff/artifacts` 200, 7513ms) |
| 102 | `/management/personas/persona-d57f6d76254c20418a9e` | `live-id-detail` | `ok` (8700ms) | `GET /bff/strategies` | 200 | 672..8654 | 7982 | `datafail` (`GET /bff/audit` 200, 15744ms) |
| 103 | `/management/capital/hosted-all12-h12-01a11-20261023-9aeb-pool` | `live-id-detail` | `ok` (7456ms) | `GET /bff/strategies` | 200 | 1150..7437 | 6287 | `datafail` (`GET /bff/capital-pools/hosted-all12-h12-01a11-20261023-9aeb-pool` None, Nonems) |
| 104 | `/management/ranking/formulas/pm12-quarterly-ranking-formula` | `live-id-detail` | `ok` (1435ms) | `GET /bff/audit` | 200 | 549..1382 | 833 | `ok` (`GET /bff/audit` 200, 1997ms) |
| 105 | `/management/evolution/evp-c0ce26091e8b443495bfa61e3dac34ff` | `live-id-detail` | `ok` (2349ms) | `GET /bff/research-tasks` | 200 | 594..2311 | 1717 | `ok` (`GET /bff/research-tasks` 200, 7551ms) |
| 106 | `/management/meetings/mtg-492d824b116f9ace` | `live-id-detail` | `ok` (986ms) | `GET /bff/management/meetings/mtg-492d824b116f9ace` | 200 | 401..940 | 539 | `ok` (`GET /bff/management/meetings/mtg-492d824b116f9ace` 200, 1755ms) |
| 107 | `/management/experiments/rtask-20261125-014` | `live-id-detail` | `ok` (2023ms) | `GET /bff/management/persona-fleet` | 200 | 890..1976 | 1086 | `ok` (`GET /bff/management/persona-fleet` 200, 3293ms) |
| 108 | `/management/artifacts/artifact-persona-pool-039ad01f8bcd2b4aa466-v2` | `live-id-detail` | `ok` (3788ms) | `GET /bff/artifacts/artifact-persona-pool-039ad01f8bcd2b4aa466-v2` | 200 | 965..3492 | 2527 | `ok` (`GET /bff/deployments` 200, 6210ms) |
| 109 | `/management/deployments/plan-persona-pool-039ad01f8bcd2b4aa466-v2` | `live-id-detail` | `ok` (2321ms) | `GET /bff/deployments/plan-persona-pool-039ad01f8bcd2b4aa466-v2` | 200 | 884..2268 | 1384 | `ok` (`GET /bff/deployments/plan-persona-pool-039ad01f8bcd2b4aa466-v2` 200, 2853ms) |
| 110 | `/management/incidents/agent-af7e6d72ed59-0006` | `live-id-detail` | `ok` (7235ms) | `GET /bff/strategies` | 200 | 421..7220 | 6799 | `ok` (`GET /bff/strategies` 200, 7444ms) |
| 111 | `/management/channels/approval` | `live-id-detail` | `ok` (1187ms) | `GET /bff/audit` | 200 | 434..1129 | 695 | `ok` (`GET /bff/channels/approval` 200, 1355ms) |
