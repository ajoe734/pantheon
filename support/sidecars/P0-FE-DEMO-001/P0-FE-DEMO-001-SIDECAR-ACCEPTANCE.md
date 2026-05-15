# P0-FE-DEMO-001 Acceptance Packet - Sidecar Support

**Sidecar Task ID:** P0-FE-DEMO-001-SIDECAR-ACCEPTANCE
**Parent Task:** P0-FE-DEMO-001 - Cut demo auth and demo islands from staging/prod frontend
**Sidecar Owner:** Codex
**Sidecar Reviewer:** Claude
**Parent Owner at Closeout:** Codex2
**Parent Reviewer:** Codex
**Prepared:** 2026-05-01
**Status:** Review approved; closeout prepared

> Scope note: this is a support artifact only. It does not modify canonical truth,
> frontend runtime code, registry code, governance code, or core contracts. State
> transitions for this sidecar are handled through `scripts/ai-status.sh`.

---

## 1. Packet Sources

This packet is based on the task-scoped context and support records below:

| Source | Use |
|---|---|
| `.orchestrator/task-briefs/p0_fe_demo_001_sidecar_acceptance.md` | sidecar scope, owner, reviewer, artifact path |
| `ai-status.json` | active sidecar state and sidecar acceptance criteria |
| `docs/02-architecture/consensus/sessions/phase6-2026-05-01-pantheon-p0-paper-loop/planning-session.json` | parent task materialization |
| `ai-task-archive/tasks/P0-FE-DEMO-001.json` | parent terminal state, handoffs, closeout metadata |
| `docs/04/pantheon_p0_sd/SD-P0-05_Frontend_Production_Adoption_Demo_Cleanup.md` | parent SD and closeout evidence |
| `support/reviews/P0-FE-DEMO-001-codex-review.md` | reviewer approval and verification summary |
| `support/reviews/P0-FE-DEMO-001-SIDECAR-ACCEPTANCE-claude-review.md` | sidecar reviewer approval |
| `support/sidecars/P0-FE-DEMO-001/P0-FE-DEMO-001-SIDECAR-REVIEW.md` | sibling review-packet sidecar context |

No full `current-work.md` or full `ai-activity-log.jsonl` scan was used.

---

## 2. Parent Acceptance Map

Parent materialized acceptance from the planning session:

| ID | Acceptance criterion | Evidence pointer | Packet status |
|---|---|---|---|
| AC-PARENT-1 | staging/prod bundle has no `@/demo/api` auth import and no demo token path | `support/reviews/P0-FE-DEMO-001-codex-review.md`; parent archive `next`; SD-P0-05 closeout evidence | Recorded as approved |
| AC-PARENT-2 | production operator/governance/runtime routes fail CI on forbidden demo imports | `npm run check:prod-demo-routes` in review and parent closeout evidence | Recorded as approved |

Parent task `P0-FE-DEMO-001` is already archived `done` with terminal outcome
`completed` at `2026-05-01T04:45:40Z`. Pantheon closeout commit:
`5038e37c64398522e3e4fb8464aacdb4252da493`.

Frontend implementation evidence recorded by the parent:

| Frontend commit | Purpose |
|---|---|
| `d321a9b` | removed production demo auth paths |
| `ea284a1` | preserved existing approved token on successful session refresh without replacement token |

Reviewer outcome:

| Field | Value |
|---|---|
| Review file | `support/reviews/P0-FE-DEMO-001-codex-review.md` |
| Reviewed commit | `ea284a1b32470bfddbbbd86093656f26dc23e48f` |
| Outcome | Approved; prior auth lifecycle blocker fixed |

---

## 3. Sidecar Acceptance Checklist

| Check | Status | Notes |
|---|---|---|
| Create support artifacts only | Done | The owner-authored packet and Claude review artifact are both support-only files. |
| Do not edit canonical truth | Done | No L1 canonical truth, runtime, registry, governance, or frontend implementation files are changed by this sidecar. |
| Prepare acceptance checklist | Done | Parent acceptance criteria are mapped to evidence in section 2. |
| Prepare dependency map | Done | See section 4. |
| Hand off packet to assigned reviewer | Done | Claude approved this sidecar in `support/reviews/P0-FE-DEMO-001-SIDECAR-ACCEPTANCE-claude-review.md`. |
| Owner closeout | Ready | Codex should create a task-scoped commit and run `scripts/ai-status.sh done` after closeout verification. |

---

## 4. Dependency Map

### 4.1 Parent Task Dependencies

| Dependency | Status | Notes |
|---|---|---|
| Explicit `depends_on` | None | Parent materialization lists an empty dependency array. |
| Planning source | Resolved | Source session: `phase6-2026-05-01-pantheon-p0-paper-loop`; consensus and human gate were materialized before execution. |
| Parent SD artifact | Durable | `docs/04/pantheon_p0_sd/SD-P0-05_Frontend_Production_Adoption_Demo_Cleanup.md` contains final closeout evidence. |
| Parent review | Complete | Codex approved commit `ea284a1`; no blocking findings remain. |
| Parent closeout | Complete | Parent task archived `done`; Pantheon closeout commit `5038e37`. |

### 4.2 Related But Not Blocking

| Item | Relationship |
|---|---|
| `P0-FE-DEMO-001-SIDECAR-REVIEW` | Sibling sidecar already archived `done`; it summarizes the review/evidence packet. |
| `P0-FE-SOURCE-001` | Deferred source-mode badge/runtime identity scope; not required for this sidecar or parent acceptance. |
| Full OIDC implementation | Explicitly out of scope for parent SD-P0-05. |
| Live broker enablement | Explicitly out of scope and remains fail-closed. |

### 4.3 Sidecar Dependencies

This sidecar has no runtime, service, CI, or cross-repo implementation dependency.
It only depends on the parent task records and support artifacts listed in section 1.

---

## 5. Verification Performed For This Packet

Support-only verification performed in the Pantheon repo:

```bash
sed -n '1,260p' .orchestrator/task-briefs/p0_fe_demo_001_sidecar_acceptance.md
jq '.tasks[] | select(.id=="P0-FE-DEMO-001-SIDECAR-ACCEPTANCE")' ai-status.json
jq '.. | objects | select(.id? == "P0-FE-DEMO-001" or .task_id? == "P0-FE-DEMO-001")' docs/02-architecture/consensus/sessions/phase6-2026-05-01-pantheon-p0-paper-loop/planning-session.json
sed -n '1,220p' ai-task-archive/tasks/P0-FE-DEMO-001.json
sed -n '1,220p' support/reviews/P0-FE-DEMO-001-codex-review.md
git status --short
```

No frontend/runtime tests were rerun for this sidecar because the task scope is an
acceptance packet only. Parent task verification is recorded in the parent archive
and SD closeout evidence:

```text
npm run check:prod-demo-routes
npm run build
npx eslint src/auth/AuthProvider.tsx src/pages/auth/Login.tsx src/lib/bffClient.ts src/pages/settings/sections/SecuritySettings.tsx scripts/check_no_demo_prod_routes.mjs
```

---

## 6. Review Approval And Closeout

Claude reviewed this sidecar packet and approved it on 2026-05-01:

| Review artifact | Outcome |
|---|---|
| `support/reviews/P0-FE-DEMO-001-SIDECAR-ACCEPTANCE-claude-review.md` | Approved; sidecar scope stayed support-only, parent evidence mapped correctly, and the parent terminal state was verified. |

Owner closeout verification performed after review approval:

```bash
sed -n '1,260p' support/sidecars/P0-FE-DEMO-001/P0-FE-DEMO-001-SIDECAR-ACCEPTANCE.md
python3 scripts/ai_status.py show P0-FE-DEMO-001-SIDECAR-ACCEPTANCE
sed -n '1,260p' support/reviews/P0-FE-DEMO-001-SIDECAR-ACCEPTANCE-claude-review.md
git status --short
git diff -- support/sidecars/P0-FE-DEMO-001/P0-FE-DEMO-001-SIDECAR-ACCEPTANCE.md
```

Closeout remains support-only. The only task-owned files to include in the
closeout commit are this packet and the Claude review artifact.
