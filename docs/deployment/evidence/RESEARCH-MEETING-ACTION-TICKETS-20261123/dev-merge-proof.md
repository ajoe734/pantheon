# Dev merge refresh (2026-11-23)

- Merged origin/dev 22bf00481219d34cac25820fa4e03e0bb8ebb5ec into task branch (non-destructive merge, same PR #7760).
- Sole conflict: docs/operations/storage-manifest.json. Kept both writer entries:
  meeting_output_dispatches.json (this task) and ordered paper risk intent and receipts (PAPER-PERSONA-RISK-PROFILE-CHANGE-20261123). No aggregate count fields exist in the file.
- scripts/check_storage_manifest.py: passed=true, failures=[]; scripts.test_check_storage_manifest: 10 OK.
- Isolated postgres:16 (RESEARCH_MEETING_TEST_DSN): test_meeting_action_tickets.py + tests/research_write_owner + test_research_orchestrator_http_service.py = 88 passed, 2 skipped, 3 failed.
- Failures are environmental/pre-existing: test_bff_routes_distinguish_data_empty_unavailable_and_missing (documented in baseline-residual.txt); two derivatives_pricing_risk_provenance cases fail only because QuantLib is not installed in this validation venv (503).
