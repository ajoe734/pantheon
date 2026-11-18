"""Single end-of-stream bounded reader helper for source ingestion connectors."""

from __future__ import annotations

from typing import Any

from .base import SourceEvidenceError


class ResponseTruncated(SourceEvidenceError):
    """An endpoint ended the body before its declared Content-Length."""


class PayloadTooLarge(SourceEvidenceError):
    """A body is above the caller's byte bound.

    ``limit`` is the bound; ``size`` is the byte count that was observed (the declared
    Content-Length when the peer states one, otherwise the bytes read when the read stopped,
    which is then only a lower bound: ``size_is_exact`` is False).
    """

    limit: int | None = None
    size: int | None = None
    size_is_exact: bool = False


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


def _oversized(cls: type[Exception], limit: int, size: int, exact: bool) -> Exception:
    error = cls(f"Payload exceeded max byte limit ({limit} bytes)")
    try:
        error.limit, error.size, error.size_is_exact = limit, size, exact  # type: ignore[attr-defined]
    except AttributeError:  # pragma: no cover - exotic error class without instance dict
        pass
    return error


def read_bounded_response(
    response: Any,
    max_bytes: int = 10485760,
    chunk_size: int = 65536,
    *,
    oversized_error_cls: type[Exception] = PayloadTooLarge,
    truncation_error_cls: type[Exception] = ResponseTruncated,
) -> bytes:
    """Read a response stream until EOF (empty chunk) up to max_bytes.

    Never treats a chunk shorter than chunk_size as EOF. If the response
    declares a Content-Length header and fewer bytes were read, raises
    truncation_error_cls. If the accumulated payload exceeds max_bytes,
    raises oversized_error_cls.
    """
    chunks: list[bytes] = []
    total = 0
    declared_size = declared_content_length(response)
    if declared_size is not None and declared_size > max_bytes:
        raise _oversized(oversized_error_cls, max_bytes, declared_size, True)
    while True:
        chunk = response.read(chunk_size)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise _oversized(oversized_error_cls, max_bytes, declared_size if declared_size is not None else total,
                             declared_size is not None)
        chunks.append(chunk)
    # http.client returns a short body without error when the peer closes early.
    declared = declared_content_length(response)
    if declared is not None and total < declared:
        raise truncation_error_cls(
            f"official response truncated: read {total} of {declared} declared bytes"
        )
    return b"".join(chunks)


__all__ = [
    "PayloadTooLarge",
    "ResponseTruncated",
    "declared_content_length",
    "read_bounded_response",
]
