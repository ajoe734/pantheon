from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


class _ComposeLoader(yaml.SafeLoader):
    pass


def _construct_override(loader: yaml.SafeLoader, node: yaml.Node):
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node)
    return loader.construct_scalar(node)


_ComposeLoader.add_constructor("!override", _construct_override)


def test_control_compose_wires_runtime_manager_to_incident_services():
    compose = yaml.load(
        (ROOT / "docker-compose.control.yml").read_text(encoding="utf-8"),
        Loader=_ComposeLoader,
    )

    for service_name in ("incidents", "postmortems"):
        environment = compose["services"][service_name]["environment"]
        assert (
            environment["PANTHEON_RUNTIME_MANAGER_URL"]
            == "${PANTHEON_RUNTIME_MANAGER_URL:-}"
        )
        assert (
            environment["PANTHEON_RUNTIME_MANAGER_TOKEN"]
            == "${PANTHEON_RUNTIME_MANAGER_TOKEN:-}"
        )
