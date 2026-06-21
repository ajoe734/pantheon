"""E2E: persona requests OpenClaw through the adapter and consumes the response."""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path
from typing import Any
from unittest import mock

from services.persona.openclaw_adapter_backed_flow import (
    FLOW_SCHEMA_VERSION,
    OODA_ITERATION_SCHEMA_VERSION,
    OPENCLAW_ADAPTER_PROVIDER_PATH,
    OSS_COMPONENTS,
    assert_openclaw_adapter_backed_persona_case,
    build_openclaw_adapter_backed_specs,
    load_openclaw_ops_client_class,
    run_openclaw_adapter_backed_ooda_iteration_validations,
    run_openclaw_adapter_backed_persona_case,
    run_openclaw_adapter_backed_persona_flow_validations,
    stable_json_hash,
    validate_openclaw_adapter_backed_ooda_episode,
    validate_openclaw_adapter_backed_persona_case,
)


ROOT = Path(__file__).resolve().parents[2]
ADAPTER_DIR = ROOT / "services" / "openclaw-gateway-adapter"
_ADAPTER_MAIN_MODULE: Any | None = None


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


def _stub_foundation_health_for_adapter_import() -> None:
    if "services.foundation.health" in sys.modules:
        return
    try:
        import services.foundation.health  # noqa: F401

        return
    except ImportError:
        pass

    for pkg in ("services", "services.foundation"):
        if pkg not in sys.modules:
            sys.modules[pkg] = types.ModuleType(pkg)

    health_mod = types.ModuleType("services.foundation.health")

    def health_payload(
        service: str,
        *,
        live: bool = True,
        ready: bool = True,
        dependencies: Any = None,
        metrics: Any = None,
        details: Any = None,
    ) -> dict[str, Any]:
        dep = {}
        if dependencies:
            resolved = dependencies() if callable(dependencies) else dependencies
            for key, value in resolved.items():
                dep[key] = value() if callable(value) else value
        dependency_statuses = [
            str(value.get("status", "ok")).lower()
            for value in dep.values()
            if isinstance(value, dict)
        ]
        status = "ok" if live and ready else "error"
        if status == "ok" and any(
            value in {"degraded", "error", "unavailable", "failed"}
            for value in dependency_statuses
        ):
            status = "degraded"
        return {
            "status": status,
            "service": service,
            "live": live,
            "ready": ready,
            "dependencies": dep,
        }

    def readiness_status_code(payload: dict[str, Any]) -> int:
        return 200 if payload.get("status") == "ok" else 503

    def register_fastapi_health_routes(
        app: Any,
        service: str,
        *,
        dependencies: Any = None,
        metrics: Any = None,
        details: Any = None,
    ) -> None:
        from fastapi.responses import JSONResponse

        async def healthz():
            return health_payload(service, dependencies=dependencies, details=details)

        async def livez():
            return health_payload(service, ready=True)

        async def readyz():
            payload = health_payload(service, dependencies=dependencies, details=details)
            return JSONResponse(payload, status_code=readiness_status_code(payload))

        async def metrics_route():
            return {"service": service}

        app.add_api_route("/healthz", healthz, methods=["GET"])
        app.add_api_route("/livez", livez, methods=["GET"])
        app.add_api_route("/readyz", readyz, methods=["GET"])
        app.add_api_route("/metrics", metrics_route, methods=["GET"])

    health_mod.health_payload = health_payload
    health_mod.readiness_status_code = readiness_status_code
    health_mod.register_fastapi_health_routes = register_fastapi_health_routes
    sys.modules["services.foundation.health"] = health_mod
    sys.modules["services.foundation"] = sys.modules.get("services.foundation") or types.ModuleType(
        "services.foundation"
    )


def _load_adapter_main() -> Any:
    global _ADAPTER_MAIN_MODULE
    if _ADAPTER_MAIN_MODULE is not None:
        return _ADAPTER_MAIN_MODULE

    _stub_foundation_health_for_adapter_import()
    for path in (ROOT, ADAPTER_DIR):
        text = str(path)
        if text not in sys.path:
            sys.path.insert(0, text)

    module_name = "_persona_e2e_openclaw_gateway_adapter_main"
    spec = importlib.util.spec_from_file_location(module_name, ADAPTER_DIR / "main.py")
    if spec is None or spec.loader is None:
        raise ImportError("Cannot load openclaw gateway adapter main.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    _ADAPTER_MAIN_MODULE = module
    return module


def _fake_provider_invoke(provider_calls: list[dict[str, Any]]):
    def fake_invoke(
        _self: Any,
        prompt: str,
        *,
        mode: str = "user",
        context_pack: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        messages: list[dict[str, Any]] | None = None,
        operator_id: str | None = None,
        trace_id: str | None = None,
    ) -> Any:
        md = dict(metadata or {})
        case_id = str(md.get("case_id") or f"case-{len(provider_calls) + 1:03d}")
        request_hash = str(md.get("adapter_request_hash") or stable_json_hash(md))
        provider_request_id = f"oc-provider-{len(provider_calls) + 1:03d}-{request_hash[:10]}"
        provider_calls.append(
            {
                "prompt": prompt,
                "mode": mode,
                "context_pack": dict(context_pack or {}),
                "metadata": md,
                "messages": list(messages or []),
                "operator_id": operator_id,
                "trace_id": trace_id,
                "provider_request_id": provider_request_id,
                "adapter_request_hash": request_hash,
            }
        )
        output = {
            "json_events": [
                {
                    "type": "item.completed",
                    "item": {
                        "id": f"item_{provider_request_id}",
                        "type": "agent_message",
                        "text": (
                            "OpenClaw provider accepted "
                            f"{case_id} for {md.get('triggering_component')}."
                        ),
                    },
                }
            ],
            "agent_id": "main",
            "request_id": provider_request_id,
            "duration_ms": 25 + len(provider_calls),
            "transport": "cli",
            "adapter_invocation": {
                "path": OPENCLAW_ADAPTER_PROVIDER_PATH,
                "operator_id": operator_id,
                "trace_id": trace_id,
                "request_hash": request_hash,
            },
        }

        class FakeOpenClawProviderResult:
            provider = "openclaw"
            status = "completed"
            redaction = {"provider_invocation": {"redacted_fields": 0}}

            def __init__(self, result_mode: str, result_output: dict[str, Any]) -> None:
                self.mode = result_mode
                self.output = result_output

            def to_dict(self) -> dict[str, Any]:
                return {
                    "provider": self.provider,
                    "mode": self.mode,
                    "status": self.status,
                    "output": self.output,
                    "redaction": self.redaction,
                }

        return FakeOpenClawProviderResult(mode, output)

    return fake_invoke


def _adapter_route_backed_openclaw_transport(calls: list[dict[str, Any]]):
    adapter_main = _load_adapter_main()
    from fastapi.testclient import TestClient

    adapter_client = TestClient(adapter_main.app)

    def fake_urlopen(request: Any, timeout: float) -> FakeHttpResponse:
        body = json.loads(request.data.decode("utf-8"))
        headers = _headers(request)
        request_hash = stable_json_hash(body)
        response = adapter_client.post(
            OPENCLAW_ADAPTER_PROVIDER_PATH,
            json=body,
            headers={
                "X-Operator-Id": headers["x-operator-id"],
                "X-Trace-Id": headers["x-trace-id"],
            },
        )
        payload = response.json()
        output = payload["data"]["output"]
        calls.append(
            {
                "url": request.full_url,
                "headers": headers,
                "body": body,
                "timeout": timeout,
                "request_hash": request_hash,
                "adapter_route_status_code": response.status_code,
                "adapter_route_executed": True,
                "provider_request_id": output["request_id"],
            }
        )
        return FakeHttpResponse(payload, status_code=response.status_code)

    return fake_urlopen


def _client_factory() -> Any:
    client_class = load_openclaw_ops_client_class()
    return client_class(base_url="http://adapter:8104", timeout_seconds=5.0)


def test_100_adapter_backed_persona_openclaw_cases_are_distinct_and_usable() -> None:
    specs = build_openclaw_adapter_backed_specs(case_count=100)
    calls: list[dict[str, Any]] = []
    provider_calls: list[dict[str, Any]] = []
    load_openclaw_ops_client_class()
    adapter_main = _load_adapter_main()

    with mock.patch.object(
        adapter_main._OPENCLAW_AGENT_PROVIDER.__class__,
        "invoke",
        _fake_provider_invoke(provider_calls),
    ):
        with mock.patch(
            "openclaw_ops_client.urllib.request.urlopen",
            _adapter_route_backed_openclaw_transport(calls),
        ):
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
    assert len(provider_calls) == 100

    assert len({spec.assertion_label for spec in specs}) == 100
    assert len({call["body"]["prompt"] for call in calls}) == 100
    assert len({call["request_hash"] for call in calls}) == 100
    assert all(call["url"] == f"http://adapter:8104{OPENCLAW_ADAPTER_PROVIDER_PATH}" for call in calls)
    assert all(call["headers"]["x-operator-id"].startswith("operator-") for call in calls)
    assert all(call["headers"]["x-trace-id"].startswith("trace-persona-openclaw-adapter-") for call in calls)
    assert all(call["adapter_route_status_code"] == 200 for call in calls)
    assert all(call["adapter_route_executed"] is True for call in calls)
    assert all(item["metadata"]["operator_id"] == item["operator_id"] for item in provider_calls)
    assert all(item["metadata"]["trace_id"] == item["trace_id"] for item in provider_calls)

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
        assert provider_calls[index]["provider_request_id"] == call["provider_request_id"]
        assert provider_calls[index]["metadata"]["adapter_request_hash"] == call["body"]["metadata"]["adapter_request_hash"]
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


def test_100_adapter_backed_persona_openclaw_ooda_cycles_iterate_response_to_next_request() -> None:
    calls: list[dict[str, Any]] = []
    provider_calls: list[dict[str, Any]] = []
    episode_id = "persona-openclaw-ooda-iteration-e2e"
    load_openclaw_ops_client_class()
    adapter_main = _load_adapter_main()

    with mock.patch.object(
        adapter_main._OPENCLAW_AGENT_PROVIDER.__class__,
        "invoke",
        _fake_provider_invoke(provider_calls),
    ):
        with mock.patch(
            "openclaw_ops_client.urllib.request.urlopen",
            _adapter_route_backed_openclaw_transport(calls),
        ):
            run = run_openclaw_adapter_backed_ooda_iteration_validations(
                cycle_count=100,
                episode_id=episode_id,
                client_factory=lambda _spec: _client_factory(),
            )

    summary = run["summary"]
    assert summary["schema_version"] == OODA_ITERATION_SCHEMA_VERSION
    assert summary["episode_id"] == episode_id
    assert summary["cycle_count"] == 100
    assert summary["passed_case_count"] == 100
    assert summary["episode_passed"] is True
    assert summary["adapter_invocation_count"] == 100
    assert summary["provider_response_count"] == 100
    assert summary["iteration_link_count"] == 99
    assert summary["unique_request_hash_count"] == 100
    assert summary["all_oss_components_covered"] is True
    assert set(OSS_COMPONENTS).issubset(set(summary["covered_related_components"]))
    assert len(calls) == 100
    assert len(provider_calls) == 100
    assert all(call["adapter_route_executed"] is True for call in calls)
    assert all(call["adapter_route_status_code"] == 200 for call in calls)

    cycles = run["cycles"]
    required_ooda_stages = {"observe", "orient", "decide", "act"}
    for index, cycle in enumerate(cycles):
        assert_openclaw_adapter_backed_persona_case(cycle)
        assert required_ooda_stages.issubset(set(cycle["decision_trace"]["ooda"]))
        assert cycle["ooda_iteration"]["episode_id"] == episode_id
        assert cycle["ooda_iteration"]["cycle_no"] == index + 1
        assert calls[index]["body"]["context_pack"]["ooda_iteration"] == cycle["ooda_iteration"]
        assert calls[index]["body"]["metadata"]["ooda_episode_id"] == episode_id
        assert calls[index]["body"]["metadata"]["ooda_cycle_no"] == index + 1
        assert provider_calls[index]["metadata"]["ooda_episode_id"] == episode_id
        assert provider_calls[index]["metadata"]["ooda_cycle_no"] == index + 1

        if index == 0:
            assert cycle["ooda_iteration"]["request_consumes_previous_cycle"] is False
            assert cycle["decision_trace"]["ooda"]["observe"]["feedback_from_previous_cycle"]["consumed"] is False
            continue

        previous = cycles[index - 1]
        previous_provider_ref = previous["provider_response"]["ref"]
        previous_decision_ref = previous["decision_trace"]["trace_ref"]
        previous_act = previous["decision_trace"]["ooda"]["act"]
        previous_action_ref = previous_act["handoff_ref"]
        expected_carryover_refs = {
            previous_provider_ref,
            previous_decision_ref,
            previous_action_ref,
        }
        iteration = cycle["ooda_iteration"]
        assert iteration["request_consumes_previous_cycle"] is True
        assert iteration["previous_case_id"] == previous["case_id"]
        assert iteration["previous_provider_response_ref"] == previous_provider_ref
        assert iteration["previous_decision_trace_ref"] == previous_decision_ref
        assert iteration["previous_action_ref"] == previous_action_ref
        assert iteration["previous_selected_action"] == previous_act["next_action"]
        assert set(iteration["carryover_refs"]) == expected_carryover_refs

        assert previous_provider_ref in calls[index]["body"]["prompt"]
        assert previous_provider_ref in calls[index]["body"]["context_pack"]["source_refs"]
        assert calls[index]["body"]["metadata"]["previous_provider_response_ref"] == previous_provider_ref
        assert calls[index]["body"]["metadata"]["previous_action_ref"] == previous_action_ref
        assert provider_calls[index]["metadata"]["previous_provider_response_ref"] == previous_provider_ref
        assert provider_calls[index]["metadata"]["previous_action_ref"] == previous_action_ref

        assert previous_provider_ref in cycle["persona_reasoning"]["input_refs"]
        assert previous_provider_ref in cycle["candidate_generation"]["input_refs"]
        assert previous_provider_ref in cycle["decision_trace"]["selected_candidate"]["evidence_refs"]
        assert previous_provider_ref in cycle["decision_trace"]["evidence_refs"]
        assert previous_provider_ref in cycle["decision_trace"]["decision_inputs"]["previous_cycle_refs"]
        assert cycle["decision_trace"]["ooda"]["observe"]["feedback_from_previous_cycle"]["consumed"] is True
        assert previous_provider_ref in cycle["decision_trace"]["ooda"]["observe"]["feedback_from_previous_cycle"]["input_refs"]
        assert previous_provider_ref in cycle["decision_trace"]["ooda"]["orient"]["input_refs"]
        assert previous_provider_ref in cycle["decision_trace"]["ooda"]["decide"]["evidence_refs"]
        assert previous_provider_ref in cycle["decision_trace"]["ooda"]["act"]["evidence_refs"]
        assert calls[index]["request_hash"] != calls[index - 1]["request_hash"]

    assert run["episode_validation"]["passed"] is True


def test_ooda_episode_fails_when_next_cycle_does_not_consume_previous_response() -> None:
    calls: list[dict[str, Any]] = []
    provider_calls: list[dict[str, Any]] = []
    load_openclaw_ops_client_class()
    adapter_main = _load_adapter_main()

    with mock.patch.object(
        adapter_main._OPENCLAW_AGENT_PROVIDER.__class__,
        "invoke",
        _fake_provider_invoke(provider_calls),
    ):
        with mock.patch(
            "openclaw_ops_client.urllib.request.urlopen",
            _adapter_route_backed_openclaw_transport(calls),
        ):
            run = run_openclaw_adapter_backed_ooda_iteration_validations(
                cycle_count=3,
                episode_id="persona-openclaw-ooda-broken-link",
                client_factory=lambda _spec: _client_factory(),
            )

    assert run["episode_validation"]["passed"] is True
    assert len(calls) == 3
    assert len(provider_calls) == 3

    broken = json.loads(
        json.dumps(
            {
                "schema_version": run["schema_version"],
                "episode_id": run["episode_id"],
                "cycles": run["cycles"],
            }
        )
    )
    previous_provider_ref = broken["cycles"][0]["provider_response"]["ref"]
    next_cycle = broken["cycles"][1]

    def remove_ref(values: list[str]) -> list[str]:
        return [value for value in values if value != previous_provider_ref]

    next_cycle["persona_request"]["context_pack"]["source_refs"] = remove_ref(
        next_cycle["persona_request"]["context_pack"]["source_refs"]
    )
    next_cycle["adapter_exchange"]["body"]["context_pack"]["source_refs"] = remove_ref(
        next_cycle["adapter_exchange"]["body"]["context_pack"]["source_refs"]
    )
    next_cycle["persona_reasoning"]["input_refs"] = remove_ref(next_cycle["persona_reasoning"]["input_refs"])
    next_cycle["candidate_generation"]["input_refs"] = remove_ref(
        next_cycle["candidate_generation"]["input_refs"]
    )
    next_cycle["decision_trace"]["selected_candidate"]["evidence_refs"] = remove_ref(
        next_cycle["decision_trace"]["selected_candidate"]["evidence_refs"]
    )
    next_cycle["decision_trace"]["evidence_refs"] = remove_ref(next_cycle["decision_trace"]["evidence_refs"])
    next_cycle["decision_trace"]["decision_inputs"]["previous_cycle_refs"] = remove_ref(
        next_cycle["decision_trace"]["decision_inputs"]["previous_cycle_refs"]
    )
    next_cycle["decision_trace"]["ooda"]["observe"]["feedback_from_previous_cycle"]["input_refs"] = remove_ref(
        next_cycle["decision_trace"]["ooda"]["observe"]["feedback_from_previous_cycle"]["input_refs"]
    )
    next_cycle["decision_trace"]["ooda"]["orient"]["input_refs"] = remove_ref(
        next_cycle["decision_trace"]["ooda"]["orient"]["input_refs"]
    )
    next_cycle["decision_trace"]["ooda"]["decide"]["evidence_refs"] = remove_ref(
        next_cycle["decision_trace"]["ooda"]["decide"]["evidence_refs"]
    )
    next_cycle["decision_trace"]["ooda"]["act"]["evidence_refs"] = remove_ref(
        next_cycle["decision_trace"]["ooda"]["act"]["evidence_refs"]
    )

    validation = validate_openclaw_adapter_backed_ooda_episode(broken)

    assert validation["passed"] is False
    assert any("next OODA cycle must consume previous response" in error for error in validation["errors"])


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
    provider_calls: list[dict[str, Any]] = []
    spec = build_openclaw_adapter_backed_specs(case_count=1)[0]
    load_openclaw_ops_client_class()
    adapter_main = _load_adapter_main()

    with mock.patch.object(
        adapter_main._OPENCLAW_AGENT_PROVIDER.__class__,
        "invoke",
        _fake_provider_invoke(provider_calls),
    ):
        with mock.patch(
            "openclaw_ops_client.urllib.request.urlopen",
            _adapter_route_backed_openclaw_transport(calls),
        ):
            case = run_openclaw_adapter_backed_persona_case(spec, client=_client_factory())

    assert_openclaw_adapter_backed_persona_case(case)
    assert len(calls) == 1
    assert len(provider_calls) == 1
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
