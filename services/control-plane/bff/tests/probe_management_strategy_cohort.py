"""Read-only hosted browser probe; use an existing admintest storage state.

python services/control-plane/bff/tests/probe_management_strategy_cohort.py \
  --frontend-url https://CURRENT-FRONTEND --storage-state /secure/admintest.json \
  --expected-persona-id ID1 --expected-persona-id ID2 --expected-persona-id ID3

Run only after the hosted manifest proves the intended FE/BFF pair. This probe
reports the browser's actual strategy request and content; it does not deploy,
log in, save storage state, take screenshots or write evidence files.
"""
from __future__ import annotations

import argparse
import json
from urllib.parse import parse_qs, urlsplit


def check_strategy_cohort(payload, expected_persona_ids):
    items = payload.get("items")
    assert isinstance(items, list), "strategy_items_missing"
    assert len(items) == 3, "expected_three_strategy_rows"
    assert payload.get("page_info", {}).get("total") == 3, "expected_three_strategy_total"
    assert payload.get("page_info", {}).get("next_page_token") is None, "unexpected_next_page"
    persona_ids = {str(pid) for row in items for pid in row.get("personaIds", [])}
    assert persona_ids == set(expected_persona_ids), "unexpected_strategy_persona_cohort"
    assert all("trader paper book" not in str(row.get("name", "")).lower() for row in items), "servant_book_visible"
    return {"strategy_ids": [row["id"] for row in items], "persona_ids": sorted(persona_ids)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frontend-url", required=True)
    parser.add_argument("--storage-state", required=True)
    parser.add_argument("--expected-persona-id", action="append", required=True)
    args = parser.parse_args()
    assert len(set(args.expected_persona_id)) == 3, "three_expected_persona_ids_required"
    from playwright.sync_api import sync_playwright

    blocked_writes = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            context = browser.new_context(storage_state=args.storage_state)

            def read_only(route):
                request = route.request
                if request.method not in {"GET", "HEAD", "OPTIONS"}:
                    blocked_writes.append(request.method)
                    route.abort()
                else:
                    route.continue_()

            context.route("**/*", read_only)
            page = context.new_page()

            def is_default_strategy_response(response):
                parsed = urlsplit(response.url)
                query = parse_qs(parsed.query)
                return (
                    parsed.path == "/bff/strategies"
                    and query.get("hide_retired_personas") == ["true"]
                    and query.get("exclude_state") == ["draft"]
                    and query.get("include_test_personas", ["false"]) == ["false"]
                )

            with page.expect_response(is_default_strategy_response, timeout=60000) as pending:
                page.goto(args.frontend_url.rstrip("/") + "/management/strategies", wait_until="domcontentloaded", timeout=60000)
            response = pending.value
            assert response.status == 200, "strategy_read_failed"
            cohort = check_strategy_cohort(response.json(), args.expected_persona_id)
            for row in response.json()["items"]:
                page.get_by_text(row["name"], exact=True).first.wait_for(state="visible", timeout=15000)
            assert page.get_by_text("agora-servant-799a3ce8c021b91cbb54 trader paper book", exact=True).count() == 0, "servant_book_rendered"
            assert not blocked_writes, "browser_attempted_write"
            parsed = urlsplit(response.url)
            print(json.dumps({
                "status": "passed", "route": "/management/strategies",
                "request_origin": f"{parsed.scheme}://{parsed.netloc}",
                "request_path": parsed.path, "request_query": parse_qs(parsed.query),
                **cohort,
            }, sort_keys=True))
        finally:
            browser.close()


if __name__ == "__main__":
    main()
