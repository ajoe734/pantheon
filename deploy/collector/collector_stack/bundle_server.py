"""Read-only bearer-token bundle server handler (SD §6.2, §6.5, §6.6, §7.2)."""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Mapping


@dataclass(frozen=True)
class BundleHttpResponse:
    status_code: int
    headers: Mapping[str, str]
    body: bytes


class ReadOnlyBundleServer:
    """Serves /status.json and /bundles/* read-only behind a constant-time bearer token check."""

    SECURITY_HEADERS = {
        "Strict-Transport-Security": "max-age=63072000; includeSubDomains",
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
        "Cache-Control": "no-store",
    }

    def __init__(self, root_dir: Path | str, *, bearer_token: str) -> None:
        token = str(bearer_token or "").strip()
        if len(token) < 16 or token.lower() in {"changeme", "placeholder", "default"}:
            raise ValueError("collector bundle server requires a strong bearer token (>= 16 chars)")
        self.root_dir = Path(root_dir).resolve()
        self._expected_auth = f"Bearer {token}".encode("utf-8")

    def handle_request(
        self,
        *,
        method: str,
        path: str,
        headers: Mapping[str, str] | None = None,
    ) -> BundleHttpResponse:
        verb = str(method or "").upper()
        if verb not in {"GET", "HEAD"}:
            return BundleHttpResponse(
                status_code=405,
                headers={**self.SECURITY_HEADERS, "Allow": "GET, HEAD"},
                body=b'{"error":"method_not_allowed_read_only"}\n',
            )

        hdrs = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
        supplied_auth = hdrs.get("authorization", "").encode("utf-8")
        if not hmac.compare_digest(supplied_auth, self._expected_auth):
            return BundleHttpResponse(
                status_code=401,
                headers={**self.SECURITY_HEADERS, "WWW-Authenticate": 'Bearer realm="collector-bundles"'},
                body=b'{"error":"unauthorized"}\n',
            )

        clean_path = str(path or "").split("?", 1)[0].strip()
        posix = PurePosixPath(clean_path.lstrip("/"))
        if not posix.parts or ".." in posix.parts:
            return BundleHttpResponse(
                status_code=403,
                headers=self.SECURITY_HEADERS,
                body=b'{"error":"forbidden_path"}\n',
            )

        if posix.as_posix() == "status.json":
            target = (self.root_dir / "status.json").resolve()
        elif posix.parts[0] == "bundles":
            target = (self.root_dir / posix.as_posix()).resolve()
        else:
            return BundleHttpResponse(
                status_code=404,
                headers=self.SECURITY_HEADERS,
                body=b'{"error":"not_found"}\n',
            )

        if not str(target).startswith(str(self.root_dir) + "/") or not target.is_file():
            return BundleHttpResponse(
                status_code=404,
                headers=self.SECURITY_HEADERS,
                body=b'{"error":"not_found"}\n',
            )

        content_type = "application/octet-stream"
        if target.name.endswith(".json"):
            content_type = "application/json; charset=utf-8"
        elif target.name.endswith(".jsonl"):
            content_type = "application/x-ndjson; charset=utf-8"

        data = b"" if verb == "HEAD" else target.read_bytes()
        return BundleHttpResponse(
            status_code=200,
            headers={
                **self.SECURITY_HEADERS,
                "Content-Type": content_type,
                "Content-Length": str(target.stat().st_size),
            },
            body=data,
        )
