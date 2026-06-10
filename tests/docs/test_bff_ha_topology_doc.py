from pathlib import Path


TOPOLOGY_DOC = Path("docs/bff/bff_ha_topology.md")


def _doc_text() -> str:
    return TOPOLOGY_DOC.read_text(encoding="utf-8")


def _mermaid_block(text: str) -> str:
    marker = "```mermaid\n"
    start = text.index(marker) + len(marker)
    end = text.index("```", start)
    return text[start:end]


def test_bff_ha_topology_mermaid_covers_required_nodes() -> None:
    text = _doc_text()
    diagram = _mermaid_block(text)

    required_nodes = [
        "Client / Lovable UI",
        "HTTPS Load Balancer",
        "BFF Replica A",
        "BFF Replica B",
        "BFF Replica C",
        "Auth / OIDC / JWKS",
        "Shared Idempotency + Audit Store",
        "SSE Event Source / Fanout",
        "Registry / Governance",
        "Runtime Manager",
        "Telemetry / Incident",
    ]

    for node in required_nodes:
        assert node in diagram

    for replica in ("BFF1", "BFF2", "BFF3"):
        assert f"LB --> {replica}" in diagram
        assert f"{replica} --> Store" in diagram
        assert f"{replica} --> SSE" in diagram
        assert f"{replica} --> RegistryGov" in diagram
        assert f"{replica} --> Runtime" in diagram
        assert f"{replica} --> Telemetry" in diagram


def test_bff_ha_topology_documents_fail_closed_cases() -> None:
    text = _doc_text()
    normalized_text = " ".join(text.split())

    required_fail_closed_terms = [
        "503 IDEMPOTENCY_UNAVAILABLE",
        "503 AUDIT_UNAVAILABLE",
        "503 REGISTRY_GOVERNANCE_UNAVAILABLE",
        "503 RUNTIME_MANAGER_UNAVAILABLE",
        "503 TELEMETRY_UNAVAILABLE",
        "No command dispatch.",
        "HA-PROD-001-V2",
    ]

    for term in required_fail_closed_terms:
        assert term in text

    assert (
        "must not invent state from local seed, fixture, or hidden localhost fallback"
        in normalized_text
    )
    assert (
        "No compose, deployment, L1 canonical policy, or production cutover change"
        in normalized_text
    )
