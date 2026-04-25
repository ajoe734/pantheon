# Starter Draft

Current rule: only `Codex` edits this file directly.

## Shared Draft

- Objective: Turn the current EP3-bounded deployment evidence into a dependency-aware plan for stable EP4, while explicitly separating later EP5 canary/live proof from paper-runtime proof. (`planning-session.json:8-9`)
- Scope boundary: In scope for this round are the machine-readable EP4 slices `OSS-004A` through `OSS-004D` and the prerequisite-only EP5 slice `EP5-001`. `EP5-002` is currently proposed in machine state, but whether it belongs in this session is still an explicit scope decision. Materialization and human acceptance remain out of scope until consensus language catches up. (`planning-session.json:69-72`, `planning-session.json:152-307`, `README.md:31-34`, `README.md:43-44`)
- Proposed wave order:
  1. `OSS-004A`: stabilize the runtime auth/authority path that EP4 proof depends on. This wave should explicitly verify per-agent workspace isolation, no implicit credential sharing, and telemetry parity fields needed for later paper/canary/live reconciliation. (`planning-session.json:152-176`, `OPENCLAW_RUNTIME_CONTRACT.md:218-221`, `PAPER_CANARY_LIVE_POLICY.md:269-270`)
  2. `OSS-004B`: replace the bootstrap paper runtime with the truthful paper execution substrate. (`planning-session.json:177-202`)
  3. `OSS-004C`: run the integrated governed paper acceptance that proves EP4 end to end. This acceptance should include at least one cited `pause_then_replace` rollback drill so rollback evidence is not reduced to a generic happy-path run. (`planning-session.json:203-229`, `EXECUTION_PROOF_AND_MATURITY_LEVELS.md:27`, `ROLLBACK_AND_POSITION_SEMANTICS.md:57-69`)
  4. `OSS-004D` and `EP5-001`: after `OSS-004C`, publish EP4 evidence/status truth and separately prepare canary/live prerequisites. (`planning-session.json:230-280`)
  5. `EP5-002`: treat first canary/live proof as a later gated step that should not silently collapse the "prerequisites only" boundary or enter the initial materialization batch by default. (`planning-session.json:281-307`, `execution-materialization.md:7-12`)
- Proposed task slices:
  - Slice A: `OSS-004A` + `OSS-004B` as the EP4 proof-raising substrate wave. Acceptance theme: auth/authority is explicit, the paper execution package is truthful rather than bootstrap-only, per-agent workspace isolation is verified, and telemetry surfaces expose `deployment_stage` plus `is_real_order` before the integrated EP4 run. (`planning-session.json:152-202`, `OPENCLAW_RUNTIME_CONTRACT.md:218-221`, `PAPER_CANARY_LIVE_POLICY.md:269-270`)
  - Slice B: `OSS-004C` as the first integrated governed paper acceptance wave. Acceptance theme: approval, deployment, runtime binding, execution, telemetry, incident/health, and rollback are archived together, including a cited `pause_then_replace` drill rather than only a generic replace path. (`planning-session.json:203-229`, `EXECUTION_PROOF_AND_MATURITY_LEVELS.md:27`, `ROLLBACK_AND_POSITION_SEMANTICS.md:57-69`)
  - Slice C: `OSS-004D` as the EP4 publication/truthing wave. Acceptance theme: the repo can claim stable EP4 without overclaiming. (`planning-session.json:230-254`)
  - Slice D: `EP5-001` as the prerequisite-only wave. Acceptance theme: canary/live readiness is explicit without making an EP5 proof claim. (`planning-session.json:257-280`)
  - Slice E: `EP5-002` as a disputed later gate. Acceptance theme: if retained, it must remain clearly downstream of prerequisites and operator-signoff readiness, and it should stay outside the first accepted materialization batch for this session. (`planning-session.json:281-307`, `execution-materialization.md:17-26`)
- Open disagreements:
  - `EP5-002` may exceed the stated objective boundary, because the objective says to separate later EP5 canary/live proof from the current paper-runtime proof plan, while `EP5-002` is itself a first canary/live proof packet. (`planning-session.json:8-9`, `planning-session.json:281-307`)
  - No further document-reconciliation delta is currently open inside the planning workspace: the session README now reflects the brief packet and the primary review path, so the remaining disagreement is scope, not documentation parity. (`README.md:11-27`, `README.md:54-67`, `planning-session.json:13-40`)
