# Review Round 01

Use cited comments only. Do not directly rewrite `starter-draft.md` unless you currently hold the baton.

## Reviewer Order

- Gemini
- Claude
- Codex

## Comments

### Gemini

- **EP5 Boundary (Risk 1)**: I agree with the `starter-draft.md` and `codex-readout.md` regarding the risk of including `EP5-002`. The objective specifically mentions "explicitly separating later EP5 canary/live proof" [`planning-session.json:8-9`]. `EP5-002` represents the actual proof execution, which should be gated behind a successful `EP5-001` prerequisite-only wave. I recommend keeping `EP5-002` as a "disputed/later gate" as suggested in the draft.
- **Runtime Auth/Identity (Slice A)**: I strongly support the prioritization of `OSS-004A`. To satisfy `OPENCLAW_RUNTIME_CONTRACT.md` §7.1, the acceptance criteria for `OSS-004A` should explicitly include verification of "per-agent workspace isolation" and "no implicit credential sharing" [`OPENCLAW_RUNTIME_CONTRACT.md:7.1`].
- **Rollback Feasibility (Slice B)**: The inclusion of an integrated rollback drill in `OSS-004C` is critical for EP4 proof [`EXECUTION_PROOF_AND_MATURITY_LEVELS.md:27`]. From a feasibility standpoint, this drill must verify the `pause_then_replace` strategy [`ROLLBACK_AND_POSITION_SEMANTICS.md:3.2`] to ensure the control-plane can stabilize the runtime before replacement.
- **Documentation Parity (Risk 2)**: I concur with the need to reconcile `README.md` with the machine-readable state in `planning-session.json` [`README.md:40`]. Stale brief file lists in the README could lead to inconsistent research bases for other agents.

### Codex

- **Initial Materialization Boundary**: I agree with preserving `EP5-002` as a downstream, disputed slice rather than silently bundling it into the first execution batch. The session objective explicitly separates later EP5 canary/live proof from the current EP4 paper-runtime plan [`planning-session.json:8-9`], and `execution-materialization.md` now treats the first batch as EP4 proof-raising + EP4 proof publication before any canary/live proof claim is made [`execution-materialization.md:7-26`].
- **EP4 Substrate Acceptance**: I agree with Gemini that `OSS-004A` needs explicit isolation and telemetry-parity checks, not only generic "auth path stabilized" wording. The authoritative contract requires `per-agent workspace` and `no implicit credential sharing` [`OPENCLAW_RUNTIME_CONTRACT.md:218-221`], and the paper/canary/live policy requires shared telemetry semantics with `deployment_stage` and `is_real_order` flags [`PAPER_CANARY_LIVE_POLICY.md:269-270`].
- **EP4 Proof Packet**: I also agree that `OSS-004C` should cite at least one `pause_then_replace` rollback drill instead of treating rollback as an unspecified appendix. EP4 requires rollback evidence as part of the cross-plane paper proof [`EXECUTION_PROOF_AND_MATURITY_LEVELS.md:27`], and `pause_then_replace` is a distinct mitigation path with its own cutover semantics [`ROLLBACK_AND_POSITION_SEMANTICS.md:57-69`].
- **Planning Workspace Parity**: The earlier README mismatch is no longer an active blocker. The session README now reflects the brief files, review order, and workflow boundaries that were previously only present in `planning-session.json` [`README.md:11-27`, `README.md:54-67`, `planning-session.json:13-40`]. The remaining open disagreement is scope, not documentation drift.

### Claude

- **Facilitator Convergence**: The cross-review path has converged on an `EP4-first` planning boundary. `OSS-004A` through `OSS-004D` form the accepted stable-EP4 path, while `EP5-001` remains a prerequisite-only downstream slice and `EP5-002` stays explicitly deferred from the initial materialization batch [`starter-draft.md:6-21`, `execution-materialization.md:7-26`].
- **Accepted EP4 Proof Shape**: There is agreement that the EP4 substrate is not complete unless `OSS-004A` encodes workspace/auth isolation plus telemetry parity, and `OSS-004C` archives a cited rollback drill rather than only a happy-path run [`OPENCLAW_RUNTIME_CONTRACT.md:218-221`, `PAPER_CANARY_LIVE_POLICY.md:269-270`, `ROLLBACK_AND_POSITION_SEMANTICS.md:57-69`, `EXECUTION_PROOF_AND_MATURITY_LEVELS.md:27`].
- **Human Gate Focus**: The remaining question is not architecture semantics or documentation parity; it is whether the human gate wants this session to materialize only the EP4 batch now, or also keep `EP5-001` queued as a clearly downstream post-EP4 prerequisite wave. `EP5-002` should not be included in the first batch by default [`planning-session.json:8-9`, `planning-session.json:257-305`, `execution-materialization.md:7-26`].
