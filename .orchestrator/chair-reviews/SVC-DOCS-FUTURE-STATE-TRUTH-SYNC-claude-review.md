# Claude Review — SVC-DOCS-FUTURE-STATE-TRUTH-SYNC

Reviewer: Claude
Task: SVC-DOCS-FUTURE-STATE-TRUTH-SYNC
Owner: Codex
Date: 2026-04-29

## Artifacts Reviewed

- `docs/02-architecture/consensus/sessions/phase4-2026-04-15-service-layer-completion/starter-draft.md`
- `docs/02-architecture/consensus/sessions/phase4-2026-04-15-service-layer-completion/phase2-phase6-gap-inventory.md`

## Acceptance Criteria Assessment

### 1. Research and learning default service status no longer conflicts with compose ✅

`starter-draft.md` §Compose profile boundaries: explicitly states `policy-learning-svc`, `research-orchestrator-svc`, and `research-worker-gateway-svc` are "safe boundary wrappers that reject production adapters and paper/canary/live activation".

`phase2-phase6-gap-inventory.md` §11: details each service's rejection of Qlib, TRL, RL, W&B, and paper/canary/live paths while noting root compose env gates are false. Disposition note correctly forbids citing these wrappers as evidence of production activation.

### 2. OpenClaw live broker session creation remains explicit deferral, not active task ✅

`starter-draft.md` §Explicit deferrals: "OpenClaw session creation remains explicitly deferred at the Pantheon adapter boundary: POST /api/openclaw-adapter/sessions returns non-retryable CAPABILITY_DENIED, and both OPENCLAW_PRODUCTION_BROKER_ENABLED and OPENCLAW_PAPER_ADAPTER_ENABLED are false in root compose."

`phase2-phase6-gap-inventory.md` §12: confirms CAPABILITY_DENIED, locked compose flags, and that the adapter facade boundary "must not be cited as evidence that upstream OpenClaw runtime session execution, paper execution, production adapters, broker execution, or EP5 activation is complete."

Neither document creates a new execution task for this path.

### 3. BFF HA remains product-scope defer ✅

`starter-draft.md` §Explicit deferrals: records BFF HA as "a product-scope defer, not a pending implementation task, because the operator frontend is expected to have low concurrent human usage. Reopen only if operator concurrency, availability SLOs, external customer access, or audit requirements make BFF outage a material risk."

`phase2-phase6-gap-inventory.md` §9 and §7: both state "2026-04-29 product-scope decision" and do not create implementation work for it.

### 4. Source/search limitations and active execution tasks represented without claiming production crawler ✅

`starter-draft.md` §source-ingest table entry: "Current configured fetch validation accepts fetch.mode == static_records; the bounded HTTP/file feed baseline is owned by active task SVC-SOURCE-INGEST-EXTERNAL-FETCH-BASELINE." §search-svc: correctly describes compat quarantine boundary owned by `SVC-SEARCH-DURABLE-COMPAT-QUARANTINE`.

`phase2-phase6-gap-inventory.md` §3 and §7/§13: correctly represent both the task and code state. §7 reflects Codex2's implemented external_feed code (correct, as that code exists now under review), while maintaining "It is not a production crawler and does not claim arbitrary external web scraping."

Code verified: `services/source_ingestion/configured.py:122-169` — both `static_records` and `external_feed` modes present with explicit allowlist/timeout/size enforcement. `services/search/main.py:278-327` — durable default path and explicit `/api/search/query/request-documents-compat` compat route present exactly as cited.

### 5. Docs links cite code and compose truth ✅

Both documents have code-backed reference sections with `docker-compose.yml` line ranges and service-level file references. Spot-checked citations are accurate against current worktree state.

## Minor Observation (non-blocking)

The `starter-draft.md` source-ingest table entry still states "accepts fetch.mode == static_records" while `phase2-phase6-gap-inventory.md` §7/§13 already reflects the `external_feed` implementation from Codex2. This is internally consistent: the starter-draft correctly attributes the external feed to the still-in-review task `SVC-SOURCE-INGEST-EXTERNAL-FETCH-BASELINE`, while the gap inventory (which is also an artifact of that task) reflects the actual code state. Once `SVC-SOURCE-INGEST-EXTERNAL-FETCH-BASELINE` closes, the starter-draft source-ingest entry can be updated to match, but this is out of scope for the current task.

## Decision

**APPROVED**

All five acceptance criteria are met. The planning docs now provide an honest three-way split: code truth / current hardening tasks / future-deferred, with no misleading claims about production crawler, research activation, OpenClaw sessions, or BFF HA. Code citations are verified accurate.

Codex should run closeout, create a task-scoped commit, and mark done.
