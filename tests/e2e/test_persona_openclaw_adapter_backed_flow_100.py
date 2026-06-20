"""E2E: persona requests OpenClaw through the adapter and consumes the response."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest import mock

from services.persona.openclaw_adapter_backed_flow import (
    FLOW_SCHEMA_VERSION,
    OPENCLAW_ADAPTER_PROVIDER_PATH,
    OSS_COMPONENTS,
    assert_openclaw_adapter_backed_persona_case,
    build_openclaw_adapter_backed_specs,
    load_openclaw_ops_client_class,
    run_openclaw_adapter_backed_persona_case,
    run_openclaw_adapter_backed_persona_flow_validations,
    stable_json_hash,
    validate_openclaw_adapter_backed_persona_case,
)


ROOT = Path(__file__).resolve().parents[2]


class FakeHttpResponse:
    def __init__(self, payload: dict[str, Any], *, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code

    def __enter__(self) -> "FakeHttpResponse":
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def getcode(self) -> int:
        return self.status_code

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def _headers(request: Any) -> dict[str, str]:
    return {key.lower(): value for key, value in dict(request.header_items()).items()}


def _fake_openclaw_adapter(calls: list[dict[str, Any]]):
    def fake_urlopen(request: Any, timeout: float) -> FakeHttpResponse:
        body = json.loads(request.data.decode("utf-8"))
        headers = _headers(request)
        call_no = len(calls) + 1
        request_hash = stable_json_hash(body)
        provider_request_id = f"oc-provider-{call_no:03d}-{request_hash[:10]}"
        calls.append(
            {
                "url": request.full_url,
                "headers": headers,
                "body": body,
                "timeout": timeout,
                "request_hash": request_hash,
                "provider_request_id": provider_request_id,
            }
        )
        return FakeHttpResponse(
            {
                "status": "ok",
                "data": {
                    "provider": "openclaw",
                    "mode": body["mode"],
                    "status": "completed",
                    "output": {
                        "json_events": [
                            {
                                "type": "item.completed",
                                "item": {
                                    "id": f"item_{provider_request_id}",
                                    "type": "agent_message",
                                    "text": (
                                        "OpenClaw provider accepted "
                                        f"{body['metadata']['case_id']} for "
                                        f"{body['metadata']['triggering_component']}."
                                    ),
                                },
                            }
                        ],
                        "agent_id": "main",
                        "request_id": provider_request_id,
                        "duration_ms": 25 + call_no,
                        "transport": "cli",
                        "adapter_invocation": {
                            "path": OPENCLAW_ADAPTER_PROVIDER_PATH,
                            "operator_id": headers.get("x-operator-id"),
                            "trace_id": headers.get("x-trace-id"),
                            "request_hash": request_hash,
                        },
                    },
                    "redaction": {"provider_invocation": {"redacted_fields": 0}},
                },
            }
        )

    return fake_urlopen


def _client_factory() -> Any:
    client_class = load_openclaw_ops_client_class()
    return client_class(base_url="http://adapter:8104", timeout_seconds=5.0)


def test_100_adapter_backed_persona_openclaw_cases_are_distinct_and_usable() -> None:
    specs = build_openclaw_adapter_backed_specs(case_count=100)
    calls: list[dict[str, Any]] = []
    load_openclaw_ops_client_class()

    with mock.patch("openclaw_ops_client.urllib.request.urlopen", _fake_openclaw_adapter(calls)):
        run = run_openclaw_adapter_backed_persona_flow_validations(
            case_count=100,
            client_factory=lambda _spec: _client_factory(),
        )

    summary = run["summary"]
    assert summary["schema_version"] == FLOW_SCHEMA_VERSION
    assert summary["case_count"] == 100
    assert summary["passed_count"] == 100
    assert summary["adapter_invocation_count"] == 100
    assert summary["provider_response_count"] == 100
    assert summary["unique_assertion_label_count"] == 100
    assert summary["unique_request_hash_count"] == 100
    assert summary["all_oss_components_covered"] is True
    assert set(OSS_COMPONENTS).issubset(set(summary["covered_related_components"]))
    assert len(calls) == 100

    assert len({spec.assertion_label for spec in specs}) == 100
    assert len({call["body"]["prompt"] for call in calls}) == 100
    assert len({call["request_hash"] for call in calls}) == 100
    assert all(call["url"] == f"http://adapter:8104{OPENCLAW_ADAPTER_PROVIDER_PATH}" for call in calls)
    assert all(call["headers"]["x-operator-id"].startswith("operator-") for call in calls)
    assert all(call["headers"]["x-trace-id"].startswith("trace-persona-openclaw-adapter-") for call in calls)

    cases = run["cases"]
    assert cases[0]["depth"]["depth_id"] == "shallow_provider_ping"
    assert cases[-1]["depth"]["depth_id"] == "lean_ready_decision_packet"
    assert {case["scenario"]["scenario_id"] for case in cases} == {
        spec.scenario.scenario_id for spec in specs
    }

    for index, case in enumerate(cases):
        assert_openclaw_adapter_backed_persona_case(case)
        call = calls[index]
        provider_ref = case["provider_response"]["ref"]
        assert case["adapter_exchange"]["request_hash"] == call["request_hash"]
        assert case["adapter_exchange"]["body"] == call["body"]
        assert case["adapter_exchange"]["client_class"] == "OpenClawOpsClient"
        assert case["provider_response"]["output_request_id"] == call["provider_request_id"]
        assert provider_ref in case["candidate_generation"]["input_refs"]
        assert provider_ref == case["scorer"]["scoring_inputs"]["openclaw_provider_response_ref"]
        assert provider_ref in case["decision_trace"]["selected_candidate"]["evidence_refs"]
        assert provider_ref in case["decision_trace"]["evidence_refs"]
        assert case["decision_trace"]["ooda"]["observe"]["response_ref"] == provider_ref
        assert case["validation_plan"]["requires_real_adapter_invocation"] is True
        assert len(case["validation_plan"]["preflight_questions"]) == 3

        seed = case["alpha_seed"]
        evidence = ROOT / seed["evidence_path"]
        assert evidence.exists(), seed["evidence_path"]
        text = evidence.read_text(encoding="utf-8")
        for anchor in seed["anchors"]:
            assert anchor in text


def test_hardcoded_openclaw_artifact_without_adapter_invocation_fails() -> None:
    fake_case = {
        "case_id": "hardcoded-openclaw-artifact",
        "assertion_label": "hardcoded:openclaw:artifact",
        "alpha_seed": {"seed_ref": "alpha-seed://qlib_tw_cross_sectional"},
        "persona_request": {
            "request_ref": "persona-request://hardcoded",
            "adapter_request_hash": "hardcoded",
            "openclaw_provider_response_ref": "openclaw-provider-response://hardcoded",
        },
        "adapter_exchange": {
            "invoked": False,
            "client_class": "HardcodedFixture",
            "path": OPENCLAW_ADAPTER_PROVIDER_PATH,
            "body": {},
            "request_hash": "hardcoded",
        },
        "provider_response": {
            "ref": "openclaw-provider-response://hardcoded",
            "provider": "openclaw",
            "status": "completed",
            "output_request_id": "hardcoded",
            "response_text_hash": "hardcoded",
        },
        "triggering_oss_response": {"response_ref": "oss://openclaw/hardcoded"},
        "candidate_generation": {"input_refs": ["openclaw-provider-response://hardcoded"], "candidates": []},
        "scorer": {"scoring_inputs": {}},
        "decision_trace": {"adapter_backed": False, "selected_candidate": {}, "evidence_refs": []},
    }

    validation = validate_openclaw_adapter_backed_persona_case(fake_case)

    assert validation["passed"] is False
    assert any("adapter_exchange.invoked" in error for error in validation["errors"])
    assert any("OpenClawOpsClient" in error for error in validation["errors"])
    assert any("provider_invocation_proof" in error for error in validation["errors"])


def test_downstream_trace_must_consume_the_actual_provider_response_ref() -> None:
    calls: list[dict[str, Any]] = []
    spec = build_openclaw_adapter_backed_specs(case_count=1)[0]
    load_openclaw_ops_client_class()

    with mock.patch("openclaw_ops_client.urllib.request.urlopen", _fake_openclaw_adapter(calls)):
        case = run_openclaw_adapter_backed_persona_case(spec, client=_client_factory())

    assert_openclaw_adapter_backed_persona_case(case)
    provider_ref = case["provider_response"]["ref"]
    broken = json.loads(json.dumps(case))
    broken["candidate_generation"]["input_refs"] = [
        ref for ref in broken["candidate_generation"]["input_refs"] if ref != provider_ref
    ]
    broken["decision_trace"]["selected_candidate"]["evidence_refs"] = [
        ref
        for ref in broken["decision_trace"]["selected_candidate"]["evidence_refs"]
        if ref != provider_ref
    ]

    validation = validate_openclaw_adapter_backed_persona_case(broken)

    assert validation["passed"] is False
    assert any("candidate_generation.input_refs" in error for error in validation["errors"])
    assert any("selected candidate" in error for error in validation["errors"])
