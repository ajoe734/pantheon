# Review: BP5-OSS-001 — Pin the OpenClaw source and governed adapter boundary

Reviewer: Claude
Date: 2026-04-15
Verdict: **APPROVED**

## Scope Check

Task scope was "lock the OpenClaw upstream pin and governed adapter boundary." That is exactly what was delivered. BP5-OSS-002 concerns (live adapter transport, configured gateway boot, end-to-end workflow execution) are clearly and consistently deferred across all artifacts. No scope creep; no premature claims.

## Artifact Review

### integrations/openclaw/integration.md
- Pin is locked consistently: tag `v2026.4.7` → commit `5050017543011b61df67744ebc6368d889c25a95`, image `ghcr.io/openclaw/openclaw:2026.4.7`, digest `sha256:be45b5187cbec1ff0f4e2503393d66acfc121c2d97eadf03bb1ac75826bad77c`.
- 48-hour soak policy rationale for holding on `v2026.4.7` instead of chasing `v2026.4.14` is sound and documented.
- The `/control/*` facade is correctly marked as a **future Pantheon internal surface**, not a native upstream OpenClaw endpoint set. This separation from the verified upstream runtime surface is the key correctness fix for this task and is cleanly executed.
- Upgrade rule (re-run smoke, update all four files, log to `ai-activity-log.jsonl`) is concrete and actionable.

### integrations/openclaw/governance.md
- Deny-first model, mandatory deny rules, approval hooks, and error escalation ladder are all present and aligned with OC-001/OC-002/OC-003.
- Kill-switch independence clause is correctly stated: "The kill-switch fast path must never depend on OpenClaw availability."
- Upgrade governance procedure is correctly tied to reviewer approval before pin change.
- References to `PAPER_CANARY_LIVE_POLICY.md` and `REG-002` are correct.

### integrations/openclaw/smoke_test.md
- Scope boundary is honest: verifies pin + container + CLI surface + normalization only. End-to-end live adapter deferred.
- Prerequisites table is complete. `--skip-docker` flag documented.

### scripts/openclaw-smoke-test.sh
- `set -euo pipefail` present. No unsafe patterns. ✓
- Step 1: `git ls-remote` tag/commit resolution. ✓
- Step 2: manifest check + digest match + `openclaw --help` + `openclaw gateway --help`. ✓
- Step 3: normalization output existence check. ✓
- Pass/fail counters exit non-zero on any failure. ✓
- Reported result of 6 passed / 0 failed is arithmetically consistent (1 + 4 + 1).

### services/control-plane/specs/normalize_handoff.py
- Clean Python with jsonschema Draft7Validator for both StrategySpec and WorkflowHandoff. ✓
- Resolver wires the `$ref` from `workflow_handoff.schema.json` to the local spec schema correctly via both the local file URI and the canonical `https://pantheon/...` store entry. ✓
- Builds governance metadata on the `WorkflowHandoff` envelope, not on the `StrategySpec` itself — consistent with `governance.md` §5.2.

### integrations/openclaw/adapter/README.md
- Establishes `integrations/openclaw/adapter/` as the only approved home for adapter code. ✓
- Non-goals are unambiguous.
- Locked inputs are constrained exactly to what `BP5-OSS-001` verified; anything else must be re-verified in `BP5-OSS-002`. ✓

### integrations/openclaw/evidence_pack.md
- Consolidates all evidence references; validation steps 1–6 map directly to the smoke script steps. ✓

### OSS_INTEGRATION_CHECKLIST.md
- OpenClaw row correctly promoted to `governed`. Description accurately separates done from deferred. ✓

## Issues Found

None blocking. One minor note for `BP5-OSS-002` tracking (not a gate for this task):

- The `normalize_handoff.py` currently produces a fixed `strategy_id` of `strat-{topic}` without a nonce. That is fine for smoke purposes, but the adapter in `BP5-OSS-002` should generate a unique ID per handoff to avoid registry collisions.

## Decision

Approved. All acceptance criteria for BP5-OSS-001 are met:

1. ✓ Upstream source selected
2. ✓ Version pinned (tag + commit + image + digest, all consistent)
3. ✓ Dependency/repo path established (`integrations/openclaw/`, `services/control-plane/specs/`)
4. ✓ Local adapter boundary defined (`integrations/openclaw/adapter/README.md`)
5. ✓ Smoke test described and executable (`scripts/openclaw-smoke-test.sh`, 6/6 passed)
6. ✓ Governance overlay documented (`governance.md`)
