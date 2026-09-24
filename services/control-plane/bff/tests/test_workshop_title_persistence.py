"""Title roundtrips; optional real Postgres uses an isolated disposable schema.

Set WORKSHOP_TITLE_TEST_DSN to a temporary local Postgres database to exercise
the same API cases against SQL and verify bootstrap/reconnect persistence.
"""
from __future__ import annotations

import os
import uuid

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from services.control_plane.bff.agora.strategy_workshop.router import create_strategy_workshop_router
from services.control_plane.bff.agora.strategy_workshop.store import MemoryWorkshopStore, PostgresWorkshopStore
from services.control_plane.privacy.private_content_store import EphemeralKeyProvider, MemoryPrivateContentStore


TITLE = "  動能策略 — EUR/USD 'alpha'  "
PRIVATE_MESSAGE = "Private hypothesis that must never become a public title"


@pytest.fixture(params=["memory", "postgres"])
def store(request):
    if request.param == "memory":
        yield MemoryWorkshopStore()
        return
    dsn = os.environ.get("WORKSHOP_TITLE_TEST_DSN")
    if not dsn:
        pytest.skip("WORKSHOP_TITLE_TEST_DSN not set; real Postgres unavailable")
    import psycopg
    from psycopg import sql

    schema = "title_test_" + uuid.uuid4().hex
    try:
        yield PostgresWorkshopStore(dsn=dsn, schema=schema)
    finally:
        with psycopg.connect(dsn) as conn:
            conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))


def _client(store, private_store, *, raise_server_exceptions=True):
    def identity(authorization):
        return {
            "operator_id": authorization.removeprefix("Bearer "),
            "roles": ["operator"],
            "claims": {"tenant_id": "tenant-a", "allowed_tenants": ["tenant-a", "tenant-b"]},
        }

    app = FastAPI()
    app.include_router(create_strategy_workshop_router(
        extract_identity=identity,
        require_read_role=lambda current: None,
        require_write_role=lambda current: None,
        bff_error=lambda status_code, *args, **kwargs: HTTPException(status_code=status_code),
        utc_now=lambda: "2026-09-24T00:00:00Z",
        workshop_store=store,
        private_content_store=private_store,
    ))
    return TestClient(app, raise_server_exceptions=raise_server_exceptions)


def _headers(user="owner", tenant="tenant-a", key=None):
    headers = {"Authorization": f"Bearer {user}", "X-Tenant-Id": tenant}
    if key is not None:
        headers["Idempotency-Key"] = key
    return headers


@pytest.mark.parametrize("title_fields", [{"title": TITLE}, {}, {"title": None}, {"title": ""}])
def test_title_api_roundtrip_privacy_and_isolation(store, title_fields):
    private_store = MemoryPrivateContentStore(key_provider=EphemeralKeyProvider())
    with _client(store, private_store) as client:
        response = client.post("/bff/agora/workshops", headers=_headers(key="create"),
                               json={"initial_message": PRIVATE_MESSAGE, **title_fields})
        assert response.status_code == 201, response.text
        created = response.json()["data"]
        workshop_id = created["workshop_id"]
        expected_title = title_fields.get("title")
        assert created["title"] == expected_title
        assert created.get("metadata", {}).get("title") == expected_title
        assert PRIVATE_MESSAGE not in response.text

    # Recreate the client and SQL store: reads must not depend on the create
    # response, connection, or browser session retaining the title.
    if isinstance(store, PostgresWorkshopStore):
        store = PostgresWorkshopStore(dsn=store.dsn, schema=store.schema)
    with _client(store, private_store) as client:
        detail = client.get(f"/bff/agora/workshops/{workshop_id}", headers=_headers())
        assert detail.status_code == 200, detail.text
        assert detail.json()["data"]["title"] == expected_title
        assert detail.json()["data"].get("metadata", {}).get("title") == expected_title
        assert detail.headers["etag"] == f'W/"workshop:{workshop_id}:v1"'
        listing = client.get("/bff/agora/workshops", headers=_headers())
        assert listing.status_code == 200
        assert listing.json()["data"] == [detail.json()["data"]]
        assert PRIVATE_MESSAGE not in listing.text + detail.text
        for headers in (_headers(user="another"), _headers(tenant="tenant-b")):
            assert client.get(f"/bff/agora/workshops/{workshop_id}", headers=headers).status_code == 403
            assert client.get("/bff/agora/workshops", headers=headers).json()["data"] == []
        assert client.post("/bff/agora/workshops", headers=_headers(key="create"),
                           json={"initial_message": PRIVATE_MESSAGE, "title": "replacement"}).status_code == 409
        assert client.post("/bff/agora/workshops", headers=_headers(),
                           json={"initial_message": PRIVATE_MESSAGE, "title": TITLE}).status_code == 400
        assert store.get_session(workshop_id)["title"] == expected_title

    events = store.list_events(workshop_id)
    assert len(events) == 1
    assert PRIVATE_MESSAGE not in str(events)
    content = private_store.get_for_owner(
        private_content_ref=events[0]["private_content_ref"], tenant_id="tenant-a",
        owner_user_id="owner", purpose="test", request_id="title-test",
    )
    assert content == PRIVATE_MESSAGE.encode()


def test_title_create_failure_rolls_back_and_allows_retry(store, monkeypatch):
    private_store = MemoryPrivateContentStore(key_provider=EphemeralKeyProvider())
    original_put = private_store.put

    def fail(**kwargs):
        raise RuntimeError("injected private storage failure")

    monkeypatch.setattr(private_store, "put", fail)
    with _client(store, private_store, raise_server_exceptions=False) as client:
        payload = {"title": TITLE, "initial_message": PRIVATE_MESSAGE}
        response = client.post("/bff/agora/workshops", headers=_headers(key="retry"), json=payload)
        assert response.status_code == 500
        assert store.list_sessions(user_id="owner", tenant_id="tenant-a")[0] == []
        monkeypatch.setattr(private_store, "put", original_put)
        response = client.post("/bff/agora/workshops", headers=_headers(key="retry"), json=payload)
        assert response.status_code == 201, response.text
        assert response.json()["data"]["metadata"]["title"] == TITLE


def test_postgres_existing_rows_nullable_bootstrap_and_reconnect(store):
    if not isinstance(store, PostgresWorkshopStore):
        pytest.skip("SQL migration and reconnect case")
    # Reproduce the old runtime table without title, including a preexisting row.
    store.create_session({"workshop_id": "legacy", "tenant_id": "tenant-a", "user_id": "owner"})
    with store._connect() as conn:
        conn.execute(f"ALTER TABLE {store._st} DROP COLUMN IF EXISTS title")
    reopened = PostgresWorkshopStore(dsn=store.dsn, schema=store.schema)
    assert reopened.get_session("legacy")["title"] is None
    reopened.create_session({"workshop_id": "titled", "tenant_id": "tenant-a", "user_id": "owner", "title": TITLE})
    reopened = PostgresWorkshopStore(dsn=store.dsn, schema=store.schema)
    assert reopened.get_session("titled")["title"] == TITLE
    sessions, _ = reopened.list_sessions(user_id="owner", tenant_id="tenant-a")
    assert {row["workshop_id"]: row["title"] for row in sessions} == {"legacy": None, "titled": TITLE}
    with reopened._connect() as conn:
        column = conn.execute(
            "SELECT is_nullable, column_default FROM information_schema.columns "
            "WHERE table_schema=%s AND table_name='strategy_workshop_session' AND column_name='title'",
            (store.schema,),
        ).fetchone()
    assert column == ("YES", None)
