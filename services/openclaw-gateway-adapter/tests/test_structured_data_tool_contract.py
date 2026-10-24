"""Contract tests for the JSON-only structured-data extraction turn.

The model is offered no tool: it answers with one JSON object over the unified
HTTP `/v1/responses` transport and the adapter validates that answer against
the caller-supplied extraction schema before returning `structured_data`.
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
    AssistantOpenClawProvider,
    OpenClawProviderError,
    _validate_extraction_arguments,
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


def _answer_events(text: str) -> list:
    return [
        {"type": "response.output_text.done", "text": text},
        {
            "type": "response.completed",
            "response": {
                "status": "completed",
                "id": "resp_1",
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
        },
    ]


EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "count": {"type": "integer"},
    },
    "required": ["title"],
}


def _invoke(provider, events):
    with patch("assistant_openclaw_provider._urlopen_with_deadline", return_value=_FakeSSEResponse(events)):
        return provider.invoke_structured(
            "extract", extraction_schema=EXTRACTION_SCHEMA, operator_id="op-1"
        )


class TestInvokeStructuredPositive:
    def test_valid_json_answer_returns_structured_data(self):
        result = _invoke(_make_provider(), _answer_events(json.dumps({"title": "Widget", "count": 3})))
        assert result.status == "completed"
        assert result.output["structured_data"] == {"title": "Widget", "count": 3}
        assert result.output["usage"] == {"input_tokens": 10, "output_tokens": 5}
        assert result.output["response_id"] == "resp_1"

    def test_no_tool_is_offered_and_schema_is_in_the_prompt(self):
        captured = {}

        def fake_urlopen(req, timeout=None, deadline=None):
            captured["body"] = json.loads(req.data.decode("utf-8"))
            return _FakeSSEResponse(_answer_events(json.dumps({"title": "x"})))

        with patch("assistant_openclaw_provider._urlopen_with_deadline", fake_urlopen):
            _make_provider().invoke_structured(
                "extract", extraction_schema=EXTRACTION_SCHEMA, operator_id="op-1"
            )
        body = captured["body"]
        assert "tools" not in body and "tool_choice" not in body
        assert json.dumps(EXTRACTION_SCHEMA) in body["input"]

    def test_caller_tool_wording_still_gets_json_only_instruction_first(self):
        captured = {}

        def fake_urlopen(req, timeout=None, deadline=None):
            captured["body"] = json.loads(req.data.decode("utf-8"))
            return _FakeSSEResponse(_answer_events(json.dumps({"title": "x"})))

        with patch("assistant_openclaw_provider._urlopen_with_deadline", fake_urlopen):
            result = _make_provider().invoke_structured(
                "You MUST call the tool emit_extraction",
                extraction_schema=EXTRACTION_SCHEMA,
                operator_id="op-1",
            )
        text = captured["body"]["input"]
        assert text.startswith("No tool is available")
        assert text.index("answer with the JSON object itself") < text.index("You MUST call")
        assert result.output["structured_data"] == {"title": "x"}

    def test_each_call_uses_a_fresh_upstream_session_user(self):
        # A stable `user` reuses one warm CLI session per caller and grows to context_overflow.
        provider = _make_provider()
        users = []

        def fake_urlopen(req, timeout=None, deadline=None):
            users.append(json.loads(req.data.decode("utf-8"))["user"])
            return _FakeSSEResponse(_answer_events(json.dumps({"title": "x"})))

        with patch("assistant_openclaw_provider._urlopen_with_deadline", fake_urlopen):
            for _ in range(2):
                provider.invoke_structured(
                    "extract", extraction_schema=EXTRACTION_SCHEMA, operator_id="monitor-agent"
                )
        assert len(set(users)) == 2
        assert all(u.startswith("|monitor-agent|structured-") for u in users)

    def test_missing_usage_is_not_reported_as_zero(self):
        events = [
            {"type": "response.output_text.done", "text": json.dumps({"title": "no usage"})},
            {"type": "response.completed", "response": {"status": "completed"}},
        ]
        assert "usage" not in _invoke(_make_provider(), events).output


class TestInvokeStructuredNegative:
    @pytest.mark.parametrize("text", ["I used ReportFindings instead.", "{not valid json", "```json\n{\"title\": \"x\"}\n```"])
    def test_non_json_answer_is_rejected(self, text):
        with pytest.raises(OpenClawProviderError) as excinfo:
            _invoke(_make_provider(), _answer_events(text))
        assert excinfo.value.error_code == "OPENCLAW_TOOL_ARGS_INVALID_JSON"
        assert excinfo.value.status_code == 422

    @pytest.mark.parametrize("answer", [{"count": 3}, {"title": "ok", "count": "3"}, {"title": "ok", "count": True}, ["title"]])
    def test_schema_invalid_json_is_rejected(self, answer):
        with pytest.raises(OpenClawProviderError) as excinfo:
            _invoke(_make_provider(), _answer_events(json.dumps(answer)))
        assert excinfo.value.error_code == "OPENCLAW_TOOL_ARGS_SCHEMA_MISMATCH"
        assert excinfo.value.status_code == 422

    def test_tool_call_alongside_answer_is_rejected(self):
        events = [
            {"type": "response.output_text.done", "text": json.dumps({"title": "ok"})},
            {
                "type": "response.completed",
                "response": {
                    "status": "completed",
                    "output": [{"type": "function_call", "name": "shell_exec", "arguments": "{}", "call_id": "c"}],
                },
            },
        ]
        with pytest.raises(OpenClawProviderError) as excinfo:
            _invoke(_make_provider(), events)
        assert excinfo.value.error_code == "OPENCLAW_TOOL_MISMATCH"


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

    def test_array_item_type_mismatch_is_rejected(self):
        """SIMPLIFY-OPENCLAW-001 reviewer defect: `records[].amount` was
        previously accepted with a non-integer value because array `items`
        were never validated at all."""
        schema = {
            "type": "object",
            "properties": {
                "records": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"amount": {"type": "integer"}},
                        "required": ["amount"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["records"],
        }
        with pytest.raises(OpenClawProviderError) as excinfo:
            _validate_extraction_arguments({"records": [{"amount": "not-an-integer"}]}, schema)
        assert excinfo.value.error_code == "OPENCLAW_TOOL_ARGS_SCHEMA_MISMATCH"
        # A well-typed array item must still pass.
        _validate_extraction_arguments({"records": [{"amount": 5}]}, schema)

    def test_additional_properties_false_rejects_extra_field(self):
        """SIMPLIFY-OPENCLAW-001 reviewer defect: an extra field alongside a
        closed (`additionalProperties: false`) object schema was previously
        silently accepted."""
        schema = {
            "type": "object",
            "properties": {"amount": {"type": "integer"}},
            "required": ["amount"],
            "additionalProperties": False,
        }
        with pytest.raises(OpenClawProviderError) as excinfo:
            _validate_extraction_arguments({"amount": 5, "extra": True}, schema)
        assert excinfo.value.error_code == "OPENCLAW_TOOL_ARGS_SCHEMA_MISMATCH"
        # additionalProperties defaults to permissive when omitted.
        _validate_extraction_arguments({"amount": 5, "extra": True}, {**schema, "additionalProperties": True})

    def test_numeric_bounds_are_enforced(self):
        schema = {
            "type": "object",
            "properties": {"score": {"type": "integer", "minimum": 0, "maximum": 100}},
            "required": ["score"],
        }
        with pytest.raises(OpenClawProviderError) as excinfo:
            _validate_extraction_arguments({"score": 101}, schema)
        assert excinfo.value.error_code == "OPENCLAW_TOOL_ARGS_SCHEMA_MISMATCH"
        with pytest.raises(OpenClawProviderError):
            _validate_extraction_arguments({"score": -1}, schema)
        _validate_extraction_arguments({"score": 50}, schema)

    def test_malformed_required_declaration_is_a_typed_rejection_not_a_crash(self):
        """SIMPLIFY-OPENCLAW-001 reviewer defect: a malformed schema
        (`required: 17` instead of a list) previously crashed with an
        unhandled `TypeError` (surfacing as an unhandled HTTP 500), since
        `for field in required` was never guarded. It must now be rejected
        as a typed schema-mismatch error."""
        schema = {"type": "object", "properties": {"a": {"type": "string"}}, "required": 17}
        with pytest.raises(OpenClawProviderError) as excinfo:
            _validate_extraction_arguments({"a": "x"}, schema)
        assert excinfo.value.error_code == "OPENCLAW_TOOL_ARGS_SCHEMA_MISMATCH"
        assert excinfo.value.status_code == 422

    def test_root_level_enum_is_enforced_even_when_type_is_object(self):
        """SIMPLIFY-OPENCLAW-001 second corrective pass (reviewer finding 6):
        the root-level validation entry point called the object validator
        directly, skipping the enum check entirely for a root schema shaped
        like `{type: "object", enum: [...]}`."""
        schema = {"type": "object", "enum": [{"kind": "ok"}]}
        with pytest.raises(OpenClawProviderError) as excinfo:
            _validate_extraction_arguments({"kind": "wrong"}, schema)
        assert excinfo.value.error_code == "OPENCLAW_TOOL_ARGS_SCHEMA_MISMATCH"
        # The exact enum member must still pass.
        _validate_extraction_arguments({"kind": "ok"}, schema)

    def test_min_length_and_max_length_are_enforced(self):
        schema = {
            "type": "object",
            "properties": {"name": {"type": "string", "minLength": 3, "maxLength": 5}},
            "required": ["name"],
        }
        with pytest.raises(OpenClawProviderError) as excinfo:
            _validate_extraction_arguments({"name": "ab"}, schema)
        assert excinfo.value.error_code == "OPENCLAW_TOOL_ARGS_SCHEMA_MISMATCH"
        with pytest.raises(OpenClawProviderError):
            _validate_extraction_arguments({"name": "abcdef"}, schema)
        _validate_extraction_arguments({"name": "abcd"}, schema)

    def test_nullable_numeric_with_null_value_does_not_raise(self):
        """SIMPLIFY-OPENCLAW-001 second corrective pass (reviewer finding 6):
        `type: ["number", "null"], minimum: 0` with value `null` previously
        raised an uncaught TypeError (`None < 0`) instead of passing, since
        the numeric-bounds check ran unconditionally after the type-union
        match succeeded via the "null" member."""
        schema = {
            "type": "object",
            "properties": {"amount": {"type": ["number", "null"], "minimum": 0}},
            "required": [],
        }
        _validate_extraction_arguments({"amount": None}, schema)
        _validate_extraction_arguments({"amount": 5}, schema)
        with pytest.raises(OpenClawProviderError) as excinfo:
            _validate_extraction_arguments({"amount": -1}, schema)
        assert excinfo.value.error_code == "OPENCLAW_TOOL_ARGS_SCHEMA_MISMATCH"

    def test_nested_const_boolean_does_not_satisfy_numeric_const(self):
        """SIMPLIFY-OPENCLAW-001 reviewer defect (fourth corrective pass):
        `_json_schema_value_equal` was type-sensitive for a boolean-vs-number
        mismatch only at the top level. Python's own `==` recurses into
        nested dict/list members using plain `==`, which reintroduces the
        exact `True == 1` confusion one level down — a nested boolean must
        still not satisfy a nested numeric `const`/`enum`."""
        schema = {
            "type": "object",
            "properties": {"x": {"const": {"x": 1}}},
            "required": ["x"],
        }
        with pytest.raises(OpenClawProviderError) as excinfo:
            _validate_extraction_arguments({"x": {"x": True}}, schema)
        assert excinfo.value.error_code == "OPENCLAW_TOOL_ARGS_SCHEMA_MISMATCH"
        _validate_extraction_arguments({"x": {"x": 1}}, schema)

    def test_nested_enum_boolean_does_not_satisfy_numeric_enum(self):
        schema = {
            "type": "object",
            "properties": {"x": {"enum": [{"x": [1]}]}},
            "required": ["x"],
        }
        with pytest.raises(OpenClawProviderError) as excinfo:
            _validate_extraction_arguments({"x": {"x": [True]}}, schema)
        assert excinfo.value.error_code == "OPENCLAW_TOOL_ARGS_SCHEMA_MISMATCH"
        _validate_extraction_arguments({"x": {"x": [1]}}, schema)

    def test_untyped_property_still_enforces_numeric_minimum(self):
        """SIMPLIFY-OPENCLAW-001 reviewer defect (fourth corrective pass):
        omitting `type` on a property previously returned before enforcing
        any constraint keyed off the value's actual runtime type — a
        property with only `minimum` (no `type`) accepted an out-of-range
        value unconditionally."""
        schema = {
            "type": "object",
            "properties": {"x": {"minimum": 0}},
            "required": ["x"],
        }
        with pytest.raises(OpenClawProviderError) as excinfo:
            _validate_extraction_arguments({"x": -1}, schema)
        assert excinfo.value.error_code == "OPENCLAW_TOOL_ARGS_SCHEMA_MISMATCH"
        _validate_extraction_arguments({"x": 0}, schema)

    def test_untyped_property_still_enforces_min_length(self):
        schema = {
            "type": "object",
            "properties": {"x": {"minLength": 1}},
            "required": ["x"],
        }
        with pytest.raises(OpenClawProviderError) as excinfo:
            _validate_extraction_arguments({"x": ""}, schema)
        assert excinfo.value.error_code == "OPENCLAW_TOOL_ARGS_SCHEMA_MISMATCH"
        _validate_extraction_arguments({"x": "a"}, schema)

    def test_untyped_property_still_enforces_additional_properties_false(self):
        schema = {
            "type": "object",
            "properties": {"x": {"additionalProperties": False}},
            "required": ["x"],
        }
        with pytest.raises(OpenClawProviderError) as excinfo:
            _validate_extraction_arguments({"x": {"extra": 1}}, schema)
        assert excinfo.value.error_code == "OPENCLAW_TOOL_ARGS_SCHEMA_MISMATCH"
        _validate_extraction_arguments({"x": {}}, schema)

    def test_required_list_with_non_string_entry_is_a_typed_rejection_not_a_crash(self):
        """SIMPLIFY-OPENCLAW-001 second corrective pass (reviewer finding 6):
        `required: [{}]` (a non-string entry) previously crashed with an
        uncaught `TypeError` (`{} not in value_dict` raises "unhashable type:
        'dict'"), since each entry was used directly as a dict key/membership
        check without validating it was a string."""
        schema = {"type": "object", "properties": {"a": {"type": "string"}}, "required": [{}]}
        with pytest.raises(OpenClawProviderError) as excinfo:
            _validate_extraction_arguments({"a": "x"}, schema)
        assert excinfo.value.error_code == "OPENCLAW_TOOL_ARGS_SCHEMA_MISMATCH"
        assert excinfo.value.status_code == 422


class TestStructuredEndpointRejectsCallerSuppliedTools:
    """Endpoint-level contract: a caller-supplied `tools`/`tool_choice` must
    be rejected (422), never silently accepted as an arbitrary tool
    definition."""

    @staticmethod
    def _gateway_snapshot(agents, *, args=True, resume_args=True, command=None):
        import main as adapter_main
        backend = {}
        if command is not None:
            backend["command"] = command
        if args:
            backend["args"] = adapter_main._CLAUDE_CLI_TOOLSEARCH_ARGS
        if resume_args:
            backend["resumeArgs"] = adapter_main._CLAUDE_CLI_TOOLSEARCH_RESUME_ARGS
        return {"valid": True, "config": {"agents": {
            "defaults": {"cliBackends": {"claude-cli": backend}}, "list": agents}}}

    @pytest.fixture(autouse=True)
    def gateway_policy(self):
        import main as adapter_main
        snapshot = self._gateway_snapshot(
            [{"id": adapter_main.OPENCLAW_STRUCTURED_AGENT_ID, "tools": {
                "deny": ["*"], "exec": {"security": "deny", "ask": "always"}}}])
        with patch.object(adapter_main._OPENCLAW_AGENT_PROVIDER, "_gateway_call", return_value=snapshot) as rpc:
            yield rpc

    def test_launch_args_carry_toolsearch_limit_and_every_default(self):
        import main as adapter_main
        args = adapter_main._CLAUDE_CLI_TOOLSEARCH_ARGS
        assert args[-2:] == ["--tools", "ToolSearch"]
        for default in ("-p", "--include-partial-messages", "--verbose", "mcp__openclaw__*",
                        "ScheduleWakeup,CronCreate,Bash(run_in_background:true),Monitor"):
            assert default in args
        assert adapter_main._CLAUDE_CLI_TOOLSEARCH_RESUME_ARGS == args + ["--resume", "{sessionId}"]

    @pytest.mark.parametrize("kwargs", [
        {"args": False}, {"resume_args": False},
    ])
    def test_gateway_without_toolsearch_limit_is_rejected_before_any_turn(self, gateway_policy, kwargs):
        client, adapter_main = self._client()
        gateway_policy.return_value = self._gateway_snapshot(
            [{"id": "structured-extraction", "tools": {
                "deny": ["*"], "exec": {"security": "deny", "ask": "always"}}}], **kwargs)
        with patch.object(adapter_main._OPENCLAW_AGENT_PROVIDER, "invoke_structured") as invoke:
            response = client.post(
                "/api/openclaw-adapter/assistant/providers/openclaw/structured",
                json={"prompt": "extract", "extraction_schema": EXTRACTION_SCHEMA},
                headers={"X-Operator-Id": "operator-1"},
            )
        assert response.status_code == 503
        assert response.json()["error_code"] == "OPENCLAW_STRUCTURED_POLICY_DENIED"
        invoke.assert_not_called()

    @pytest.mark.parametrize("snapshot", [
        None, {}, {"valid": False},
        {"valid": True, "config": {"agents": {"list": []}}},
        {"valid": True, "config": {"agents": {"list": [{"id": "structured-extraction", "tools": {"deny": ["*"]}}]}}},
        {"valid": True, "config": {"agents": {"list": [{"id": "structured-extraction"}]}}},
        {"valid": True, "config": {"agents": {"list": [{"id": "other", "tools": {"deny": ["*"]}}]}}},
        {"valid": True, "config": {"agents": {"list": [{"id": "structured-extraction", "tools": {"deny": ["exec"]}}]}}},
        {"valid": True, "config": {"agents": {"list": [
            {"id": "structured-extraction", "tools": {"deny": ["*"]}}, {"id": "structured-extraction"},
        ]}}},
        # missing_exec_policy (with valid toolsearch launch args)
        {"valid": True, "config": {"agents": {
            "defaults": {"cliBackends": {"claude-cli": {
                "args": [
                    "-p", "--output-format", "stream-json", "--include-partial-messages", "--verbose",
                    "--setting-sources", "user", "--allowedTools", "mcp__openclaw__*",
                    "--disallowedTools", "ScheduleWakeup,CronCreate,Bash(run_in_background:true),Monitor",
                    "--tools", "ToolSearch",
                ],
                "resumeArgs": [
                    "-p", "--output-format", "stream-json", "--include-partial-messages", "--verbose",
                    "--setting-sources", "user", "--allowedTools", "mcp__openclaw__*",
                    "--disallowedTools", "ScheduleWakeup,CronCreate,Bash(run_in_background:true),Monitor",
                    "--tools", "ToolSearch", "--resume", "{sessionId}",
                ],
            }}},
            "list": [{"id": "structured-extraction", "tools": {"deny": ["*"]}}],
        }}},
        # full_off_exec_policy (with valid toolsearch launch args)
        {"valid": True, "config": {"agents": {
            "defaults": {"cliBackends": {"claude-cli": {
                "args": [
                    "-p", "--output-format", "stream-json", "--include-partial-messages", "--verbose",
                    "--setting-sources", "user", "--allowedTools", "mcp__openclaw__*",
                    "--disallowedTools", "ScheduleWakeup,CronCreate,Bash(run_in_background:true),Monitor",
                    "--tools", "ToolSearch",
                ],
                "resumeArgs": [
                    "-p", "--output-format", "stream-json", "--include-partial-messages", "--verbose",
                    "--setting-sources", "user", "--allowedTools", "mcp__openclaw__*",
                    "--disallowedTools", "ScheduleWakeup,CronCreate,Bash(run_in_background:true),Monitor",
                    "--tools", "ToolSearch", "--resume", "{sessionId}",
                ],
            }}},
            "list": [{"id": "structured-extraction", "tools": {
                "deny": ["*"], "exec": {"security": "full", "ask": "off"},
            }}],
        }}},
        # wrong_backend_command
        {"valid": True, "config": {"agents": {
            "defaults": {"cliBackends": {"claude-cli": {
                "command": "not-claude-offline-negative",
                "args": [
                    "-p", "--output-format", "stream-json", "--include-partial-messages", "--verbose",
                    "--setting-sources", "user", "--allowedTools", "mcp__openclaw__*",
                    "--disallowedTools", "ScheduleWakeup,CronCreate,Bash(run_in_background:true),Monitor",
                    "--tools", "ToolSearch",
                ],
                "resumeArgs": [
                    "-p", "--output-format", "stream-json", "--include-partial-messages", "--verbose",
                    "--setting-sources", "user", "--allowedTools", "mcp__openclaw__*",
                    "--disallowedTools", "ScheduleWakeup,CronCreate,Bash(run_in_background:true),Monitor",
                    "--tools", "ToolSearch", "--resume", "{sessionId}",
                ],
            }}},
            "list": [{"id": "structured-extraction", "tools": {
                "deny": ["*"], "exec": {"security": "deny", "ask": "always"},
            }}],
        }}},
    ])
    def test_unverified_policy_blocks_before_dispatch(self, gateway_policy, snapshot):
        client, adapter_main = self._client()
        gateway_policy.return_value = snapshot
        with patch.object(adapter_main._OPENCLAW_AGENT_PROVIDER, "invoke_structured") as invoke:
            response = client.post(
                "/api/openclaw-adapter/assistant/providers/openclaw/structured",
                json={"prompt": "CASE_denied", "extraction_schema": EXTRACTION_SCHEMA},
                headers={"X-Operator-Id": "operator-1"},
            )
        assert response.status_code == 503
        assert response.json()["error_code"] == "OPENCLAW_STRUCTURED_POLICY_DENIED"
        invoke.assert_not_called()
        assert gateway_policy.call_args.args == ("config.get",)
        assert 0 < gateway_policy.call_args.kwargs["timeout_seconds"] <= adapter_main._OPENCLAW_AGENT_PROVIDER._timeout

    @pytest.mark.parametrize("command", [None, "claude"])
    def test_supported_backend_command_allows_turn(self, gateway_policy, command):
        from types import SimpleNamespace
        client, adapter_main = self._client()
        gateway_policy.return_value = self._gateway_snapshot(
            [{"id": "structured-extraction", "tools": {
                "deny": ["*"], "exec": {"security": "deny", "ask": "always"}}}],
            command=command,
        )
        result = SimpleNamespace(to_dict=lambda: {"status": "completed", "output": {"structured_data": {"count": 1}}})
        with patch.object(adapter_main._OPENCLAW_AGENT_PROVIDER, "invoke_structured", return_value=result) as invoke:
            response = client.post(
                "/api/openclaw-adapter/assistant/providers/openclaw/structured",
                json={"prompt": "extract", "extraction_schema": EXTRACTION_SCHEMA},
                headers={"X-Operator-Id": "operator-1"},
            )
        assert response.status_code == 200
        invoke.assert_called_once()

    def test_policy_startup_over_ten_seconds_uses_remaining_turn_budget(self, gateway_policy):
        from types import SimpleNamespace
        client, adapter_main = self._client()
        clock = [100.0]
        snapshot = gateway_policy.return_value

        def slow_policy(*args, **kwargs):
            # Model a healthy CLI cold start that the former cap rejected.
            assert kwargs["timeout_seconds"] == 15.0
            clock[0] += 12.0
            return snapshot

        gateway_policy.side_effect = slow_policy
        result = SimpleNamespace(to_dict=lambda: {"status": "completed"})
        with (
            patch.object(adapter_main, "time", SimpleNamespace(monotonic=lambda: clock[0])),
            patch.object(adapter_main._OPENCLAW_AGENT_PROVIDER, "_timeout", 15),
            patch.object(adapter_main._OPENCLAW_AGENT_PROVIDER, "invoke_structured", return_value=result) as invoke,
        ):
            response = client.post(
                "/api/openclaw-adapter/assistant/providers/openclaw/structured",
                json={"prompt": "extract", "extraction_schema": EXTRACTION_SCHEMA},
                headers={"X-Operator-Id": "operator-1"},
            )
        assert response.status_code == 200, response.text
        gateway_policy.assert_called_once()
        invoke.assert_called_once()
        assert invoke.call_args.kwargs["timeout_seconds"] == 3.0

    def test_unavailable_policy_blocks_before_dispatch(self, gateway_policy):
        client, adapter_main = self._client()
        gateway_policy.side_effect = OpenClawProviderError(
            "unavailable", status_code=504, error_code="OPENCLAW_GATEWAY_TIMEOUT",
        )
        with patch.object(adapter_main._OPENCLAW_AGENT_PROVIDER, "invoke_structured") as invoke:
            response = client.post(
                "/api/openclaw-adapter/assistant/providers/openclaw/structured",
                json={"prompt": "CASE_denied", "extraction_schema": EXTRACTION_SCHEMA},
                headers={"X-Operator-Id": "operator-1"},
            )
        assert response.status_code == 503
        assert response.json()["error_code"] == "OPENCLAW_STRUCTURED_POLICY_UNAVAILABLE"
        invoke.assert_not_called()

    def test_policy_read_cannot_restart_the_total_deadline(self, gateway_policy):
        import time
        client, adapter_main = self._client()
        snapshot = gateway_policy.return_value

        def slow_policy(*args, **kwargs):
            time.sleep(0.02)
            return snapshot

        gateway_policy.side_effect = slow_policy
        with (
            patch.object(adapter_main._OPENCLAW_AGENT_PROVIDER, "_timeout", 0.01),
            patch.object(adapter_main._OPENCLAW_AGENT_PROVIDER, "invoke_structured") as invoke,
        ):
            response = client.post(
                "/api/openclaw-adapter/assistant/providers/openclaw/structured",
                json={"prompt": "extract", "extraction_schema": EXTRACTION_SCHEMA},
                headers={"X-Operator-Id": "operator-1"},
            )
        assert response.status_code == 504
        assert response.json()["error_code"] == "OPENCLAW_STRUCTURED_POLICY_UNAVAILABLE"
        invoke.assert_not_called()

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
        assert mocked.call_args.kwargs["agent_id"] == adapter_main.OPENCLAW_STRUCTURED_AGENT_ID
        assert 0 < mocked.call_args.kwargs["timeout_seconds"] <= adapter_main._OPENCLAW_AGENT_PROVIDER._timeout

    def test_schema_invalid_answer_is_typed_422_never_500(self):
        """A schema-mismatched tool-call response must surface as a typed
        422 OPENCLAW_TOOL_ARGS_SCHEMA_MISMATCH through the HTTP endpoint,
        never an uncaught 500 and never a false-positive 200/completed."""
        client, adapter_main = self._client()
        events = _answer_events(json.dumps({"count": "not-a-number"}))
        with (
            patch.object(adapter_main._OPENCLAW_AGENT_PROVIDER, "_gateway_url", "http://openclaw.test"),
            patch.object(adapter_main._OPENCLAW_AGENT_PROVIDER, "_token", "test-token"),
            patch("assistant_openclaw_provider._urlopen_with_deadline", return_value=_FakeSSEResponse(events)),
        ):
            resp = client.post(
                "/api/openclaw-adapter/assistant/providers/openclaw/structured",
                json={"prompt": "extract", "extraction_schema": EXTRACTION_SCHEMA},
                headers={"X-Operator-Id": "operator-1"},
            )
        assert resp.status_code == 422
        assert resp.json()["error_code"] == "OPENCLAW_TOOL_ARGS_SCHEMA_MISMATCH"

    def test_explicit_model_override_is_rejected_not_silently_dropped(self):
        """SIMPLIFY-OPENCLAW-001 mounted-acceptance gap: an explicit `model`
        field on the mounted request must not be silently dropped -- this
        adapter does not thread a model override through this route, so it
        must fail closed with a typed 4xx instead."""
        client, _adapter_main = self._client()
        resp = client.post(
            "/api/openclaw-adapter/assistant/providers/openclaw/structured",
            json={"prompt": "extract", "extraction_schema": EXTRACTION_SCHEMA, "model": "gpt-5"},
            headers={"X-Operator-Id": "operator-1"},
        )
        assert resp.status_code == 422

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
                    "agent_id": adapter_main.OPENCLAW_STRUCTURED_AGENT_ID,
                },
                headers={"X-Operator-Id": "operator-1"},
            )
        assert resp.status_code == 200, resp.text
        assert mocked.call_count == 1


class TestPersonaOpinionEnsureModelPolicyAndFallbackPropagation:
    """Contract regressions for H2 (`HOSTED-PERSONA-MODEL-FALLBACK-20261024`):
    admitted Persona opinion `ensure` must propagate declared runtime-profile
    fallbacks and canonical model routing into the OpenClaw agent's `agents.list`
    config and update already-provisioned agents without weakening tool denial,
    memory disablement, or frozen admission checks.
    """

    @staticmethod
    def _client():
        import main as adapter_main  # noqa: PLC0415
        from fastapi.testclient import TestClient  # noqa: PLC0415

        return TestClient(adapter_main.app), adapter_main

    @staticmethod
    def _opinion_admission(
        adapter_main,
        *,
        persona_id: str = "agora-servant-4eef59cbdf80a6b9fea0",
        persona_version: str = "2026-10-16T04:10:02.683698+00:00",
        snapshot_id: str = "cap-servant-4f4c89ce8a18f2b1de54",
        tenant_id: str = "pantheon-dev",
        requested_environment: str = "paper",
        display_name: str = "Agora Servant",
    ) -> dict:
        agent_id = adapter_main._persona_opinion_agent_id(
            tenant_id,
            persona_id,
            persona_version,
            snapshot_id,
            requested_environment,
        )
        return {
            "persona_id": persona_id,
            "tenant_id": tenant_id,
            "persona_version": persona_version,
            "agent_id": agent_id,
            "workspace_ref": f"/home/node/.openclaw/workspaces/{agent_id}",
            "capability_snapshot_id": snapshot_id,
            "allowed_capabilities": ["persona_opinion"],
            "environment_ceiling": "paper",
            "requested_environment": requested_environment,
            "execution_authority": "none",
            "display_name": display_name,
            "mandate": "user_private_agora_servant",
            "archetype": "agora_servant",
            "strategy_family": "agora_servant",
            "traits": {"decision_style": "operator-guided"},
        }

    @staticmethod
    def _load_bff_ops_client():
        import importlib.util  # noqa: PLC0415

        bff_client_path = (
            Path(__file__).resolve().parents[3]
            / "services"
            / "control-plane"
            / "bff"
            / "openclaw_ops_client.py"
        )
        spec = importlib.util.spec_from_file_location("bff_openclaw_ops_client", bff_client_path)
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_ensure_propagates_default_fallbacks_and_updates_already_provisioned_single_string_agent(
        self, tmp_path
    ):
        import subprocess
        from unittest.mock import MagicMock

        ops_client_mod = self._load_bff_ops_client()
        OpenClawOpsClient = ops_client_mod.OpenClawOpsClient

        client, adapter_main = self._client()
        admission = self._opinion_admission(adapter_main)
        agent_id = admission["agent_id"]

        # Pre-seed an already-provisioned agent in `agents.list` with a bare
        # single-string `"model": "anthropic/claude-opus-4-8"` (matching the H2
        # failure condition on `persona-opinion-cc717e9a40ac3b5f7883bf62`).
        runtime_agents = [
            {
                "id": agent_id,
                "workspace": admission["workspace_ref"],
                "model": "anthropic/claude-opus-4-8",
            }
        ]
        written_souls = {}

        def fake_runtime(args):
            if args[1:4] == ["agents", "list", "--json"]:
                return subprocess.CompletedProcess(
                    args=args,
                    returncode=0,
                    stdout=json.dumps({"agents": runtime_agents}),
                    stderr="",
                )
            if args[1:3] == ["agents", "add"]:
                runtime_agents.append(
                    {
                        "id": args[3],
                        "workspace": args[args.index("--workspace") + 1],
                        "model": args[args.index("--model") + 1],
                    }
                )
                return subprocess.CompletedProcess(args=args, returncode=0, stdout="{}", stderr="")
            if args[1:5] == ["config", "get", "agents.list", "--json"]:
                return subprocess.CompletedProcess(
                    args=args,
                    returncode=0,
                    stdout=json.dumps(runtime_agents),
                    stderr="",
                )
            if args[1:4] == ["config", "set", "agents.list"]:
                runtime_agents[:] = json.loads(args[4])
                return subprocess.CompletedProcess(args=args, returncode=0, stdout="ok", stderr="")
            if args[1:3] == ["agents", "set-identity"]:
                return subprocess.CompletedProcess(args=args, returncode=0, stdout="ok", stderr="")
            return subprocess.CompletedProcess(
                args=args, returncode=1, stdout="", stderr=f"unexpected command: {args}"
            )

        class _TestClientBridgeResponse:
            def __init__(self, resp):
                self._resp = resp

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def getcode(self) -> int:
                return self._resp.status_code

            def read(self) -> bytes:
                return self._resp.content

        def fake_urlopen(request, timeout):
            del timeout
            headers = dict(request.header_items())
            body = json.loads(request.data.decode("utf-8"))
            path = "/" + request.full_url.split("://", 1)[1].split("/", 1)[1]
            resp = client.post(path, json=body, headers=headers)
            return _TestClientBridgeResponse(resp)

        ops_client = OpenClawOpsClient(
            base_url="http://openclaw-gateway-adapter:8104",
            service_token="adapter-secret",
            service_auth_required=True,
        )
        db_path = tmp_path / "agent-ensure.sqlite3"
        provider_result = MagicMock()
        provider_result.to_dict.return_value = {
            "provider": "openclaw",
            "mode": "user",
            "status": "completed",
            "output": {"agent_id": agent_id, "json_events": []},
        }

        with (
            patch.object(adapter_main, "_ASSISTANT_SERVICE_TOKEN", "adapter-secret"),
            patch.object(adapter_main, "_ASSISTANT_SERVICE_AUTH_REQUIRED", True),
            patch.object(adapter_main, "_OPENCLAW_AGENT_IDEMPOTENCY_DB", db_path),
            patch.object(adapter_main, "_gateway_state_agent_runner", side_effect=fake_runtime),
            patch.object(
                adapter_main,
                "_gateway_state_soul_writer",
                side_effect=lambda ws, soul: written_souls.__setitem__(ws, soul),
            ),
            patch.object(
                adapter_main._OPENCLAW_AGENT_PROVIDER,
                "gateway_agents_list",
                side_effect=lambda **_kw: list(runtime_agents),
            ),
            patch.object(
                adapter_main._OPENCLAW_AGENT_PROVIDER,
                "invoke",
                return_value=provider_result,
            ),
            patch.object(ops_client_mod.urllib.request, "urlopen", fake_urlopen),
        ):
            ensured = ops_client.ensure_persona_opinion_agent(
                admission,
                persona_profile={
                    "persona_id": admission["persona_id"],
                    "name": admission["display_name"],
                    "mandate": admission["mandate"],
                },
            )
            assert ensured["execution_authority"] == "none"
            assert ensured["agent"]["agent_id"] == agent_id
            assert ensured["agent"]["status"] == "updated"
            assert ensured["agent"]["model"] == "anthropic/claude-opus-4-8"
            assert ensured["agent"]["fallback_models"] == [
                "anthropic/claude-sonnet-4-6",
                "openai/gpt-5.5",
            ]
            assert ensured["agent"]["model_policy"] == {
                "primary": "anthropic/claude-opus-4-8",
                "fallbacks": ["anthropic/claude-sonnet-4-6", "openai/gpt-5.5"],
            }
            assert runtime_agents[0]["model"] == {
                "primary": "anthropic/claude-opus-4-8",
                "fallbacks": ["anthropic/claude-sonnet-4-6", "openai/gpt-5.5"],
            }
            assert runtime_agents[0]["tools"] == {
                "allow": [],
                "deny": ["*"],
                "exec": {"security": "deny", "ask": "always"},
            }
            assert runtime_agents[0]["memorySearch"]["enabled"] is False
            assert runtime_agents[0]["contextInjection"] == "never"

            # Update the same already-provisioned agent's fallback order while
            # preserving its primary model via canonical `model_routing`:
            ensured_routed = ops_client.ensure_persona_opinion_agent(
                admission,
                persona_profile={
                    "persona_id": admission["persona_id"],
                    "name": admission["display_name"],
                    "mandate": admission["mandate"],
                    "metadata": {
                        "model_routing": {
                            "mode": "fallback",
                            "fallback_models": [
                                "anthropic/claude-opus-4-8",
                                "openai/gpt-5.5",
                            ],
                        }
                    },
                },
            )
            assert ensured_routed["agent"]["model"] == "anthropic/claude-opus-4-8"
            assert ensured_routed["agent"]["fallback_models"] == ["openai/gpt-5.5"]
            assert ensured_routed["agent"]["routing_mode"] == "fallback"
            assert runtime_agents[0]["model"] == {
                "primary": "anthropic/claude-opus-4-8",
                "fallbacks": ["openai/gpt-5.5"],
            }

            # Attempting to drift the existing agent's primary model must fail
            # closed with `model_drift_update_unavailable` rather than rewriting:
            with pytest.raises(ops_client_mod.OpenClawOpsClientError) as drift_exc:
                ops_client.ensure_persona_opinion_agent(
                    admission,
                    persona_profile={
                        "persona_id": admission["persona_id"],
                        "name": admission["display_name"],
                        "mandate": admission["mandate"],
                        "preferred_model": "openai/gpt-5.5",
                    },
                )
            assert drift_exc.value.status_code == 503
            assert "model_drift_update_unavailable" in drift_exc.value.message
            assert runtime_agents[0]["model"] == {
                "primary": "anthropic/claude-opus-4-8",
                "fallbacks": ["openai/gpt-5.5"],
            }

            # Verify that governed `/invoke` still accepts the unchanged frozen
            # `admission` dict after model policy propagation:
            invoked = ops_client.invoke_assistant_provider(
                provider="openclaw",
                mode="user",
                prompt="Return governed opinion JSON",
                context_pack={"participant": {"persona_id": admission["persona_id"]}},
                operator_id="operator-1",
                agent_id=agent_id,
                persona_admission=admission,
                idempotency_key="inv-h2-regression-001",
            )
            assert invoked["data"]["status"] == "completed"

    def test_ensure_supports_hard_pin_and_replay_reconciles_single_string_agent(self, tmp_path):
        import subprocess

        client, adapter_main = self._client()
        admission = self._opinion_admission(adapter_main)
        agent_id = admission["agent_id"]
        runtime_agents = []

        def fake_runtime(args):
            if args[1:4] == ["agents", "list", "--json"]:
                return subprocess.CompletedProcess(
                    args=args, returncode=0, stdout=json.dumps({"agents": runtime_agents}), stderr=""
                )
            if args[1:3] == ["agents", "add"]:
                runtime_agents.append(
                    {
                        "id": args[3],
                        "workspace": args[args.index("--workspace") + 1],
                        "model": args[args.index("--model") + 1],
                    }
                )
                return subprocess.CompletedProcess(args=args, returncode=0, stdout="{}", stderr="")
            if args[1:5] == ["config", "get", "agents.list", "--json"]:
                return subprocess.CompletedProcess(
                    args=args, returncode=0, stdout=json.dumps(runtime_agents), stderr=""
                )
            if args[1:4] == ["config", "set", "agents.list"]:
                runtime_agents[:] = json.loads(args[4])
                return subprocess.CompletedProcess(args=args, returncode=0, stdout="ok", stderr="")
            if args[1:3] == ["agents", "set-identity"]:
                return subprocess.CompletedProcess(args=args, returncode=0, stdout="ok", stderr="")
            return subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr="unexpected")

        db_path = tmp_path / "agent-ensure-replay.sqlite3"
        headers = {
            "X-Pantheon-Service-Token": "adapter-secret",
            "Idempotency-Key": "ensure-replay-hard-pin-key",
            "X-Request-Id": "req-hard-pin-1",
        }
        payload = {
            **admission,
            "model_routing": {"mode": "hard_pin", "model": "openai/gpt-5.5"},
        }
        with (
            patch.object(adapter_main, "_ASSISTANT_SERVICE_TOKEN", "adapter-secret"),
            patch.object(adapter_main, "_ASSISTANT_SERVICE_AUTH_REQUIRED", True),
            patch.object(adapter_main, "_OPENCLAW_AGENT_IDEMPOTENCY_DB", db_path),
            patch.object(adapter_main, "_gateway_state_agent_runner", side_effect=fake_runtime),
            patch.object(adapter_main, "_gateway_state_soul_writer", side_effect=lambda ws, s: None),
            patch.object(
                adapter_main._OPENCLAW_AGENT_PROVIDER,
                "gateway_agents_list",
                side_effect=lambda **_kw: list(runtime_agents),
            ),
        ):
            first = client.post(
                "/api/openclaw-adapter/agents/persona-opinion/ensure",
                json=payload,
                headers=headers,
            )
            assert first.status_code == 201, first.text
            assert runtime_agents[0]["model"] == {
                "primary": "openai/gpt-5.5",
                "fallbacks": [],
            }
            # Simulate an external config downgrade back to a bare string and
            # verify that even a cached replay restores the structured model policy:
            runtime_agents[0]["model"] = "openai/gpt-5.5"
            replay = client.post(
                "/api/openclaw-adapter/agents/persona-opinion/ensure",
                json=payload,
                headers={**headers, "X-Request-Id": "req-hard-pin-2"},
            )
            assert replay.status_code == 201, replay.text
            assert runtime_agents[0]["model"] == {
                "primary": "openai/gpt-5.5",
                "fallbacks": [],
            }

    def test_ensure_rejects_out_of_pool_model_routing_fail_closed(self):
        ops_client_mod = self._load_bff_ops_client()
        OpenClawOpsClient = ops_client_mod.OpenClawOpsClient
        OpenClawOpsClientError = ops_client_mod.OpenClawOpsClientError

        client, adapter_main = self._client()
        admission = self._opinion_admission(adapter_main)
        ops_client = OpenClawOpsClient(
            base_url="http://openclaw-gateway-adapter:8104",
            service_token="adapter-secret",
            service_auth_required=True,
        )
        with pytest.raises(OpenClawOpsClientError) as excinfo:
            ops_client.ensure_persona_opinion_agent(
                admission,
                persona_profile={
                    "persona_id": admission["persona_id"],
                    "name": "Bad Model Persona",
                    "preferred_model": "outside-pool/model-x",
                },
            )
        assert excinfo.value.status_code == 400
        assert excinfo.value.error_code == "OPENCLAW_PERSONA_MODEL_ROUTING_INVALID"

        with (
            patch.object(adapter_main, "_ASSISTANT_SERVICE_TOKEN", "adapter-secret"),
            patch.object(adapter_main, "_ASSISTANT_SERVICE_AUTH_REQUIRED", True),
        ):
            resp = client.post(
                "/api/openclaw-adapter/agents/persona-opinion/ensure",
                json={
                    **admission,
                    "model_routing": {"mode": "hard_pin", "model": "outside-pool/model-x"},
                },
                headers={
                    "X-Pantheon-Service-Token": "adapter-secret",
                    "Idempotency-Key": "bad-model-key",
                    "X-Request-Id": "bad-model-req",
                },
            )
        assert resp.status_code == 422
