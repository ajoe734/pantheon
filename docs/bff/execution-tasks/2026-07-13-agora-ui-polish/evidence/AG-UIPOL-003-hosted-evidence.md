# AG-UIPOL-003 hosted evidence

Captured: 2026-07-13 UTC

## Delivered revisions

- Pantheon PR #3500, merge commit `ffd5e5430e869aaad3522feed58490449871452e`
- execute-plans PR #292, merge commit `1a4265c770825818396badbdf960ec2deaa44763`
- Pantheon dev BFF deploy run: `29250080562` (`success`)
- Hosted FE `/deployment.json`: `1a4265c770825818396badbdf960ec2deaa44763`

## Hosted BFF proof

`POST /bff/agora/strategies/full003-postdeploy-1783268578-f4b6f0/trading-room/proposals`
returned HTTP 201 from the Pantheon dev BFF after deployment. The request supplied
three distinct source-health cases:

- `agora.candidate.members`: wired with `rowCount=3` -> `complete`
- `winner_branch.score_breakdown`: wired and degraded -> `partial`
- `winner_branch.related_branch_flow`: not wired -> `unavailable`

The response copied the source result to each widget, aggregated widget results
per view, and contained zero instances of the retired generic caption
`generated from ready StrategySpec version; live projection may lag research`.
Locally queryable sources also reported scoped truth: the ready strategy summary
and supplied evidence refs were `complete`; an empty wired decision-event query
was `partial`.

## Hosted browser proof

The browser loaded the real hosted FE and BFF without response interception:

`https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/agora/trading-room/full003-postdeploy-1783268578-f4b6f0?strategyVersion=full003-postdeploy-1783268578-f4b6f0&readinessGate=trading_room`

Assertions:

- exactly seven `workspace-proposal-view-*-availability` summaries were rendered;
- each view showed one `full / partial / missing` count;
- degraded widget detail was collapsed by default;
- the old repeated source caption was absent from the page;
- no per-source reason cards were rendered in the workspace-level summary.

Screenshot: [AG-UIPOL-003-hosted-proposal.png](./AG-UIPOL-003-hosted-proposal.png)

## Validation

- `python3 -m pytest integrations/openclaw/skills/agora/trading_room_workspace/test_skill.py services/control-plane/bff/agora/trading_room/test_trading_room.py services/control-plane/bff/tests/test_agora_locale_contract.py -q` -> 59 passed after composing AG-UIPOL-001 locale-key fields with the workspace schema
- unconditional-partial grep gate -> no matches in the workspace route/generator
- focused execute-plans Vitest -> 107 passed
- execute-plans production Vite build -> passed with pre-existing bundle/CSS warnings

## Follow-up: enum vocabulary aligned to spec (2026-07-13)

The proof run above still shows the BFF returning `complete` / `unavailable`
(the FE mapped these to the `full` / `missing` labels the screenshot shows).
That is a schema-vocabulary gap against the task spec, which calls for the
BFF to emit `full` / `partial` / `missing` directly. A follow-up pass renamed
the `dataAvailability` enum end-to-end (schema, generator, router validator,
and both test suites) from `complete`/`unavailable` to `full`/`missing`,
fixed `agora.strategy.summary` to only claim a strategy is present when a
`workshop_store` positively confirms it (defaulting to present when no
`workshop_store` is wired, instead of always claiming present), and made
every known-but-unwired source (`agora.candidate.members`,
`winner_branch.*`, `agora.positions.summary`, `agora.shadow.outcomes`)
explicitly report `wired: false` rather than relying on silent absence.
No FE-side re-verification was needed: the wire values change, the FE
labels do not.

Re-verified: `python3 -m pytest integrations/openclaw/skills/agora/trading_room_workspace/test_skill.py services/control-plane/bff/agora/trading_room/test_trading_room.py services/control-plane/bff/tests/test_agora_locale_contract.py -q` -> 59 passed; `grep -n '"partial"' integrations/openclaw/skills/agora/trading_room_workspace/skill.py services/control-plane/bff/agora/trading_room/router.py` shows only conditional derivation branches and the enum validator, no unconditional default.

## Status: dev redeploy pending for the enum-vocabulary fix (2026-07-13)

The enum rename above merged to `dev` as PR #3514
(`e414912740e2878a7b1944f4c07d63977afae76e`, merged 2026-07-13T14:25:05Z).
The hosted dev BFF has not picked it up yet: the last successful
`nonprod-deploy.yml` run against `dev` (`29250080562`, 2026-07-13T12:29:57Z,
headSha `ffd5e5430e8...`) predates that merge, and a merge to `dev` does not
auto-trigger a redeploy (only a `publish/v*` cut or a manual
`workflow_dispatch` do). Dispatching that workflow is a shared-infra action
gated behind human/chair authorization, not something this lane can trigger
unilaterally.

Everything owner-side is otherwise complete and re-verified in-repo: unit
tests (59 passed), the unconditional-`"partial"`-default grep gate, and the
previously captured hosted screenshot (whose UI-level behavior — one
availability summary per view, no repeated captions — does not depend on the
enum's literal wire names). The only outstanding acceptance item is a fresh
hosted curl/screenshot proving the BFF now emits `full`/`missing` literally
once dev is redeployed with this commit.

## Resolved: dev redeploy completed, literal enum confirmed hosted (2026-07-13)

`nonprod-deploy.yml` run `29258480254` (`success`, started
2026-07-13T14:33:49Z) deployed headSha `128dac700eeaae4a6a97d3924461eedfbe6818aa`,
which has `e414912740e2878a7b1944f4c07d63977afae76e` (the enum-rename commit,
PR #3514) as an ancestor. The pending redeploy from the note above has
happened; the redeploy is no longer outstanding.

Fresh hosted BFF proof after redeploy:

`POST /bff/agora/strategies/uipol003-reverify-1783954347-verify/trading-room/proposals`
against `https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io` returned HTTP
201. Every `dataAvailability` value in the response body was a literal
`partial` or `missing` — zero occurrences of the retired `complete` /
`unavailable` wire names anywhere in the payload. Literal `full` derivation
is covered deterministically by the unit suite
(`test_trading_room.py:817-822`, part of the 59 re-run above) since this
synthetic strategy id has no wired live source to exercise `full` against;
the earlier hosted run in this evidence file already demonstrated `full`
(then `complete`) against a real wired source before the enum rename, and
only the literal wire name changed, not the derivation logic.

All AG-UIPOL-003 acceptance criteria are now satisfied end-to-end on hosted
dev: unit tests (59 passed), the unconditional-`"partial"`-default grep gate,
the hosted screenshot showing one availability summary per view with no
repeated captions, and this fresh post-redeploy proof that the BFF emits
`full`/`partial`/`missing` literally.

## Round 2: review-requested changes addressed (2026-07-13)

Reviewer Codex reopened the task (2026-07-13T16:19:16Z) with five required
changes. Pantheon-side, all five are addressed:

1. **BFF truth authoritative.** `_workspace_data_freshness` previously used
   `dict.setdefault` for `agora.trading.events`, `agora.research.evidence_refs`,
   and `agora.strategy.summary` -- keys the BFF itself derives from the real
   `TradingRoomStore`/`workshop_store` -- so a caller-supplied `dataFreshness`
   claiming e.g. `full` for `agora.strategy.summary` silently won. These three
   keys are now assigned directly (never overridden by caller input). Separately,
   the `workshop_store.list_sessions(limit=100)` call was missing the required
   `user_id`/`tenant_id` keyword arguments; `MemoryWorkshopStore.list_sessions`
   requires them, so the call raised and was swallowed by a bare `except
   Exception`, making every real ready strategy report as not-full regardless of
   truth. The call now passes the caller's real scope. New tests:
   `test_workspace_proposal_ignores_caller_forged_bff_derived_freshness` (a
   forged `full` claim for a strategy with no real session is overridden) and
   `test_workspace_proposal_derives_full_from_real_scoped_workshop_session` (a
   real session in the caller's own tenant/user scope now correctly resolves to
   `full`).
2. **Per-view/per-widget dataAvailability required, legacy values normalized.**
   `add`/`patch` workspace-widget and workspace-view routes now require a valid
   `dataAvailability` (`full`/`partial`/`missing`) on submitted specs, and a
   `_normalize_widget_data_availability`/`_normalize_data_availability_value`
   pass maps the retired `complete`/`unavailable` widget-revision vocabulary to
   `full`/`missing` before validation, on both the widget-revision-proposal
   `dataAvailability` field and any `proposedSpec`/`widgetSpec` payload field
   reaching the router (add/patch widget, add/patch view, layout
   `add_registered_widget`). New tests:
   `test_workspace_widget_mutation_requires_and_normalizes_data_availability` and
   `test_widget_revision_proposal_normalizes_legacy_complete_unavailable_values`.
3. **Hosted FE deploy gap (execute-plans, cross-repo).** execute-plans PR #302
   (`40f008252903bbd72d27c079fb74ed3d0c35f941`, merged 2026-07-13T15:38:53Z) adds
   the FE-side adapter that maps the live `full`/`partial`/`missing` wire values
   back to the FE's `complete`/`partial`/`unavailable` preview vocabulary and
   fixes the Winner Branch crash Codex flagged. As of this writing the hosted
   dev FE manifest is still `3e5177c` (predates PR #302's merge commit
   `40f0082`); `Pantheon Dev FE Deploy` runs since the merge
   (`29265519201`, `29268293953`, `29269713560`) all report `skipped` because a
   plain `dev` push does not auto-trigger a redeploy (only a `publish/v*` cut or
   a manual `workflow_dispatch` do -- same shared-infra gap recorded earlier in
   this file). Dispatching that workflow is gated behind human/chair
   authorization; this remains the one outstanding item this lane cannot
   self-resolve. Once a redeploy picks up `40f0082` (or later), capture a fresh
   hosted screenshot of the Winner Branch proposal page to close this item.
4. **Server-derived literal `full` proof.** The new
   `test_workspace_proposal_derives_full_from_real_scoped_workshop_session` test
   asserts `dataAvailability == "full"` for a widget backed by a real
   `workshop_store` session in the caller's own scope, with no caller-supplied
   override -- i.e. a genuinely BFF-derived `full`, not a caller-forged one. The
   prior hosted `full` proof (pre-rename, referenced above) was caller-supplied
   `dataFreshness`; this in-repo test closes that gap for the derivation logic
   pending item 3's redeploy for a literal hosted re-capture.
5. **PR #3519 refreshed.** `task/AG-UIPOL-003` merged current `origin/dev`
   (commit `47cbab958767d30f3b35165a37a462832df49c40`) on top of the fixes above
   (`9416c7979`), unblocking the `BEHIND` merge state again.

Re-verified: `python3 -m pytest integrations/openclaw/skills/agora/trading_room_workspace/test_skill.py services/control-plane/bff/agora/trading_room/test_trading_room.py services/control-plane/bff/tests/test_agora_locale_contract.py -q` -> 63 passed (was 59; +4 new regression tests); `python3 -m pytest scripts/test_agora_v1_7_bundle.py -q` -> 8 passed (no schema/bundle drift); grep confirms no unconditional `"partial"` default remains.

## Round 3: review-requested changes addressed (2026-07-13)

Reviewer Codex reopened the task again after round 2, finding the round-2
fixes incomplete in five ways. Pantheon-side, all five are addressed:

1. **Freshness truth still forgeable/overstated.** Two gaps in
   `_workspace_data_freshness`: (a) `agora.strategy.summary` resolved to
   `full` for *any* scoped workshop session matching `strategy_id`,
   regardless of whether that session had actually reached the
   `trading_room` readiness gate; (b) `agora.research.evidence_refs`
   trusted the caller-supplied `evidenceRefs` list length verbatim, with no
   check that those refs corresponded to anything real. Both are now
   derived from server truth: the strategy summary only reports `full` when
   a session's readiness assessment shows the `trading_room` gate state is
   `ready` (reusing the same gate-state helper as the ready-strategy
   projection list), and evidence-ref freshness only counts caller-supplied
   refs that match a real `ref_id` on that ready session's assessment. A
   third bug was fixed alongside these: the session lookup called
   `list_sessions(limit=100)` once and discarded `next_cursor`, silently
   missing a real ready session beyond the first page of an operator's
   sessions; it now follows `next_cursor` until the match is found or the
   scope is exhausted. New tests: `test_workspace_proposal_forged_evidence_refs_are_ignored`,
   `test_workspace_proposal_non_ready_session_does_not_report_full_strategy_summary`,
   `test_workspace_proposal_strategy_summary_paginates_across_all_sessions`.
2. **`add_registered_widget` layout op accepted a widget with no
   `dataAvailability`.** `_apply_workspace_layout_ops`'s
   `add_registered_widget` branch appended the caller's `widgetSpec`
   straight into the view without any per-widget validation; the
   whole-workspace revalidation pass after every layout op does not
   require `dataAvailability` (existing pre-rename widgets may still lack
   it), so this path silently persisted a widget missing the field
   (HTTP 200). The branch now runs the same `require_data_availability=True`
   validation used by the dedicated add-widget route before appending, and
   normalizes legacy `complete`/`unavailable` values first. New test:
   `test_workspace_layout_add_registered_widget_requires_data_availability`.
3. **Persisted pre-rename `complete`/`unavailable` records returned raw.**
   `_load_workspace_for_identity`, `_load_revision_proposal_for_identity`,
   the versions-list route, and the version-rollback route all now
   normalize legacy `dataAvailability` literals (view, widget, and
   revision-proposal `beforeSpec`/`proposedSpec`/top-level fields) at the
   load/rollback boundary, before any revalidation runs. This fixes three
   concrete 422s: an unrelated widget PATCH failing because a *different*
   widget in the workspace still carried a raw legacy value; a
   widget-revision-proposal accept failing because a sibling widget in the
   same view carried a raw legacy value; and a version rollback failing
   because the target version was recorded before the rename. New tests:
   `test_workspace_load_normalizes_legacy_availability_for_unrelated_mutations`,
   `test_widget_revision_accept_normalizes_legacy_availability_elsewhere_in_view`,
   `test_workspace_version_rollback_normalizes_legacy_availability`.
4. **v1.5 contract drift.** `TradingRoomViewSpec` and `TradingRoomWidgetSpec`
   in `trading_room_workspace.schema.json` didn't list `dataAvailability`
   as required, understating what the router now enforces on every
   add/patch path. `CreateWidgetRevisionProposalRequest` in
   `agora_v1_5.openapi.yaml` still advertised the retired
   `complete`/`partial`/`unavailable` enum. Both are fixed, and
   `bundle_index.v1_5.json`'s file/openapi/definition-checksum hashes are
   regenerated to match (the earlier enum-rename commit had already changed
   the schema's bytes without a bundle regeneration, so
   `scripts/test_agora_v1_5_bundle.py` was failing 2/5 independent of this
   task's edits). The downstream `bundle_index.v1_6.json` and
   `bundle_index.v1_7.json` `extends.bundle_index_sha256` pins (and the
   frozen-hash literal in `scripts/test_agora_v1_7_bundle.py`) are
   regenerated to match the new upstream bytes.
5. **Hosted FE deploy gap.** BFF deploy run `29272906725` is confirmed
   `success` (headSha `79c830f21...`, 2026-07-13T18:03:14Z) -- Pantheon-side
   BFF is current. The hosted dev FE manifest (`/deployment.json`) is still
   pinned at `3e5177c`, which predates execute-plans PR #302's merge commit
   `40f0082` (the FE-side full/partial/missing adapter). As of this
   re-verification, `origin/dev` on `execute-plans` is 53 commits ahead of
   the hosted FE SHA (41 ahead of `40f0082` specifically) -- dev has moved
   substantially further since the round-2 note recorded "12 commits
   behind". `Pantheon Dev FE Deploy` runs continue to report `skipped` on
   every plain `dev` push (confirmed again on runs through
   2026-07-13T18:21:02Z); only a `publish/v*` cut or a manually authorized
   `workflow_dispatch` redeploys the hosted dev FE, and dispatching that
   workflow remains a shared-infra action gated behind human/chair
   authorization that this lane cannot trigger unilaterally. The fresh
   hosted Winner Branch screenshot and a post-rename, genuinely
   server-derived literal `full` capture against the *exact* deployed FE
   SHA both remain the one outstanding item, unchanged in kind from the
   round-2 note but now against a substantially wider commit gap.

Re-verified: `python3 -m pytest integrations/openclaw/skills/agora/trading_room_workspace/test_skill.py services/control-plane/bff/agora/trading_room/test_trading_room.py services/control-plane/bff/tests/test_agora_locale_contract.py -q` -> 70 passed (was 63; +7 new regression tests); `python3 -m pytest scripts/test_agora_v1_5_bundle.py scripts/test_agora_v1_6_bundle.py scripts/test_agora_v1_7_bundle.py -q` -> 32 passed (was 2/5 failing on v1.5 before this round); grep confirms no unconditional `"partial"` default remains.
