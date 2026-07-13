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
