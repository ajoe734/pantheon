"""Single end-of-stream bounded reader helper for source ingestion connectors."""

from __future__ import annotations

from typing import Any

from .base import SourceEvidenceError


class OfficialResponseTruncated(SourceEvidenceError):
    """An endpoint ended the body before its declared Content-Length."""


ResponseTruncated = OfficialResponseTruncated


def declared_content_length(response: Any) -> int | None:
    """Return declared Content-Length integer if valid and non-negative, else None."""
    headers = getattr(response, "headers", None)
    value = headers.get("Content-Length") if hasattr(headers, "get") else None
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        return None
    try:
        declared = int(value)
    except ValueError:
        return None
    return declared if declared >= 0 else None


_declared_content_length = declared_content_length


def read_bounded_response(
    response: Any,
    max_bytes: int = 10485760,
    chunk_size: int = 65536,
    *,
    oversized_error_cls: type[Exception] = SourceEvidenceError,
    truncation_error_cls: type[Exception] = OfficialResponseTruncated,
) -> bytes:
    """Read a response stream until EOF (empty chunk) up to max_bytes.

    Never treats a chunk shorter than chunk_size as EOF. If the response
    declares a Content-Length header and fewer bytes were read, raises
    truncation_error_cls. If the accumulated payload exceeds max_bytes,
    raises oversized_error_cls.
    """
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = response.read(chunk_size)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise oversized_error_cls(f"Payload exceeded max byte limit ({max_bytes} bytes)")
        chunks.append(chunk)
    # http.client returns a short body without error when the peer closes early.
    declared = _declared_content_length(response)
    if declared is not None and total < declared:
        raise truncation_error_cls(
            f"official response truncated: read {total} of {declared} declared bytes"
        )
    return b"".join(chunks)


_read_bounded_response = read_bounded_response

__all__ = [
    "OfficialResponseTruncated",
    "ResponseTruncated",
    "declared_content_length",
    "_declared_content_length",
    "read_bounded_response",
    "_read_bounded_response",
]
