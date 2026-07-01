# MGMT-GAP-008 - Management Detail DTO And Render Honesty

Owner: Claude
Reviewer: Codex
Batch: 2.5
Fleet lane: frontend detail honesty
Depends on: `MGMT-GAP-002`, `MGMT-GAP-003`

## Problem

The 2026-07-01 hosted re-audit proved that route render success is not enough.
Several live-id detail pages open but display impossible production text such as
`status.undefined`, `risk.undefined`, blank h1 fields, blank owner/update fields,
or `NaN%`. Empty capability registries also expose seed-like detail ids that 404
against live BFF data.

These are operator trust failures. The console must show live truth, explicit
empty/degraded state, or a clear unavailable reason.

## Scope

Fix detail render honesty for the live-id route families captured in the
re-audit:

- capital pool detail: `/management/capital/:id`
- capital pool alias: `/management/capital-pools/:id`
- experiment/research detail: `/management/experiments/:id`,
  `/management/research/:id`
- artifact detail: `/management/artifacts/:id`
- deployment detail: `/management/deployments/:id`
- channel detail: `/management/channels/:id`
- evidence detail: `/management/evidence/:id`
- empty capability detail families: `/management/tools/:id`,
  `/management/mcp/:id`, `/management/skills/:id`

Required behavior:

1. normalize missing enum/status/risk values before rendering;
2. render `Unknown`, `Unassigned`, `No update`, or `N/A` style placeholders
   instead of raw `undefined`, blank h1, blank owner/update, or `NaN%`;
3. convert old detail aliases to redirects or route through one canonical mapper
   so aliases cannot drift;
4. show explicit `live registry empty` or `not found in live registry` states
   for Tools/MCP/Skills instead of seed-id loading shells;
5. show evidence source resolution as live, degraded, or unavailable with the
   reason and source id.

## Non-Scope

- Do not fabricate source data to make detail pages look complete.
- Do not mark empty Tools/MCP/Skills registries as populated.
- Do not hide command-truth gaps; hand them to `MGMT-GAP-004`.

## Acceptance

- Hosted live-id probe finds no `status.undefined`, `risk.undefined`,
  standalone `undefined`, blank h1, blank owner/update, or `NaN%` on in-scope
  detail pages.
- Alias routes either redirect to the canonical path or share a tested canonical
  DTO mapper.
- Empty capability detail paths fail honestly with live-empty/not-found copy and
  no seed id is promoted as production data.
- Tests cover success, missing optional fields, unknown enum values, empty
  registry, not found, and degraded evidence-source resolution.
- Evidence is archived under the management-console gap archive with route ids,
  BFF payload family, screenshot or DOM text probe, commit SHA, and PR link.
