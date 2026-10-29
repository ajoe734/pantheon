# PERSONA-FLEET-DEV-ACCEPTANCE-20261029

Status: release DONE on final pair; hosted acceptance NOT passed (warm latency target failed).

- Pair served on dev: BFF `71829313630064996553693bf9766a8dd2009e9c` (includes PERSONA-FLEET-HOSTED-LATENCY PR #6857) + FE `56bec9ad581d7731551d685dbeeba0d7ba28eca3` (includes archive-count FE).
- Release: Pantheon Nonprod Deploy run 40940459676 success (19:28Z-20:26Z); admit-and-switch job 123693883081. Baseline pair earlier: run 40887021529.
- Read-only QA (hosted-ui-readonly-result.json, timings.json, pagination.json, screenshots/): login/logout 200, FE/BFF identity unchanged across batch, no unsolicited archived-only fetch, filters/traversal/unknown purpose/focus beyond 100 collected, Agora servant 404 -> UI shows 尚未建立.
- Cold (3 fresh contexts): API 1257/1801/1178 ms (<=2000 OK), usable 1970/2479/1853 ms (<=2500 OK).
- **Warm: 20 samples, nearest-rank p95 = 2643 ms vs target 1000 ms: FAIL.** Median ~1.2 s; two outliers 4590 and 2643 ms. Target not waived.
- Not run: runtime/finance missing-value readbacks, local privacy regression rerun. Agora operational readiness reported unavailable (source_instance_identity_not_unique); not claimed healthy.
- Next: reopen responsible latency source task for warm path.
