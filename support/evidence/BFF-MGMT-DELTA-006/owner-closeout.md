# BFF-MGMT-DELTA-006 Owner Closeout

Task: GET /bff/management/incident-timeline
Owner: Codex
Reviewer: Claude
Closeout date: 2026-05-24

## Delivered Scope

- Added the read-only `GET /bff/management/incident-timeline` FastAPI route.
- Composed the Management Console timeline from the existing IncidentCase read
  surface used by `/bff/incidents`; no new incident source of truth was added.
- Returned chronological rows with incident, runtime, deployment, capital pool,
  artifact, telemetry event, lineage, evidence summary, source refs, and links.
- Preserved the canonical aggregate envelope with `data`, `items`, `rows`,
  `incidents`, `events`, `summary`, `severityBuckets`, `page_info`, and `meta`.
- Added execute-plans typed query, response, path, and fetch helper wiring.

## Review

Claude approved the implementation in
`support/reviews/BFF-MGMT-DELTA-006-review-claude.md`.

Implementation PR #536 merged into `dev` at:

```text
2a8874a83b80c437a72043ea5006690ede5d7d07
```

Task commits reviewed in PR #536:

- `d3a1f3da` - anchor incident timeline
- `952d63c5` - update validation record

## Owner Verification

Owner closeout revalidation before refreshing the branch with `origin/dev`:

```bash
git diff --check
pytest -q services/control-plane/bff/test_bff_management_delta_routes.py \
  services/control-plane/bff/test_bff_pm12_portfolio_book_contract.py \
  services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py \
  services/control-plane/bff/tests/test_auth_jwks_strict.py
```

Result:

```text
75 passed, 3 existing datetime.utcnow DeprecationWarnings
```

Owner closeout revalidation on current `origin/dev`
(`b8009c57`, after the BFF-MGMT-DELTA-007 merge):

```bash
git diff --check
pytest -q services/control-plane/bff/test_bff_management_delta_routes.py \
  services/control-plane/bff/test_bff_pm12_portfolio_book_contract.py \
  services/control-plane/bff/test_execute_plans_final_live_wiring_contract.py \
  services/control-plane/bff/tests/test_auth_jwks_strict.py
```

Result:

```text
81 passed, 3 existing datetime.utcnow DeprecationWarnings
```

## Closeout Notes

- `meta.policy` remains `read_only_incident_timeline`.
- Severity buckets expose `high`, `medium`, and `low` counts before pagination.
- Anonymous requests return HTTP 401; authenticated requests return HTTP 200.
- CORS preflight returns HTTP 204 with the matching allowed origin.
- This closeout artifact must merge through the task PR before
  `AI_NAME=Codex ./scripts/ai-status.sh done BFF-MGMT-DELTA-006 ...` is run.
