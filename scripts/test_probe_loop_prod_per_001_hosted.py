from scripts.probe_loop_prod_per_001_hosted import is_canonical_strategy_artifact_id


def test_strategy_artifact_id_accepts_persona_artifact_authority_metadata():
    record = {
        "metadata": {
            "strategy_artifact_id": "artifact-persona-paper-abc123",
            "strategy_spec_registry_id": "reg-strategy-spec-persona-abc123",
            "authoritative_loader_attestation": {
                "artifact_id": "artifact-persona-paper-abc123",
            },
        }
    }

    assert is_canonical_strategy_artifact_id("artifact-persona-paper-abc123", record)


def test_strategy_artifact_id_rejects_strategy_spec_registry_id():
    record = {
        "metadata": {
            "strategy_artifact_id": "artifact-persona-paper-abc123",
            "strategy_spec_registry_id": "reg-strategy-spec-persona-abc123",
        }
    }

    assert not is_canonical_strategy_artifact_id("reg-strategy-spec-persona-abc123", record)


def test_strategy_artifact_id_requires_authority_match_when_metadata_present():
    record = {
        "metadata": {
            "strategy_artifact_id": "artifact-persona-paper-abc123",
        }
    }

    assert not is_canonical_strategy_artifact_id("artifact-persona-paper-other", record)


def test_strategy_artifact_id_accepts_legacy_strategy_artifact_prefix_without_metadata():
    assert is_canonical_strategy_artifact_id("reg-strategy-artifact-abc123")
