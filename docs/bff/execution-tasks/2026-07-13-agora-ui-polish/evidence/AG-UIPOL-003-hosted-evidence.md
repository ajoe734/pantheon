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
4. **v1.5 contract drift -- superseded mid-task by a concurrent frozen-v1.5
   decision.** The initial diagnosis (`TradingRoomViewSpec`/
   `TradingRoomWidgetSpec` in `trading_room_workspace.schema.json` not
   requiring `dataAvailability`, `CreateWidgetRevisionProposalRequest` in
   `agora_v1_5.openapi.yaml` still advertising the retired
   `complete`/`partial`/`unavailable` enum) was correct, and an initial fix
   regenerated `bundle_index.v1_5.json`/`v1_6.json`/`v1_7.json` to match.
   While that fix was in flight, `AG-UIPOL-001` (commit `b6034dbc9`,
   reviewer Codex2, merged to `dev` 2026-07-13T18:09:19Z) explicitly
   *restored* `trading_room_workspace.schema.json` to its frozen v1.5 state
   and created an additive `specs/agora/v8/trading_room_workspace_v1_7.schema.json`
   as the new live contract for this domain (`test_trading_room.py`'s
   `_workspace_schema_validate` now points at the v1.7 file). Merging that
   in produced a broken hybrid (part-frozen, part-renamed) file; the merge
   was reset and redone cleanly. This task's v1.5/v1.6 edits were reverted
   byte-for-byte to `origin/dev` (`trading_room_workspace.schema.json`,
   `agora_v1_5.openapi.yaml`, `bundle_index.v1_5.json`, `bundle_index.v1_6.json`,
   `bundle_index.v1_7.json`, and the frozen-hash literal in
   `scripts/test_agora_v1_7_bundle.py` are now identical to `dev`, verified
   with `git diff origin/dev -- <path>` for each). The substantive fix --
   requiring `dataAvailability` on `TradingRoomViewSpec`/`TradingRoomWidgetSpec`
   -- was re-applied to the correct, live file instead:
   `specs/agora/v8/trading_room_workspace_v1_7.schema.json` (its
   `WidgetRevisionProposal` definition already required it).
   `bundle_index.v1_7.json`'s `files` hash for that one schema is refreshed
   to match (no test currently locks it, but leaving a stale hash next to a
   correct one would be misleading). `CreateWidgetRevisionProposalRequest`'s
   enum bug in `agora_v1_5.openapi.yaml` is a genuine drift against current
   runtime behavior, but the file is part of the same frozen v1.5 bundle
   (untouched by AG-UIPOL-001, originally established by `AG-XR-DYNUI-001`)
   with no v1.7 counterpart to redirect the fix to; deliberately left
   unfixed rather than unilaterally reinterpreting a decision made under a
   different task/reviewer pair. Flagging as a residual gap for a
   follow-up that either amends the frozen-bundle scope or adds a v1.7
   OpenAPI delta for this request body.
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

Re-verified after reconciling with `origin/dev`'s frozen-v1.5/live-v1.7 split: `python3 -m pytest integrations/openclaw/skills/agora/trading_room_workspace/test_skill.py services/control-plane/bff/agora/trading_room/test_trading_room.py services/control-plane/bff/tests/test_agora_locale_contract.py -q` -> 70 passed (was 63; +7 new regression tests); `python3 -m pytest scripts/test_agora_v1_2_bundle.py scripts/test_agora_v1_5_bundle.py scripts/test_agora_v1_6_bundle.py scripts/test_agora_v1_7_bundle.py scripts/test_agora_compat_manifest.py -q` -> 41 passed; grep confirms no unconditional `"partial"` default remains.

## Round 4: review-requested changes addressed (2026-07-13)

Reviewer Codex reopened the task again after round 3 (findings recorded at
19:02), rejecting the round-3 code as unchanged against two items. Both are
now fixed pantheon-side; the third is the same standing deploy gap.

1. **Source health still forgeable for nine known families; no-store path
   still forged `full`.** `_workspace_data_freshness` used
   `resolved.setdefault(source, {...})` for the nine known-but-unwired
   source families (`agora.candidate.members`, `winner_branch.*`,
   `agora.positions.summary`, `agora.shadow.outcomes`) -- `setdefault` only
   fills in a key when the caller's `dataFreshness` did not already supply
   it, so a caller sending e.g. `{"winner_branch.score_breakdown": {"wired":
   true, "rowCount": 999}}` had that forged value pass straight through
   unchecked, since the BFF has no local query path for any of the nine.
   Separately, `has_ready_strategy` defaulted to `True` whenever no
   `workshop_store` was wired at all, so `agora.strategy.summary` reported
   `rowCount: 1` (and, via `_source_availability`, `full`) from a plain POST
   with no verification of any kind; `agora.research.evidence_refs` in the
   same no-store branch trusted the caller-supplied `evidenceRefs` list
   length verbatim as `rowCount`. Reproduced exactly as the reviewer
   described: a normal POST with no `workshop_store` wired returned
   `agora.trading.events`, `agora.strategy.summary`, and
   `agora.research.evidence_refs` all effectively full/present.

   Fixed: the nine known-unwired sources are now assigned unconditionally
   (`resolved[source] = {...}`, not `setdefault`) to `wired: false,
   rowCount: 0`, so no caller-supplied value for them ever survives, wired
   or not. `has_ready_strategy` now defaults to `False`, and
   `agora.strategy.summary["wired"]` is `workshop_store is not None` --
   without a workshop store this source is `missing`, not a caller-trusted
   `partial`/`full`. `agora.research.evidence_refs` in the no-store branch
   is now `wired: false, rowCount: 0` instead of echoing the caller's claimed
   ref count. New tests:
   `test_workspace_proposal_forces_missing_for_unverified_known_sources_regardless_of_caller_claim`
   (anti-forgery: all nine known-unwired sources report `missing` even when
   the caller claims `full`/`rowCount: 999` for every one of them) and
   `test_workspace_proposal_no_workshop_store_reports_strategy_and_evidence_missing`
   (no-store regression: a plain POST with no `workshop_store` wired at all
   never reports `agora.strategy.summary` or `agora.research.evidence_refs`
   as `full`). Two pre-existing tests
   (`test_workspace_proposal_derives_widget_availability_from_scoped_sources`,
   `test_workspace_proposal_preserves_generator_metadata_on_create_and_get`)
   had encoded the old vulnerable behavior as their expected assertions
   (asserting a caller-forged `dataFreshness` for one of the nine sources
   produced `full`/`partial`); both are corrected to assert the honest
   `missing` outcome instead.

2. **Session scan still stopped at the first matching session, not the
   first ready one.** Round 3 fixed `list_sessions()` to follow
   `next_cursor` across pages, but within that corrected pagination the loop
   still picked the first session matching `strategy_id` it encountered
   (`next(... , None)` then `break`) and only checked *that one* session's
   readiness. An operator with an older, non-ready workshop session for a
   strategy_id and a separate newer, ready session for the same
   `strategy_id` (a real scenario: re-running a workshop after an earlier
   attempt) had the older non-ready session found first (sessions are
   listed oldest-first), so the ready session was never inspected and
   `agora.strategy.summary` incorrectly reported `rowCount: 0` (`missing`)
   despite a real ready strategy existing in scope.

   Fixed: the scan now collects every session matching `strategy_id` across
   all pages first, then iterates that full list checking readiness on each
   one, stopping only once a session whose `trading_room` gate is `ready` is
   found (or the list is exhausted). New test:
   `test_workspace_proposal_strategy_summary_finds_ready_session_behind_non_ready_one`
   (an older non-ready session and a newer ready session share the same
   `strategy_id`; the ready one's evidence refs and readiness are still
   found and reported).

3. **Hosted FE deploy gap -- unchanged.** Pantheon-side BFF work for this
   fix has not been deployed yet (it is not merged to `dev`). The
   standing gap from rounds 2/3 is unchanged in kind: the hosted dev FE
   manifest was still pinned at `3e5177c` (pre-execute-plans-PR-#302) as of
   the last check, and `Pantheon Dev FE Deploy` does not auto-fire on a
   plain `dev` push -- only a `publish/v*` cut or a human/chair-authorized
   `workflow_dispatch` redeploys it. Once this round-4 fix merges to `dev`
   (BFF deploys automatically on merge, per rounds 2/3) and the FE redeploy
   gap above is separately resolved by a human, capture a fresh hosted
   curl/screenshot pair against the exact deployed SHAs for both.

Re-verified: `python3 -m pytest integrations/openclaw/skills/agora/trading_room_workspace/test_skill.py services/control-plane/bff/agora/trading_room/test_trading_room.py services/control-plane/bff/tests/test_agora_locale_contract.py -q` -> 73 passed (was 70; +3 new regression tests); `python3 -m pytest scripts/test_agora_v1_2_bundle.py scripts/test_agora_v1_5_bundle.py scripts/test_agora_v1_6_bundle.py scripts/test_agora_v1_7_bundle.py scripts/test_agora_compat_manifest.py -q` -> 41 passed (no schema/bundle drift); grep confirms no unconditional `"partial"` default remains.
