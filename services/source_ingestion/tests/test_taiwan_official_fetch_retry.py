from __future__ import annotations

import json
import urllib.error
import urllib.request
from contextlib import contextmanager
from typing import Any

import pytest

from services.source_ingestion.connectors import taiwan_official
from services.source_ingestion.connectors.base import SourceEvidenceError
from services.source_ingestion.connectors.taiwan_official import (
    OFFICIAL_FETCH_ATTEMPTS,
    OfficialResponseTruncated,
    TaiwanOfficialMarketDatasetAdapter,
    _fetch_official,
    _read_bounded_response,
)

BODY = json.dumps([{"Code": "2330", "ClosingPrice": "2550.00"}] * 50).encode("utf-8")


class _Response:
    """http.client-like response: short body at early close, never an error."""

    def __init__(self, body: bytes, *, declared: int | None, read_error: Exception | None = None) -> None:
        self._body = body
        self._offset = 0
        self._read_error = read_error
        self.headers = {} if declared is None else {"Content-Length": str(declared)}

    def read(self, amount: int) -> bytes:
        if self._read_error is not None:
            raise self._read_error
        chunk = self._body[self._offset : self._offset + amount]
        self._offset += len(chunk)
        return chunk


class _ChunkedResponse:
    """Fake response delivering discrete chunks (e.g. short read before EOF)."""

    def __init__(self, chunks: list[bytes], *, declared: int | None = None) -> None:
        self._chunks = list(chunks)
        self.headers = {} if declared is None else {"Content-Length": str(declared)}

    def read(self, amount: int = -1) -> bytes:
        if self._chunks:
            return self._chunks.pop(0)
        return b""


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.sleeps: list[float] = []


def _install(monkeypatch: pytest.MonkeyPatch, outcomes: list[Any]) -> _Recorder:
    recorder = _Recorder()

    @contextmanager
    def fake_open(request: urllib.request.Request, *, caller: str, timeout: float):
        recorder.calls.append(request.full_url)
        outcome = outcomes[len(recorder.calls) - 1]
        if isinstance(outcome, Exception):
            raise outcome
        yield outcome

    monkeypatch.setattr(taiwan_official, "open_external_url", fake_open)
    monkeypatch.setattr(taiwan_official.time, "sleep", recorder.sleeps.append)
    return recorder


def _fetch(**overrides: Any) -> Any:
    kwargs: dict[str, Any] = {
        "caller": "source_ingest.taiwan_official",
        "timeout_seconds": 5.0,
        "max_bytes": 10_485_760,
        "parse": lambda raw: json.loads(raw.decode("utf-8")),
    }
    kwargs.update(overrides)
    return _fetch_official(urllib.request.Request("https://www.tpex.org.tw/openapi/v1/x"), **kwargs)


def test_short_body_against_content_length_is_a_named_truncation() -> None:
    with pytest.raises(OfficialResponseTruncated, match=r"read 100 of 4000 declared bytes"):
        _read_bounded_response(_Response(b"x" * 100, declared=4000))


def test_body_without_content_length_is_returned_as_read() -> None:
    assert _read_bounded_response(_Response(BODY, declared=None)) == BODY


def test_short_chunk_stream_with_content_length_reads_full_body_without_truncation() -> None:
    chunks = [b'{"ok":', b'true}']
    response = _ChunkedResponse(chunks, declared=11)
    body = _read_bounded_response(response)
    assert body == b'{"ok":true}'


def test_short_chunk_stream_without_content_length_reads_full_body() -> None:
    chunks = [b'{"ok":', b'true}']
    response = _ChunkedResponse(chunks, declared=None)
    body = _read_bounded_response(response)
    assert body == b'{"ok":true}'


def test_short_chunk_stream_in_fetch_official_succeeds_without_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _install(
        monkeypatch,
        [_ChunkedResponse([b'{"ok":', b'true}'], declared=11)],
    )

    assert _fetch() == {"ok": True}
    assert len(recorder.calls) == 1
    assert len(recorder.sleeps) == 0


def test_truncated_then_complete_response_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _install(
        monkeypatch,
        [_Response(BODY[: len(BODY) // 2], declared=len(BODY)), _Response(BODY, declared=len(BODY))],
    )

    assert _fetch() == json.loads(BODY)
    assert len(recorder.calls) == 2
    assert recorder.sleeps == [taiwan_official.OFFICIAL_FETCH_BACKOFF_SECONDS]


def test_read_timeout_then_complete_response_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _install(
        monkeypatch,
        [
            _Response(BODY, declared=len(BODY), read_error=TimeoutError("The read operation timed out")),
            _Response(BODY, declared=len(BODY)),
        ],
    )

    assert _fetch() == json.loads(BODY)
    assert len(recorder.calls) == 2


def test_incomplete_json_without_content_length_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _install(
        monkeypatch,
        [_Response(BODY[:-10], declared=None), _Response(BODY, declared=None)],
    )

    assert _fetch() == json.loads(BODY)
    assert len(recorder.calls) == 2


def test_persistent_truncation_fails_after_bounded_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    truncated = [_Response(BODY[:100], declared=len(BODY)) for _ in range(OFFICIAL_FETCH_ATTEMPTS)]
    recorder = _install(monkeypatch, truncated)

    with pytest.raises(SourceEvidenceError) as raised:
        _fetch()

    assert not isinstance(raised.value, OfficialResponseTruncated)
    message = str(raised.value)
    assert f"failed after {OFFICIAL_FETCH_ATTEMPTS} attempts" in message
    for attempt in range(1, OFFICIAL_FETCH_ATTEMPTS + 1):
        assert f"attempt {attempt}: OfficialResponseTruncated" in message
    assert len(recorder.calls) == OFFICIAL_FETCH_ATTEMPTS
    assert len(recorder.sleeps) == OFFICIAL_FETCH_ATTEMPTS - 1


def test_http_error_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    error = urllib.error.HTTPError("https://www.tpex.org.tw/openapi/v1/x", 503, "unavailable", {}, None)
    recorder = _install(monkeypatch, [error, _Response(BODY, declared=len(BODY))])

    with pytest.raises(urllib.error.HTTPError):
        _fetch()
    assert len(recorder.calls) == 1


def test_byte_limit_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _install(monkeypatch, [_Response(BODY, declared=len(BODY)), _Response(BODY, declared=len(BODY))])

    with pytest.raises(SourceEvidenceError, match="max byte limit"):
        _fetch(max_bytes=10)
    assert len(recorder.calls) == 1


def test_official_dataset_fetch_uses_the_retrying_reader(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _install(
        monkeypatch,
        [_Response(BODY[:200], declared=len(BODY)), _Response(BODY, declared=len(BODY))],
    )

    payload = TaiwanOfficialMarketDatasetAdapter().fetch_payload("tw_price_daily", "TPEx", timeout_seconds=5.0)

    assert payload == json.loads(BODY)
    assert len(recorder.calls) == 2
