# Task Brief: OPS-FLEET-CAP-CONTRACT-001

This document records a historical, independently approved delivery for the existing `reconcile_merged_done` recovery path. The status below describes the authentic approval at 2026-09-09T16:51:17Z; it is not a new review, present-tense approval, or replacement for canonical task state. At verification on 2026-09-09T23:38:28Z the current canonical task is **blocked, generation 4**, following an operational-hold reopen that removed its review bindings. Recovery has not yet been performed.

- Owner: Codex
- Reviewer: Antigravity2
- Status: review_approved
- Historical approval time: 2026-09-09T16:51:17Z
- Historical task generation: 2
- Delivery repository: ajoe734/pantheon
- Pull request: https://github.com/ajoe734/pantheon/pull/5726
- Exact approved head: ac30c1ee7823d8e4c65d8f48d9fe5ca208f603c4
- Merge commit: 677365cb1aff86e74ec30b290255db05c6036e2f
- Merge target: dev
- Frozen manifest: docs/deployment/evidence/OPS-FLEET-CAP-CONTRACT-001/evidence.json
- Frozen manifest Git blob: 447d8215b7030a8bf4bec3737470ac96bfbffd97
- Frozen manifest SHA256: b480e8ff4f9d6aa88e3bcc805610f86511de741a148c6025057b4ed1a042cba6

The historical approval is canonical activity event `ai-status-event-7dc5684392e2cc60133b7d28fea83f72a4a5f9075377f767cbe315980cb109c6`, actor `Antigravity2`, type `review_approved`. Its exact review binding records PR 5726, base `dev`, branch `task/OPS-FLEET-CAP-CONTRACT-001`, and head `ac30c1ee7823d8e4c65d8f48d9fe5ca208f603c4`. The event carries reviewer worker run `antigravity2-20260909T164755Z-1e778c7f`, task generation 2, and command-runtime source `7ab59e582e369580b138693c52c32265f474bdc9`. Recomputing the canonical event digest matches the recorded event ID.

The reviewer recorded that independent review passed for this exact head and manifest. The review verified the sole capacity authority `ready_dispatcher.max_concurrent_workers=13`, rejection of retired `watchdog.max_active_workers` at startup/render/drift, the 13/14 worker boundary, clean trailers/diff, and successful existing test batches: 174 tests with 39 subtests, and 112 tests with 7 subtests. These are historical reviewer and frozen-manifest results; this document does not claim a new test run. The frozen manifest was authored before approval and still says review pending and activation not performed, with its original reviewer field `Antigravity`. The actual subsequent approval came from `Antigravity2`; neither the frozen manifest nor its reviewer field has been rewritten.

The current canonical integration receipt independently records `canonical_auto_integrator`, observation `performed_merge`, result `landed`, PR 5726, exact head `ac30c1ee7823d8e4c65d8f48d9fe5ca208f603c4`, merge `677365cb1aff86e74ec30b290255db05c6036e2f`, target `dev`, task generation 2, and observation time 2026-09-09T16:55:54Z. Local Git verification confirms both commits are ancestors of `origin/dev`; the frozen manifest bytes are identical at the approved head and merge.

Subsequent operational acceptance is recorded in `/tmp/fleet_cap_activation_readonly/postpromotion-acceptance.md`, SHA256 `327d13e574f661a5422466a163210fe2b002cc4cd7321724c8515babe0c2edb8`. Existing promotion completed at 2026-09-09T23:26:37Z to immutable runtime `17bc3c5588da9613586ddd83b5893ee8278f9756`, which contains the source merge. Its rendered configuration SHA256 is `a9ed96eb9e8d15397e2f2f62aab150193ac45e1586a4928aadf05ab2d8878d97`: dispatcher and effective watchdog cap are 13, and the retired field is absent. Pure boundary fixtures report no pressure at 13 and above-threshold pressure at 14. The drift command exited 0 with no fleet-capacity, source, or integration drift errors. The real host supervisor was PID 812478, start ticks 68413627, using runtime and versioned Python 17bc; observed heartbeat 23:27:32Z and successful loop 23:27:05Z. The existing watchdog dry-run exited 0 with `observe_only` / `supervisor_healthy`, no new PID and no split runtime. These observations establish the recorded activation acceptance, not perpetual future health or product hosting.

The operational-hold reopen is canonical event `ai-status-event-806a671cfc107d54d309c674a5de422560825331de2854c5b12b4fe87cd75189`, actor `Human/Ops`, type `reopen`, at 2026-09-09T23:29:05Z. Its message explicitly requested postmerge closeout while preserving frozen approval; the ordinary reopen lifecycle nevertheless removed the review bindings. The owner then reported the resulting governance blocker. This historical record supports recovering the already-approved and already-merged delivery through the existing audited API. It does not restore fields by hand, create a fresh reviewer decision, or alter the task's scope, assigned roles, source or manifest.

Supporting read-only extraction is `/tmp/fleet_cap_reconcile_readonly_20260909/`: `authentic-events.json` contains the exact approval and reopen events plus digest checks; `current-canonical-task.json` contains the observed blocked task and integration receipt; `verification.json` contains Git ancestry and activation-artifact hashes. Raw activation files remain in `/tmp/fleet_cap_activation_readonly/`.

Repository copies for durable verification: [canonical review events](canonical-review-events.json) and [activation, ancestry and integration readback](activation-proof.json). The `/tmp` paths above describe the original observation locations. These committed copies preserve the observed records and do not become task-state authority.
