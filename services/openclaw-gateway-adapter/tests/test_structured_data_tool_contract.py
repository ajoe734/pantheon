"""Contract tests for the restricted `emit_extraction` structured-data tool.

SIMPLIFY-OPENCLAW-001 part 2: a minimal, server-approved, pure data-emission
function tool riding the same unified HTTP `/v1/responses` transport. The
caller supplies only a JSON-schema `parameters` body; the tool name/type/
description/strict flag are fixed so a caller cannot smuggle in an arbitrary
shell/tool definition, and the tool call never triggers a domain mutation.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ADAPTER_DIR = Path(__file__).resolve().parents[1]
if str(ADAPTER_DIR) not in sys.path:
    sys.path.insert(0, str(ADAPTER_DIR))

from assistant_openclaw_provider import (  # noqa: E402
    EMIT_EXTRACTION_TOOL_NAME,
    AssistantOpenClawProvider,
    OpenClawProviderError,
    _validate_extraction_arguments,
    emit_extraction_tool_schema,
)


def _sse_bytes(events: list) -> list:
    lines = [("data: " + json.dumps(evt) + "\n").encode("utf-8") for evt in events]
    lines.append(b"data: [DONE]\n")
    return lines


class _FakeSSEResponse:
    def __init__(self, events: list) -> None:
        self._lines = _sse_bytes(events)

    def __iter__(self):
        return iter(self._lines)

    def close(self) -> None:
        pass


def _forbidden_run(*_args, **_kwargs):
    raise AssertionError("must not spawn subprocess for a structured extraction turn")


def _make_provider() -> AssistantOpenClawProvider:
    return AssistantOpenClawProvider(
        gateway_url="ws://openclaw-gateway:18789",
        agent_id="main",
        token="test-token",
        _which_func=lambda _: None,
        _run_func=_forbidden_run,
    )


def _tool_call_events(name: str, arguments: str, call_id: str = "call_1") -> list:
    return [
        {
            "type": "response.completed",
            "response": {
                "status": "completed",
                "id": "resp_1",
                "output": [
                    {"type": "function_call", "name": name, "arguments": arguments, "call_id": call_id}
                ],
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
        }
    ]


EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "count": {"type": "integer"},
    },
    "required": ["title"],
}


class TestEmitExtractionToolSchema:
    def test_schema_is_fixed_shape_regardless_of_caller_input(self):
        schema = emit_extraction_tool_schema(EXTRACTION_SCHEMA)
        assert schema["type"] == "function"
        assert schema["name"] == EMIT_EXTRACTION_TOOL_NAME
        assert schema["strict"] is True
        assert schema["parameters"] == EXTRACTION_SCHEMA
        assert "no domain action is executed" in schema["description"].lower()

    def test_schema_only_exposes_parameters_not_arbitrary_fields(self):
        # Even if a caller-supplied schema dict smuggles top-level keys, the
        # emitted tool definition only ever nests them under "parameters".
        sneaky = {"type": "object", "properties": {}, "command": "rm -rf /"}
        schema = emit_extraction_tool_schema(sneaky)
        assert set(schema.keys()) == {"type", "name", "description", "parameters", "strict"}
        assert schema["parameters"] == sneaky


class TestInvokeStructuredPositive:
    def test_valid_tool_call_returns_parsed_structured_data(self):
        provider = _make_provider()
        events = _tool_call_events(
            EMIT_EXTRACTION_TOOL_NAME, json.dumps({"title": "Widget", "count": 3})
        )
        with patch("urllib.request.urlopen", return_value=_FakeSSEResponse(events)):
            result = provider.invoke_structured(
                "extract the widget",
                extraction_schema=EXTRACTION_SCHEMA,
                operator_id="op-1",
            )
        assert result.status == "completed"
        assert result.output["structured_data"] == {"title": "Widget", "count": 3}
        assert result.output["tool_call"]["name"] == EMIT_EXTRACTION_TOOL_NAME
        assert result.output["tool_call"]["id"] == "call_1"
        assert result.output["usage"] == {"input_tokens": 10, "output_tokens": 5}
        assert result.output["response_id"] == "resp_1"

    def test_pinned_tool_choice_and_tools_sent_on_the_wire(self):
        provider = _make_provider()
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["body"] = json.loads(req.data.decode("utf-8"))
            return _FakeSSEResponse(
                _tool_call_events(EMIT_EXTRACTION_TOOL_NAME, json.dumps({"title": "x"}))
            )

        with patch("urllib.request.urlopen", fake_urlopen):
            provider.invoke_structured(
                "extract", extraction_schema=EXTRACTION_SCHEMA, operator_id="op-1"
            )
        body = captured["body"]
        assert body["tool_choice"] == {"type": "function", "name": EMIT_EXTRACTION_TOOL_NAME}
        assert len(body["tools"]) == 1
        assert body["tools"][0]["name"] == EMIT_EXTRACTION_TOOL_NAME
        assert body["tools"][0]["parameters"] == EXTRACTION_SCHEMA

    def test_missing_usage_is_not_reported_as_zero(self):
        provider = _make_provider()
        events = [
            {
                "type": "response.completed",
                "response": {
                    "status": "completed",
                    "output": [
                        {
                            "type": "function_call",
                            "name": EMIT_EXTRACTION_TOOL_NAME,
                            "arguments": json.dumps({"title": "no usage reported"}),
                            "call_id": "call_2",
                        }
                    ],
                    # deliberately no "usage" key
                },
            }
        ]
        with patch("urllib.request.urlopen", return_value=_FakeSSEResponse(events)):
            result = provider.invoke_structured(
                "extract", extraction_schema=EXTRACTION_SCHEMA, operator_id="op-1"
            )
        assert "usage" not in result.output


class TestInvokeStructuredNegative:
    def test_no_matching_tool_call_in_response(self):
        provider = _make_provider()
        events = [
            {
                "type": "response.output_text.done",
                "text": "I decided to answer in plain text instead.",
            },
            {"type": "response.completed", "response": {"status": "completed"}},
        ]
        with patch("urllib.request.urlopen", return_value=_FakeSSEResponse(events)):
            with pytest.raises(OpenClawProviderError) as excinfo:
                provider.invoke_structured(
                    "extract", extraction_schema=EXTRACTION_SCHEMA, operator_id="op-1"
                )
        assert excinfo.value.error_code == "OPENCLAW_TOOL_NO_MATCH"
        assert excinfo.value.status_code == 502

    def test_wrong_tool_name_is_rejected(self):
        provider = _make_provider()
        events = _tool_call_events("some_other_tool", json.dumps({"title": "x"}))
        with patch("urllib.request.urlopen", return_value=_FakeSSEResponse(events)):
            with pytest.raises(OpenClawProviderError) as excinfo:
                provider.invoke_structured(
                    "extract", extraction_schema=EXTRACTION_SCHEMA, operator_id="op-1"
                )
        assert excinfo.value.error_code == "OPENCLAW_TOOL_MISMATCH"

    def test_invalid_json_arguments_are_rejected(self):
        provider = _make_provider()
        events = _tool_call_events(EMIT_EXTRACTION_TOOL_NAME, "{not valid json")
        with patch("urllib.request.urlopen", return_value=_FakeSSEResponse(events)):
            with pytest.raises(OpenClawProviderError) as excinfo:
                provider.invoke_structured(
                    "extract", extraction_schema=EXTRACTION_SCHEMA, operator_id="op-1"
                )
        assert excinfo.value.error_code == "OPENCLAW_TOOL_ARGS_INVALID_JSON"
        assert excinfo.value.status_code == 422

    def test_missing_required_field_is_rejected(self):
        provider = _make_provider()
        events = _tool_call_events(EMIT_EXTRACTION_TOOL_NAME, json.dumps({"count": 3}))
        with patch("urllib.request.urlopen", return_value=_FakeSSEResponse(events)):
            with pytest.raises(OpenClawProviderError) as excinfo:
                provider.invoke_structured(
                    "extract", extraction_schema=EXTRACTION_SCHEMA, operator_id="op-1"
                )
        assert excinfo.value.error_code == "OPENCLAW_TOOL_ARGS_SCHEMA_MISMATCH"
        assert excinfo.value.status_code == 422

    def test_wrong_type_for_declared_property_is_rejected(self):
        provider = _make_provider()
        events = _tool_call_events(
            EMIT_EXTRACTION_TOOL_NAME, json.dumps({"title": "ok", "count": "not-a-number"})
        )
        with patch("urllib.request.urlopen", return_value=_FakeSSEResponse(events)):
            with pytest.raises(OpenClawProviderError) as excinfo:
                provider.invoke_structured(
                    "extract", extraction_schema=EXTRACTION_SCHEMA, operator_id="op-1"
                )
        assert excinfo.value.error_code == "OPENCLAW_TOOL_ARGS_SCHEMA_MISMATCH"

    def test_boolean_is_not_accepted_as_integer(self):
        provider = _make_provider()
        events = _tool_call_events(
            EMIT_EXTRACTION_TOOL_NAME, json.dumps({"title": "ok", "count": True})
        )
        with patch("urllib.request.urlopen", return_value=_FakeSSEResponse(events)):
            with pytest.raises(OpenClawProviderError) as excinfo:
                provider.invoke_structured(
                    "extract", extraction_schema=EXTRACTION_SCHEMA, operator_id="op-1"
                )
        assert excinfo.value.error_code == "OPENCLAW_TOOL_ARGS_SCHEMA_MISMATCH"

    def test_never_spawns_subprocess(self):
        """`_run_func` raises if called; the structured turn must succeed
        without any CLI subprocess involvement."""
        provider = _make_provider()
        events = _tool_call_events(EMIT_EXTRACTION_TOOL_NAME, json.dumps({"title": "ok"}))
        with patch("urllib.request.urlopen", return_value=_FakeSSEResponse(events)):
            result = provider.invoke_structured(
                "extract", extraction_schema=EXTRACTION_SCHEMA, operator_id="op-1"
            )
        assert result.status == "completed"


class TestValidateExtractionArgumentsSchemaCoverage:
    """Direct unit coverage of `_validate_extraction_arguments` for schema
    capabilities the wire-level tests above don't exercise: `enum`
    membership, nested-object `required`, and a nullable `type: [<t>, "null"]`
    union (which previously crashed with an unhandled TypeError because a
    list is unhashable and cannot key `_JSON_TYPE_TO_PYTHON.get(...)`)."""

    def test_enum_violation_is_rejected(self):
        schema = {
            "type": "object",
            "properties": {"status": {"type": "string", "enum": ["open", "closed"]}},
            "required": ["status"],
        }
        with pytest.raises(OpenClawProviderError) as excinfo:
            _validate_extraction_arguments({"status": "pending"}, schema)
        assert excinfo.value.error_code == "OPENCLAW_TOOL_ARGS_SCHEMA_MISMATCH"
        # A valid enum member must still pass.
        _validate_extraction_arguments({"status": "open"}, schema)

    def test_nested_object_required_violation_is_rejected(self):
        schema = {
            "type": "object",
            "properties": {
                "address": {
                    "type": "object",
                    "properties": {"city": {"type": "string"}, "zip": {"type": "string"}},
                    "required": ["city", "zip"],
                }
            },
            "required": ["address"],
        }
        with pytest.raises(OpenClawProviderError) as excinfo:
            _validate_extraction_arguments({"address": {"city": "NYC"}}, schema)
        assert excinfo.value.error_code == "OPENCLAW_TOOL_ARGS_SCHEMA_MISMATCH"
        # A fully-populated nested object must still pass.
        _validate_extraction_arguments({"address": {"city": "NYC", "zip": "10001"}}, schema)

    def test_nullable_type_list_does_not_raise_and_validates(self):
        schema = {
            "type": "object",
            "properties": {"note": {"type": ["string", "null"]}},
            "required": [],
        }
        # None is a legal value for a nullable field — must not raise.
        _validate_extraction_arguments({"note": None}, schema)
        _validate_extraction_arguments({"note": "hello"}, schema)
        with pytest.raises(OpenClawProviderError) as excinfo:
            _validate_extraction_arguments({"note": 42}, schema)
        assert excinfo.value.error_code == "OPENCLAW_TOOL_ARGS_SCHEMA_MISMATCH"


class TestStructuredEndpointRejectsCallerSuppliedTools:
    """Endpoint-level contract: a caller-supplied `tools`/`tool_choice` must
    be rejected (422), never silently accepted as an arbitrary tool
    definition."""

    @staticmethod
    def _client():
        import main as adapter_main  # noqa: PLC0415
        from fastapi.testclient import TestClient  # noqa: PLC0415

        return TestClient(adapter_main.app), adapter_main

    def test_request_with_raw_tools_list_is_rejected(self):
        client, _adapter_main = self._client()
        resp = client.post(
            "/api/openclaw-adapter/assistant/providers/openclaw/structured",
            json={
                "prompt": "extract",
                "extraction_schema": EXTRACTION_SCHEMA,
                "tools": [{"type": "function", "name": "shell_exec", "parameters": {}}],
            },
            headers={"X-Operator-Id": "operator-1"},
        )
        assert resp.status_code == 422

    def test_request_with_tool_choice_is_rejected(self):
        client, _adapter_main = self._client()
        resp = client.post(
            "/api/openclaw-adapter/assistant/providers/openclaw/structured",
            json={
                "prompt": "extract",
                "extraction_schema": EXTRACTION_SCHEMA,
                "tool_choice": {"type": "function", "name": "shell_exec"},
            },
            headers={"X-Operator-Id": "operator-1"},
        )
        assert resp.status_code == 422

    def test_valid_structured_request_is_accepted_and_delegates_to_provider(self):
        client, adapter_main = self._client()
        fake_result = adapter_main._OPENCLAW_AGENT_PROVIDER.invoke_structured

        class _Result:
            def to_dict(self):
                return {
                    "provider": "openclaw",
                    "mode": "user",
                    "status": "completed",
                    "output": {"structured_data": {"title": "ok"}},
                }

        with patch.object(
            adapter_main._OPENCLAW_AGENT_PROVIDER, "invoke_structured", return_value=_Result()
        ) as mocked:
            resp = client.post(
                "/api/openclaw-adapter/assistant/providers/openclaw/structured",
                json={"prompt": "extract", "extraction_schema": EXTRACTION_SCHEMA},
                headers={"X-Operator-Id": "operator-1"},
            )
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["output"]["structured_data"] == {"title": "ok"}
        assert mocked.call_args.kwargs["extraction_schema"] == EXTRACTION_SCHEMA

    def test_missing_operator_id_is_rejected(self):
        client, _adapter_main = self._client()
        resp = client.post(
            "/api/openclaw-adapter/assistant/providers/openclaw/structured",
            json={"prompt": "extract", "extraction_schema": EXTRACTION_SCHEMA},
        )
        assert resp.status_code == 401

    def test_arbitrary_agent_id_is_rejected_without_admission_or_policy_check(self):
        """Unlike ordinary invoke (which pairs `agent_id` with a governed
        `persona_admission` and enforces runtime policy), this endpoint has no
        admission mechanism at all. An arbitrary `agent_id` must be rejected
        (422) rather than silently routed, and the provider must never even
        be called."""
        client, adapter_main = self._client()
        with patch.object(adapter_main._OPENCLAW_AGENT_PROVIDER, "invoke_structured") as mocked:
            resp = client.post(
                "/api/openclaw-adapter/assistant/providers/openclaw/structured",
                json={
                    "prompt": "extract",
                    "extraction_schema": EXTRACTION_SCHEMA,
                    "agent_id": "persona-opinion-abcdef0123456789abcdef01",
                },
                headers={"X-Operator-Id": "operator-1"},
            )
        assert resp.status_code == 422
        assert resp.json()["error_code"] == "OPENCLAW_STRUCTURED_AGENT_NOT_ALLOWED"
        assert mocked.call_count == 0

    def test_explicit_default_agent_id_is_still_accepted(self):
        client, adapter_main = self._client()

        class _Result:
            def to_dict(self):
                return {
                    "provider": "openclaw",
                    "mode": "user",
                    "status": "completed",
                    "output": {"structured_data": {"title": "ok"}},
                }

        with patch.object(
            adapter_main._OPENCLAW_AGENT_PROVIDER, "invoke_structured", return_value=_Result()
        ) as mocked:
            resp = client.post(
                "/api/openclaw-adapter/assistant/providers/openclaw/structured",
                json={
                    "prompt": "extract",
                    "extraction_schema": EXTRACTION_SCHEMA,
                    "agent_id": adapter_main.OPENCLAW_DEFAULT_AGENT_ID,
                },
                headers={"X-Operator-Id": "operator-1"},
            )
        assert resp.status_code == 200, resp.text
        assert mocked.call_count == 1
