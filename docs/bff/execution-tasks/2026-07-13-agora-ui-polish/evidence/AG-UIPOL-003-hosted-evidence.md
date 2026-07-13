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
