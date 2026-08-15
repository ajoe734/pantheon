"""L12-CURRENT-AGORA-HANDOFF-CUTOVER-20260814: durable handoff sole intake.

``docker-compose.yml`` only ever ran ``scheduler_worker.py`` as the scheduled
policy-learning worker -- there was no separate container for
``agora_handoff_drainer.py`` -- so the drainer's claim/ack cycle was dead code
in production even though it had its own unit coverage
(``test_agora_handoff_drainer.py``).  Every real scheduled tick instead went
through ``run_tick`` -> ``/api/policy-learning/shadow-eval-tick``, which
discovers work by scanning ``agora.agora_dataset_records`` directly through
``AgoraDatasetAuthority``.

This module proves the cutover: ``scheduler_worker.main()`` now drives the
durable handoff drain/ack cycle instead of the scanner-backed discovery tick,
so the direct database scanner has zero production callers, a handoff is
claimed and acknowledged exactly once even across scheduled windows and ack
retries, and the resulting candidate keeps the same handoff and dataset
identity throughout.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any
from unittest import mock

from conftest import authorized_client


SERVICE_DIR = Path(__file__).resolve().parents[1]
_REPO_ROOT = SERVICE_DIR.parents[1]


def _load_scheduler_module():
    sys.modules.pop("l12_agora_cutover_scheduler_test", None)
    spec = importlib.util.spec_from_file_location(
        "l12_agora_cutover_scheduler_test",
        SERVICE_DIR / "scheduler_worker.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["l12_agora_cutover_scheduler_test"] = module
    spec.loader.exec_module(module)
    return module


def _load_service_module(data_dir: str):
    for key in list(sys.modules):
        if "l12_agora_cutover_service_test" in key:
            del sys.modules[key]
    sys.modules.pop("store", None)
    if str(SERVICE_DIR) not in sys.path:
        sys.path.insert(0, str(SERVICE_DIR))
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))
    try:
        spec = importlib.util.spec_from_file_location(
            "l12_agora_cutover_service_test_main",
            SERVICE_DIR / "main.py",
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules["l12_agora_cutover_service_test_main"] = module
        with mock.patch.dict(
            "os.environ",
            {
                "POLICY_LEARNING_DATA_DIR": data_dir,
                "POLICY_LEARNING_STORE_BACKEND": "json",
                "PERSISTENCE_POSTURE": "lenient",
            },
        ):
            spec.loader.exec_module(module)
        return module
    finally:
        sys.modules.pop("store", None)


# ---------------------------------------------------------------------------
# A fake Agora BFF: only the two routes the drainer calls.
# ---------------------------------------------------------------------------


class _FakeAgoraBff:
    """In-memory stand-in for ``operator-bff``'s dataset-worker handoff routes.

    Mirrors the real contract the drainer depends on: an acknowledged handoff
    is not returned by a later ``GET .../handoffs`` poll, so a scheduler that
    polls twice only ever claims an outstanding handoff once.
    """

    def __init__(self) -> None:
        self.handoffs: dict[str, dict[str, Any]] = {}
        self.ack_attempts: list[str] = []
        self.calls: list[dict[str, Any]] = []
        self.fail_ack_once_for: set[str] = set()

    def add(self, *, handoff_id: str, dataset_version_id: str, tenant_id: str) -> None:
        self.handoffs[handoff_id] = {
            "handoff_id": handoff_id,
            "dataset_version_id": dataset_version_id,
            "dataset_ref": {"dataset_version_id": dataset_version_id, "tenant_id": tenant_id},
            "acknowledged": False,
        }

    def handle(
        self,
        method: str,
        path: str,
        body: dict[str, Any],
        headers: dict[str, str],
    ) -> tuple[int, dict[str, Any]]:
        self.calls.append(
            {"method": method, "path": path, "headers": dict(headers)}
        )
        if method == "GET" and path.endswith("/dataset-handoffs"):
            items = list(self.handoffs.values())
            return 200, {"status": "success", "items": items, "total": len(items)}

        if method == "POST" and path.endswith("/ack") and "/dataset-handoffs/" in path:
            handoff_id = path.split("/dataset-handoffs/", 1)[1].rsplit("/ack", 1)[0]
            self.ack_attempts.append(handoff_id)
            if handoff_id in self.fail_ack_once_for:
                self.fail_ack_once_for.discard(handoff_id)
                return 503, {"status": "error", "detail": "simulated transient ack failure"}
            record = self.handoffs.get(handoff_id)
            if record is not None:
                record["acknowledged"] = True
            return 200, {"status": "acknowledged", "idempotent": False}

        raise AssertionError(f"unexpected fake Agora BFF call: {method} {path}")


class _TransportResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self) -> "_TransportResponse":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False

    def read(self) -> bytes:
        return self._body


class _CombinedTransport:
    """Routes ``urllib.request.urlopen`` calls to the fake BFF or the real app.

    ``run_intake_cycle`` talks to two independent services in production: the
    Agora BFF (durable handoffs) and policy-learning (candidate admission).
    Faking both behind one transport, keyed by URL prefix, exercises the real
    drainer and the real FastAPI app together instead of mocking either one's
    internal functions away.
    """

    def __init__(self, *, agora_url: str, app: Any, fake_bff: _FakeAgoraBff) -> None:
        from fastapi.testclient import TestClient

        self.agora_url = agora_url.rstrip("/")
        self.fake_bff = fake_bff
        self.client = TestClient(app)
        self.policy_learning_calls: list[dict[str, Any]] = []

    def __call__(self, request: urllib.request.Request, timeout: float | None = None):
        full_url = request.full_url
        path = urllib.parse.urlsplit(full_url).path
        body = json.loads(request.data.decode("utf-8")) if request.data else {}

        if full_url.startswith(self.agora_url):
            status, payload = self.fake_bff.handle(
                request.get_method(),
                path,
                body,
                dict(request.header_items()),
            )
            if status >= 400:
                raise urllib.error.HTTPError(
                    full_url, status, "error", {}, io.BytesIO(json.dumps(payload).encode("utf-8"))
                )
            return _TransportResponse(json.dumps(payload).encode("utf-8"))

        headers = dict(request.header_items())
        response = self.client.request(
            request.get_method(), path, content=request.data, headers=headers
        )
        self.policy_learning_calls.append(
            {"method": request.get_method(), "path": path, "status": response.status_code}
        )
        if response.status_code >= 400:
            raise urllib.error.HTTPError(
                full_url, response.status_code, response.reason_phrase, dict(response.headers), io.BytesIO(response.content)
            )
        return _TransportResponse(response.content)


# ---------------------------------------------------------------------------
# Production caller search
# ---------------------------------------------------------------------------


def _main_function_source() -> str:
    """The exact source text of ``scheduler_worker.main``'s body."""

    source = (SERVICE_DIR / "scheduler_worker.py").read_text(encoding="utf-8")
    start = source.index("\ndef main() -> int:")
    end = source.index("\nif __name__ ==", start)
    return source[start:end]


def test_direct_scanner_has_zero_scheduled_production_callers() -> None:
    """The scheduled loop no longer reaches the DB-scan discovery route.

    ``run_tick`` posts to ``/api/policy-learning/shadow-eval-tick``, which is
    the only production caller of ``discover_eligible_datasets`` (the direct
    Agora database scanner). It must stay defined -- other tests and the
    compose end-to-end proof call it directly -- but the scheduled ``main()``
    loop must not call it anymore.
    """

    scheduler = _load_scheduler_module()
    assert hasattr(scheduler, "run_tick"), "run_tick must remain available for direct/manual use"

    main_source = _main_function_source()
    assert "run_tick(" not in main_source, (
        "scheduler_worker.main() must not call the DB-scan discovery tick; "
        "the durable Agora handoff is the sole scheduled production intake"
    )
    assert "run_intake_cycle(" in main_source, (
        "scheduler_worker.main() must drive the durable handoff intake cycle"
    )

    # Only ``main()`` needs to change; the scanner-backed route itself must
    # keep existing so a human/manual trigger is still possible.
    full_source = (SERVICE_DIR / "scheduler_worker.py").read_text(encoding="utf-8")
    assert "def run_tick(" in full_source
    assert "def window_tick_id(" in full_source


def test_scheduler_agora_bff_default_matches_compose_service() -> None:
    """The fallback BFF URL must resolve inside the real compose network.

    The prior default, ``http://control-plane-bff-svc:8000``, named a service
    that does not exist in ``docker-compose.yml``; the real Agora BFF service
    is ``operator-bff`` on port 8001 (see its healthcheck hitting
    ``127.0.0.1:8001``). ``docker-compose.yml`` is out of scope for this task,
    so the fallback default in code is what makes the scheduled intake
    actually reach the BFF without a compose change.
    """

    scheduler = _load_scheduler_module()
    import agora_handoff_drainer

    assert agora_handoff_drainer.DEFAULT_AGORA_BFF_URL == "http://operator-bff:8001"
    assert scheduler.AGORA_BFF_URL_ENV == agora_handoff_drainer.AGORA_BFF_URL_ENV == "AGORA_BFF_URL"


# ---------------------------------------------------------------------------
# Two scheduled-window integration test
# ---------------------------------------------------------------------------


def test_intake_claims_and_acks_handoff_exactly_once_across_two_windows() -> None:
    """Two scheduled ticks over one still-pending handoff drain it once.

    Runs the real ``scheduler_worker.main()`` loop for two ticks against a
    fake Agora BFF holding a single handoff and the real policy-learning
    FastAPI app.  The first window must claim, admit, and acknowledge the
    handoff; because the BFF then reports it acknowledged, the second window
    must not repeat the claim, the POST to policy-learning, or the ack.
    """

    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        scheduler = _load_scheduler_module()

        fake_bff = _FakeAgoraBff()
        fake_bff.add(handoff_id="h-window-001", dataset_version_id="dsv-window-001", tenant_id="tenant-a")

        agora_url = "http://fake-agora-bff.test"
        transport = _CombinedTransport(agora_url=agora_url, app=svc.app, fake_bff=fake_bff)

        env = {
            "POLICY_LEARNING_SERVICE_TOKEN": "l12-imit-001-test-service-token",
            "AGORA_HANDOFF_SERVICE_TOKEN": "l12-agora-handoff-test-token",
            "POLICY_LEARNING_AGORA_TENANT_ID": "tenant-a",
            "POLICY_LEARNING_API_URL": "http://policy-learning-svc.test",
            "AGORA_BFF_URL": agora_url,
            "SHADOW_EVAL_SCHEDULER_INTERVAL_SECONDS": "1",
            "SHADOW_EVAL_SCHEDULER_MAX_TICKS": "2",
        }
        with mock.patch.dict(os.environ, env, clear=False), mock.patch(
            "urllib.request.urlopen", transport
        ):
            exit_code = scheduler.main()

        assert exit_code == 0
        # Claimed and acknowledged exactly once even though two windows ran.
        assert fake_bff.ack_attempts == ["h-window-001"]
        assert fake_bff.handoffs["h-window-001"]["acknowledged"] is True
        for call in fake_bff.calls:
            headers = {key.lower(): value for key, value in call["headers"].items()}
            assert headers["authorization"] == (
                "Bearer l12-agora-handoff-test-token"
            )
            assert headers["x-pantheon-service-actor"] == (
                "policy-learning-agora-handoff-drainer"
            )

        handoff_posts = [
            call
            for call in transport.policy_learning_calls
            if call["path"] == "/api/policy-learning/agora-handoff"
        ]
        assert len(handoff_posts) == 1, "the second window must not re-post the already-acked handoff"

        client = authorized_client(svc.app, tenant_id="tenant-a")
        candidates = client.get("/api/policy-learning/candidates").json()
        assert len(candidates) == 1
        candidate = candidates[0]
        assert candidate["handoff_id"] == "h-window-001"
        assert candidate["dataset_ref"]["dataset_version_id"] == "dsv-window-001"
        assert candidate["dataset_source"] == "agora_dataset_version_handoff"


# ---------------------------------------------------------------------------
# Claim/ack replay test
# ---------------------------------------------------------------------------


def test_intake_replay_after_failed_ack_does_not_duplicate_candidate() -> None:
    """A handoff whose ack is lost in transit is safely reclaimed and re-acked.

    Simulates a transient failure acknowledging the handoff on the BFF: the
    candidate is admitted on the first drain, but the BFF still reports the
    handoff outstanding, so the next cycle re-fetches and re-posts it. That
    replay must resolve to the same candidate identity (dedupe, not a
    duplicate) and must succeed in acknowledging on the retry.
    """

    with tempfile.TemporaryDirectory() as data_dir:
        svc = _load_service_module(data_dir)
        scheduler = _load_scheduler_module()

        fake_bff = _FakeAgoraBff()
        fake_bff.add(handoff_id="h-replay-001", dataset_version_id="dsv-replay-001", tenant_id="tenant-a")
        fake_bff.fail_ack_once_for.add("h-replay-001")

        agora_url = "http://fake-agora-bff.test"
        transport = _CombinedTransport(agora_url=agora_url, app=svc.app, fake_bff=fake_bff)

        with mock.patch.dict(
            os.environ,
            {
                "POLICY_LEARNING_SERVICE_TOKEN": "l12-imit-001-test-service-token",
                "POLICY_LEARNING_AGORA_TENANT_ID": "tenant-a",
            },
            clear=False,
        ), mock.patch("urllib.request.urlopen", transport):
            # First drain: ingest succeeds, ack fails (transient), so the BFF
            # still reports the handoff outstanding.
            first = scheduler.run_intake_cycle(
                agora_url=agora_url,
                policy_learning_url="http://policy-learning-svc.test",
                tenant_id="tenant-a",
                agora_token="l12-agora-handoff-test-token",
                policy_learning_token="l12-imit-001-test-service-token",
            )
            assert first["status"] == "degraded"
            assert first["processed_count"] == 1
            assert first["acked_count"] == 0
            assert fake_bff.handoffs["h-replay-001"]["acknowledged"] is False

            client = authorized_client(svc.app, tenant_id="tenant-a")
            after_first = client.get("/api/policy-learning/candidates").json()
            assert len(after_first) == 1
            candidate_id = after_first[0]["candidate_id"]

            # Second drain (replay): the still-outstanding handoff is
            # reclaimed, re-posted, and this time acknowledged.
            second = scheduler.run_intake_cycle(
                agora_url=agora_url,
                policy_learning_url="http://policy-learning-svc.test",
                tenant_id="tenant-a",
                agora_token="l12-agora-handoff-test-token",
                policy_learning_token="l12-imit-001-test-service-token",
            )
            assert second["status"] == "ok"
            assert second["acked_count"] == 1
            assert fake_bff.handoffs["h-replay-001"]["acknowledged"] is True

        assert fake_bff.ack_attempts == ["h-replay-001", "h-replay-001"]

        after_second = client.get("/api/policy-learning/candidates").json()
        assert len(after_second) == 1, "the replay must dedupe onto the same candidate, not duplicate it"
        assert after_second[0]["candidate_id"] == candidate_id
        assert after_second[0]["handoff_id"] == "h-replay-001"
        assert after_second[0]["dataset_ref"]["dataset_version_id"] == "dsv-replay-001"
