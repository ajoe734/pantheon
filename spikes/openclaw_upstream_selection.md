# OpenClaw Upstream Selection Spike

## Goal

Pick the upstream OpenClaw source and define how this repo should integrate with it instead of treating it as a conceptual box.

## Decision Summary

- selected upstream source: `https://github.com/openclaw/openclaw`
- official landing/docs page: `https://openclaw.im/`
- chosen integration mode for v1: **separate runtime/service dependency**
- rejected for v1: local rewrite and source vendoring
- pinning rule: use an immutable upstream release tag plus commit SHA, not a floating branch

## Why This Is the Right Upstream

The upstream project explicitly presents itself as:

- an open-source AI automation framework
- a self-hosted runtime
- a plugin/workflow system
- a system deployable by Docker or Kubernetes

That matches our architecture box much more closely than any local skeleton in this repo.

## Integration Mode Options

### Option A: Vendor or submodule the upstream monorepo

Rejected for v1.

Why:

- upstream OpenClaw is a large TypeScript application/runtime
- this repo is primarily LEAN plus local Python/C# integration logic
- vendoring it now would create high coupling before we have a clean adapter seam

### Option B: Rebuild OpenClaw behavior locally inside Lean

Rejected.

Why:

- it turns a real upstream dependency into an accidental fork
- it makes `OC-*` tasks ambiguous again
- it defeats the purpose of adopting the upstream OSS project

### Option C: Run upstream OpenClaw as a separate runtime and adapt its outputs

Chosen for v1.

Why:

- it preserves upstream semantics
- it keeps the boundary explicit
- it lets us wrap OpenClaw with local governance, registry, and execution rules

## Recommended Pinning Strategy

When implementation begins, record all of these in a repo-local integration note:

1. upstream repo URL
2. release tag
3. Git commit SHA
4. image tag or package reference

Do not use:

- `main`
- `latest`
- unpinned container images

## Local Adapter Points

### `OC-001`

Map upstream OpenClaw tool or plugin permission concepts into the local deny-first permission contract.

The local model should remain the governance wrapper around upstream behavior, not a hidden replacement.

### `OC-002`

Use upstream OpenClaw workflow or scheduler entrypoints for:

- ingest
- review
- retrain
- deploy

Then layer local promotion and execution constraints on top.

### `OC-003`

Normalize upstream OpenClaw outputs into:

- `StrategySpec`
- `WorkflowHandoff`

This is the main seam between upstream orchestration and local registry/execution governance.

## Minimal Smoke Test

The first integration smoke test should prove:

1. a pinned upstream OpenClaw runtime can start
2. one minimal approved workflow can be invoked
3. that workflow can emit a governed handoff payload
4. the payload can be normalized into local `StrategySpec`
5. the normalized output validates against the local schema

Suggested flow:

1. launch pinned OpenClaw in Docker
2. register one small research-intake or review workflow
3. invoke it with a fixed test payload
4. capture the output as a local JSON handoff
5. validate it against `services/control-plane/specs/workflow_handoff.schema.json`

## Follow-up Deliverables

When this spike becomes implementation, create:

- `integrations/openclaw/integration.md`
- `integrations/openclaw/governance.md`
- `integrations/openclaw/smoke_test.md`

## Remaining Open Questions

All open questions have been resolved in `integrations/openclaw/integration.md` §7:

- **Pinned release**: `v2026.6.8` (SHA `8c802aa`) — bumped by OPENCLAW-GOVERNED-BUMP-2026-6-6
- **Transport**: Deferred to adapter implementation phase — integration.md §7 tracks this
- **First smoke test workflow**: research-intake (ingest) — defined in smoke_test.md §3 Step 2
