# MGMT-GAP-008 BFF Handoff Follow-up 2

| Field | Value |
|---|---|
| Task ID | `MGMT-GAP-008-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` |
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
governance policy. It follows the earlier sidecar packet
`support/sidecars/MGMT-GAP-008/MGMT-GAP-008-SIDECAR-BFF-HANDOFF.md` and updates
the handoff now that the parent task has moved into review.

## Sources Read

| Source | Relevant finding |
|---|---|
| `AI_COLLABORATION_GUIDE.md` | L0 state coordinates ownership; support packets do not override canonical architecture or policy truth. |
| `.orchestrator/task-briefs/mgmt_gap_008_sidecar_bff_handoff_followup_2.md` | This sidecar is support-only: summarize BFF query gap, operator journey, and frontend handoff material without changing canonical truth. |
| `AI_NAME=Codex ./scripts/ai-status.sh show MGMT-GAP-008-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` | Task is `in_progress`, owner `Codex`, reviewer `Claude`, artifact path is this file. |
| `AI_NAME=Codex ./scripts/ai-status.sh show MGMT-GAP-008` | Parent is in `review`; base delivery is execute-plans PR #133 and follow-up bugfix is PR #135. |
| `support/sidecars/MGMT-GAP-008/MGMT-GAP-008-SIDECAR-BFF-HANDOFF.md` | Earlier packet already covers the broad BFF query gap, operator journeys, route aliases, empty capability registries, and evidence resolution expectations. |
| `gh pr view 133 --repo ajoe734/execute-plans` | PR #133, `MGMT-GAP-008: fix detail DTO/render honesty`, merged to `dev` at 2026-07-01T10:49:14Z. |
| `gh pr view 135 --repo ajoe734/execute-plans` | PR #135, `MGMT-GAP-008: fix remaining id/name/kind and capability-loading gaps`, is open against `dev` at head `619cabd52c220de97141ef5cb40e05a0892d96f3`; integration-gate was pending/in progress when checked. |
| `gh pr diff 135 --repo ajoe734/execute-plans --name-only` | Follow-up PR #135 changes only `src/lib/bff-v1/seed.ts`, `ToolDetail.tsx`, `McpDetail.tsx`, and `SkillDetail.tsx`. |
| `gh pr view 2669 --repo ajoe734/pantheon` | Pantheon PR #2669 is open, spec-only, and `BEHIND`; parent status says it should be closed or superseded in favor of execute-plans delivery PRs. |

`current-work.md` and the full `ai-activity-log.jsonl` were not scanned.

## Delta Since Earlier Packet

The broad handoff packet should remain the main support reference. This
follow-up narrows review attention to the live issues found after PR #133
merged and before parent closeout:

| Area | Current state | Reviewer meaning |
|---|---|---|
| Base parent delivery | execute-plans PR #133 is merged. | Treat the shared normalizer, status/risk fallback, alias redirect factory, and empty registry list states as delivered baseline unless the hosted environment disproves them. |
| Remaining live defects | execute-plans PR #135 fixes three reproed defects: experiment blank h1, artifact `undefined` subtitle, and capability detail 404 loading forever. | Review #135 for narrow correctness first; do not reopen the whole MGMT-GAP-008 surface unless new probes expose another in-scope raw render defect. |
| BFF query gap | No new BFF route gap was identified by this follow-up. | The remaining fixes are frontend DTO aliasing and rejected-detail handling, not new BFF endpoints or canonical contract edits. |
| Capability registries | The list-empty state was already addressed in #133; detail 404 handling is the #135 delta. | Confirm Tools, MCP servers/tools, and Skills all leave the spinner and render the same live-empty/not-found detail state after strict-live 404. |
| Pantheon spec PR | Pantheon PR #2669 is still open and behind. | Parent/reviewer should close or supersede it once execute-plans #133/#135 are accepted, because the actual implementation lives in execute-plans. |

## Follow-up Review Checklist

| Check | Expected proof |
|---|---|
| Experiment detail id/name recovery | `/management/experiments/exp-mgmt-qlib-006` has a non-empty h1 using `experiment_id`/`experiment_name` aliases, with no raw `undefined`. |
| Artifact kind subtitle | `/management/artifacts/rart-20260615-002` does not render `undefined` in the subtitle; `artifact_type` is adapted or an explicit unavailable fallback is shown. |
| Capability detail 404 | Stale or seed ids for Tools, MCP servers/tools, and Skills render `CapabilityDetailEmptyState` or equivalent live-empty/not-found copy, not a permanent loading shell. |
| Regression envelope | Previously clean capital-pool, deployment, channel, and alias redirect probes remain clean after #135. |
| Command boundary | Any write-like buttons remain governed by the MGMT-GAP-004 command/receipt truth; #135 should not claim toast-only actions are production-safe. |
| BFF contract boundary | No new canonical endpoint, OpenAPI, or L1 policy change is required for the #135 defects. |

## Operator Journey Delta

### Journey E: Follow-up Detail Defects Stay Honest

1. Operator opens `/management/experiments/exp-mgmt-qlib-006`.
2. FE maps `experiment_id` and `experiment_name` into the display header.
3. Operator opens `/management/artifacts/rart-20260615-002`.
4. FE maps artifact-specific `artifact_type` into the subtitle or renders an
   explicit unavailable value.
5. Operator opens stale capability detail ids for Tools, MCP, and Skills while
   strict-live BFF returns 404.
6. FE catches the rejected detail call, marks the page loaded, and renders the
   live-empty/not-found state.

Success means the page is honest under strict-live data. It does not require
new BFF data fabrication or new canonical contracts.

## Suggested Parent Closeout Notes

If PR #135 merges cleanly, parent closeout can cite:

- PR #133 merged at 2026-07-01T10:49:14Z as the base MGMT-GAP-008 delivery.
- PR #135 as the narrow post-merge bugfix for experiment id/name aliases,
  artifact kind aliasing, and capability detail 404 loading.
- Hosted strict-live probes covering the three #135 repro routes plus the
  previously clean PR #133 routes.
- Pantheon PR #2669 as superseded or closed, since it is spec-only and behind
  while execute-plans contains the actual frontend delivery.

If PR #135 is still open or failing, parent `MGMT-GAP-008` should remain in
`review` and should not move to `review_approved` based on PR #133 alone.

## Handoff

This follow-up packet is ready for `Claude` review. The main actionable point is
that the remaining MGMT-GAP-008 delivery risk is no longer a broad BFF route
gap; it is whether execute-plans PR #135 merges and whether hosted strict-live
probes confirm the three post-merge defects are gone without regressing the
already-clean detail routes.
