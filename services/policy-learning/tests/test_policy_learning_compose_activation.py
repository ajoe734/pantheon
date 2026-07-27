from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[3]


class _ComposeLoader(yaml.SafeLoader):
    """SafeLoader that tolerates compose's merge tags such as ``!override``."""


def _passthrough_tag(loader: yaml.Loader, suffix: str, node: yaml.Node) -> object:
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node, deep=True)
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node, deep=True)
    return loader.construct_scalar(node)


_ComposeLoader.add_multi_constructor("!", _passthrough_tag)


def test_compose_wires_policy_learning_service_without_production_activation() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]

    policy_learning = services["policy-learning-svc"]
    assert policy_learning["build"]["dockerfile"] == "services/policy-learning/Dockerfile"
    assert policy_learning["environment"]["PORT"] == "8100"
    assert policy_learning["environment"]["POLICY_LEARNING_DATA_DIR"] == "/data/policy-learning"
    assert policy_learning["environment"]["POLICY_LEARNING_ENABLE_PRODUCTION_ADAPTERS"] == "false"
    assert "policy-learning-data:/data/policy-learning" in policy_learning["volumes"]
    assert policy_learning["ports"] == ["${POLICY_LEARNING_PORT:-18100}:8100"]
    assert "healthcheck" in policy_learning

    smoke = services["smoke-stack"]
    assert smoke["environment"]["POLICY_LEARNING_URL"] == "http://policy-learning-svc:8100"
    assert smoke["depends_on"]["policy-learning-svc"]["condition"] == "service_healthy"
    assert "policy-learning-data" in compose["volumes"]


def test_compose_wires_the_imitation_loop_credential_and_tenant_scope() -> None:
    """The scheduler sidecar must be an authorized caller of the API (L12-IMIT-001).

    The imitation-loop routes are authenticated and tenant-bound, so a compose
    file that declares both services but gives them no shared credential and no
    tenant produces a scheduler that 401s on every tick.  The rendered values
    and the end-to-end loop are proven in
    ``test_l12_imit_001_default_compose_loop.py``; this keeps the *declaration*
    from regressing.
    """

    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    api = compose["services"]["policy-learning-svc"]["environment"]
    scheduler = compose["services"]["policy-learning-shadow-eval-scheduler"]["environment"]

    for key in (
        "POLICY_LEARNING_SERVICE_TOKEN",
        "POLICY_LEARNING_AGORA_TENANT_ID",
        "PANTHEON_PERSISTENCE_POSTURE",
    ):
        assert api[key] == scheduler[key], f"{key} must resolve identically for both services"
    assert "POLICY_LEARNING_SERVICE_TENANTS" in api
    assert "POLICY_LEARNING_AGORA_TENANT_ID" in api["POLICY_LEARNING_SERVICE_TENANTS"]


def test_staging_compose_keeps_the_published_dev_credential_fail_closed() -> None:
    """Staging inherits the base service, so it must not inherit its credential.

    ``docker-compose.staging-full.yml`` extends ``policy-learning-svc`` and
    pins a production persistence posture.  That posture is what makes
    ``inbound_authority`` reject the published compose token, so a staging
    stack has to supply its own ``POLICY_LEARNING_SERVICE_TOKEN`` rather than
    quietly running on a credential published in this repository.
    """

    staging = yaml.load(
        (ROOT / "docker-compose.staging-full.yml").read_text(encoding="utf-8"),
        Loader=_ComposeLoader,
    )
    service = staging["services"]["policy-learning-svc"]
    assert service["extends"]["service"] == "policy-learning-svc"
    assert service["environment"]["PANTHEON_PERSISTENCE_POSTURE"].endswith(":-production}")
    assert "POLICY_LEARNING_SERVICE_TOKEN" not in service["environment"]
