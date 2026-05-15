# WB-006 Acceptance Packet (Sidecar)

**Task ID**: `WB-006-SIDECAR-ACCEPTANCE`
**Parent Task**: `WB-006` — Define the Consultation Workbench backlog and wave plan
**Parent Owner**: Qwen
**Parent Reviewer**: Claude
**Sidecar Owner**: Claude
**Sidecar Reviewer**: Codex
**Helper Kind**: `acceptance_packet`
**Generated**: 2026-04-14T10:30:00Z
**Review Context Refresh**: 2026-04-14T10:40:06Z — sidecar review was later reassigned from Qwen to Codex in `ai-status.json`

> This is a support artifact only. It does not modify canonical truth, L1 policy files, or the main runtime / registry / governance implementation.

## Source References

| Document | Role |
|---|---|
| `ai-status.json` | Live task registry for `WB-006` and this sidecar |
| `docs/02-architecture/consensus/sessions/phase3-2026-04-14-pantheon-console-loop/planning-session.json` | Source of the parent task title, summary, dependencies, and acceptance criteria |
| `docs/02-architecture/consensus/sessions/phase3-2026-04-14-pantheon-console-loop/execution-materialization.md` | Confirms `WB-006` is Step 14 in workbench backlog definition and depends only on `LOOP-001` |
| `docs/02-architecture/consensus/sessions/phase3-2026-04-14-pantheon-console-loop/pantheon-console-workbench-backlog.md` | Primary artifact for Consultation Workbench objective, module inventory, current-state wording, readiness, dependencies, and wave |
| `docs/02-architecture/consensus/sessions/phase3-2026-04-14-pantheon-console-loop/qwen-readout.md` | Explicit statement that Consultation Workbench has no BFF surfaces, is blueprint only (§9.3.5 of 總索引), and should be marked `blocked_on_bff`; suggests Wave 3+ vs the parent artifact's Wave 4 |
| `docs/02-architecture/consensus/sessions/phase3-2026-04-14-pantheon-console-loop/claude-readout.md` | Cross-lane confirmation that `WB-005` through `WB-008` should be scoped as gap inventory and blocker list, not Lovable-ready packets; non-BFF-backed workbenches need a different handoff model |
| `Pantheon_總索引版系統分析文件.md` §9.3.5 | Blueprint section that anchors Consultation Workbench domain truth (per qwen-readout.md citation) |

---

## 1. Acceptance Checklist For Parent Task `WB-006`

This checklist is derived from the three `WB-006` acceptance items in `ai-status.json`.

### AC-1: All four planning modules are mapped

> `consult request, committee board, debate transcript, and red-team memo are all mapped to planning modules`

| # | Verification Item | Evidence | Status |
|---|---|---|---|
| 1.1 | Consultation Workbench exists as its own workbench in the backlog summary table | `pantheon-console-workbench-backlog.md` summary table | ✅ Verified |
| 1.2 | `Consult request` is listed as a separate screen/module | `pantheon-console-workbench-backlog.md` → Consultation Workbench → Screens and modules | ✅ Verified |
| 1.3 | `Committee board` is listed as a separate screen/module | same section | ✅ Verified |
| 1.4 | `Debate transcript` is listed as a separate screen/module | same section | ✅ Verified |
| 1.5 | `Red-team memo` is listed as a separate screen/module | same section | ✅ Verified |

**Verdict**: AC-1 is fully evidenced by the current parent artifact. All four modules appear by name in the Screens and modules inventory.

### AC-2: Missing backend orchestration dependencies are surfaced explicitly

> `missing backend orchestration dependencies are surfaced explicitly`

| # | Verification Item | Evidence | Status |
|---|---|---|---|
| 2.1 | The parent artifact has a distinct `Backend dependencies` section | `pantheon-console-workbench-backlog.md` → Consultation Workbench | ✅ Verified |
| 2.2 | `Consultation orchestration and transcript surfaces` are listed as backend requirements | same section | ✅ Verified |
| 2.3 | `Committee and red-team domain truth` is listed as a backend requirement | same section | ✅ Verified |
| 2.4 | There are no BFF surfaces for Consultation Workbench today — parent artifact does not falsely imply any existing support | `Existing Pantheon support: blueprint-level direction only` — no partial or in-progress claims | ✅ Verified |
| 2.5 | Qwen's readout cross-confirms: `No BFF surfaces — Blueprint only` and explicitly recommends `blocked_on_bff` status | `qwen-readout.md` Risk 3 table | ✅ Corroborated |
| 2.6 | The two backend dependency bullets are stated at category level without further decomposition | `pantheon-console-workbench-backlog.md` → Consultation Workbench → Backend dependencies | ⚠️ Reviewer should decide whether `consultation orchestration and transcript surfaces` and `committee and red-team domain truth` are sufficiently granular, or whether the backlog should decompose these into discrete BFF route gaps analogous to the Research Workbench section |

**Verdict**: AC-2 is structurally satisfied. The main open question is depth: the Research Workbench (WB-003) provides a per-module BFF gap and packetization-prerequisite breakdown, while WB-006 uses two broader backend-dependency bullets. The reviewer should decide whether that depth gap is acceptable given WB-006's Wave 4 placement.

### AC-3: The backlog states Consultation Workbench is not yet packet-ready and needs canonical screen specs first

> `the backlog states that Consultation Workbench is not yet packet-ready and needs canonical screen specs first`

| # | Verification Item | Evidence | Status |
|---|---|---|---|
| 3.1 | Parent artifact states `Missing canonical screen specs: all Consultation Workbench screens` | `pantheon-console-workbench-backlog.md` → Consultation Workbench | ✅ Verified |
| 3.2 | Parent artifact marks `Lovable readiness: not ready` | same section and summary table | ✅ Verified |
| 3.3 | Parent artifact marks backend dependency on orchestration and domain truth before any Lovable invitation | same section → Backend dependencies | ✅ Verified |
| 3.4 | The summary table in the parent artifact labels Consultation Workbench as `no` for Lovable-ready and `high` for backend dependency | `pantheon-console-workbench-backlog.md` summary table | ✅ Verified |
| 3.5 | Cross-lane readouts (Qwen and Claude) both confirm that WB-006 should remain a gap inventory and not enter the packet-ready set | `qwen-readout.md` Risk 3 table; `claude-readout.md` WB-005 through WB-008 scoping statement | ✅ Corroborated |

**Verdict**: AC-3 is fully satisfied. The parent artifact clearly marks Consultation Workbench as not-packet-ready and explicitly requires canonical screen specs and backend support as prerequisites.

---

## 2. Dependency Map

### 2.1 Parent Dependency

`WB-006` has one formal upstream dependency:

```text
LOOP-001 -> WB-006
```

Why this matters:

- `execution-materialization.md` places `WB-006` at Step 14 of the phase3 rollout — immediately after `WB-005` (Trainer) and before `WB-007` (Governance, which depends on `PKT-001`).
- `WB-006` is a backlog-definition slice. It does not require the `PKT-*` family, the front-repo bootstrap (`LOOP-003`), or any BFF route implementation to write the inventory.
- `LOOP-001` is confirmed `done` as of 2026-04-14 per the task brief.

### 2.2 Important Non-Dependencies

These are not formal blockers for `WB-006` itself, but they shape how the parent artifact should be reviewed:

| Item | Why it is not a direct dependency of `WB-006` | Why it still matters later |
|---|---|---|
| `LOOP-003` | `WB-006` defines backlog structure, not front-repo dispatch mirroring | Any future Consultation packet / Lovable handoff will need front-repo bootstrap before a UI task can be dispatched |
| `PKT-*` family | Consultation is not part of the APP-002 packet-ready family | Consultation packetization is a future Wave 4 slice even after APP-002 packetization finishes |
| Consultation orchestration and transcript BFF routes | The parent task is allowed to inventory these gaps without implementing them | These are the actual blockers that keep Consultation out of the packet-ready set; they must land before any Lovable task can be scoped |
| Committee and red-team domain truth | Parent task names them as backend requirements, not as resolved dependencies | Domain model for committee lifecycle and red-team memo semantics must be defined at L1 or service contract level before BFF routes can be spec'd |
| Research Workbench (WB-003) | No formal dependency declared | Both WB-003 and WB-006 are Wave 3+ / Wave 4 blueprint-only workbenches; they may share orchestration or domain architecture patterns when their BFF surfaces are eventually defined |

### 2.3 Wave Placement Note

The parent artifact places Consultation Workbench at **Wave 4**, the latest of all eight workbenches. Qwen's readout independently assessed it as **Wave 3+**.

This is not a conflict requiring resolution before WB-006 closes — Wave 4 is a more conservative, defensible position for a workbench with no BFF surfaces. The reviewer should confirm whether the parent artifact's Wave 4 designation is intentional and should stand, or whether a `Wave 3+` label is preferred to match the qwen readout.

### 2.4 Downstream Consumers

There are no direct downstream execution tasks materialized in `ai-status.json` yet that depend on `WB-006`.

The intended future consumers are:

1. A Consultation Workbench BFF / contract-definition slice for consultation orchestration and transcript surfaces.
2. A committee and red-team domain model slice to define lifecycle states, membership semantics, and memo payload contracts.
3. A future per-module BFF route definition for each of the four modules (Consult request, Committee board, Debate transcript, Red-team memo) — analogous to the per-module prerequisite breakdown the Research Workbench already provides.
4. A front-end / Lovable handoff only after all four modules have canonical screen specs and backed BFF routes.

### 2.5 Reviewer Gates

Before the parent task `WB-006` is accepted, the reviewer should confirm:

| Gate | Question | Expected outcome |
|---|---|---|
| G1 | Are all four Consultation modules listed by name? | Yes: Consult request, Committee board, Debate transcript, Red-team memo |
| G2 | Does the artifact explicitly mark all four modules as missing their canonical screen specs? | Yes, under `Missing canonical screen specs: all Consultation Workbench screens` |
| G3 | Are the two backend dependency categories explicit enough? | Reviewer should accept the current category-level statement, or request per-module BFF gap decomposition analogous to the Research Workbench section |
| G4 | Is the `Lovable readiness: not ready` statement unambiguous? | Yes — both the section and summary table confirm `no` readiness |
| G5 | Should `Wave 4` stand, or should the backlog adopt `Wave 3+` to align with Qwen's readout? | Reviewer should decide and request a wave label adjustment only if alignment across lanes matters for the current sprint; both are conservative positions |

---

## 3. Support Notes

### 3.1 What This Sidecar Establishes

- `WB-006` is a backlog and readiness-definition task, not a Consultation UI implementation task.
- The current parent artifact satisfies all three acceptance criteria structurally: the four modules are listed, the backend dependencies are named, and the not-packet-ready status is unambiguous.
- The two open review questions are about precision and depth, not about missing required content:
  - **Depth**: the parent artifact's backend-dependency bullets are category-level; should they be decomposed per module like the Research Workbench section?
  - **Wave label**: `Wave 4` (parent artifact) vs `Wave 3+` (Qwen readout) — both are defensible conservative positions

### 3.2 What This Sidecar Does Not Do

- It does not invent new Consultation Workbench contracts.
- It does not upgrade any of the four modules toward Lovable readiness.
- It does not define BFF routes for consultation orchestration, transcript, committee, or red-team surfaces.
- It does not resolve the Wave 3+ vs Wave 4 interpretation — it only makes that review decision explicit.
- It does not propose a per-module BFF gap breakdown — it only notes that the reviewer should decide whether the category-level statement is sufficient.

### 3.3 Review Posture

This sidecar assumes the reviewer may accept the parent artifact in either of two ways:

1. Accept the current category-level backend dependency statement and Wave 4 placement as appropriate for a blueprint-only workbench with no BFF surfaces and no near-term packetization path.
2. Reopen the parent task and ask for a tighter Consultation section that decomposes backend dependencies per module (Consult request, Committee board, Debate transcript, Red-team memo) and optionally aligns the wave label with the qwen readout.

Either outcome is consistent with this sidecar remaining support-only.

---

## 4. Handoff Packet To Reviewer

**From**: Claude
**To**: Codex
**For**: `WB-006-SIDECAR-ACCEPTANCE` review handoff record, and secondarily as evidence scaffolding the parent lane can use while closing `WB-006`

### Delivered In This Sidecar

1. A parent-task acceptance checklist mapped to the actual phase3 source files and cross-lane readouts.
2. A dependency map that distinguishes the true parent dependency (`LOOP-001`, already done) from the future Consultation BFF blockers.
3. A reviewer scaffold for the two open judgment calls in `WB-006`:
   - whether backend dependency bullets should remain category-level or be expanded per module
   - whether `Wave 4` should stand or be softened to `Wave 3+` to match the Qwen readout

### Recommended Review Outcome Logic

- Approve this sidecar if the packet is accurate and useful as support material.
- For the parent task `WB-006`, do not treat the current artifact as a Consultation packetization slice.
- If category-level backend dependency bullets are acceptable for a Wave 4 blueprint-only workbench, the current parent artifact is ready to approve.
- If per-module BFF gap decomposition is needed for consistency with the Research Workbench format, reopen the parent task and request that Qwen add per-module prerequisite blocks analogous to the Research section.

### Suggested Reviewer Comment For Parent Task

`WB-006` is acceptable as a backlog-definition artifact. All four planning modules are listed, the not-packet-ready status is unambiguous, and backend dependencies are named. The two optional tightening opportunities are: (1) decompose backend dependencies per module (Consult request, Committee board, Debate transcript, Red-team memo) to match the Research Workbench section format; (2) consider whether `Wave 4` or `Wave 3+` better reflects the current sprint sequencing intent given Qwen's cross-lane assessment. Neither is a blocking defect unless backlog-level consistency requires it.

---

*Prepared by Claude for the `WB-006-SIDECAR-ACCEPTANCE` sidecar slice. This file is intentionally support-only and does not modify canonical truth.*
