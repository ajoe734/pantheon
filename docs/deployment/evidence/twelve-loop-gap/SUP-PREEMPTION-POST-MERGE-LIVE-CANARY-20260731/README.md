# SUP-PREEMPTION-POST-MERGE-LIVE-CANARY-20260731

This packet closes the auxiliary post-merge scheduler canary independently
reviewed by Antigravity. It records runtime evidence only; the canary made no
product, scheduler, configuration, or deployment change.

The accepted run is `codex-20260731T153631Z-e3ad5e7a`, launched from exact
live merge `894eb813c7cb5609ae517103a727d93ba8cbd1ed`. Its runner/provider-child
PID pair was `839609/839667`, with supervisor PID `669007`. The governed handoff
contains ten direct samples from `2026-07-31T15:38:17Z` through
`2026-07-31T15:43:47Z`; adjacent sample gaps were at most 60 seconds and every
sample retained the same run, PIDs, supervisor, heartbeat, and `in_progress`
task state.

The audited acceptance window was `2026-07-31T15:38:03Z` through
`2026-07-31T15:43:47Z`, or 344 seconds. At its end the worker had actually been
alive for 436 seconds from its `15:36:31Z` start. Supervisor scan lines 102-137
independently bracket the same lane as continuously running; the immutable
runner status later records a clean exit at `15:45:32Z`, after 541 seconds.

Exact-live-code readbacks at `15:44:14Z` and `15:44:37Z` returned no eligible
higher-priority preemption: four Codex2 candidates were failure-loop and triage
blocked, three Claude2 candidates were provider-ineligible, and one
Antigravity review candidate was in unchanged-event cooldown. No legitimate
terminal event occurred during the accepted window.

Antigravity independently reviewed these facts and used the governed approval
command. The task then entered `review_approved`; the exact reviewer output,
source hashes, samples, gate classifications, and acceptance mapping are in
[evidence.json](./evidence.json).
