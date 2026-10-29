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
