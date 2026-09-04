"""Jobs canonical router.

ACG-01-002 (docs/04/pantheon_architecture_cleanup_gap_2026-08-27): moves the
four ``/bff/jobs*`` handlers out of main.py into their own owner module with
explicit narrow dependency ports, matching the pattern already established
by evolution/router.py and research/router.py. No router imports main as
bff_main, inspects main.__dict__, or uses globals()/dynamic-proxy forwarding.

The in-memory ``_GOV_BFF_JOB_OVERLAY`` dict stays defined in main.py because
non-route code (management shell summary, assistant tenant filtering) and
several test modules reach into it directly (``bff_main._GOV_BFF_JOB_OVERLAY``);
this router observes it through an injected getter instead of owning it.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter, Body, Header, Query

from ..models import ErrorCode

JobOverlay = Callable[[], Dict[str, Dict[str, Any]]]
SubmitJobAction = Callable[[str, str, str, Any, Dict[str, Any]], Dict[str, Any]]


def create_jobs_router(
    *,
    get_read_store: Callable[[], Any],
    extract_identity: Callable[[Optional[str]], Any],
    require_read_role: Callable[[Any], None],
    bff_error: Callable[..., Exception],
    utc_now: Callable[[], str],
    page_slice: Callable[..., Any],
    read_surface_meta: Callable[..., Dict[str, Any]],
    dataset_surface_status: Callable[..., Dict[str, Any]],
    raise_if_read_surface_unavailable: Callable[..., None],
    get_job_overlay: JobOverlay,
    reject_body_idempotency_key: Callable[[Dict[str, Any]], None],
    resolve_final_idempotency_key: Callable[[Optional[str], Optional[str]], str],
    submit_job_action: SubmitJobAction,
) -> APIRouter:
    """Build the canonical Jobs router. Mount with ``app.include_router(...)``."""

    router = APIRouter()

    def _lookup_job(job_id: str) -> Optional[Dict[str, Any]]:
        read_store = get_read_store()
        return get_job_overlay().get(job_id) or read_store.get_job_bff(job_id)

    @router.get("/bff/jobs")
    async def list_jobs(
        status: Optional[str] = None,
        job_type: Optional[str] = None,
        page_token: Optional[str] = None,
        page_size: int = Query(default=20, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """BFF: job list using narrow port read_store.list_jobs_bff."""
        identity = extract_identity(authorization)
        require_read_role(identity)
        read_store = get_read_store()
        snapshot_at = utc_now()
        jobs = list(get_job_overlay().values()) or read_store.list_jobs_bff(status=status, job_type=job_type)
        if status:
            jobs = [j for j in jobs if j.get("status") == status]
        if job_type:
            jobs = [j for j in jobs if j.get("job_type") == job_type]
        total = len(jobs)
        page_items, next_page_token = page_slice(jobs, page_token, page_size)
        return {
            "items": page_items,
            "data": page_items,
            "page_info": {"next_page_token": next_page_token, "total": total},
            "meta": read_surface_meta("jobs", "job_list", snapshot_at=snapshot_at, total=total),
        }

    @router.get("/bff/jobs/{job_id}")
    async def get_job(
        job_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """BFF: job detail using narrow port read_store.get_job_bff."""
        identity = extract_identity(authorization)
        require_read_role(identity)
        snapshot_at = utc_now()
        job = _lookup_job(job_id)
        if not job:
            surface = dataset_surface_status("jobs", snapshot_at=snapshot_at)
            raise_if_read_surface_unavailable(surface, label="Job")
            raise bff_error(
                404, ErrorCode.RESOURCE_NOT_FOUND,
                "Job not found",
                f"Job {job_id} does not exist",
            )
        return {
            "data": job,
            "meta": read_surface_meta("jobs", "job_detail", snapshot_at=snapshot_at),
        }

    @router.get("/bff/jobs/{job_id}/logs")
    async def get_job_logs(
        job_id: str,
        authorization: Optional[str] = Header(default=None),
    ) -> Dict[str, Any]:
        """BFF: job logs using narrow port read_store.get_job_logs_bff."""
        identity = extract_identity(authorization)
        require_read_role(identity)
        read_store = get_read_store()
        snapshot_at = utc_now()
        job = _lookup_job(job_id)
        if not job:
            raise bff_error(
                404, ErrorCode.RESOURCE_NOT_FOUND,
                "Job not found",
                f"Job {job_id} does not exist",
            )
        logs = job.get("logs") if isinstance(job, dict) and "logs" in job else read_store.get_job_logs_bff(job_id)
        return {
            "job_id": job_id,
            "status": job.get("status") if isinstance(job, dict) else "unknown",
            "logs": logs,
            "data": logs,
            "meta": read_surface_meta("jobs", "job_logs", snapshot_at=snapshot_at),
        }

    @router.post("/bff/jobs/{job_id}/actions/{action_id}", status_code=202)
    async def job_action(
        job_id: str,
        action_id: str,
        payload: Dict[str, Any] = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ):
        """BFF: job action."""
        identity = extract_identity(authorization)
        require_read_role(identity)
        reject_body_idempotency_key(payload)
        resolved_key = resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
        job = _lookup_job(job_id)
        if not job:
            raise bff_error(
                404, ErrorCode.RESOURCE_NOT_FOUND,
                "Job not found",
                f"Job {job_id} does not exist",
            )
        return submit_job_action(job_id, action_id, resolved_key, identity, payload)

    return router
