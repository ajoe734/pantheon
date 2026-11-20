"""Read-only fetch of one paper broker order for journey booking reconciliation."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class BrokerPaperError(RuntimeError):
    """The paper broker order could not be read."""


class BrokerOrderNotFound(BrokerPaperError):
    """The broker has no record of the order (HTTP 404)."""


class BrokerPaperUnavailable(BrokerPaperError):
    """The broker was unreachable, timed out or returned an unusable response."""


def fetch_paper_order(
    broker_url: str,
    order_id: str,
    *,
    timeout_seconds: float = 3.0,
) -> dict[str, Any]:
    """GET /api/broker/paper/orders/{order_id}; exactly one bounded request."""
    url = (
        f"{broker_url.rstrip('/')}/api/broker/paper/orders/"
        f"{urllib.parse.quote(str(order_id), safe='')}"
    )
    request = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
    try:
        with urllib.request.urlopen(  # noqa: S310 - operator configured service URL.
            request, timeout=max(0.1, float(timeout_seconds))
        ) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise BrokerOrderNotFound(f"paper order {order_id} not found") from exc
        raise BrokerPaperUnavailable(f"paper broker returned HTTP {exc.code}") from exc
    except (urllib.error.URLError, OSError) as exc:  # timeouts are bare OSError
        raise BrokerPaperUnavailable(f"paper broker unavailable: {exc!r}") from exc
    try:
        body = json.loads(raw.decode("utf-8")) if raw else {}
    except ValueError as exc:
        raise BrokerPaperUnavailable("paper broker response was not valid JSON") from exc
    order = body.get("order") if isinstance(body, dict) else None
    if not isinstance(order, dict):
        raise BrokerPaperUnavailable("paper broker response carried no order")
    return order
