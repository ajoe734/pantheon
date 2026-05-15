# Gemini Readout

## Lane

- Agent: Gemini
- Capability focus: Stress-test runtime, replay, and tooling feasibility.

## Canonical Sources Read

- L0: `EXECUTION_PROOF_AND_MATURITY_LEVELS.md`
- L1: `OPENCLAW_RUNTIME_CONTRACT.md`, `PAPER_CANARY_LIVE_POLICY.md`, `ROLLBACK_AND_POSITION_SEMANTICS.md`
- L2: `docs/reviews/2026-04-18-ep4-ep5-planning-entry-packet.md`, `docs/reviews/2026-04-18-current-state-reconciliation.md`

## Working Interpretation

- Architecture summary: EP4 is the pivot from "system smoke" (EP3) to "governed authority" (EP4). It requires the full control-plane to execution-plane path to be truthful, meaning paper-mode must use the exact same binding, telemetry, and rollback logic as live/canary, only diverging at the venue/matching layer.
- Delivery order: Support the proposed P1-P4 wave structure. P1 (EP4 prereqs) must prioritize the identity and auth isolation specified in `OPENCLAW_RUNTIME_CONTRACT.md`.
- Ownership boundaries: Pantheon (Runtime Manager) owns the binding and cutover logic; OpenClaw (Runtime) owns the session execution. The adapter is the critical feasibility bridge that must be "stress-tested" during EP4.

## Risks / Contradictions

- Risk 1: **Simulated vs. Real Rollback Drift**: If EP4 "governed paper" proof only tests `replace` (the simplest rollback) and skips `liquidate_then_replace` because paper has no real positions, we risk failing the first real canary incident.
- Risk 2: **Identity Isolation Gap**: `OPENCLAW_RUNTIME_CONTRACT.md` §7 requires per-agent workspace and auth isolation. If the current EP3 baseline uses shared credentials or overlapping workspaces, promoting to EP4 without fixing this would violate the platform's security mandate.

## Suggested Task Slices

- Slice 1 (P1): **Identity-Aware Adapter Hardening**: Implement and verify the `openclaw-gateway-adapter`'s ability to enforce per-persona workspace isolation and capability filtering as defined in `OPENCLAW_RUNTIME_CONTRACT.md` §4.
- Slice 2 (P1): **Telemetry-Parity Validation**: Ensure paper-mode telemetry includes `deployment_stage` and `is_real_order` flags as required by `PAPER_CANARY_LIVE_POLICY.md` §10 to support apples-to-apples reconciliation.
- Slice 3 (P2): **Integrated Paper Rollback Drill**: A mandatory EP4 proof task to execute a `pause_then_replace` rollback in paper mode, verifying that the `RuntimeBinding` lineage and `rollback_parent` links are correctly established in the audit trail.

## Citations

- [EXECUTION_PROOF_AND_MATURITY_LEVELS.md:27] EP4 requires "cross-plane acceptance plus paper-runtime execution with telemetry, governance, and rollback evidence".
- [OPENCLAW_RUNTIME_CONTRACT.md:7.1] "per-agent workspace" and "no implicit credential sharing" are mandatory for isolation.
- [PAPER_CANARY_LIVE_POLICY.md:10] Paper/canary/live "must share the same telemetry schema" but with distinct flags for reconciliation.
- [ROLLBACK_AND_POSITION_SEMANTICS.md:3.2] `pause_then_replace` is a distinct strategy requiring stable-state verification.
