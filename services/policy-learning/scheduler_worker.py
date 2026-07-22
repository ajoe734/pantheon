"""Scheduled worker for human imitation and shadow evaluation ticks.

Posts periodic shadow-eval-tick requests to the policy-learning service.
Each tick proposes ShadowImitationCandidate records for trace datasets.
Production training remains fail-closed; candidates require a separate
experiment approval gate before any training is activated.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any


def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    value = int(os.getenv(name, str(default)))
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def run_tick(
    *,
    api_url: str,
    tick_id: str | None = None,
    eval_type: str = "shadow",
    dataset_refs: list[dict[str, Any]] | None = None,
    max_datasets: int | None = None,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    """POST /api/policy-learning/shadow-eval-tick and return the response."""
    body: dict[str, Any] = {"eval_type": eval_type}
    if tick_id:
        body["tick_id"] = tick_id
    if dataset_refs is not None:
        body["dataset_refs"] = dataset_refs
    if max_datasets is not None:
        body["max_datasets"] = max_datasets
    payload = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        api_url.rstrip("/") + "/api/policy-learning/shadow-eval-tick",
        data=payload,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
            response_body = response.read().decode("utf-8")
        return json.loads(response_body) if response_body else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        return {"status": "error", "code": exc.code, "detail": detail}
    except urllib.error.URLError as exc:
        return {"status": "error", "detail": str(exc.reason)}


def main() -> int:
    api_url = os.getenv("POLICY_LEARNING_API_URL", "http://policy-learning-svc:8100")
    interval_seconds = _env_int("SHADOW_EVAL_SCHEDULER_INTERVAL_SECONDS", 3600, minimum=1)
    max_ticks = _env_int("SHADOW_EVAL_SCHEDULER_MAX_TICKS", 0, minimum=0)
    eval_type = os.getenv("SHADOW_EVAL_TYPE", "shadow").strip() or "shadow"
    max_datasets_raw = os.getenv("SHADOW_EVAL_MAX_DATASETS", "").strip()
    max_datasets = int(max_datasets_raw) if max_datasets_raw else None

    tick = 0
    last_success: dict[str, Any] | None = None
    last_failure: dict[str, Any] | None = None

    while True:
        tick += 1
        try:
            result = run_tick(
                api_url=api_url,
                eval_type=eval_type,
                max_datasets=max_datasets,
            )
            if result.get("status") == "error":
                last_failure = result
            else:
                last_success = result
        except Exception as exc:
            last_failure = {"status": "error", "detail": str(exc)}
            result = last_failure

        metrics = {
            "tick": tick,
            "result": result,
            "last_success_candidate_count": (last_success or {}).get("candidate_count"),
            "last_failure_detail": (last_failure or {}).get("detail"),
        }
        print(json.dumps(metrics, sort_keys=True), flush=True)

        if max_ticks and tick >= max_ticks:
            return 0
        time.sleep(interval_seconds)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
