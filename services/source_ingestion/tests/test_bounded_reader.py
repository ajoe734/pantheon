"""Tests for end-of-stream bounded reader helper across all source connectors."""

from __future__ import annotations

import json
import urllib.request
from contextlib import contextmanager
from typing import Any

import pytest

from services.source_ingestion.connectors import alpha_db, social, taiwan_official
from services.source_ingestion.connectors.alpha_db import ExternalAlphaDbAdapter
from services.source_ingestion.connectors.base import SourceEvidenceError
from services.source_ingestion.connectors.bounded_reader import (
    OfficialResponseTruncated,
    ResponseTruncated,
    declared_content_length,
    read_bounded_response,
)
from services.source_ingestion.connectors.social import AdmittedSocialMediaAdapter
from services.source_ingestion.connectors.taiwan_official import _fetch_official


class _ChunkedResponse:
    """Mock HTTP response delivering discrete byte chunks ending with empty bytes."""

    def __init__(self, chunks: list[bytes], *, declared: int | None = None) -> None:
        self._chunks = list(chunks)
        self.headers = {} if declared is None else {"Content-Length": str(declared)}

    def read(self, amount: int = -1) -> bytes:
        if self._chunks:
            return self._chunks.pop(0)
        return b""


def test_bounded_reader_reads_multi_chunk_stream_to_eof() -> None:
    chunks = [b"A" * 65536, b"B" * 65536, b"C" * 1024]
    response = _ChunkedResponse(chunks, declared=len(b"".join(chunks)))
    body = read_bounded_response(response, max_bytes=200000)
    assert body == b"A" * 65536 + b"B" * 65536 + b"C" * 1024


def test_bounded_reader_never_treats_short_chunk_as_eof() -> None:
    """A chunk shorter than chunk_size must not terminate reading early."""
    chunks = [b'{"status":', b'"ok",', b'"data":[1,2,3]}']
    response = _ChunkedResponse(chunks, declared=None)
    body = read_bounded_response(response, max_bytes=1024, chunk_size=65536)
    assert body == b'{"status":"ok","data":[1,2,3]}'


def test_bounded_reader_short_chunk_with_matching_content_length() -> None:
    chunks = [b'{"valid":', b'true}']
    total = len(b"".join(chunks))
    response = _ChunkedResponse(chunks, declared=total)
    body = read_bounded_response(response)
    assert body == b'{"valid":true}'


def test_bounded_reader_without_content_length_returns_as_read() -> None:
    payload = b"simple unlengthed payload"
    response = _ChunkedResponse([payload], declared=None)
    assert read_bounded_response(response) == payload


def test_bounded_reader_truncation_against_content_length_raises() -> None:
    response = _ChunkedResponse([b"short body"], declared=1000)
    with pytest.raises(OfficialResponseTruncated, match=r"read 10 of 1000 declared bytes"):
        read_bounded_response(response)


def test_bounded_reader_exceeding_max_bytes_raises_oversized_error() -> None:
    response = _ChunkedResponse([b"x" * 500, b"x" * 600])
    with pytest.raises(SourceEvidenceError, match=r"Payload exceeded max byte limit \(1000 bytes\)"):
        read_bounded_response(response, max_bytes=1000)


def test_bounded_reader_custom_error_classes() -> None:
    class CustomOversizedError(Exception):
        pass

    class CustomTruncationError(Exception):
        pass

    resp_oversized = _ChunkedResponse([b"too big payload"])
    with pytest.raises(CustomOversizedError):
        read_bounded_response(
            resp_oversized,
            max_bytes=5,
            oversized_error_cls=CustomOversizedError,
        )

    resp_truncated = _ChunkedResponse([b"part"], declared=20)
    with pytest.raises(CustomTruncationError):
        read_bounded_response(
            resp_truncated,
            truncation_error_cls=CustomTruncationError,
        )


def test_declared_content_length_helper() -> None:
    class _H:
        def __init__(self, headers: dict[str, Any]) -> None:
            self.headers = headers

    assert declared_content_length(_H({"Content-Length": "123"})) == 123
    assert declared_content_length(_H({"Content-Length": 456})) == 456
    assert declared_content_length(_H({"Content-Length": "-1"})) is None
    assert declared_content_length(_H({"Content-Length": "invalid"})) is None
    assert declared_content_length(_H({"Content-Length": True})) is None
    assert declared_content_length(_H({})) is None
    assert declared_content_length(object()) is None


def test_taiwan_official_connector_call_path_handles_short_chunk(monkeypatch: pytest.MonkeyPatch) -> None:
    """Taiwan official call path via _fetch_official returns full body across short reads."""
    chunks = [b'{"dataset":', b' "twse_day_trading",', b' "rows": [1, 2, 3]}']
    full_bytes = b"".join(chunks)

    @contextmanager
    def fake_open(request: urllib.request.Request, *, caller: str, timeout: float):
        yield _ChunkedResponse(chunks, declared=len(full_bytes))

    monkeypatch.setattr(taiwan_official, "open_external_url", fake_open)

    result = _fetch_official(
        urllib.request.Request("https://www.twse.com.tw/test"),
        caller="source_ingest.taiwan_official",
        timeout_seconds=5.0,
        max_bytes=10485760,
        parse=lambda raw: json.loads(raw.decode("utf-8")),
    )
    assert result == {"dataset": "twse_day_trading", "rows": [1, 2, 3]}


def test_taiwan_official_connector_call_path_oversized_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    """Taiwan official call path fails immediately when max_bytes is exceeded."""
    @contextmanager
    def fake_open(request: urllib.request.Request, *, caller: str, timeout: float):
        yield _ChunkedResponse([b"x" * 200])

    monkeypatch.setattr(taiwan_official, "open_external_url", fake_open)

    with pytest.raises(SourceEvidenceError, match=r"Payload exceeded max byte limit \(100 bytes\)"):
        _fetch_official(
            urllib.request.Request("https://www.twse.com.tw/test"),
            caller="source_ingest.taiwan_official",
            timeout_seconds=5.0,
            max_bytes=100,
            parse=lambda raw: json.loads(raw.decode("utf-8")),
        )


def test_alpha_db_connector_call_path_handles_short_chunk(monkeypatch: pytest.MonkeyPatch) -> None:
    """Alpha DB fetch_payload reads full payload when stream delivers short chunks before EOF."""
    data = [{"symbol": "AAPL", "date": "2026-06-10", "rsi": 58.2}]
    raw_json = json.dumps(data).encode("utf-8")
    part1 = raw_json[:15]
    part2 = raw_json[15:]

    @contextmanager
    def fake_open(request: urllib.request.Request, *, caller: str, timeout: float):
        yield _ChunkedResponse([part1, part2], declared=len(raw_json))

    monkeypatch.setattr(alpha_db, "open_external_url", fake_open)
    monkeypatch.setenv("ALPHA_DB_API_KEY", "test-key-alpha")

    adapter = ExternalAlphaDbAdapter()
    result = adapter.fetch_payload(entity_id="AAPL", signal_id="technical_rsi_14d")
    assert result == data


def test_alpha_db_connector_call_path_oversized_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    """Alpha DB fetch_payload enforces 4194304 byte limit and raises SourceEvidenceError."""
    @contextmanager
    def fake_open(request: urllib.request.Request, *, caller: str, timeout: float):
        yield _ChunkedResponse([b"x" * 4194305])

    monkeypatch.setattr(alpha_db, "open_external_url", fake_open)
    monkeypatch.setenv("ALPHA_DB_API_KEY", "test-key-alpha")

    adapter = ExternalAlphaDbAdapter()
    with pytest.raises(SourceEvidenceError, match=r"Payload exceeded max byte limit \(4194304 bytes\)"):
        adapter.fetch_payload(entity_id="AAPL", signal_id="technical_rsi_14d")


def test_alpha_db_connector_call_path_does_not_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Alpha DB connector does not have a retry policy; errors fail on attempt 1."""
    call_count = 0

    @contextmanager
    def fake_open(request: urllib.request.Request, *, caller: str, timeout: float):
        nonlocal call_count
        call_count += 1
        yield _ChunkedResponse([b"short"], declared=5000)

    monkeypatch.setattr(alpha_db, "open_external_url", fake_open)
    monkeypatch.setenv("ALPHA_DB_API_KEY", "test-key-alpha")

    adapter = ExternalAlphaDbAdapter()
    with pytest.raises(OfficialResponseTruncated):
        adapter.fetch_payload(entity_id="AAPL", signal_id="technical_rsi_14d")

    assert call_count == 1


def test_social_connector_call_path_handles_short_chunk(monkeypatch: pytest.MonkeyPatch) -> None:
    """Social connector fetch_payload reads full payload when stream delivers short chunks before EOF."""
    data = {"response": {"status": 200}, "messages": [{"id": 1, "body": "$AAPL bullish"}]}
    raw_json = json.dumps(data).encode("utf-8")
    part1 = raw_json[:20]
    part2 = raw_json[20:]

    @contextmanager
    def fake_open(request: urllib.request.Request, *, caller: str, timeout: float):
        yield _ChunkedResponse([part1, part2], declared=len(raw_json))

    monkeypatch.setattr(social, "open_external_url", fake_open)

    adapter = AdmittedSocialMediaAdapter()
    result = adapter.fetch_payload(symbol="AAPL")
    assert result == data


def test_social_connector_call_path_oversized_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    """Social connector fetch_payload enforces 2097152 byte limit and raises SourceEvidenceError."""
    @contextmanager
    def fake_open(request: urllib.request.Request, *, caller: str, timeout: float):
        yield _ChunkedResponse([b"x" * 2097153])

    monkeypatch.setattr(social, "open_external_url", fake_open)

    adapter = AdmittedSocialMediaAdapter()
    with pytest.raises(SourceEvidenceError, match=r"Payload exceeded max byte limit \(2097152 bytes\)"):
        adapter.fetch_payload(symbol="AAPL")


def test_social_connector_call_path_does_not_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Social connector does not have a retry policy; errors fail on attempt 1."""
    call_count = 0

    @contextmanager
    def fake_open(request: urllib.request.Request, *, caller: str, timeout: float):
        nonlocal call_count
        call_count += 1
        yield _ChunkedResponse([b"short"], declared=5000)

    monkeypatch.setattr(social, "open_external_url", fake_open)

    adapter = AdmittedSocialMediaAdapter()
    with pytest.raises(OfficialResponseTruncated):
        adapter.fetch_payload(symbol="AAPL")

    assert call_count == 1


def test_all_connectors_import_single_bounded_reader() -> None:
    """Verify that taiwan_official, alpha_db, and social all import the single bounded reader."""
    assert taiwan_official.read_bounded_response is read_bounded_response
    assert alpha_db.read_bounded_response is read_bounded_response
    assert social.read_bounded_response is read_bounded_response

    # Verify none of the modules defines its own function
    assert taiwan_official.read_bounded_response.__code__ is read_bounded_response.__code__
    assert alpha_db.read_bounded_response.__code__ is read_bounded_response.__code__
    assert social.read_bounded_response.__code__ is read_bounded_response.__code__
