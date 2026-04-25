# KW-05 Strategy Spec Review Packet

## Date

2026-04-24

## Reviewer

Codex

## Findings

### 1. Medium: the Git-visible feedback bundle still overclaims compare-side contract mismatch handling

- `../front-ai-trading-system/docs/pantheon-feedback/KW-05-strategy-spec/QA_STATUS.md:12-24`
  says runtime contract checks were reviewed in code for compare
  `changed_sections`, `breaking_changes`, and
  `meta.surfaces.strategy_spec_compare`.
- `../front-ai-trading-system/docs/pantheon-feedback/KW-05-strategy-spec/UI_DECISIONS.md:13-14`
  and
  `../front-ai-trading-system/docs/pantheon-feedback/KW-05-strategy-spec/LOVABLE_CHANGE_FEEDBACK.md:43-44`
  also say contract mismatch alerts cover the compare surface.
- In the committed UI snapshot at
  `6321613cff3c49b11a7619e0f9170217a27a7b17`,
  `src/pages/knowledge/StrategySpecCompare.tsx:64-92` stores version-history
  and compare responses directly with no required-field validation or
  `contractGap` path, `:102-103` derives the compare surface state straight
  from the returned payload, and `:255-309` dereferences
  `changed_sections`, `breaking_changes`, and `evidence_refs` directly.
- Impact: the route family and transport are now acceptable, but the
  Git-visible feedback bundle is still not a truthful description of the
  delivered compare hardening. Pantheon should keep KW-05 in front follow-up
  until the bundle is trimmed or the validation is actually implemented.

## Confirmed Positives

- The Git-visible request pair and feedback bundle are now published on
  `origin/pkt-004-detail-fix` at
  `1a1a42eebda033a1fbda4696df5b81271f5eed9b`.
- The advertised `source_commit`
  `6321613cff3c49b11a7619e0f9170217a27a7b17` is replayable from that branch:
  `git branch -r --contains` includes `origin/pkt-004-detail-fix`, and
  `git merge-base --is-ancestor` returned `0`.
- `python3 -m pytest -q services/control-plane/bff/test_kw05_strategy_spec_contract.py`
  passed in the current Pantheon workspace (`3 passed`).
- `npx tsc --noEmit` passed in `/home/edna/code/front-ai-trading-system`.
- The current front snapshot keeps KW-05 on the published shared BFF client in
  `src/lib/bffClient.ts`, and the list/detail/history screens still implement
  contract-gap handling for their required read fields.

## Reviewed Artifacts

- Pantheon contract bundle:
  - `docs/bff/KW-05-strategy-spec.md`
  - `docs/examples/KW-05-strategy-spec.json`
  - `docs/pantheon-handoffs/KW-05-strategy-spec/FRONTEND_CHANGE_SPEC.md`
  - `services/control-plane/bff/test_kw05_strategy_spec_contract.py`
- Git-visible front branch `origin/pkt-004-detail-fix`:
  - `.coordination/requests/KW-05-strategy-spec-ui-done.yaml`
  - `.coordination/requests/KW-05-strategy-spec-frontend-feedback.yaml`
  - `docs/pantheon-feedback/KW-05-strategy-spec/LOVABLE_CHANGE_FEEDBACK.md`
  - `docs/pantheon-feedback/KW-05-strategy-spec/API_GAP_REQUESTS.json`
  - `docs/pantheon-feedback/KW-05-strategy-spec/UI_DECISIONS.md`
  - `docs/pantheon-feedback/KW-05-strategy-spec/QA_STATUS.md`
- Reviewed UI source commit `6321613cff3c49b11a7619e0f9170217a27a7b17`:
  - `src/lib/bffClient.ts`
  - `src/pages/knowledge/StrategySpecTypes.ts`
  - `src/pages/knowledge/StrategySpecList.tsx`
  - `src/pages/knowledge/StrategySpecDetail.tsx`
  - `src/pages/knowledge/StrategySpecCompare.tsx`
  - `src/App.tsx`
  - `src/components/AppSidebar.tsx`
  - `src/components/WorkbenchBreadcrumb.tsx`

## Decision

`KW-05-strategy-spec` is **follow-up-required**.

The published request pair is now replay-clean and Pantheon does not owe new
API work. The only remaining blocker is front-owned feedback-bundle fidelity
around compare payload mismatch handling. Refresh the bundle to match the
current UI, or backfill the compare validation and republish the request pair
if the UI commit changes.
