"""Authenticated public-BFF readback for lifecycle Postgres cutover proof."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from services.trade_journey.hosted_lifecycle_probe import (
    EXPECTED_STAGES,
    SCHEMA_VERSION as SOURCE_SCHEMA_VERSION,
    TASK_ID,
    _atomic_write_json,
)
from services.trade_journey.lifecycle_projector import STABLE_IDENTITY_FIELDS


SCHEMA_VERSION = "pantheon.lifecycle-proj-cutover-bff-readback.v1"
CONTROLLER_FIELDS = (
    "deployment_sha",
    "generation",
    "checkpoint",
    "mode",
    "accepted_live",
    "truth_level",
    "status",
    "backlog",
    "source_high_watermark",
    "quarantine_count",
)


class ReadbackError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        safe_details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.safe_message = message
        self.safe_details = dict(safe_details or {})


@dataclass(frozen=True)
class HttpResult:
    status: int
    payload: Any


@dataclass(frozen=True)
class SurfaceRead:
    result: HttpResult
    data: Mapping[str, Any]
    attempts: int
    transient_http_statuses: tuple[int, ...]


class HttpClient(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> HttpResult: ...


class UrlLibHttpClient:
    def __init__(self, *, timeout_seconds: float = 20.0) -> None:
        self.timeout_seconds = timeout_seconds

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> HttpResult:
        body = None
        request_headers = dict(headers or {})
        if payload is not None:
            body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        request = Request(url, data=body, headers=request_headers, method=method)
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:  # noqa: S310 - governed HTTPS endpoint
                status = int(response.status)
                raw = response.read()
        except HTTPError as exc:
            status = int(exc.code)
            raw = exc.read()
        except (OSError, URLError) as exc:
            raise ReadbackError("bff_transport_error", "public BFF request failed") from exc
        try:
            decoded = json.loads(raw.decode("utf-8")) if raw else None
        except (UnicodeDecodeError, json.JSONDecodeError):
            decoded = None
        return HttpResult(status=status, payload=decoded)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _require(condition: bool, code: str, message: str) -> None:
    if not condition:
        raise ReadbackError(code, message)


def _object(value: Any, code: str, message: str) -> Mapping[str, Any]:
    _require(isinstance(value, Mapping), code, message)
    return value


def _read_surface_data(
    *,
    client: HttpClient,
    url: str,
    headers: Mapping[str, str],
    code: str,
    label: str,
    max_attempts: int,
    poll_seconds: float,
    sleep: Callable[[float], None],
) -> SurfaceRead:
    """Wait for a newly published public read surface without leaking payloads."""

    attempts = max(1, int(max_attempts))
    delay = max(0.0, float(poll_seconds))
    transient_http_statuses: list[int] = []
    last_status: int | None = None
    last_transport_error = False
    for attempt in range(1, attempts + 1):
        try:
            result = client.request("GET", url, headers=headers)
        except ReadbackError as exc:
            if exc.code != "bff_transport_error":
                raise
            result = None
            last_status = None
            last_transport_error = True
        else:
            last_status = result.status
            last_transport_error = False
            payload = result.payload
            data = payload.get("data") if isinstance(payload, Mapping) else None
            if result.status == 200 and isinstance(data, Mapping):
                return SurfaceRead(
                    result=result,
                    data=data,
                    attempts=attempt,
                    transient_http_statuses=tuple(transient_http_statuses),
                )
            transient_http_statuses.append(result.status)
        if attempt < attempts:
            sleep(delay)

    details: dict[str, Any] = {
        "surface": label,
        "attempts": attempts,
        "transport_error": last_transport_error,
    }
    if last_status is not None:
        details["last_http_status"] = last_status
    raise ReadbackError(
        code,
        f"{label} did not converge after bounded retries",
        safe_details=details,
    )


def _controller_summary(controller: Mapping[str, Any]) -> dict[str, Any]:
    summary = {field: controller.get(field) for field in CONTROLLER_FIELDS}
    if summary.get("truth_level") in (None, ""):
        summary["truth_level"] = (
            "canonical_live"
            if controller.get("mode") == "live"
            and controller.get("accepted_live") is True
            else "not_accepted_live"
        )
    return summary


def _canonical_controller(
    controller: Mapping[str, Any],
    *,
    expected_sha: str,
    surface_generation: Any,
    minimum_generation: Any,
    checkpoint: int,
) -> dict[str, Any]:
    _require(controller.get("deployment_sha") == expected_sha, "bff_sha_mismatch", "BFF controller deployment SHA mismatched")
    try:
        observed_generation = int(surface_generation)
        admitted_generation = int(minimum_generation)
        controller_generation = int(controller.get("generation"))
    except (TypeError, ValueError) as exc:
        raise ReadbackError(
            "bff_generation_mismatch",
            "BFF controller generation was invalid",
        ) from exc
    _require(
        observed_generation >= admitted_generation,
        "bff_generation_mismatch",
        "BFF surface generation lagged hosted proof",
    )
    _require(
        controller_generation == observed_generation,
        "bff_generation_mismatch",
        "BFF controller generation mismatched its surface",
    )
    controller_checkpoint = int(controller.get("checkpoint") or 0)
    controller_high = int(controller.get("source_high_watermark") or 0)
    _require(controller_checkpoint >= checkpoint, "bff_checkpoint_mismatch", "BFF controller checkpoint lagged hosted proof")
    _require(controller_checkpoint == controller_high, "bff_checkpoint_mismatch", "BFF controller checkpoint did not equal its source high watermark")
    _require(controller.get("backlog") == 0, "bff_controller_not_live", "BFF controller backlog was nonzero")
    _require(int(controller.get("quarantine_count") or 0) == 0, "bff_controller_not_live", "BFF controller quarantine count was nonzero")
    expected = {
        "mode": "live",
        "accepted_live": True,
        "status": "ready",
    }
    _require(all(controller.get(key) == value for key, value in expected.items()), "bff_controller_not_live", "BFF controller was not canonical live truth")
    return _controller_summary(controller)


def _postgres_journey_surface_controller(
    payload: Mapping[str, Any],
    *,
    expected_sha: str,
    minimum_generation: Any,
    checkpoint: int,
    label: str,
) -> tuple[dict[str, Any], int]:
    meta = _object(
        payload.get("meta"),
        "bff_journey_surface_invalid",
        f"{label} metadata was missing",
    )
    freshness = _object(
        meta.get("freshness"),
        "bff_journey_surface_invalid",
        f"{label} freshness was missing",
    )
    _require(
        meta.get("read_state") == "formal"
        and freshness.get("rebuild_status") == "postgres_projection_reader"
        and freshness.get("projector_owned") is True
        and freshness.get("projection_schema_version")
        == "pantheon.trade-journey-projection.v1"
        and freshness.get("accepted_live") is True
        and freshness.get("projection_mode") == "live"
        and freshness.get("truth_level") == "canonical_live",
        "bff_journey_surface_invalid",
        f"{label} was not canonical Postgres live truth",
    )
    controller = _object(
        freshness.get("controller"),
        "bff_journey_surface_invalid",
        f"{label} controller was missing",
    )
    generation = int(freshness.get("generation"))
    return (
        _canonical_controller(
            controller,
            expected_sha=expected_sha,
            surface_generation=generation,
            minimum_generation=minimum_generation,
            checkpoint=checkpoint,
        ),
        generation,
    )


def _load_source(path: Path, expected_sha: str) -> tuple[dict[str, Any], str]:
    try:
        raw = path.read_bytes()
        source = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise ReadbackError("source_artifact_unreadable", "hosted source artifact was unreadable") from exc
    _require(isinstance(source, dict), "source_artifact_invalid", "hosted source artifact was malformed")
    _require(source.get("schema_version") == SOURCE_SCHEMA_VERSION, "source_artifact_invalid", "hosted source schema mismatched")
    _require(source.get("task_id") == TASK_ID, "source_artifact_invalid", "hosted source task mismatched")
    _require(source.get("outcome") == "passed", "source_artifact_failed", "hosted source proof did not pass")
    _require(source.get("expected_deployment_sha") == expected_sha, "source_sha_mismatch", "hosted source expected SHA mismatched")
    proof = _object(source.get("proof"), "source_artifact_invalid", "hosted source proof was missing")
    projection = _object(proof.get("projection"), "source_artifact_invalid", "hosted projection proof was missing")
    _require(projection.get("deployment_sha") == expected_sha, "source_sha_mismatch", "hosted projection SHA mismatched")
    _require(projection.get("backend") == "postgres", "source_artifact_invalid", "hosted projection proof did not use Postgres")
    return source, hashlib.sha256(raw).hexdigest()


def verify_readback(
    *,
    source_path: Path,
    expected_sha: str,
    base_url: str,
    client_id: str,
    client_secret: str,
    client: HttpClient,
    expected_login_identity: str = "operator",
    surface_read_attempts: int = 4,
    surface_read_poll_seconds: float = 5.0,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    _require(bool(expected_sha) and expected_sha != "unknown", "expected_sha_invalid", "a concrete expected SHA was required")
    _require(base_url.startswith("https://"), "bff_url_invalid", "public BFF URL must use HTTPS")
    _require(bool(client_id and client_secret), "bff_credentials_missing", "dev-login credentials were missing")
    source, source_sha256 = _load_source(source_path, expected_sha)
    proof = source["proof"]
    identity = _object(proof.get("identity"), "source_artifact_invalid", "hosted identity proof was missing")
    _require(all(identity.get(field) not in (None, "") for field in STABLE_IDENTITY_FIELDS), "source_artifact_invalid", "hosted stable identity was incomplete")
    _require(identity.get("environment") == "paper", "source_artifact_invalid", "hosted lifecycle was not paper")
    source_proof = _object(proof.get("source"), "source_artifact_invalid", "hosted source watermark was missing")
    projection = _object(proof.get("projection"), "source_artifact_invalid", "hosted projection proof was missing")
    generation = projection.get("generation")
    checkpoint = int(source_proof.get("source_high_watermark") or 0)
    _require(generation is not None and checkpoint > int(source_proof.get("baseline_high_watermark") or -1), "source_artifact_invalid", "hosted post-deploy watermark proof was invalid")

    base = base_url.rstrip("/")
    version_result = client.request("GET", f"{base}/bff/version")
    version = _object(version_result.payload, "bff_version_invalid", "public BFF version response was invalid")
    posture = _object(version.get("config_posture"), "bff_version_invalid", "public BFF posture was missing")
    _require(version_result.status == 200, "bff_version_invalid", "public BFF version request failed")
    _require(version.get("source_commit_sha") == expected_sha and version.get("source_commit_known") is True, "bff_sha_mismatch", "public BFF source SHA mismatched")
    _require(version.get("environment") == "dev", "bff_environment_mismatch", "public BFF was not dev")
    _require(posture.get("auth_stub") is False and posture.get("auth_mode") == "strict" and posture.get("dev_login_enabled") is True, "bff_auth_posture_invalid", "public BFF auth posture was not strict")
    _require(
        posture.get("trade_journey_reader_backend") == "postgres"
        and posture.get("trade_journey_projection_schema")
        == "trade_journey_projection",
        "bff_reader_posture_invalid",
        "public BFF lifecycle reader posture was not the accepted Postgres configuration",
    )

    login_result = client.request(
        "POST",
        f"{base}/bff/auth/dev-login",
        payload={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        },
    )
    login = _object(login_result.payload, "bff_login_failed", "public BFF dev-login response was invalid")
    login_meta = _object(login.get("meta"), "bff_login_failed", "public BFF dev-login metadata was missing")
    token = str(login.get("access_token") or "")
    _require(login_result.status == 200 and token and login.get("token_type") == "bearer", "bff_login_failed", "public BFF dev-login failed")
    _require(
        login_meta.get("identity") == expected_login_identity
        and 300 <= int(login.get("expires_in") or 0) <= 3600,
        "bff_login_failed",
        "public BFF dev-login identity was unexpected",
    )
    auth = {"Authorization": f"Bearer {token}"}

    loop_id = str(identity["loop_run_id"])
    journey_id = str(identity["journey_id"])
    tenant_id = str(identity["tenant_id"])
    journey_query = urlencode({"tenant_id": tenant_id, "environment": "paper"})
    loop_url = (
        f"{base}/bff/v5/loop-runs/{quote(loop_id, safe='')}?{journey_query}"
    )
    list_url = (
        f"{base}/bff/management/trade-journeys?"
        f"{urlencode({'tenant_id': tenant_id, 'environment': 'paper', 'q': journey_id, 'page_size': 50})}"
    )
    journey_url = f"{base}/bff/management/trade-journeys/{quote(journey_id, safe='')}?{journey_query}"
    timeline_url = f"{base}/bff/management/trade-journeys/{quote(journey_id, safe='')}/timeline?{journey_query}"
    graph_url = f"{base}/bff/management/trade-journeys/{quote(journey_id, safe='')}/graph?{journey_query}"
    evidence_url = f"{base}/bff/management/trade-journeys/{quote(journey_id, safe='')}/evidence?{journey_query}"
    cross_tenant_url = (
        f"{base}/bff/management/trade-journeys/{quote(journey_id, safe='')}?"
        f"{urlencode({'tenant_id': f'{tenant_id}-outside', 'environment': 'paper'})}"
    )
    cross_environment_url = (
        f"{base}/bff/management/trade-journeys/{quote(journey_id, safe='')}?"
        f"{urlencode({'tenant_id': tenant_id, 'environment': 'live'})}"
    )
    stale_page_url = f"{list_url}&page_token=stale-cutover-token"

    negative_statuses = {
        "loop_unauthorized": client.request("GET", loop_url).status,
        "loop_arbitrary_bearer": client.request("GET", loop_url, headers={"Authorization": "Bearer lifecycle-cutover-fixed-invalid"}).status,
        "list_unauthorized": client.request("GET", list_url).status,
        "journey_unauthorized": client.request("GET", journey_url).status,
        "journey_arbitrary_bearer": client.request("GET", journey_url, headers={"Authorization": "Bearer lifecycle-cutover-fixed-invalid"}).status,
        "timeline_unauthorized": client.request("GET", timeline_url).status,
        "graph_unauthorized": client.request("GET", graph_url).status,
    }
    _require(all(status == 401 for status in negative_statuses.values()), "bff_auth_negative_failed", "public BFF protected read accepted an invalid identity")
    scope_negative_statuses = {
        "cross_tenant_detail": client.request(
            "GET", cross_tenant_url, headers=auth
        ).status,
        "cross_environment_live_sensitive_detail": client.request(
            "GET", cross_environment_url, headers=auth
        ).status,
    }
    _require(
        all(status == 404 for status in scope_negative_statuses.values()),
        "bff_scope_negative_failed",
        "public BFF cross-scope detail did not use protected not-found semantics",
    )
    conflict_negative_statuses = {
        "stale_or_scope_conflicting_page_token": client.request(
            "GET", stale_page_url, headers=auth
        ).status,
    }
    _require(
        all(status == 400 for status in conflict_negative_statuses.values()),
        "bff_conflict_negative_failed",
        "public BFF accepted a stale or scope-conflicting page token",
    )

    loop_read = _read_surface_data(
        client=client,
        url=loop_url,
        headers=auth,
        code="bff_loop_read_invalid",
        label="loop-run detail",
        max_attempts=surface_read_attempts,
        poll_seconds=surface_read_poll_seconds,
        sleep=sleep,
    )
    loop_result = loop_read.result
    loop_payload = _object(loop_result.payload, "bff_loop_read_invalid", "loop-run response was invalid")
    loop = loop_read.data
    loop_surface = _object(_object(loop_payload.get("meta"), "bff_loop_read_invalid", "loop-run metadata was missing").get("surfaces"), "bff_loop_read_invalid", "loop-run surfaces were missing")
    loop_surface = _object(loop_surface.get("loop_run_detail"), "bff_loop_read_invalid", "loop-run detail surface was missing")
    _require(all(str(loop.get(field) or "") == str(identity[field]) for field in STABLE_IDENTITY_FIELDS), "bff_loop_identity_mismatch", "loop-run stable identity mismatched hosted proof")
    _require(loop.get("id") == loop_id and loop.get("loop_run_id") == loop_id and loop.get("journey_id") == journey_id, "bff_loop_identity_mismatch", "loop-run identifiers mismatched hosted proof")
    _require(loop.get("source") == "postgres_lifecycle_projection", "bff_loop_truth_invalid", "loop-run record did not come from the Postgres projection")
    loop_freshness = _object(loop.get("freshness_lineage"), "bff_loop_truth_invalid", "loop-run freshness lineage was missing")
    _require(loop_freshness.get("accepted_live") is True and loop_freshness.get("mode") == "live", "bff_loop_truth_invalid", "loop-run record was not canonical live truth")
    _require(loop.get("status") == projection.get("loop_status"), "bff_loop_truth_invalid", "loop-run terminal truth mismatched hosted proof")
    _require(loop_surface.get("status") == "ok" and loop_surface.get("source") == "postgres_lifecycle_projection" and loop_surface.get("truth_status") == "formal", "bff_loop_surface_invalid", "loop-run BFF surface was not formal Postgres truth")
    _require(
        loop_surface.get("projection_schema_version")
        == "pantheon.trade-journey-projection.v1",
        "bff_loop_surface_invalid",
        "loop-run projection metadata mismatched",
    )
    loop_controller = _object(loop_surface.get("controller"), "bff_loop_surface_invalid", "loop-run controller was missing")
    loop_generation = loop_controller.get("generation")
    loop_controller_summary = _canonical_controller(
        loop_controller,
        expected_sha=expected_sha,
        surface_generation=loop_generation,
        minimum_generation=generation,
        checkpoint=checkpoint,
    )

    journey_read = _read_surface_data(
        client=client,
        url=journey_url,
        headers=auth,
        code="bff_journey_read_invalid",
        label="Trade Journey detail",
        max_attempts=surface_read_attempts,
        poll_seconds=surface_read_poll_seconds,
        sleep=sleep,
    )
    journey_result = journey_read.result
    journey_payload = _object(journey_result.payload, "bff_journey_read_invalid", "Trade Journey response was invalid")
    journey = journey_read.data
    journey_meta = _object(journey_payload.get("meta"), "bff_journey_read_invalid", "Trade Journey metadata was missing")
    freshness = _object(journey_meta.get("freshness"), "bff_journey_read_invalid", "Trade Journey freshness was missing")
    _require(journey_result.status == 200 and journey.get("journey_id") == journey_id, "bff_journey_read_invalid", "Trade Journey detail request failed")
    _require(journey.get("tenant_id") == tenant_id and journey.get("environment") == "paper" and journey.get("status") == projection.get("loop_status"), "bff_journey_identity_mismatch", "Trade Journey identity or status mismatched")
    _require(journey.get("read_state") == "formal" and journey_meta.get("read_state") == "formal" and int(journey.get("event_count") or 0) >= len(proof.get("events") or []), "bff_journey_truth_invalid", "Trade Journey was not formal complete truth")
    journey_generation = freshness.get("generation")
    _require(freshness.get("projector_owned") is True and freshness.get("projection_schema_version") == "pantheon.trade-journey-projection.v1", "bff_journey_surface_invalid", "Trade Journey projection metadata mismatched")
    _require(freshness.get("rebuild_status") == "postgres_projection_reader" and freshness.get("accepted_live") is True and freshness.get("projection_mode") == "live" and freshness.get("truth_level") == "canonical_live", "bff_journey_surface_invalid", "Trade Journey surface was not canonical Postgres live truth")
    journey_controller = _object(freshness.get("controller"), "bff_journey_surface_invalid", "Trade Journey controller was missing")
    journey_controller_summary = _canonical_controller(
        journey_controller,
        expected_sha=expected_sha,
        surface_generation=journey_generation,
        minimum_generation=generation,
        checkpoint=checkpoint,
    )

    list_read = _read_surface_data(
        client=client,
        url=list_url,
        headers=auth,
        code="bff_list_read_invalid",
        label="Trade Journey list",
        max_attempts=surface_read_attempts,
        poll_seconds=surface_read_poll_seconds,
        sleep=sleep,
    )
    list_payload = _object(
        list_read.result.payload,
        "bff_list_read_invalid",
        "Trade Journey list response was invalid",
    )
    list_items = list_read.data.get("items")
    _require(
        isinstance(list_items, list)
        and any(
            isinstance(item, Mapping) and item.get("journey_id") == journey_id
            for item in list_items
        ),
        "bff_list_read_invalid",
        "Trade Journey list did not contain the hosted lifecycle",
    )
    list_controller_summary, list_generation = _postgres_journey_surface_controller(
        list_payload,
        expected_sha=expected_sha,
        minimum_generation=generation,
        checkpoint=checkpoint,
        label="Trade Journey list",
    )

    timeline_read = _read_surface_data(
        client=client,
        url=timeline_url,
        headers=auth,
        code="bff_timeline_read_invalid",
        label="Trade Journey timeline",
        max_attempts=surface_read_attempts,
        poll_seconds=surface_read_poll_seconds,
        sleep=sleep,
    )
    timeline_payload = _object(
        timeline_read.result.payload,
        "bff_timeline_read_invalid",
        "Trade Journey timeline response was invalid",
    )
    timeline_items = timeline_read.data.get("items")
    _require(
        isinstance(timeline_items, list),
        "bff_timeline_read_invalid",
        "Trade Journey timeline items were missing",
    )
    expected_event_ids = {
        str(event.get("event_id") or "") for event in proof.get("events") or []
    }
    timeline_event_ids = {
        str(item.get("event_id") or "")
        for item in timeline_items
        if isinstance(item, Mapping)
    }
    _require(
        expected_event_ids <= timeline_event_ids,
        "bff_timeline_read_invalid",
        "Trade Journey timeline did not contain every hosted event",
    )
    timeline_controller_summary, timeline_generation = (
        _postgres_journey_surface_controller(
            timeline_payload,
            expected_sha=expected_sha,
            minimum_generation=generation,
            checkpoint=checkpoint,
            label="Trade Journey timeline",
        )
    )

    graph_read = _read_surface_data(
        client=client,
        url=graph_url,
        headers=auth,
        code="bff_graph_read_invalid",
        label="Trade Journey graph",
        max_attempts=surface_read_attempts,
        poll_seconds=surface_read_poll_seconds,
        sleep=sleep,
    )
    graph_payload = _object(
        graph_read.result.payload,
        "bff_graph_read_invalid",
        "Trade Journey graph response was invalid",
    )
    graph_nodes = graph_read.data.get("nodes")
    _require(
        graph_read.data.get("journey_id") == journey_id
        and isinstance(graph_nodes, list)
        and any(
            isinstance(node, Mapping) and node.get("id") == journey_id
            for node in graph_nodes
        ),
        "bff_graph_read_invalid",
        "Trade Journey graph did not contain the hosted journey",
    )
    graph_controller_summary, graph_generation = _postgres_journey_surface_controller(
        graph_payload,
        expected_sha=expected_sha,
        minimum_generation=generation,
        checkpoint=checkpoint,
        label="Trade Journey graph",
    )
    evidence_read = _read_surface_data(
        client=client,
        url=evidence_url,
        headers=auth,
        code="bff_evidence_invalid",
        label="Trade Journey evidence",
        max_attempts=surface_read_attempts,
        poll_seconds=surface_read_poll_seconds,
        sleep=sleep,
    )
    evidence_result = evidence_read.result
    evidence_payload = _object(evidence_result.payload, "bff_evidence_invalid", "Trade Journey evidence response was invalid")
    evidence = evidence_read.data
    by_stage = _object(evidence.get("by_stage"), "bff_evidence_invalid", "Trade Journey stage evidence was missing")
    correlated = 0
    for event in proof.get("events") or []:
        event = _object(event, "source_artifact_invalid", "hosted lifecycle event proof was malformed")
        stage = EXPECTED_STAGES.get(str(event.get("event_type") or ""))
        bucket = _object(by_stage.get(stage), "bff_evidence_mismatch", "Trade Journey stage evidence was missing")
        expected_event_id = str(event.get("event_id") or "")
        _require(expected_event_id in (bucket.get("event_ids") or []), "bff_evidence_mismatch", "Trade Journey event ID correlation mismatched")
        correlated += 1
    _require(evidence_result.status == 200 and evidence.get("journey_id") == journey_id, "bff_evidence_invalid", "Trade Journey evidence request failed")
    evidence_controller_summary, evidence_generation = (
        _postgres_journey_surface_controller(
            evidence_payload,
            expected_sha=expected_sha,
            minimum_generation=generation,
            checkpoint=checkpoint,
            label="Trade Journey evidence",
        )
    )

    ordered_controllers = (
        loop_controller_summary,
        journey_controller_summary,
        list_controller_summary,
        timeline_controller_summary,
        graph_controller_summary,
        evidence_controller_summary,
    )
    ordered_generations = (
        int(loop_generation),
        int(journey_generation),
        int(list_generation),
        int(timeline_generation),
        int(graph_generation),
        int(evidence_generation),
    )
    controller_identity_fields = (
        "deployment_sha",
        "mode",
        "accepted_live",
        "truth_level",
        "status",
    )
    _require(
        all(
            all(
                controller.get(field) == ordered_controllers[0].get(field)
                for field in controller_identity_fields
            )
            for controller in ordered_controllers[1:]
        )
        and list(ordered_generations) == sorted(ordered_generations),
        "bff_cross_surface_mismatch",
        "BFF surfaces did not preserve one deployment with monotonic controller generations",
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "task_id": TASK_ID,
        "outcome": "passed",
        "observed_at": _utc_now(),
        "expected_deployment_sha": expected_sha,
        "source_artifact_sha256": source_sha256,
        "public_bff": {
            "base_url": base,
            "version": {
                "http_status": version_result.status,
                "source_commit_sha": version.get("source_commit_sha"),
                "environment": version.get("environment"),
                "config_posture": {
                    "auth_stub": posture.get("auth_stub"),
                    "auth_mode": posture.get("auth_mode"),
                    "dev_login_enabled": posture.get("dev_login_enabled"),
                    "trade_journey_reader_backend": posture.get(
                        "trade_journey_reader_backend"
                    ),
                    "trade_journey_projection_schema": posture.get(
                        "trade_journey_projection_schema"
                    ),
                },
            },
            "auth": {
                "identity": login_meta.get("identity"),
                "token_type": login.get("token_type"),
                "expires_in": login.get("expires_in"),
                "negative_statuses": negative_statuses,
                "scope_negative_statuses": scope_negative_statuses,
                "conflict_negative_statuses": conflict_negative_statuses,
            },
            "loop_run": {
                "http_status": loop_result.status,
                "read_attempts": loop_read.attempts,
                "transient_http_statuses": list(loop_read.transient_http_statuses),
                "loop_run_id": loop_id,
                "journey_id": journey_id,
                "status": loop.get("status"),
                "source": loop.get("source"),
                "source_projection_generation": generation,
                "projection_generation": loop_generation,
                "controller": loop_controller_summary,
            },
            "trade_journey": {
                "http_status": journey_result.status,
                "evidence_http_status": evidence_result.status,
                "read_attempts": journey_read.attempts,
                "transient_http_statuses": list(journey_read.transient_http_statuses),
                "evidence_read_attempts": evidence_read.attempts,
                "evidence_transient_http_statuses": list(
                    evidence_read.transient_http_statuses
                ),
                "journey_id": journey_id,
                "tenant_id": tenant_id,
                "environment": "paper",
                "status": journey.get("status"),
                "read_state": journey.get("read_state"),
                "event_count": journey.get("event_count"),
                "correlated_event_count": correlated,
                "source_projection_generation": generation,
                "projection_generation": journey_generation,
                "controller": journey_controller_summary,
                "list_http_status": list_read.result.status,
                "timeline_http_status": timeline_read.result.status,
                "graph_http_status": graph_read.result.status,
                "list_projection_generation": list_generation,
                "timeline_projection_generation": timeline_generation,
                "graph_projection_generation": graph_generation,
                "evidence_projection_generation": evidence_generation,
            },
            "cross_surface": {
                "same_deployment_identity": True,
                "monotonic_controller_generations": True,
                "ordered_generations": list(ordered_generations),
                "exact_event_ids": True,
                "generation_advanced_since_source_proof": (
                    max(ordered_generations) > int(generation)
                ),
            },
            "sensitive_field_posture": {
                "response_payloads_persisted": False,
                "access_token_persisted": False,
                "credentials_persisted": False,
                "paper_only": True,
            },
        },
        "redaction": {
            "access_token_included": False,
            "credentials_included": False,
            "response_payloads_included": False,
        },
    }


def execute_readback(**kwargs: Any) -> tuple[int, dict[str, Any]]:
    output = Path(kwargs.pop("output"))
    expected_sha = str(kwargs.get("expected_sha") or "")
    source_path = Path(kwargs.get("source_path"))
    source_sha256 = None
    try:
        if source_path.is_file():
            source_sha256 = hashlib.sha256(source_path.read_bytes()).hexdigest()
        artifact = verify_readback(**kwargs)
        code = 0
    except ReadbackError as exc:
        artifact = {
            "schema_version": SCHEMA_VERSION,
            "task_id": TASK_ID,
            "outcome": "failed",
            "observed_at": _utc_now(),
            "expected_deployment_sha": expected_sha,
            "source_artifact_sha256": source_sha256,
            "failure": {"code": exc.code, "message": exc.safe_message},
            "redaction": {"access_token_included": False, "credentials_included": False, "response_payloads_included": False},
        }
        if exc.safe_details:
            artifact["failure"]["details"] = exc.safe_details
        code = 1
    except Exception:  # noqa: BLE001 - always emit allowlist-only failure evidence
        artifact = {
            "schema_version": SCHEMA_VERSION,
            "task_id": TASK_ID,
            "outcome": "failed",
            "observed_at": _utc_now(),
            "expected_deployment_sha": expected_sha,
            "source_artifact_sha256": source_sha256,
            "failure": {"code": "unexpected_readback_error", "message": "public BFF readback failed unexpectedly"},
            "redaction": {"access_token_included": False, "credentials_included": False, "response_payloads_included": False},
        }
        code = 1
    _atomic_write_json(output, artifact)
    return code, artifact


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--expected-login-identity", default="operator")
    parser.add_argument("--base-url", required=True)
    args = parser.parse_args(argv)
    code, artifact = execute_readback(
        source_path=args.source,
        output=args.output,
        expected_sha=args.expected_sha,
        base_url=args.base_url,
        client_id=os.getenv("DEV_BFF_OIDC_CLIENT_ID", "").strip(),
        client_secret=os.getenv("DEV_BFF_OIDC_CLIENT_SECRET", "").strip(),
        client=UrlLibHttpClient(),
        expected_login_identity=args.expected_login_identity,
    )
    print(json.dumps({"outcome": artifact["outcome"], "output": str(args.output)}, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
