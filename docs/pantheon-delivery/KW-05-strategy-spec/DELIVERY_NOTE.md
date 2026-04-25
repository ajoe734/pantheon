# KW-05 Strategy Spec Backend Delivery Note

## Status

`front-followup-required`

## Summary

Pantheon re-reviewed the returned KW-05 Strategy Spec cycle against the current
route family, example payload, and the Git-visible front branch.

Pantheon still owns a sufficient KW-05 contract:

- `GET /api/v1/knowledge/strategy-specs`
- `GET /api/v1/knowledge/strategy-specs/{strategy_id}`
- `GET /api/v1/knowledge/strategy-specs/{strategy_id}/versions`
- `GET /api/v1/knowledge/strategy-specs/{strategy_id}/compare`

Pantheon re-ran the targeted contract proof:

- `python3 -m pytest -q services/control-plane/bff/test_kw05_strategy_spec_contract.py`
  - Result: `3 passed`
  - Date: `2026-04-24`

No new Pantheon endpoint, compare diff rule, ancestry reconstruction, or
runtime handoff is required.

The loop remains `front-followup-required` for one front-owned reason:

1. the Git-visible feedback bundle still claims compare-field contract mismatch
   alerts that the committed `StrategySpecCompare.tsx` does not implement

## Contract State

The current KW-05 contract remains unchanged:

- version identity is anchored on `strategy_id + spec_version_id`
- lifecycle state is backend-owned
- ancestry is backend-owned through `parent_spec_version_id` and
  `derived_from_source_refs[]`
- compare output is backend-generated only
- version history navigation remains BFF-owned through `route_href`

## Git-visible Replay State

Pantheon confirmed the transport tuple is now replay-clean:

- `origin/pkt-004-detail-fix` resolves to
  `1a1a42eebda033a1fbda4696df5b81271f5eed9b`
- the canonical `ui-done` request, `frontend-feedback` request, and four-file
  feedback bundle all resolve directly from that branch head via `git show`
- the published UI `source_commit`
  `6321613cff3c49b11a7619e0f9170217a27a7b17` is contained in the returned
  branch and remains replayable

## Remaining Front Follow-up

- Refresh `docs/pantheon-feedback/KW-05-strategy-spec/QA_STATUS.md`,
  `UI_DECISIONS.md`, and `LOVABLE_CHANGE_FEEDBACK.md` so the bundle no longer
  claims compare-field contract mismatch alerts that the delivered UI does not
  implement.
- Or add the missing compare required-field validation to
  `src/pages/knowledge/StrategySpecCompare.tsx` and republish the request pair
  if the UI `source_commit` changes.
- Redispatch Pantheon review on the unchanged contract after the bundle is
  truthful again.

## Verification Performed

- Re-ran the Pantheon KW-05 contract proof:
  - `python3 -m pytest -q services/control-plane/bff/test_kw05_strategy_spec_contract.py`
- Re-checked the canonical contract bundle:
  - `docs/bff/KW-05-strategy-spec.md`
  - `docs/examples/KW-05-strategy-spec.json`
  - `docs/pantheon-handoffs/KW-05-strategy-spec/FRONTEND_CHANGE_SPEC.md`
- Re-checked the Git-visible front branch:
  - `git -C /home/edna/code/front-ai-trading-system rev-parse origin/pkt-004-detail-fix`
  - `git -C /home/edna/code/front-ai-trading-system show origin/pkt-004-detail-fix:.coordination/requests/KW-05-strategy-spec-ui-done.yaml`
  - `git -C /home/edna/code/front-ai-trading-system show origin/pkt-004-detail-fix:.coordination/requests/KW-05-strategy-spec-frontend-feedback.yaml`
  - `git -C /home/edna/code/front-ai-trading-system branch -r --contains 6321613cff3c49b11a7619e0f9170217a27a7b17`
  - `git -C /home/edna/code/front-ai-trading-system merge-base --is-ancestor 6321613cff3c49b11a7619e0f9170217a27a7b17 origin/pkt-004-detail-fix`
- Verified the reviewed front snapshot still type-checks:
  - `cd /home/edna/code/front-ai-trading-system && npx tsc --noEmit`

## Files Updated

- `.coordination/reviews/KW-05-strategy-spec-review.md`
- `.coordination/responses/KW-05-strategy-spec-frontend-feedback.yaml`
- `.coordination/responses/KW-05-strategy-spec-backend-delivery.yaml`
- `docs/pantheon-delivery/KW-05-strategy-spec/DELIVERY_NOTE.md`
- `docs/pantheon-delivery/KW-05-strategy-spec/CONTRACT_LOCK.json`
