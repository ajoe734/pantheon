# MGMT-GAP-008 BFF Handoff Follow-up 3

| Field | Value |
|---|---|
| Task ID | `MGMT-GAP-008-SIDECAR-BFF-HANDOFF-FOLLOWUP-3` |
| Helper kind | `bff_handoff_packet` |
| Parent task | `MGMT-GAP-008` - Management detail DTO and render honesty |
| Parent owner / reviewer | `Claude` / `Codex2` |
| Prepared by | `Codex` |
| Reviewer | `Claude` |
| Date | 2026-07-01 |
| Mutates canonical truth | `false` |
| Status | Ready for reviewer handoff |

This packet is a support artifact only. It does not modify L1 canonical truth,
OpenAPI, BFF runtime code, frontend source, registry implementation, or
governance policy. It follows
`support/sidecars/MGMT-GAP-008/MGMT-GAP-008-SIDECAR-BFF-HANDOFF.md` and
`support/sidecars/MGMT-GAP-008/MGMT-GAP-008-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md`.

## Sources Read

| Source | Relevant finding |
|---|---|
| `AI_COLLABORATION_GUIDE.md` | L0 state coordinates ownership; support packets do not override canonical architecture or policy truth. |
| `.orchestrator/task-briefs/mgmt_gap_008_sidecar_bff_handoff_followup_3.md` | This sidecar is support-only: summarize BFF query gap, operator journey, and frontend handoff material without changing canonical truth. |
| `AI_NAME=Codex ./scripts/ai-status.sh show MGMT-GAP-008-SIDECAR-BFF-HANDOFF-FOLLOWUP-3` | Task is active, `in_progress`, owner `Codex`, reviewer `Claude`, artifact path is this file. |
| `AI_NAME=Codex ./scripts/ai-status.sh show MGMT-GAP-008` | Parent remains in `review`; owner notes PR #133 merged and PR #135 is the remaining follow-up bugfix. |
| `support/sidecars/MGMT-GAP-008/MGMT-GAP-008-SIDECAR-BFF-HANDOFF.md` | Original packet covers the broad BFF query gap, operator journeys, route aliases, empty capability registries, and evidence resolution expectations. |
| `support/sidecars/MGMT-GAP-008/MGMT-GAP-008-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md` | Follow-up 2 narrowed review to PR #135 and Pantheon PR #2669 disposition. |
| `docs/bff/execution-tasks/2026-06-30-management-console-production-gap/MGMT-GAP-008-detail-render-honesty.md` | Parent acceptance still requires no raw undefined/blank/NaN, canonical alias behavior, honest empty capability registry details, and archived evidence. |
| `gh pr view 133 --repo ajoe734/execute-plans` | PR #133, `MGMT-GAP-008: fix detail DTO/render honesty`, is merged to `dev` at 2026-07-01T10:49:14Z with merge commit `225765a81cbbaa9f958c0d9e97627425f555f5e2`. |
| `gh pr view 135 --repo ajoe734/execute-plans` | PR #135 remains open, non-draft, `CLEAN`, head `619cabd52c220de97141ef5cb40e05a0892d96f3`; `integration-gate` completed successfully at 2026-07-01T11:38:13Z. |
| `gh pr view 135 --repo ajoe734/execute-plans --json autoMergeRequest,reviewDecision,reviews,comments,latestReviews,commits` | PR #135 has no review decision, no latest reviews, and no auto-merge request recorded. The release-gate bot comment says overall `WARN`, but the critical gate summary says zero failing or missing checks. |
| `gh pr diff 135 --repo ajoe734/execute-plans --name-only` | PR #135 changes only `src/lib/bff-v1/seed.ts`, `ToolDetail.tsx`, `McpDetail.tsx`, and `SkillDetail.tsx`. |
| `gh pr diff 135 --repo ajoe734/execute-plans --patch` | Patch adds BaseObject id/name aliases, artifact `kind`/`sourceExperimentId` alias normalization, and catch paths so Tool/MCP/Skill detail 404s leave loading and render the live-empty/not-found state. |
| `gh pr view 2669 --repo ajoe734/pantheon` | Pantheon PR #2669 is still open, spec-only, and check-green; it should remain superseded/closeout context rather than the actual frontend delivery if execute-plans PR #133/#135 are accepted. |

`current-work.md` and the full `ai-activity-log.jsonl` were not scanned.

## Delta Since Follow-up 2

The main new fact is that execute-plans PR #135 is no longer merely pending CI:
GitHub reports the `integration-gate` check run as `SUCCESS`. That changes the
review posture from "wait for the gate" to "review the narrow patch, decide
whether the WARN items are unrelated/accepted, then merge or return #135."

| Area | Current state | Reviewer meaning |
|---|---|---|
| Base delivery | execute-plans PR #133 is merged. | Treat PR #133 as the baseline for shared header fallback, route alias redirects, and empty capability list states. |
| Follow-up PR | execute-plans PR #135 is open, clean, and check-green at head `619cabd52c220de97141ef5cb40e05a0892d96f3`. | It is ready for human review, but not merged and not reviewed. Parent `MGMT-GAP-008` should remain in `review` until #135 merges and the hosted repro routes are re-probed. |
| Release gate summary | The bot comment says overall `WARN`; the check run still concluded `SUCCESS`. WARN items are anonymous health/liveness probing, create dry-run skipped creates, and F10 rollback exception. | These WARNs do not directly refute the three #135 detail-render fixes, but parent closeout should cite them as release-gate residuals if left open. |
| BFF query gap | No new BFF route gap was identified. | The remaining #135 delta is frontend DTO aliasing and rejected-detail handling, not new BFF endpoints or canonical contract edits. |
| Pantheon PR #2669 | Still open and check-green, but spec-only. | Do not treat #2669 as the production delivery for MGMT-GAP-008. Parent/reviewer should close or supersede it once execute-plans delivery is accepted. |

## PR #135 Patch Summary

| File | Handoff summary |
|---|---|
| `src/lib/bff-v1/seed.ts` | Adds `id` alias recovery for `experiment_id`, `artifact_id`, `pool_id`, `plan_id`, `channel_id`, and `incident_id`; expands `name` aliases with `experiment_name`, `plan_name`, and `pool_name`; adds artifact-specific normalization for `artifact_type -> kind` and `produced_by_experiment_id -> sourceExperimentId`. |
| `src/management/pages/ToolDetail.tsx` | Catches rejected `bff.tools.get(id)` calls and marks the page loaded so missing/seed ids render the live-empty/not-found detail state instead of a permanent spinner. |
| `src/management/pages/McpDetail.tsx` | Applies the same rejected-detail loading fix to MCP server and MCP tool detail pages. |
| `src/management/pages/SkillDetail.tsx` | Applies the same rejected-detail loading fix to skill detail pages. |

This patch is intentionally narrow. It does not prove every possible
domain-specific id alias by itself; the parent evidence still needs the hosted
route probe envelope from `MGMT-GAP-008` acceptance.

## Reviewer Focus For Follow-up 3

| Check | Expected proof before parent approval |
|---|---|
| PR #135 review state | `gh pr view 135 --repo ajoe734/execute-plans` shows an accepted review or explicit reviewer decision, not only a green check. |
| PR #135 merge state | PR #135 is merged to execute-plans `dev`, or parent `MGMT-GAP-008` remains in `review` with the exact merge blocker recorded. |
| Hosted repro routes | Re-probe `/management/experiments/exp-mgmt-qlib-006`, `/management/artifacts/rart-20260615-002`, and stale Tool/MCP/Skill detail ids after #135 deploys; results show no blank h1, no literal `undefined` subtitle, and no permanent loading spinner. |
| Regression envelope | Re-probe previously clean capital-pool, deployment, channel, and alias-redirect detail routes so #135 does not regress PR #133. |
| Empty registry semantics | Strict-live 404 for Tools/MCP/Skills renders live-empty/not-found copy without seed detail leakage. |
| Release-gate WARN disposition | Parent closeout either explains why the #135 WARN items are unrelated to MGMT-GAP-008 or routes them to the owning gap task; it must not silently present WARN as a clean release decision. |
| Pantheon PR #2669 disposition | Parent closeout says #2669 is superseded/closed or gives a concrete reason it remains open. |

## Operator Journey Delta

### Journey F: Green Follow-up PR Still Needs Merge And Hosted Proof

1. Operator/reviewer sees execute-plans PR #135 has a successful
   `integration-gate` check.
2. Reviewer confirms the patch is limited to field alias recovery and
   capability detail rejected-fetch handling.
3. Reviewer confirms the release-gate WARN items are not new failures of the
   three MGMT-GAP-008 repro defects.
4. PR #135 merges to execute-plans `dev`.
5. Hosted strict-live probes run against the deployed #135 commit and the dev
   BFF.
6. Parent `MGMT-GAP-008` moves out of `review` only after the merged PR and
   hosted evidence prove the three repro routes plus the prior clean routes.

Success means the parent can close the render-honesty gap without claiming that
green CI alone is production proof.

## Suggested Parent Closeout Notes

If PR #135 merges and hosted probes pass, parent closeout can cite:

- execute-plans PR #133 merged at 2026-07-01T10:49:14Z with merge commit
  `225765a81cbbaa9f958c0d9e97627425f555f5e2` as the base delivery.
- execute-plans PR #135 head `619cabd52c220de97141ef5cb40e05a0892d96f3` as the
  narrow follow-up for experiment id/name aliases, artifact kind aliasing, and
  capability detail 404 loading.
- The #135 `integration-gate` success at 2026-07-01T11:38:13Z, plus the release
  gate WARN disposition.
- Hosted strict-live route evidence for the three #135 repro routes and the
  previously clean PR #133 routes.
- Pantheon PR #2669 as superseded/closed because execute-plans contains the
  actual frontend implementation.

If PR #135 remains open, unreviewed, or unmerged, `MGMT-GAP-008` should remain
in `review`; do not approve parent closeout based on PR #133 alone.

## Handoff

This follow-up packet is ready for `Claude` review. The main actionable point is
that PR #135 is now check-green but still lacks review/merge state. The BFF side
does not need new routes for the current repros; the parent needs #135 merge,
hosted strict-live proof, explicit WARN disposition, and Pantheon PR #2669
supersession before claiming `MGMT-GAP-008` complete.
