# Claude Readout

Session: `phase3-2026-04-14-pantheon-console-loop`
Round: 0
Date: 2026-04-14
Status: submitted

---

## Lane

- **Agent**: Claude
- **Capability focus**: Facilitator — closed-loop protocol integrity, cited disagreement integration, screen-packet vs BFF-handoff distinction, consensus packet ownership. Also covering Copilot fallback on front-end IA and Lovable packet critique per `planning-session.json` §fallback_policy.

---

## Canonical Sources Read

- **L0**: `.coordination/README.md`, `ai-status.json`
- **L1**: `OPERATOR_ACCEPTANCE_MATRIX.md`
- **L2**: `docs/delivery-coordination-bus.md`, `docs/orchestrator-state-plane-redesign.md`, `docs/02-architecture/consensus/sessions/phase3-2026-04-14-pantheon-console-loop/planning-session.json`, `starter-draft.md`, `consensus-packet.md`
- **L3**: `Pantheon_總索引版系統分析文件.md` (§2–4, §9–13)
- **Sidecars**: `APP-002-SIDECAR-BFF-HANDOFF.md`, `APP-002-FRONTEND-STATE-MATRIX.md`, `APP-002-W2-READ-INCIDENT`, `APP-002-W2-CONTROL-INCIDENT`, `APP-002-W3-POSTINCIDENT-EVOLUTION`, `APP-002-W4-PERSONA-MGMT`, `APP-002-W4-REMAINING-CATALOG`, `APP-002-W5-SSE-LIVE`

---

## Working Interpretation

### Architecture summary

The Pantheon ↔ Lovable closed loop rests on four already-implemented payload types in `.coordination/`: `contract-ready`, `lovable-ui-task`, `bff-gap`, and `ui-done`. Three further types exist (`needs-runtime`, `needs-engine`, `dispatch-request`) for escalation. The delivery coordination bus doc formalizes GitHub as the audit surface while Pantheon retains runtime and governance authority.

The APP-002 wave series (W1–W5) produced **BFF handoff packets** (sidecars), not Lovable UI task packets. These are related but structurally distinct artifacts:

- A **BFF handoff sidecar** describes what the BFF contract exposes: endpoint list, staleness semantics, composed view shape, write-side gaps, canonical objects. It is aimed at Codex / Qwen / BFF owners.
- A **Lovable UI task packet** is a short prompt packet plus screen spec, BFF example responses, and action gating rules. It is aimed at Lovable and a human UI executor. Only `F-042` currently exists as a true Lovable-ready packet (per `starter-draft.md`).

This distinction is the most important structural gap between what has been delivered (wave 1–5 BFF truth) and what `PKT-001` to `PKT-005` must still produce.

### Delivery order

I agree with the starter-draft proposed wave order with one sequencing adjustment (see Risks §1 below):

1. Closed-loop infra: extend `.coordination` payload schema, define GitHub `repository_dispatch` target, bootstrap front-repo mirror.
2. Packetize APP-002-backed screens into actual Lovable UI task packets (`PKT-001` to `PKT-005`).
3. Expand into the 8-workbench backlog — but only `WB-001` (Governance/Promotion), `WB-002` (Incident Response), `WB-003` (Post-Incident/Evolution), and `WB-004` (Persona Management) have BFF truth ready. `WB-005` through `WB-008` (Research, Knowledge, Trainer, Consultation) should be scoped as **gap inventory and blocker list**, not Lovable-ready packets, until backend support lands.
4. Human gate → materialize `LOOP-*`, `PKT-*`, `WB-*` into `ai-status.json`.

### Ownership boundaries

- `.coordination/` remains canonical machine protocol per `README.md` rule: "`.coordination` remains the canonical machine protocol; this session must not introduce `.ai-loop` as a second source of truth."
- Lovable is explicitly human-triggered per `delivery-coordination-bus.md` §6: "Pantheon does not treat Lovable as a zero-touch worker … A human opens the Lovable project and explicitly submits the task."
- Planning artifacts must not enter execution plane. Per `docs/orchestrator-state-plane-redesign.md` §4.1: "execution tasks are materialized outputs from planning, not live edits inside planning docs."
- `OPERATOR_ACCEPTANCE_MATRIX.md` is L1 and defines 5 operator surfaces (`S-BFF`, `S-IAPI`, `S-CLI`, `S-EMRG`, `S-SUPP`). Pantheon Console only reaches `S-BFF`. Non-BFF-backed workbenches (Research, Knowledge, Trainer, Consultation) will need a different handoff model when their backend plane is defined.

---

## Risks / Contradictions

### Risk 1: GitHub `repository_dispatch` is proposed, not currently implemented

**Evidence**: `delivery-coordination-bus.md` §5 documents GitHub comment commands (`/dispatch`, `/needs-runtime`, `/contract-ready`, etc.) as the current live coordination primitives. These work via comment-triggered orchestrator parsing. The session objective calls for a "GitHub dispatch model" which implies `repository_dispatch` (cross-repo webhook automation from Pantheon into `front-ai-trading-system`). This is forward-looking infrastructure, not a current capability.

**Implication**: `LOOP-002` (GitHub automation target) should be sequenced after a label bus audit and should clearly scope whether it means "formalize the comment-dispatch protocol" or "implement `repository_dispatch` webhook triggers." Conflating these in the same task risks scope creep. I recommend `LOOP-002` define the event schema and GitHub action contract, and `LOOP-003` implement the cross-repo mirror bootstrap as a prerequisite.

**Starter-draft open disagreement**: "whether front-repo GitHub dispatch should be the primary trigger immediately, or only after the legacy GitHub issue bus and labels are stabilized." I support: label bus stabilization first; `repository_dispatch` as a second-wave trigger. The current comment-command system already provides replay and escalation primitives.

### Risk 2: `backend-delivery` version fields are under-specified for the current front-end reality

**Evidence**: `starter-draft.md` raises "whether `contracts_version` and `sdk_version` stay mandatory in `backend-delivery` when the front repo is still using direct BFF client wiring rather than a published SDK." The W5 sidecar confirms the BFF is live at versioned HTTP endpoints, not a published SDK package. The W4-REMAINING-CATALOG sidecar confirms all 33 surfaces are implemented in `main.py`.

**Recommendation**: The `backend-delivery` payload should carry `bff_contract_version` (a semantic version or hash tied to `BFF_API_CONTRACT.md`) as mandatory, and `sdk_version` as optional/omitted until a front-end SDK package is published. Requiring `sdk_version` now would create phantom version strings with no artifact backing.

**Impact on `LOOP-001`**: The payload schema extension for `frontend-feedback` and `backend-delivery` must resolve this field question before `PKT-*` tasks can use the schema. This is a prerequisite, not a parallel track.

### Risk 3: Sidecar packets ≠ Lovable UI task packets — `PKT-*` scope is under-specified

**Evidence**: `starter-draft.md` states "only `F-042` currently exists as a true Lovable-ready packet; the rest of APP-002 is mostly sidecar handoff truth, not canonical screen packets." I confirm this reading. The sidecars describe BFF contract truth but do not contain:
- A short Lovable prompt packet (the task brief a human pastes into Lovable)
- Screen wireframe or information architecture spec
- BFF example response JSON keyed to the actual screen layout
- Button/action gating rules in a front-end-consumable format (the `APP-002-FRONTEND-STATE-MATRIX.md` has this for Deployment Review, but it is a design artifact, not a canonical `.coordination/` payload)

**Implication**: `PKT-001` to `PKT-005` each need a deliverable definition that goes beyond "refer to the sidecar." The sidecar is an input; the Lovable UI task packet is the output. The deliverable gap for each `PKT-*` task is: prompt packet text + screen IA spec + example BFF response + gating rules, packaged as a `.coordination/responses/lovable-ui-task-*.yaml`.

### Risk 4: Non-APP-002 workbench packetization readiness is overstated if treated symmetrically

**Evidence**: The Pantheon 總索引版 §9 covers Console, BFF, Persona, Consultation as "第一包." §10 covers Source Ingestion, Knowledge, Research, Policy Learning, Optimizer. §11 covers Capital Pool, Governance, Execution. §12 covers Telemetry, Postmortem, Evolution. The APP-002 wave work touched Governance (W1), Incident/Control (W2), Evolution/Post-Incident (W3), Persona (W4), and SSE (W5). Research, Knowledge, Trainer, and Consultation have **no BFF-backed composed views** as of the current sidecar inventory.

**Implication**: `WB-005` through `WB-008` (or whichever workbench IDs cover Research, Knowledge, Trainer, Consultation) must be explicitly scoped as "gap inventory + backend blocker list" in the workbench backlog. Claiming these are Lovable-ready or packet-ready in the same wave as `PKT-001` to `PKT-005` would be premature. The `starter-draft.md` open question about "how much of Operator Home, Research, Knowledge, Trainer, and Consultation should be packetized before backend gaps are closed" should be answered conservatively: define the gap, do not produce Lovable packets.

### Risk 5: `planning-session.json` marks `consensus-packet.md` as `draft` with `owner: Claude` — but readouts are not yet complete

**Evidence**: `planning-session.json` §expected_outputs shows `consensus_packet` with `status: draft` and `owner: Claude`. The current `consensus-packet.md` contains a pre-drafted decision summary and agreed task slices. Per `README.md` §Baton Loop rule 5: "the facilitator drafts `consensus-packet.md`" — but this happens after cited cross-review is complete. The session instructions also explicitly say: "不要提早定稿 consensus packet，除非所有 readout 已齊."

**Facilitation note**: I will not finalize the consensus packet in this round. The existing `consensus-packet.md` content is a pre-populated draft placeholder; it should be treated as pending, not accepted, until Qwen, Gemini, Copilot, and Codex readouts are complete.

---

## Suggested Task Slices

These are readout-level suggestions, not execution-board items. Materialization awaits human gate.

### LOOP-001 — Extend `.coordination/` payload schema

- Add `frontend-feedback` and `backend-delivery` as first-class payload types to `.coordination/README.md` and any schema validators.
- Resolve `bff_contract_version` (mandatory) vs `sdk_version` (optional/omitted) before implementation.
- Input: current `.coordination/README.md` payload type list. Output: updated schema + validation.
- Owner: Codex. Blocker for: all `PKT-*` tasks.

### LOOP-002 — Define GitHub cross-repo dispatch contract

- Scope: define the `repository_dispatch` event schema for `pantheon → front-ai-trading-system` triggers.
- Do not conflate with the existing comment-command bus; document as a second-wave automation layer.
- Prerequisite: confirm label bus is stable (GitHub label inventory audit).
- Owner: Codex. Reviewer: Claude (feasibility check against delivery bus §5).

### LOOP-003 — Front-repo `.coordination/` mirror bootstrap

- Scope: define how Pantheon mirrors `contract-ready`, `lovable-ui-task`, prompt packets, and BFF example docs into the front-repo checkout so Lovable sees the same handoff.
- Must not introduce `.ai-loop` or any secondary protocol surface.
- Owner: Codex.

### PKT-001 — Governance/Promotion Review Lovable UI task packet

- Input: `APP-002-SIDECAR-BFF-HANDOFF.md` §3.1 and `APP-002-FRONTEND-STATE-MATRIX.md` §3.1.
- Output: `.coordination/responses/lovable-ui-task-governance-review.yaml` with prompt packet, screen IA, BFF example response JSON, and button gating rules.
- Missing piece: write-side `ApprovalDecision` command contract (noted in `APP-002-SIDECAR-BFF-HANDOFF.md` §3.1).
- Owner: Codex. Reviewer: Copilot.

### PKT-002 — Incident Response Lovable UI task packet

- Input: `APP-002-W2-READ-INCIDENT` and `APP-002-W2-CONTROL-INCIDENT` sidecars.
- Output: `.coordination/responses/lovable-ui-task-incident-response.yaml`.
- Owner: Codex. Reviewer: Qwen (contract truth audit).

### PKT-003 — Post-Incident / Evolution Review Lovable UI task packet

- Input: `APP-002-W3-POSTINCIDENT-EVOLUTION-SIDECAR-BFF-HANDOFF.md`.
- Output: `.coordination/responses/lovable-ui-task-postincident-evolution.yaml`.
- Known gap: `EV-004` evolution execution boundary not yet settled (noted in `APP-002-SIDECAR-BFF-HANDOFF.md` §2).
- Owner: Codex. Reviewer: Gemini (runtime boundary feasibility).

### PKT-004 — Persona Management Lovable UI task packet

- Input: `APP-002-W4-PERSONA-MGMT-SIDECAR-BFF-HANDOFF.md`.
- Output: `.coordination/responses/lovable-ui-task-persona-management.yaml`.
- `snapshot` param alignment is non-blocking per sidecar review notes.
- Owner: Codex. Reviewer: Claude (already reviewed W4 sidecar).

### PKT-005 — Global degradation banner + SSE live state packet

- Input: `APP-002-W5-SSE-LIVE-SIDECAR-BFF-HANDOFF.md`, `APP-002-FRONTEND-STATE-MATRIX.md` §2.
- Output: `.coordination/responses/lovable-ui-task-sse-live-state.yaml` covering all 3 SSE streams and the 5-state data freshness model.
- Owner: Codex. Reviewer: Qwen.

### WB-001 through WB-004 — Workbench backlog for BFF-backed workbenches

- Governance/Promotion Review, Incident Response, Post-Incident/Evolution, Persona Management.
- Each entry: module inventory, existing BFF support citations, missing write-side specs, Lovable readiness flag, backend dependency list, execution wave assignment.
- These can reference the corresponding `PKT-*` task as the packet source.
- Owner: Codex.

### WB-005 through WB-008 — Gap inventory for blueprint-only workbenches

- Research, Knowledge, Trainer, Consultation (and/or Operator Home if not covered above).
- Scope: define what backend support would need to land before a Lovable packet is possible. Do **not** produce a Lovable UI task packet in this wave.
- Output per workbench: backend gap list, Pantheon blueprint section citation, estimated blockers.
- Owner: Codex. Reviewer: Claude (facilitation check on scope boundary).

---

## Open Questions for Cross-Review

The following three open questions from `starter-draft.md` need explicit positions from other lanes before I can draft the consensus packet:

1. **`backend-delivery` version fields** — Is `bff_contract_version` (not `sdk_version`) acceptable as the mandatory version identifier until a front-end SDK is published? (Qwen and Codex should opine.)

2. **GitHub dispatch sequencing** — Should label bus stabilization be a hard prerequisite for enabling `repository_dispatch` automation, or can both tracks run in parallel? (Gemini should cover runtime/automation feasibility; Codex owns the implementation track.)

3. **Non-APP-002 workbench packetization wave boundary** — Do we define the gap inventory for Research, Knowledge, Trainer, Consultation now (recommended), or defer the `WB-005` to `WB-008` tasks entirely until backend support lands? (Copilot lane, or Claude coverage per fallback policy if Copilot is waived.)

---

## Citations

- [`.coordination/README.md`] "`.coordination` remains the canonical machine protocol; this session must not introduce `.ai-loop` as a second source of truth." — session `README.md` §Rules
- [`docs/delivery-coordination-bus.md` §6] "Pantheon does not treat Lovable as a zero-touch worker … A human opens the Lovable project and explicitly submits the task."
- [`docs/delivery-coordination-bus.md` §5] `/dispatch pantheon-bff F-xxx` and `/contract-ready F-xxx` are live GitHub comment commands — current bus capability.
- [`starter-draft.md`] "only `F-042` currently exists as a true Lovable-ready packet; the rest of APP-002 is mostly sidecar handoff truth, not canonical screen packets."
- [`APP-002-SIDECAR-BFF-HANDOFF.md` §2] "APP-002 should not pretend the final evolution execution boundary is settled until `EVO-004` lands."
- [`APP-002-W5-SSE-LIVE-SIDECAR-BFF-HANDOFF.md` §2.1] Three SSE streams live: runtime events, incident events, kill-switch updates — all with `last_event_id` replay and 30s heartbeat.
- [`APP-002-W4-REMAINING-CATALOG-SIDECAR-BFF-HANDOFF.md` §2] All 33 canonical read surfaces implemented in `main.py` and backed by `ReadSurfaceStore`.
- [`OPERATOR_ACCEPTANCE_MATRIX.md` §3] Five operator surfaces: `S-BFF`, `S-IAPI`, `S-CLI`, `S-EMRG`, `S-SUPP`. Pantheon Console reaches only `S-BFF`.
- [`docs/orchestrator-state-plane-redesign.md` §4.1] "execution tasks are materialized outputs from planning, not live edits inside planning docs."
- [`Pantheon_總索引版系統分析文件.md` §9–12] Package inventory confirms Research, Knowledge, Trainer, Consultation have no BFF-backed composed views in the current wave — they are blueprint-only.
- [`planning-session.json` §lane_focus.Claude] "Facilitate the Pantheon Console loop session, integrate cited disagreements, and finalize the consensus packet once loop mechanics and workbench backlog converge."
