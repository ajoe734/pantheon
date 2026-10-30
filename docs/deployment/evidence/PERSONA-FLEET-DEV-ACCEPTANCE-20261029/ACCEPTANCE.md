# PERSONA-FLEET-DEV-ACCEPTANCE-20261029

Status: release DONE on the final pair; hosted acceptance NOT passed (warm p95 still above target).

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
