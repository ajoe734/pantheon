# Closeout Evidence: SENTINEL-RULE-COVERAGE-HEALTHREASON-001

- Task: Add Sentinel rules covering 6 HealthReasonCode values (rule engine; not an endpoint)
- Owner: Claude
- Reviewer: Claude2
- Decision: review_approved → done
- Review artifact: support/reviews/SENTINEL-RULE-COVERAGE-HEALTHREASON-001-review-claude2.md
- PR: #660 → dev

## Deliverable (unchanged from approval, commit 80a8af6b)

`services/control-plane/bff/main.py` adds `_SENTINEL_HEALTH_REASON_RULES`
(6 rules) and `_health_reason_sentinel_findings`, merged additively (id-deduped)
into `GET /bff/v5/sentinel/findings` as open `persona_health` findings. Rule
registry keys exactly equal the reason set emitted by
`_project_persona_fleet_health` (no drift); severity uses canonical
critical/high/medium/low plus a Pack D info/warn/alert `severity_bucket`.

## Owner verification reproduced (2026-05-29)

```
cd services/control-plane/bff
python3 -m pytest test_sentinel_healthreason_rule_coverage.py \
  test_sent001_sentinel_findings_contract.py \
  test_bff_v5_loop_sentinel_contract.py test_read_store_loop_sentinel.py -q
=> 71 passed
```

## Merge note

PR #660 was BEHIND dev at closeout. Branch updated to dev tip; the first
post-update CI run failed the "Commit trailers" gate on an unowned dev
squash-merge commit (`e26ae859` `... (#659)`, 73-char subject) pulled into the
push-event range — a known push-range false positive, not a task-owned commit.
Resolved by pushing this fresh owner closeout commit on top so the next
push-event range excludes the offending dev commit. No force-push.
