# Codex Readout

## Lane

- Agent: `Codex`
- Capability focus: ground the OSS maturity plan in repo evidence, absorb research-readiness and acceptance review that would normally fall to `Copilot`, and cut the next executable task slices. `[planning-session.json:54-60]`

## Canonical Sources Read

- L0: `README.md` defines the narrowed scope, the governed-versus-activation-ready-versus-not-started maturity split, the baton loop, the review discipline, and the close-out rule for deferred backends. `[README.md:35-81]`
- L1: `planning-session.json` sets the active objective, baton ownership, waived lanes, current review state, and the eight `OSS-NEXT-*` proposed execution tasks with owners, reviewers, dependencies, and acceptance hooks. `[planning-session.json:8-23] [planning-session.json:61-83] [planning-session.json:145-416]`
- L2: `starter-draft.md` now maps the task graph into a three-domain architecture and three-wave order, while `consensus-packet.md` is still blank on scope, accepted architecture, delivery order, and agreed task slices. `[starter-draft.md:12-59] [consensus-packet.md:5-17]`

## Working Interpretation

- Architecture summary: the canonical material is converging on three closure domains rather than one flat backlog: `activation-ready realization`, `missing backend materialization`, and `conditional decision gates`. That architecture is consistent across the README scope/gap framing, the machine-state task set, and the current starter draft. `[README.md:39-54] [planning-session.json:173-416] [starter-draft.md:12-17]`
- Delivery order: the current task graph implies `Wave A = OSS-NEXT-001/002/008`, `Wave B = OSS-NEXT-005/006/007`, and `Wave C = OSS-NEXT-003/004`, because `OSS-NEXT-003` depends on both activation baselines and `OSS-NEXT-004` depends on the governed-path regression refresh. `[starter-draft.md:24-47] [planning-session.json:241-244] [planning-session.json:274-276]`
- Ownership boundaries: `Claude` owns the two activation baselines, `Gemini` owns two backend-materialization lanes plus the regression refresh, and `Codex` owns the two decision gates plus `QuantLib` materialization. The active review discipline also shifts schema review to `Claude` and research/acceptance review to `Codex` because `Qwen` and `Copilot` are waived. `[README.md:68-71] [planning-session.json:54-79] [starter-draft.md:38-47]`

## Risks / Contradictions

- Risk 1: reviewer baton ambiguity. The brief and `review_sequence` both say the active reviewer order is `Gemini -> Claude -> Codex`, but `cross_review_rounds[0].reviewers` still lists `Qwen` and `Copilot`. That mismatch will create baton confusion unless one list is normalized. `[README.md:68-71] [planning-session.json:19-23] [planning-session.json:145-170]`
- Risk 2: consensus-packet lag. The machine state and shared draft already encode a concrete task graph, but the consensus packet is still empty on the exact fields that should ratify that graph. Without packet alignment, human review will inherit implicit architecture instead of explicit decisions. `[planning-session.json:173-416] [starter-draft.md:21-59] [consensus-packet.md:5-17]`
- Risk 3: gate semantics are still under-specified. `OSS-NEXT-003` depends on `OSS-NEXT-001` and `OSS-NEXT-002`, while `OSS-NEXT-004` depends on `OSS-NEXT-008`, but canon does not yet say whether those gates require full completion of upstream acceptance criteria or only enough evidence to make an approve/defer call. `[planning-session.json:188-190] [planning-session.json:218-220] [planning-session.json:241-244] [planning-session.json:274-276] [starter-draft.md:49-54]`
- Risk 4: Wave B is architecturally grouped but operationally split across owners. `vectorbt` and `statsmodels` are `Gemini` lanes, while `QuantLib` is a `Codex` lane. Reviewers should confirm that mixed ownership is intentional and not a sign that the backend-materialization tranche still needs another slicing pass. `[starter-draft.md:30-33] [planning-session.json:300-364]`

## Suggested Task Slices

- Slice 1: `Activation-ready realization` tranche. Keep `OSS-NEXT-001`, `OSS-NEXT-002`, and `OSS-NEXT-008` together as the evidence-building wave that unlocks later gate decisions. `[starter-draft.md:24-29] [planning-session.json:175-220] [planning-session.json:387-404]`
- Slice 2: `Missing backend materialization` tranche. Keep `OSS-NEXT-005`, `OSS-NEXT-006`, and `OSS-NEXT-007` together as the not-started backend wave, but explicitly confirm whether mixed ownership is acceptable before materialization. `[starter-draft.md:30-33] [planning-session.json:300-373]`
- Slice 3: `Conditional decision gate` tranche. Keep `OSS-NEXT-003` and `OSS-NEXT-004` as explicit post-baseline gate tasks with written approve/defer outputs and re-entry criteria. `[starter-draft.md:34-36] [planning-session.json:235-286]`
- Slice 4: `Planning-state normalization` tranche. Before human gating, reconcile the reviewer-order mismatch and fill the consensus packet so the architecture, delivery order, and task slices are explicit rather than only encoded in `planning-session.json`. `[starter-draft.md:56-59] [planning-session.json:145-170] [consensus-packet.md:5-17]`

## Citations

- `[README.md:35-54]` scopes the session around deferred-framework advancement, backend task materialization, and explicit defer decisions.
- `[README.md:68-81]` records the waived lanes, active reviewer order, and deferred-backend closure rule.
- `[planning-session.json:173-416]` holds the current machine-state task graph, including owners, reviewers, dependencies, and acceptance criteria for `OSS-NEXT-001` through `OSS-NEXT-008`.
- `[starter-draft.md:24-59]` is the current Codex-seeded mapping from the task graph into waves, disagreements, and reviewer hooks.
