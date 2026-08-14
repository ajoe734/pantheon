"""Research candidate intake HTTP client module for policy-learning.

Performs remote intake of imitation candidates into the Research experiment authority
via HTTP (POST /api/research-orchestrator/intake/imitation-candidate) and exact readback.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, Optional

RESEARCH_SERVICE_URL_ENV = "RESEARCH_SERVICE_URL"
RESEARCH_ORCHESTRATOR_URL_ENV = "RESEARCH_ORCHESTRATOR_URL"
DEFAULT_RESEARCH_SERVICE_URL = "http://research-orchestrator-svc:8101"
DEFAULT_TIMEOUT_SECONDS = 30.0


class ResearchCandidateClientError(RuntimeError):
    """Raised when an imitation candidate HTTP intake or readback request fails."""


@dataclass(frozen=True)
class ResearchCandidateClientReceipt:
    task_id: str
    run_id: str
    candidate_id: str
    status: str
    created_at: str
    raw_response: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "run_id": self.run_id,
            "candidate_id": self.candidate_id,
            "status": self.status,
            "created_at": self.created_at,
            "raw_response": self.raw_response,
        }


def get_research_service_url() -> str:
    """Return the configured or default Research service URL."""
    url = os.getenv(RESEARCH_SERVICE_URL_ENV) or os.getenv(RESEARCH_ORCHESTRATOR_URL_ENV) or DEFAULT_RESEARCH_SERVICE_URL
    return url.rstrip("/")


def post_imitation_candidate_intake_http(
    candidate: Dict[str, Any],
    *,
    research_url: Optional[str] = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> ResearchCandidateClientReceipt:
    """Send processed candidate to Research intake endpoint via HTTP.

    Calls POST /api/research-orchestrator/intake/imitation-candidate and verifies
    exact readback of task_id and run_id.
    """
    base_url = (research_url or get_research_service_url()).rstrip("/")
    intake_url = f"{base_url}/api/research-orchestrator/intake/imitation-candidate"

    payload = json.dumps(candidate).encode("utf-8")
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    req = urllib.request.Request(
        intake_url,
        data=payload,
        headers=headers,
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as response:
            status_code = response.status
            resp_bytes = response.read()
            data = json.loads(resp_bytes.decode("utf-8")) if resp_bytes else {}
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="replace")
        raise ResearchCandidateClientError(
            f"Research HTTP intake failed with status {exc.code}: {err_body}"
        ) from exc
    except urllib.error.URLError as exc:
        raise ResearchCandidateClientError(
            f"Research HTTP connection failed: {exc.reason}"
        ) from exc

    if status_code not in (200, 201):
        raise ResearchCandidateClientError(
            f"Unexpected status code {status_code} from Research HTTP intake: {data}"
        )

    task_id = str(data.get("task_id") or "").strip()
    run_id = str(data.get("run_id") or "").strip()
    candidate_id = str(data.get("candidate_id") or "").strip()
    status = str(data.get("status") or "").strip()
    created_at = str(data.get("created_at") or "").strip()

    if not task_id or not run_id:
        raise ResearchCandidateClientError(
            f"Research HTTP intake returned incomplete receipt (task_id: {task_id!r}, run_id: {run_id!r})"
        )

    # Perform exact readback verification via GET /api/research-orchestrator/runs/{run_id}
    readback_url = f"{base_url}/api/research-orchestrator/runs/{run_id}"
    readback_req = urllib.request.Request(
        readback_url,
        headers={"Accept": "application/json"},
        method="GET",
    )

    try:
        with urllib.request.urlopen(readback_req, timeout=timeout_seconds) as rb_resp:
            rb_bytes = rb_resp.read()
            rb_data = json.loads(rb_bytes.decode("utf-8")) if rb_bytes else {}
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="replace")
        raise ResearchCandidateClientError(
            f"Research HTTP readback failed for run_id {run_id!r} (status {exc.code}): {err_body}"
        ) from exc
    except urllib.error.URLError as exc:
        raise ResearchCandidateClientError(
            f"Research HTTP readback connection failed for run_id {run_id!r}: {exc.reason}"
        ) from exc

    rb_task_id = str(rb_data.get("task_id") or "").strip()
    rb_run_id = str(rb_data.get("run_id") or "").strip()

    if rb_task_id != task_id or rb_run_id != run_id:
        raise ResearchCandidateClientError(
            f"Research HTTP readback identity mismatch (expected task_id={task_id!r}, run_id={run_id!r}; got task_id={rb_task_id!r}, run_id={rb_run_id!r})"
        )

    return ResearchCandidateClientReceipt(
        task_id=task_id,
        run_id=run_id,
        candidate_id=candidate_id,
        status=status,
        created_at=created_at,
        raw_response=data,
    )
