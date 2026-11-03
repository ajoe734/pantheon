# PERSONA-FLEET-DEV-ACCEPTANCE-20261029

Status: **hosted acceptance PASSED on the final served pair** BFF `28b2579e39c235ef0bb6e252946de76aa9c80ac0` + FE `56bec9ad581d7731551d685dbeeba0d7ba28eca3` (see the 2026-11-03 section at the end). Sections below are the historical failing batches and are retained as evidence.

- Pair served on dev: BFF `fd3c0ba1f968243228208350c599f314ef86fbba` (includes PERSONA-FLEET-WARM-P95 PR #6865) + FE `56bec9ad581d7731551d685dbeeba0d7ba28eca3`.
- Release: Pantheon Nonprod Deploy run 40961611194 success (21:54Z-22:58Z), served pairId `0c9dce09...a95c2`. Previous pair run 40940459676 (BFF 71829313) had warm p95 2643 ms.
- Read-only QA (hosted-ui-readonly-result.json, timings.json, pagination.json, screenshots/): login/logout 200, post-logout me 401, FE/BFF identity matches before and after, no unsolicited archived-only fetch, 109 authorized records traversed, Agora servant 404 -> UI shows 尚未建立, no blocked writes or page errors.
- Cold (3 fresh contexts): API 1115/1320/740 ms (<=2000 OK), usable 1836/2076/1525 ms (<=2500 OK).
- **Warm: 20 samples, nearest-rank p95 = 1500 ms vs target 1000 ms: FAIL** (improved from 2643). Sorted range 707-1740 ms, median ~1075 ms. Target not waived.
- A first QA attempt at 22:59Z timed out (30 s) on its first API GET before collecting any sample; the rerun is the recorded batch.
- Not run: runtime/finance missing-value readbacks, local privacy regression rerun, second-identity hosted privacy. Agora operational readiness is reported unavailable; not claimed healthy.
- Next: reopen the responsible warm-path source task; hosted track stays pending.

## Update 2026-10-30: PR #6867 (registry detoast) deployed — warm p95 still FAIL

- Release: Pantheon Nonprod Deploy run 40978598720 success, `Dev release 3867ec9b4c3d0eb950af2a77ae290c5f78da818d + 56bec9ad581d7731551d685dbeeba0d7ba28eca3` (BFF includes PR #6867 ad45734e9; 3867ec9b4 only adds a staging script change). `/bff/version` source_commit_sha = 3867ec9b4; served pairId `0b9f25a1bf78e7f36534b0753635d970785ecc2166ccb7a176dec33735e02fb8` before and after QA.
- Read-only QA (same client): login/logout 200, post-logout me 401, 109 authorized records traversed, no blocked writes or page errors, Agora servant 404 -> 尚未建立, operational readiness reported unavailable.
- Cold (3 fresh contexts): API 984/1187/1376 ms, usable 1827/1938/2074 ms — within targets.
- **Warm: 20 samples, nearest-rank p95 = 1679 ms vs 1000 ms: FAIL** (previous pair 1500 ms; not an improvement). Sorted: 633–1803 ms, median ~1085 ms. Raw samples in timings.json `latest_batch_3867ec9b`. Target not waived.
- Not rerun in this batch: finance/runtime probe, local privacy regression, Registry file-hash identity proof. Hosted milestone remains NOT done.

## Update 2026-10-30: PR #6873 (ledger payload) deployed — warm p95 still FAIL

- Release: Pantheon Nonprod Deploy run 40996778825 success, `Dev release fa656a8c19ae2db595e7815ce2a10b0c1ab7b516 + 56bec9ad581d7731551d685dbeeba0d7ba28eca3` (BFF includes PR #6873 merge; Branch CI Gate on that SHA passed before dispatch). `/bff/version` source_commit_sha = fa656a8c1, image sha256:c93c3c2ca123b41e42d3035d650634c50a2fc5609c67a2feda918c24ddeb6893. FE sha equals execute-plans dev head. FE gate/deploy runs 40998949523 / 41000806767 (candidate ca34eb53...) succeeded.
- Read-only QA (same client): login/logout 200, post-logout me 401, no blocked writes. Raw: `hosted-ui-readonly-result.json`, `timings.json` `latest_batch_fa656a8c`.
- Cold (3 fresh contexts): API 1018/1118/857 ms, usable 1755/1836/1589 ms — within targets.
- **Warm: 20 samples, nearest-rank p95 = 1475 ms vs 1000 ms: FAIL.** 19 samples 548–1475 ms (median 823 ms; 5 of them >1000 ms); sample 9 = **54536 ms** outlier, retained. Target not waived.
- The harness reported completed_subset=false / exit 1; the full finance-runtime probe, local privacy rerun and Registry file-hash proof were NOT rerun for this pair. Hosted milestone remains NOT done.
- Next: investigate the 54.5 s stall and residual >1 s warm reads via the responsible source task.

## Update 2026-10-30: PR #6874 (HEALTH-IO) deployed — warm p95 1007 ms, still FAIL by 7 ms

- Release: Pantheon Nonprod Deploy run 41139527088 success, `Dev release 808ab01cdfe00ab22b8b38ce4c8d6b445eeb86ca + 56bec9ad581d7731551d685dbeeba0d7ba28eca3` (dev head after HEALTH-IO merge #6874; Branch CI Gate on that SHA passed before dispatch). `/bff/version` source_commit_sha = 808ab01cd, image sha256:438b31a8fb042c26307195bdb2a1ef63c161150c242dc98fdaa51f0c23b6c5fb; served pairId `97a0f619757e345885b6f3517a6f19f2fea72b746dd0cad82197c0eda54d8e8c`, identical before and after QA.
- Read-only QA (same client): login/logout 200, post-logout me 401, no blocked writes, no page errors, no unsolicited archived-only fetch, Agora servant 404 -> 尚未建立, operational readiness reported unavailable (not claimed healthy). Raw: `hosted-ui-readonly-result.json`, `timings.json` `latest_batch_808ab01c`.
- Cold (3 fresh contexts): API 905/1055/927 ms, usable 1609/1751/1638 ms — within targets.
- **Warm: 20 samples, nearest-rank p95 = 1007 ms vs 1000 ms: FAIL.** Sorted 425–1222 ms, median ~660 ms; two samples >1000 ms (1007, 1222). Large improvement over fa656a8c (1475 ms, 54.5 s stall) — no stall this time — but the target is not waived.
- Harness reported completed_subset=false / exit 1. Finance-runtime probe, local privacy rerun on this SHA and Registry file-hash proof were NOT rerun for this pair. Hosted milestone remains NOT done.
- Next: report blocker and reopen responsible warm-path source task for the residual ~7 ms / tail samples.

## Update 2026-11-01: PR #6887 (STRATEGY-COUNT-READ) served — warm p95 1043 ms FAIL, cold entry 1 usable 2555 ms FAIL

- Pair: BFF `d05cd59d9ae61070c47b9f385395cb5dfa9b7760` (merge of PR #6887) + FE `56bec9ad581d7731551d685dbeeba0d7ba28eca3`, deployed by hourly dev-pair reconcile run 41358101624 (up_to_date). `/bff/version` source_commit_sha = d05cd59d9, image sha256:5f6486b2340db216fb282ecc70d25303a4869b1255a85b019f8ac92a1767ce71; `/deployment.json` accepted 2026-11-01T19:23:27Z, pairId `75ec3b62b84ab6b381519d56acd0e0e6a3941dbc5dad1acd75ac4b1c9876dbba`, identical before and after QA.
- Same client VM, same harness/protocol. Read-only QA: login/logout 200, post-logout me 401, no blocked writes, no page errors, no unsolicited archived-only fetch, Agora servant 404 -> 尚未建立, operational readiness unavailable (not claimed healthy). Raw: `hosted-ui-readonly-result.json`, `timings.json` `latest_batch_d05cd59d`.
- Dataset drifted since the previous batch: 117 authorized records (13 active / 104 archived); the warm scope returns 9 rows (previous batch 5 rows), so before/after is not strictly like-for-like.
- **Warm: 20 samples, nearest-rank p95 = 1043 ms vs 1000 ms: FAIL** (previous 808ab01c: 1007 ms). Sorted 499–1074 ms, median ~816 ms; samples >1000 ms: 1043, 1074. No stall.
- **Cold (3 fresh contexts): API 1667/1152/962 ms (<=2000 OK); usable 2555/1967/1791 ms — entry 1 exceeds 2500 ms: FAIL** (that entry's initial post-login API was 2088 ms; previous batch all within target). Login latency reported separately (893–958 ms to shell).
- Harness reported completed_subset=false / exit 1 (performance gate). Finance-runtime probe, local privacy rerun and Registry file-hash proof were NOT rerun for this pair. Targets not waived; hosted milestone remains NOT done. Dominant span not profiled in this batch (no server-side tracing run); the improvement from the strategy-count change is not visible in hosted p95 within noise.
- Next: report blocker and reopen the responsible warm-path source task with these measured numbers.

## Update 2026-11-03: PR #6890 (WARM-TAIL) + PR #6889 (BACKGROUND-PROMOTE) served — hosted acceptance targets PASS

- Pair: BFF `28b2579e39c235ef0bb6e252946de76aa9c80ac0` (/bff/version source_commit_sha, image sha256:0424deba9f769873a7740b10d66985c3002703f69ac52a403ea16dfa2d56f8b9) + FE `56bec9ad581d7731551d685dbeeba0d7ba28eca3`; release 20261103T001610Z, integration gate run 41547638201, `/deployment.json` deploymentState accepted at 2026-11-03T00:20:21Z, pairId `d850d393c1dc7f7ddf89d0159db501938c95e1355882f3ec833c5db95f9db7bb`. Identity verified identical before and after the batch (harness) and again by direct curl afterwards. No deploy, restart, persona creation, model call or write was performed by this task.
- Same client VM, same harness/protocol (harness sha256 a6105ca8...). Read-only QA: login/logout 200, post-logout me 401, no blocked writes, no page errors, no unsolicited archived-only fetch, Agora servant 404 -> UI shows 尚未建立, operational readiness reported unavailable (not claimed healthy). Raw result.json (sha256 bb7291c01dfd281093ee405a1f359604139b90b31c1e57cc48dc63d8f822efc0, not committed: outside the declared artifact contract; kept in /tmp/pantheon-persona-fleet-20261029/hosted-ui-m3V8aO/), `timings.json` `latest_batch_28b2579e3`, `pagination.json`, `screenshots/`.
- Dataset: 120 authorized records (14 active / 106 archived); full pagination 6 pages, 120 unique IDs, totals consistent. Warm scope returns 10 rows (previous batch 9), so before/after is not strictly like-for-like.
- **Warm: 20 samples, nearest-rank p95 = 926 ms vs 1000 ms: PASS** (previous d05cd59d: 1043 ms). Sorted 347–1006 ms, median ~480 ms; one sample >1000 ms (1006) retained.
- **Cold (3 fresh contexts): API 1097/920/563 ms (<=2000 OK); usable 2037/1674/1378 ms (<=2500 OK).** Login latency (887–920 ms to shell) reported separately. Previous batch's entry-1 failure (2555 ms) did not recur.
- Harness reported completed_subset=true, performance_pass=true. Finance-runtime probe, local privacy rerun and Registry file-hash proof were NOT rerun for this pair; Agora operational readiness is unavailable on dev. These remain gaps for the native review.
- Single-batch result: targets met once after two failing batches; not repeated for variance.


## Final closeout 2026-11-03: remaining acceptance items on the final pair

- Release: Pantheon Nonprod Deploy root run 41544133483 success (release 20261103T001610Z, gate run 41547638201); served pairId `d850d393...db7bb`, BFF image `sha256:0424deba...f8b9`. All ten prerequisite tasks and PRs are listed in `evidence.json` `source_prs`.
- Finance/runtime readback (GET only, 2026-11-03T00:38Z): 15 reads all HTTP 200, 0 errors, 0 blocked writes, logout 200, identity identical before and after. 10 paper personas isolated ledger with no live pool; the 3 without trading telemetry keep null metrics, source=unavailable. File sha256 `8d64eee0...c121` retained outside the repo.
- Registry + BFF image proof: Registry container `c427d2b8...19f9`, image `sha256:7e66974c...c09e`; all 5 Registry/Foundation files match source 28b2579e3 (SHA-256). BFF `/bff/version` image digest equals the release image. No OCI revision label is claimed.
- Local privacy: 4 existing contract tests pass on an exact 28b2579e3 export (8.10s). Local fixtures only; no hosted cross-user pass claimed. Archived-scaling evidence reused from PERSONA-FLEET-HOSTED-LATENCY-20261029 (archived_100/1000) and WARM-TAIL; hosted stores were not grown.
- `pagination.json` and `timings.json` were compacted to fit the diff budget; full copies and SHA-256 digests are recorded inside them.
- Remaining gaps: Agora operational readiness unavailable on dev; single passing batch (not repeated for variance); dataset drift between batches. Hosted milestone may be marked done with these limitations.
