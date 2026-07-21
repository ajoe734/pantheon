# KW-01 Institutional Memory Review

Date: `2026-04-20`
Task: `EXEC-FRONT-KW01-001`
Reviewer: `Codex`
Disposition: `close`

## Final Verification

- Verified `../front-ai-trading-system/.coordination/requests/KW-01-institutional-memory-ui-done.yaml` now points `source_commit` at `ba560610044d5f11c97b2b48cfb5b7621d812e4e`, and `git ls-tree` confirms that commit contains the claimed KW-01 route wiring, shared BFF client changes, and list/detail screen files.
- Verified `../front-ai-trading-system` branch head `2820e449dc95ab4677d9a7dc61d6eb7da4363aa4` publishes the canonical KW-01 `ui-done` request together with `docs/pantheon-feedback/KW-01-institutional-memory/{LOVABLE_CHANGE_FEEDBACK.md,API_GAP_REQUESTS.json,UI_DECISIONS.md,QA_STATUS.md}`.
- Re-ran `python3 -m pytest services/control-plane/bff/test_kw01_institutional_memory_contract.py -q` in `pantheon`; `4 passed`.
- Re-validated degraded and unavailable behavior with a local FastAPI `TestClient`: the KW-01 list returns `memory_list=degraded` with rows preserved and `memory_list=unavailable` with the list suppressed, while detail returns `entry_detail/source_context=degraded` and `unavailable` without fabricating data.
- Corrected Pantheon-owned `source_event.href` fallback/example values so current BFF payloads now target mounted owner screens: post-incident review uses `/operator/post-incident-review?incident=...`, mutation review uses `/evolution/mutation-review/:decision_id`, and list-row `route_href` remains `/knowledge/memory/{entry_id}`.

## Findings

None.

## Reviewer Note

KW-01 is replay-clean and contract-aligned for the current loop. The only residual risk is deployed-environment QA: this review verified the Pantheon BFF locally and cross-checked the mounted front routes, but did not exercise an external deployed environment in a browser.
