# Persona Fleet Mutation And Evolution Journal Gap - 2026-07-10

Status: gap specification and execution source of truth

Owner: Codex

Scope:

- `/management/persona-fleet`
- `/management/evolution-journal`
- `execute-plans:src/management/pages/oversight/personaFleetLinks.ts`
- `execute-plans:src/management/pages/oversight/evolutionJournalFocus.ts`
- `execute-plans:src/management/pages/oversight/_core.tsx`
- management BFF payloads that feed Persona Fleet and Evolution Journal

Related operations workflow:

- `docs/04/pantheon_management_console_operations_workflow_2026-07-07/MANAGEMENT_CONSOLE_OPERATIONS_WORKFLOW_PLAN.md`
- `docs/bff/execution-tasks/2026-07-07-management-console-operations-workflow/INDEX.md`
- `docs/04/pantheon_management_console_operations_workflow_2026-07-07/MGMT-OPS-004-performance-attribution-evidence.md`

Execution packet:

- `docs/bff/execution-tasks/2026-07-10-persona-fleet-mutation-evolution-gap/INDEX.md`

## Problem Statement

Persona Fleet currently has a `最近 MUTATION` column that is supposed to help an
operator understand what changed recently and open the detailed evidence behind
that change. The current behavior is not trustworthy enough:

- the Fleet row can display a recent mutation date, but the linked Evolution
  Journal may only show a synthetic fleet summary;
- the URL/query layer can carry missing values such as `mutation: nan`;
- the fallback card calls a date an `Action`, which makes the row look like a
  real mutation event even when no formal mutation entry exists;
- the frontend link builder mixes mutation identity, mutation date, evolution
  entry identity, and fallback summary routing;
- the target page can look "loaded" while still showing semantically wrong
  content.

The correct fix is not to remove the hyperlink. The correct fix is to make the
link target and target page honest about the evidence level.

## Definitions

`recent mutation`

: A Fleet-row summary field that tells the operator the most recent known
  strategy/persona change time or change identifier. It may be a formal
  mutation entry, an evolution journal entry, or only a fleet status summary.

`formal mutation entry`

: A persisted mutation/evolution record with a stable id that can be opened in
  Evolution Journal and audited as a real change event.

`fleet status summary`

: A Persona Fleet row-derived summary. It is useful context, but it is not a
  formal mutation log entry and must not be displayed as if it were one.

`mutation identity`

: A stable id such as `mutation_review`, `item`, `evolution_entry_id`, or another
  backend-provided identifier. A date string is not a mutation identity.

`mutation timestamp`

: The time the latest change was observed or summarized. It can be shown as
  `changed_at` or `last_mutation_at`, but must not be used as an action id.

## Current Failure Mode

Observed operator path:

```text
Persona Fleet -> click 最近 MUTATION -> Evolution Journal
```

Observed bad target state:

```text
已聚焦 Persona: persona-20260528-04688755 · mutation: nan · 1 筆匹配演化項目

Persona Fleet mutation summary · Crypto-Alt-Hunter
persona-fleet-summary:persona-20260528-04688755:2026-06-03
paper broker sandbox readback and funding-rate stress review · state paper_running
Action
2026-06-03
Target
Persona:persona-20260528-04688755
落地時間
2026/6/3 上午8:00:00
```

Why this is wrong:

- `nan` is not an operator-facing value and must never become a filter key.
- `2026-06-03` is a timestamp, not an action.
- "mutation summary" implies a formal mutation entry when the data is only a
  Fleet-row fallback summary.
- The page count says there is one matched evolution item, but the card is
  synthetic and should be labeled as fallback.

## Root Cause

The row summary and detail page do not share a strict contract.

Persona Fleet has enough information to show a useful row, but it does not have
a guaranteed formal mutation id for every row. The frontend currently tries to
infer a detail link from optional fields and then lets Evolution Journal patch
over missing matches by building a fallback row from the Fleet summary. That
fallback path is valuable, but it is not labeled or filtered rigorously enough.

This creates a repeating failure pattern:

1. a row looks valid because it has a date and status;
2. the link target is built without a formal entry id;
3. Evolution Journal receives a missing or invalid mutation focus;
4. fallback synthesis creates a visible card;
5. the operator sees content, but the semantics are wrong.

## Target Contract

Persona Fleet rows must expose the recent-change fields as explicit facts, not
implicit guesses.

Minimum BFF/adapter fields:

```json
{
  "persona_id": "persona-20260528-04688755",
  "persona_label": "Crypto-Alt-Hunter",
  "last_mutation_label": "2026-06-03",
  "last_mutation_at": "2026-06-03T00:00:00Z",
  "last_mutation_kind": "fleet_summary",
  "mutation_entry_id": null,
  "evolution_entry_id": null,
  "evolution_href": "/management/evolution-journal?persona=persona-20260528-04688755&source=fleet_summary",
  "mutation_confidence": "fallback",
  "mutation_diagnostics": [
    "No formal mutation entry id declared for this persona row."
  ]
}
```

When a formal entry exists:

```json
{
  "last_mutation_kind": "formal_mutation",
  "mutation_entry_id": "mutation-review-123",
  "evolution_entry_id": "evo-456",
  "evolution_href": "/management/evolution-journal?persona=persona-20260528-04688755&mutation_review=mutation-review-123"
}
```

Rules:

- Do not put `nan`, `NaN`, `undefined`, empty strings, or date strings into
  `mutation_entry_id`.
- Do not generate `mutation=nan`, `source=nan`, or `item=nan` query params.
- If the backend cannot provide a formal id, set the id field to null and set
  `last_mutation_kind` to `fleet_summary` or `unavailable`.
- The frontend may derive a fallback href only from persona id and an explicit
  fallback source marker, never from `nan`.
- Date strings may be shown as timestamps, but they are not ids.

## Target UI Behavior

### Persona Fleet

The `最近 MUTATION` cell remains a hyperlink.

If a formal mutation id exists:

- show the formal mutation label or date;
- link to Evolution Journal with the formal id query param;
- show a tooltip or secondary text that says it is a formal mutation/evolution
  entry.

If only a Fleet summary exists:

- show the latest date or `無正式 mutation`;
- keep the hyperlink to Evolution Journal focused by persona;
- label the link as a fallback/status summary, not a formal mutation.

If no useful recent-change data exists:

- show `--` or `無資料`;
- do not create a misleading link;
- include a diagnostic in row metadata or the detail page.

### Evolution Journal

The focus banner must name what is actually focused:

Formal example:

```text
已聚焦 Persona: persona-20260528-04688755 · mutation: mutation-review-123 · 1 筆正式演化項目
```

Fallback example:

```text
已聚焦 Persona: persona-20260528-04688755 · fleet summary fallback · 無正式 mutation id
```

The fallback card must be renamed and restructured:

- title: `Persona Fleet status summary · <persona label>`;
- id: `persona-fleet-summary:<persona_id>:<last_mutation_at or as_of>`;
- summary: current work, runtime state, data confidence, and diagnostics;
- fields:
  - `Changed at` / `最近更新`;
  - `Target`;
  - `Source`;
  - `Confidence`;
  - `Diagnostics`.

It must not show:

- `mutation: nan`;
- `Action 2026-06-03`;
- a formal-match count for a synthetic fallback summary.

## Non-Goals

- Do not delete the Fleet row hyperlinks to hide target page bugs.
- Do not create a new all-in-one page for OODA or mutation status.
- Do not add demo/mock data to fill missing fields.
- Do not treat fallback summaries as formal journal evidence.
- Do not mutate live capital, broker state, or persona state.

## Required Implementation Checks

The worker must inspect and test both sides of every changed link:

1. Persona Fleet cell content and href.
2. Evolution Journal query parsing and focus matching.
3. Formal mutation entry path.
4. Fallback-only fleet summary path.
5. Missing-data path.
6. Hosted browser click from Persona Fleet to Evolution Journal.

The target page is not "normal" just because it renders. It is normal only if
the page content, labels, counts, and diagnostics match the source data level.

## Acceptance Criteria

- `nan`, `NaN`, `undefined`, and empty-derived fake ids never appear in the
  operator-facing Fleet/Evolution Journal UI or URL query string.
- Fleet `最近 MUTATION` links remain present when there is meaningful detail or
  fallback context.
- Formal mutation ids deep-link to the exact Evolution Journal row.
- Fallback-only rows deep-link to a persona-scoped fallback page that says it is
  a Fleet status summary and that no formal mutation id is available.
- Date values appear only as timestamps, never as action ids.
- Evolution Journal counts distinguish formal matches from fallback summaries.
- Tests cover `persona-20260528-04688755` and at least one formal mutation row.
- Hosted evidence includes screenshots or trace output for:
  - Fleet row before click;
  - formal mutation target page;
  - fallback target page;
  - missing-data/no-link state.

## Dispatch

Use the execution packet:

```sh
python3 scripts/dispatch_persona_fleet_mutation_evolution_gap_2026-07-10.py --dry-run
PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon python3 scripts/dispatch_persona_fleet_mutation_evolution_gap_2026-07-10.py
PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon python3 scripts/ai_status.py sync
```
