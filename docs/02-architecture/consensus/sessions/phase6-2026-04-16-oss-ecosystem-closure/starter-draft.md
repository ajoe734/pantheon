# Starter Draft — Phase 6: OSS Ecosystem Closure

Current rule: only `Codex` edits this file directly.

## Shared Draft

### Objective

- Convert the residual OSS ecosystem maturity gap into a dependency-aware next-wave execution plan that distinguishes governed integrations, activation-ready frameworks, and not-started research backends. `[planning-session.json:8-10]`
- Keep this round focused on the narrower operational questions called out in the session brief: which deferred frameworks should advance, which research backends need first-class task materialization, and which optional ecosystems should be explicitly deferred again. `[README.md:35-43]`

### Proposed Architecture

- Domain A `activation-ready realization`: advance `Qlib` and `TRL` from criteria-defined state into real governed adapters, while refreshing no-regression smoke evidence for already governed `OpenClaw`, `DSPy`, `imitation`, and `MLflow`. `[README.md:47-54] [planning-session.json:175-220] [planning-session.json:387-404]`
- Domain B `missing backend materialization`: cut named execution-ready task families for `vectorbt`, `statsmodels`, and `QuantLib` so those backends stop living only in maturity documents. `[README.md:47-54] [planning-session.json:300-373]`
- Domain C `conditional decision gates`: keep RL path activation and W&B parity as explicit yes/no gates with re-entry criteria instead of letting them drift into another planning cycle. `[README.md:41-43] [planning-session.json:235-286]`
- Closure rule: every deferred backend should leave this session either as an execution slice, an explicit defer with re-entry criteria, or a human-gated unresolved item. `[README.md:81-81]`

### Planning-State Boundary

- The machine state already contains eight `OSS-NEXT-*` proposed execution tasks, but `consensus_status` remains `draft` and `human_gate_status` remains `not_requested`, so the current task graph is still planning-plane input rather than approved execution output. `[planning-session.json:81-83] [planning-session.json:173-416]`
- Materialization still happens only after human acceptance through `scripts/planning-state.sh materialize`, and execution tasks should receive planning references rather than copied planning narrative. `[README.md:63-64]`

### Proposed Wave Order

1. `Wave A / activation-ready realization`
   - `OSS-NEXT-001` Qlib adapter realization
   - `OSS-NEXT-002` TRL activation baseline
   - `OSS-NEXT-008` governed-path regression refresh
2. `Wave B / missing backend materialization`
   - `OSS-NEXT-005` vectorbt task materialization
   - `OSS-NEXT-006` statsmodels task materialization
   - `OSS-NEXT-007` QuantLib task materialization
3. `Wave C / conditional decision gates`
   - `OSS-NEXT-003` RL path activation gate closure, after `OSS-NEXT-001` and `OSS-NEXT-002`
   - `OSS-NEXT-004` W&B parity versus explicit defer, after `OSS-NEXT-008`

### Ownership and Dependency Cut

- `OSS-NEXT-001` is owned by `Claude`, reviewed by `Codex`, and has no recorded dependency. `[planning-session.json:175-181]`
- `OSS-NEXT-002` is owned by `Claude`, reviewed by `Gemini`, and has no recorded dependency. `[planning-session.json:205-212]`
- `OSS-NEXT-003` is owned by `Codex`, reviewed by `Claude`, and depends on both `OSS-NEXT-001` and `OSS-NEXT-002`. `[planning-session.json:235-244]`
- `OSS-NEXT-004` is owned by `Codex`, reviewed by `Gemini`, and depends on `OSS-NEXT-008`. `[planning-session.json:268-276]`
- `OSS-NEXT-005` is owned by `Gemini`, reviewed by `Codex`, and has no recorded dependency. `[planning-session.json:300-306]`
- `OSS-NEXT-006` is owned by `Gemini`, reviewed by `Claude`, and has no recorded dependency. `[planning-session.json:329-335]`
- `OSS-NEXT-007` is owned by `Codex`, reviewed by `Claude`, and has no recorded dependency. `[planning-session.json:358-364]`
- `OSS-NEXT-008` is owned by `Gemini`, reviewed by `Codex`, and has no recorded dependency. `[planning-session.json:387-393]`

### Open Disagreements

- Should `OSS-NEXT-003` remain blocked on both `OSS-NEXT-001` and `OSS-NEXT-002`, or is one activation baseline enough to decide the first RL lane? `[planning-session.json:241-244]`
- Should `OSS-NEXT-004` remain a post-`OSS-NEXT-008` gate, or does W&B parity need to be pulled earlier if telemetry assumptions affect Wave A? `[planning-session.json:274-276]`
- Should `vectorbt`, `statsmodels`, and `QuantLib` stop at task-materialization and governed-adapter design in this wave, or be split immediately into follow-on implementation slices? `[planning-session.json:300-373]`
- Does `OSS-NEXT-008` belong as a parallel sibling in Wave A, or should governed-path regression refresh become a hard prerequisite before any new activation baseline lands? `[planning-session.json:387-404]`

### Review Hooks

- The consensus packet is still blank on scope, accepted architecture, delivery order, and agreed task slices, so cross-review should treat this draft as a proposal that still needs explicit ratification. `[consensus-packet.md:5-17]`
- `Qwen` and `Copilot` are waived in the brief and in `unresolved_items`, but `cross_review_rounds[0].reviewers` still lists them even though both the brief and `review_sequence` say the active reviewer order is `Gemini -> Claude -> Codex`. Reviewers should normalize which reviewer list drives the baton. `[README.md:68-71] [planning-session.json:19-23] [planning-session.json:145-170]`
