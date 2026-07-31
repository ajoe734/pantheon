# SUP-PREEMPTION-DISPATCH-ELIGIBILITY-20260731

This packet records the governed closeout of the scheduler preemption eligibility repair delivered by PR #4399.

The accepted cut is exact PR head `6f391cfd4cde8fcee0a7f913bfe2937aba955d15`, merged to `dev` as `894eb813c7cb5609ae517103a727d93ba8cbd1ed` after dependency PR #4397. The live supervisor runs that exact merge from the immutable root `/home/lupin/pantheon-ci-deploy/dev-root-894eb813c7cb-live` as PID `669007`. A systemd watchdog sweep completed successfully after the swap without replacing the healthy PID.

The real auto-worker canary is task `SUP-PREEMPTION-POST-MERGE-LIVE-CANARY-20260731`, lane `codex2-1`, run `codex-20260731T151146Z-323146a8`, with runner/child/provider PIDs `638723/638790/638797`. It started at `2026-07-31T15:11:46Z`, survived the live supervisor replacement, and remained continuously running through a nine-sample, 360-second post-restart observation window ending `2026-07-31T15:27:12Z`.

The auxiliary canary task remains independently reviewable: supervisor helper-claim selected Codex2 rather than its packet's initially requested Antigravity lane, and the observation window did not contain live provider-ineligible or unchanged-cooldown urgent candidates. This packet therefore uses the canary only for real process survival and live failure-loop/classifier evidence; provider-ineligible and cooldown exclusion are proven compositionally by the exact merged code path and focused passing regressions. It does not mark the auxiliary canary task done.

The machine-readable acceptance, validation commands, review status, live identities, canary correlations, and residual risk are in [evidence.json](./evidence.json).
