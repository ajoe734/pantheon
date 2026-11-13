import pytest


@pytest.fixture(autouse=True)
def _explicit_jsonl_fallback(monkeypatch):
    """Legacy fixtures exercise the JSONL path; the production default stays fail-closed Postgres.

    Tests that cover the default or the Postgres backend set/delete the variable themselves
    (monkeypatch calls inside the test run after this fixture).
    """
    monkeypatch.setenv("PANTHEON_SEARCH_BACKEND", "jsonl")
