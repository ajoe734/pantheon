from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[3]


def test_compose_wires_training_session_service_and_bff_normal_path() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]

    training = services["training-session-svc"]
    assert training["build"]["dockerfile"] == "services/training-session/Dockerfile"
    assert training["environment"]["PORT"] == "8099"
    assert training["environment"]["TRAINING_SESSION_DATA_DIR"] == "/data/training-session"
    assert training["environment"]["PANTHEON_VECTORBT_BACKEND"] == "${PANTHEON_VECTORBT_BACKEND:-real}"
    assert training["environment"]["TRAINING_SESSION_RUNTIME_EVIDENCE_PATH"] == (
        "${TRAINING_SESSION_RUNTIME_EVIDENCE_PATH:-/data/training-session/runtime-evidence.jsonl}"
    )
    assert training["environment"]["SOURCE_INGEST_API_URL"] == (
        "${SOURCE_INGEST_API_URL:-http://source-ingest:8097}"
    )
    assert training["environment"]["TRAINING_SESSION_SOURCE_CONNECTOR_ID"] == (
        "${TRAINING_SESSION_SOURCE_CONNECTOR_ID:-crypto-coingecko-spot}"
    )
    assert training["environment"]["TRAINING_SESSION_SOURCE_DATASET_ID"] == (
        "${TRAINING_SESSION_SOURCE_DATASET_ID:-ds-coingecko-crypto-spot}"
    )
    assert "training-session-data:/data/training-session" in training["volumes"]
    assert "source-ingest-data:/data/source-ingest:ro" in training["volumes"]
    assert training["ports"] == ["${TRAINING_SESSION_PORT:-18099}:8099"]
    assert training["depends_on"]["source-ingest"]["condition"] == "service_healthy"
    assert "healthcheck" in training

    worker = services["training-session-preview-worker"]
    assert "profiles" not in worker
    assert worker["build"]["dockerfile"] == "services/training-session/Dockerfile"
    assert worker["command"] == ["python", "services/training-session/preview_eval_worker.py"]
    assert worker["restart"] == "unless-stopped"
    assert worker["environment"]["TRAINING_SESSION_API_URL"] == "http://training-session-svc:8099"
    assert worker["environment"]["TRAINING_SESSION_PREVIEW_WORKER_TIMEOUT_SECONDS"] == (
        "${TRAINING_SESSION_PREVIEW_WORKER_TIMEOUT_SECONDS:-30}"
    )
    assert worker["environment"]["TRAINING_SESSION_PREVIEW_WORKER_ALIVE_PATH"] == (
        "${TRAINING_SESSION_PREVIEW_WORKER_ALIVE_PATH:-/data/training-session/preview-worker-alive}"
    )
    assert "training-session-data:/data/training-session" in worker["volumes"]
    assert worker["depends_on"]["training-session-svc"]["condition"] == "service_healthy"
    assert "getmtime" in " ".join(worker["healthcheck"]["test"])

    bff = services["operator-bff"]
    assert bff["environment"]["PANTHEON_TRAINING_SESSION_API_URL"] == "http://training-session-svc:8099"
    assert bff["depends_on"]["training-session-svc"]["condition"] == "service_healthy"

    smoke = services["smoke-stack"]
    assert smoke["environment"]["TRAINING_SESSION_URL"] == "http://training-session-svc:8099"
    assert smoke["depends_on"]["training-session-svc"]["condition"] == "service_healthy"
    assert "training-session-data" in compose["volumes"]


def test_training_session_image_installs_pinned_vectorbt_requirements() -> None:
    dockerfile = (ROOT / "services" / "training-session" / "Dockerfile").read_text(encoding="utf-8")
    vectorbt_requirements = (
        ROOT / "services" / "research" / "vectorbt" / "requirements.txt"
    ).read_text(encoding="utf-8")

    assert "COPY services/research/vectorbt/requirements.txt /tmp/vectorbt-requirements.txt" in dockerfile
    assert "-r /tmp/vectorbt-requirements.txt" in dockerfile
    assert "vectorbt==0.26.2" in vectorbt_requirements.splitlines()
