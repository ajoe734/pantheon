# BFF-B2-004 Owner Closeout Evidence

Task: Research and search facade — `/bff/research-experiments` and `/bff/search`
Owner: Codex2
Reviewer: Claude2
Status: review_approved → done
Branch: task/BFF-B2-004

## Reviewer Approval

Claude2 approval (2026-05-23T12:07:03Z):

- All 11 BFF-B2-004 acceptance criteria met.
- `pytest services/control-plane/bff/tests/test_bff_b2_004_research_search.py -v`
  passed 17 focused cases.
- `pytest services/control-plane/bff/tests/ -q` passed 231 compose-suite cases.
- Dead catch-all entries were confirmed removed from the generic BFF handlers.
- Review packet: `support/reviews/BFF-B2-004-review-claude2.md`.

## Verification

```
pytest services/control-plane/bff/tests/test_bff_b2_004_research_search.py -q
17 passed in 4.52s
```

Verified at closeout after merging origin/dev (commit 82b91de3).

Final owner closeout refresh by Codex2 (2026-05-23T12:07Z):

- PR #466 is merged into `origin/dev` at `ac911bcb`.
- Current task branch HEAD is an ancestor of `origin/dev`.
- Refreshed this evidence file to match the final owner/reviewer assignment
  recorded in the task brief.

Redispatch repair by Codex2 (2026-05-23T11:52:32Z):

- Refreshed PR #466 after `origin/dev` advanced to `a0705dda`.
- Resolved the `services/control-plane/bff/main.py` catch-all conflict by keeping
  both dedicated surfaces out of `sem_final_id_named_read_alias`:
  `/bff/research-experiments/{id}` (BFF-B2-004) and
  `/bff/v5/interventions/{id}` (BFF-B2-006).
- Merge repair commit: `40332910`.
- Validation:

```
pytest services/control-plane/bff/tests/test_bff_b2_004_research_search.py services/control-plane/bff/tests/test_bff_b2_003_capabilities.py services/control-plane/bff/tests/test_bff_b2_006_v5_closed_loop_reads.py -q
53 passed, 3 warnings in 13.22s
```

Follow-up dev sync by Codex2 (2026-05-23T11:55:00Z):

- Merged `origin/dev` at `05b011ff`; incoming changes were BFF-B2-006 closeout
  artifacts only.
- Merge sync commit: `f8574215`.
- Validation:

```
pytest services/control-plane/bff/tests/test_bff_b2_004_research_search.py -q
17 passed in 4.58s
```

Second follow-up dev sync by Codex2 (2026-05-23T11:56:54Z):

- Merged `origin/dev` at `1192589b`; incoming changes were BFF-B6-001
  management NL ask surface updates.
- Merge sync commit: `36d16299`.
- Validation:

```
pytest services/control-plane/bff/tests/test_bff_b2_004_research_search.py services/control-plane/bff/tests/test_bff_b6_management_nl_ask.py -q
25 passed in 9.20s
```

## Delivered Endpoints

| # | Method | Path | Handler |
|---|---|---|---|
| 1 | GET | `/bff/research-experiments` | `bff_list_research_experiments` |
| 2 | GET | `/bff/research-experiments/{id}` | `bff_get_research_experiment` |
| 3 | GET | `/bff/search` | `bff_search` |
| 4 | GET | `/bff/capabilities` | `sem_bff_capabilities` |

## Merge Conflict Resolution

When merging origin/dev (which had BFF-B2-003 merged), §B2.3 spec section had
conflicts. Resolution:

- Combined BFF-B2-003 (capabilities facade) and BFF-B2-004 (research/search)
  content in the spec section.
- Removed `/bff/research-experiments` from `sem_final_generic_read_alias` (dedicated
  handler replaces it).
- Removed `/bff/research-experiments/{id}` from `sem_final_id_named_read_alias`
  (dedicated handler replaces it).
- Restored `/bff/research-experiments/{id}` PATCH decorator that auto-merge
  incorrectly dropped (no dedicated PATCH handler exists; generic handler retained).
- Fixed PATCH handler comment to correctly describe exclusion list.

## Key Commits

- `6c09bc85` BFF-B2-004: research and search facade — dedicated handlers
- `0129d3b4` BFF-B2-004: add cursor pagination to bff_search
- `0c49ebda` BFF-B2-004: preserve limit as backward-compat alias in bff_search
- `82b91de3` Merge remote-tracking branch 'origin/dev' into task/BFF-B2-004
