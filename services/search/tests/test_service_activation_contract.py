from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[3]


def test_compose_wires_search_service_and_bff_normal_path() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]

    search = services["search-svc"]
    assert search["build"]["dockerfile"] == "services/search/Dockerfile"
    assert search["environment"]["PORT"] == "8098"
    assert search["environment"]["SEARCH_DATA_DIR"] == "/data/search"
    assert search["environment"]["SEARCH_INDEX_STORE_PATH"] == "/data/search/search-index.jsonl"
    assert search["environment"]["SEARCH_EVIDENCE_STORE_PATH"] == "/data/source-ingest/source_evidence.jsonl"
    assert "search-data:/data/search" in search["volumes"]
    assert "source-ingest-data:/data/source-ingest:ro" in search["volumes"]
    assert search["ports"] == ["${SEARCH_PORT:-18098}:8098"]
    assert "healthcheck" in search

    bff = services["operator-bff"]
    assert bff["environment"]["PANTHEON_SEARCH_API_URL"] == "http://search-svc:8098"
    assert bff["depends_on"]["search-svc"]["condition"] == "service_healthy"


def test_honest_stack_smoke_waits_for_and_queries_search_service() -> None:
    smoke = (ROOT / "scripts/smoke_honest_stack.py").read_text(encoding="utf-8")

    assert 'SEARCH_URL = os.getenv("SEARCH_URL", "http://127.0.0.1:8098")' in smoke
    assert '_wait_for_health("search-svc", f"{SEARCH_URL}/readyz")' in smoke
    assert 'f"{SEARCH_URL}/api/search/index/reload"' in smoke
    assert 'f"{SEARCH_URL}/api/search/query"' in smoke
    assert 'f"{SEARCH_URL}/api/search/snapshots/{search_body[\'request_id\']}"' in smoke
    assert '"query": f"momentum volatility {source_search_token}"' in smoke
    assert '"documents": [' not in smoke


def test_search_dockerfile_exposes_service_port_and_uses_service_requirements() -> None:
    dockerfile = (ROOT / "services/search/Dockerfile").read_text(encoding="utf-8")
    requirements = (ROOT / "services/search/requirements.txt").read_text(encoding="utf-8").splitlines()

    assert "COPY services/search/requirements.txt /tmp/requirements.txt" in dockerfile
    assert "ENV PORT=8098" in dockerfile
    assert "EXPOSE 8098" in dockerfile
    assert "uvicorn services.search.main:app" in dockerfile
    assert {"fastapi", "uvicorn", "pydantic"}.issubset(set(requirements))
