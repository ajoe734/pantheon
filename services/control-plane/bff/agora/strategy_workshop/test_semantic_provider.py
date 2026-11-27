"""Restricted provider wiring and fail-closed reconstruction contracts."""
from copy import deepcopy

import pytest

from . import semantic_provider as provider
from .reconstruction import StrategyMap, SemanticReconstructionDraft, reconstruct_strategy_from_events
from .runner import run_reconstruction_worker
from .store import MemoryWorkshopStore
from services.research.strategy_spec.test_models import _strategy_spec_payload


def draft():
    return {
        "strategy_map": {"hypothesis": {"status": "partial", "summary": "Not specified"}},
        "explicit_facts": [], "inferences": [], "assumptions": [], "contradictions": [],
        "next_best_question": {"question_id": "q1", "text": "請先說明你的假設？",
                               "resolves": ["hypothesis"], "why_now": "尚未提出策略"},
        "strategy_spec": None,
    }


def envelope(payload):
    return {"status": "ok", "data": {"provider": "openclaw", "status": "completed",
                                       "output": {"structured_data": payload}}}


def invoke(messages=None):
    return provider.reconstruct_strategy_with_agent(
        workshop_id="ws1", sequence_no=1, events=[{"event_id": "e1", "sequence_no": 1}],
        messages_content=messages or ["沒有定義買進訊號，也尚未決定停損，不要幫我補上。"],
        tenant_id="tenant1", user_id="user1",
    )


def test_real_worker_calls_restricted_provider_and_preserves_semantic_output(monkeypatch):
    calls = []
    payload = draft()
    def request(self, method, path, **kw):
        calls.append((method, path, kw))
        return envelope(payload)
    monkeypatch.setattr(provider.OpenClawOpsClient, "_request", request)
    result = invoke()
    assert result.strategy_map.hypothesis.status == "partial"
    assert result.next_best_question.text == payload["next_best_question"]["text"]
    assert result.completeness.grade == "insufficient"
    assert result.draft_proposal is None
    method, path, kw = calls[0]
    assert method == "POST" and path.endswith("/openclaw/structured")
    assert "沒有定義買進訊號" in kw["body"]["prompt"]
    assert "tools" not in kw["body"] and "agent_id" not in kw["body"]
    assert kw["headers"]["X-Operator-Id"] == "user1"
    assert kw["timeout_seconds"] > 0
    assert result.provider_lineage["engine"] == provider.ENGINE_VERSION
    assert len(result.provider_lineage["input_sha256"]) == 64


@pytest.mark.parametrize("bad", [None, {}, {"status": "provider_error"}, envelope({}),
                                     envelope(draft() | {"completeness": {"grade": "trading_room_ready"}})])
def test_provider_malformed_or_authority_claims_fail_closed(monkeypatch, bad):
    monkeypatch.setattr(provider.OpenClawOpsClient, "_request", lambda *a, **kw: bad)
    with pytest.raises(provider.ReconstructionProviderError):
        invoke()


@pytest.mark.parametrize("citations", [None, [], [0], [2], [True], ["1"]])
def test_confirmed_fields_require_valid_message_citations(monkeypatch, citations):
    payload = draft()
    payload["strategy_map"]["hypothesis"] = {
        "status": "confirmed", "summary": "assertion", "details": {"message_numbers": citations},
    }
    monkeypatch.setattr(provider.OpenClawOpsClient, "_request", lambda *a, **kw: envelope(payload))
    with pytest.raises(provider.ReconstructionProviderError):
        invoke()


def test_provider_timeout_is_failed_card_and_retry_is_not_stale_success(monkeypatch):
    store = MemoryWorkshopStore()
    store.create_session({"workshop_id": "ws1", "tenant_id": "tenant1", "user_id": "user1"})
    store.create_event({"workshop_id": "ws1", "actor_type": "operator", "event_type": "message", "redacted_summary": "No entry defined"})
    def timeout(*a, **kw):
        raise provider.OpenClawOpsClientError("upstream secret detail", status_code=504, error_code="TIMEOUT")
    monkeypatch.setattr(provider.OpenClawOpsClient, "_request", timeout)
    kwargs = dict(store=store, canonical=None, workshop_id="ws1", tenant_id="tenant1", user_id="user1")
    with pytest.raises(provider.ReconstructionProviderError) as exc:
        run_reconstruction_worker(**kwargs)
    assert exc.value.status_code == 504
    assert "secret" not in str(exc.value)
    card = store.list_workshop_cards("ws1")[0]
    assert card["status"] == "failed" and "reconstruction" not in card["payload"]
    monkeypatch.setattr(provider.OpenClawOpsClient, "_request", lambda *a, **kw: envelope(draft()))
    assert run_reconstruction_worker(**kwargs)["job_status"] == "completed"
    monkeypatch.setattr(provider.OpenClawOpsClient, "_request", lambda *a, **kw: pytest.fail("replayed provider"))
    assert run_reconstruction_worker(**kwargs)["job_status"] == "replayed"
    with pytest.raises(ValueError, match="scope mismatch"):
        run_reconstruction_worker(**(kwargs | {"tenant_id": "other"}))


@pytest.mark.parametrize("mutation", ["invalid", "approved", "waive", "contradiction", "assumption", "uncited", "universe_mismatch", "cadence_mismatch", "valid"])
def test_only_valid_consistent_semantics_can_propose_a_draft_never_trade_ready(mutation):
    payload = draft()
    payload["strategy_map"] = {name: {"status": "confirmed", "summary": name,
                                       "details": {"message_numbers": [1]}}
                               for name in StrategyMap.model_fields}
    spec = deepcopy(_strategy_spec_payload())
    spec["lifecycle_state"] = "draft"
    spec["governance"]["approval_required"] = True
    payload["strategy_spec"] = spec
    payload["strategy_map"]["universe"]["details"].update(deepcopy(spec["market_scope"]))
    payload["strategy_map"]["exit_rules"]["details"]["rebalance_cadence"] = spec["execution_profile"].get("rebalance_cadence")
    if mutation == "uncited":
        payload["strategy_map"]["hypothesis"]["details"] = {}
    elif mutation == "universe_mismatch":
        payload["strategy_map"]["universe"]["details"]["symbols"] = ["DIFFERENT"]
    elif mutation == "cadence_mismatch":
        payload["strategy_map"]["exit_rules"]["details"]["rebalance_cadence"] = "different"
    elif mutation == "invalid":
        spec.pop("market_scope")
    elif mutation == "approved":
        spec["lifecycle_state"] = "active"
    elif mutation == "waive":
        spec["governance"]["approval_required"] = False
    elif mutation == "contradiction":
        payload["contradictions"] = ["buy and do not buy"]
    elif mutation == "assumption":
        payload["assumptions"] = ["assume 2% risk"]
    result = reconstruct_strategy_from_events(
        workshop_id="ws", sequence_no=1, events=[], messages_content=["fixture conversation"],
        semantic_draft=SemanticReconstructionDraft.model_validate(payload),
    )
    assert result.completeness.grade == ("draftable" if mutation == "valid" else "insufficient")
    assert (result.draft_proposal is not None) == (mutation == "valid")


@pytest.mark.parametrize("error,status", [(TimeoutError("private detail"), 504),
                                          (ValueError("private JSON failure"), 502),
                                          (ConnectionError("private connection failure"), 502)])
def test_unexpected_transport_errors_are_sanitized(monkeypatch, error, status):
    def fail(*a, **kw):
        raise error
    monkeypatch.setattr(provider.OpenClawOpsClient, "_request", fail)
    with pytest.raises(provider.ReconstructionProviderError) as exc:
        invoke()
    assert exc.value.status_code == status
    assert "private" not in str(exc.value)


def test_composed_schema_validates_real_specs_and_namespaces_refs(monkeypatch):
    from jsonschema import Draft202012Validator
    schema = provider.reconstruction_extraction_schema()
    Draft202012Validator.check_schema(schema)
    payload = draft() | {"strategy_spec": _strategy_spec_payload()}
    validator = Draft202012Validator(schema)
    validator.validate(payload)
    payload["strategy_spec"].pop("market_scope")
    assert list(validator.iter_errors(payload))

    # A future canonical schema may introduce local refs with names that also
    # appear in the reconstruction schema. Keep them in a separate namespace.
    monkeypatch.setattr(provider, "load_strategy_spec_schema", lambda: {
        "$id": "https://example.invalid/spec", "$schema": "http://json-schema.org/draft-07/schema#",
        "$defs": {"StrategyMap": {"type": "string", "const": "scoped"}},
        "type": "object", "required": ["value"],
        "properties": {"value": {"$ref": "#/$defs/StrategyMap"}},
    })
    validator = Draft202012Validator(provider.reconstruction_extraction_schema())
    validator.validate(draft() | {"strategy_spec": {"value": "scoped"}})
    assert list(validator.iter_errors(draft() | {"strategy_spec": {"value": "wrong"}}))


def test_content_changes_have_distinct_reconstruction_identity():
    kwargs = dict(workshop_id="ws", sequence_no=1, events=[], messages_content=["conversation"])
    first = reconstruct_strategy_from_events(**kwargs, semantic_draft=SemanticReconstructionDraft.model_validate(draft()))
    second_draft = draft() | {"inferences": ["Different interpretation"]}
    second = reconstruct_strategy_from_events(**kwargs, semantic_draft=SemanticReconstructionDraft.model_validate(second_draft))
    assert first.reconstruction_id != second.reconstruction_id


def test_input_bound_fails_before_provider_call(monkeypatch):
    monkeypatch.setattr(provider.OpenClawOpsClient, "_request", lambda *a, **kw: pytest.fail("provider called"))
    with pytest.raises(provider.ReconstructionProviderError) as exc:
        invoke(["x" * 65537])
    assert exc.value.status_code == 422


# --------------------------------------------------------------------------- #
# AGORA-WORKSHOP-RECONSTRUCTION-HOSTED-20261127: hosted 422 root cause.
# --------------------------------------------------------------------------- #
import json
from pathlib import Path

_ADAPTER_DIR = Path(__file__).resolve().parents[5] / "services" / "openclaw-gateway-adapter"


@pytest.fixture
def adapter(monkeypatch):
    """The real adapter provider module (no stub of its validator)."""
    import importlib

    monkeypatch.syspath_prepend(str(_ADAPTER_DIR))
    return importlib.import_module("assistant_openclaw_provider")

# A hosted-sized walkthrough description (the hosted text is private; this is
# the same shape: one long Chinese strategy message, ~790 chars / 1.9 kB UTF-8).
HOSTED_SIZED_MESSAGE = (
    "[走查] 我想做一個台股與美股科技龍頭的動能輪動策略。假設：過去 60 個交易日報酬排名前段的大型科技股，"
    "在接下來 20 個交易日仍有延續性，因為法人資金調整部位需要時間，散戶追價也會放大趨勢。"
    "標的池：AAPL、MSFT、NVDA、GOOGL、AMZN、META、TSM、2330.TW、2454.TW、2317.TW，日頻資料。"
    "資料需求：日 K 收盤價、成交量、還原權息價格、每日外資買賣超（台股）、VIX 指數作為市場情緒過濾。"
    "訊號定義：每週五收盤計算 60 日報酬與 20 日報酬的加權分數（0.7/0.3），扣除 5 日短期反轉，"
    "取分數前 3 名做多；若 VIX 大於 30 則整體曝險降到一半。進場：下週一開盤以限價單進場，限價為前收盤價上浮 0.3%，"
    "沒成交就放棄當週。出場：每週再平衡，掉出前 5 名就出場；單一標的自進場後跌幅超過 8% 立即停損；"
    "持有超過 40 個交易日仍無獲利也出場。部位：等權重，每檔不超過總資金 20%，總曝險不超過 60%，"
    "現金部位放短期國庫券 ETF。風控：單日組合虧損超過 2.5% 暫停新進場一週；最大回撤超過 15% 全部出場並人工檢討；"
    "財報公布前兩天不新進場。成本：台股手續費 0.1425% 打六折、證交稅 0.3%，美股每股 0.005 美元，滑價各假設 5bp；"
    "這些都是大型股，流動性不是問題，但部位規模上限先設 500 萬台幣。驗證：用 2015 到 2024 年資料回測，"
    "2015-2021 做樣本內、2022-2024 做樣本外，看年化報酬、夏普、最大回撤、換手率，並做參數敏感度（40/60/90 日）。"
    "失效條件：如果樣本外夏普低於 0.5，或連續兩季跑輸等權重基準超過 5%，就視為失效。"
    "治理：先只做紙上交易，任何實盤都需要我另外確認。我不確定 VIX 門檻 30 是否合適，也還沒決定台股與美股的資金比例。"
)


def hosted_sized_draft():
    from services.research.strategy_spec.test_models import _strategy_spec_payload

    summaries = {
        "hypothesis": "60 日動能在大型科技股有 20 日延續性（法人調整部位、散戶追價）",
        "universe": "AAPL、MSFT、NVDA、GOOGL、AMZN、META、TSM、2330.TW、2454.TW、2317.TW，日頻",
        "data_requirements": "日 K、成交量、還原權息價、台股外資買賣超、VIX",
        "signal_definition": "週五 60 日與 20 日報酬 0.7/0.3 加權，扣 5 日反轉，前 3 名做多；VIX>30 曝險減半",
        "entry_rules": "週一開盤限價（前收 +0.3%）進場，未成交放棄當週",
        "exit_rules": "每週再平衡，掉出前 5 名出場；-8% 停損；40 日無獲利出場",
        "position_sizing": "等權，單檔 ≤20%，總曝險 ≤60%，現金放短期國庫券 ETF",
        "risk_controls": "單日虧損 >2.5% 暫停一週；回撤 >15% 全出場檢討；財報前兩天不進場",
        "cost_liquidity_capacity": "台股手續費六折與證交稅、美股每股 0.005 美元、滑價 5bp；規模上限 500 萬台幣",
        "validation_plan": "2015-2021 樣本內、2022-2024 樣本外；夏普、回撤、換手；40/60/90 敏感度",
        "regime_invalidation": "樣本外夏普 <0.5 或連兩季落後等權基準 >5% 即失效",
        "governance_constraints": "只做紙上交易，實盤需另行確認",
    }
    strategy_map = {name: {"status": "confirmed", "summary": text, "details": {"message_numbers": [1]}}
                    for name, text in summaries.items()}
    strategy_map["signal_definition"]["status"] = "partial"
    strategy_map["signal_definition"]["details"]["open_question"] = "VIX 門檻 30 尚未確認"
    strategy_map["position_sizing"]["status"] = "partial"
    strategy_map["position_sizing"]["details"]["open_question"] = "台股與美股資金比例未決定"
    spec = _strategy_spec_payload()
    spec["lifecycle_state"] = "draft"
    spec["governance"]["approval_required"] = True
    return {
        "strategy_map": strategy_map,
        "explicit_facts": [summaries[name] for name in ("hypothesis", "universe", "entry_rules", "exit_rules",
                                                         "risk_controls", "governance_constraints")],
        "inferences": ["策略以週為再平衡週期", "曝險上限暗示保守的資金配置"],
        "assumptions": ["VIX 門檻 30 由交易員暫定"],
        "contradictions": [],
        "next_best_question": {
            "question_id": "nbq-allocation", "text": "台股與美股的資金比例要怎麼分配？VIX 30 這個門檻你想用回測決定嗎？",
            "resolves": ["position_sizing", "signal_definition"],
            "why_now": "這兩個未決參數會改變部位上限與訊號過濾，決定後才能形成可研究的草稿",
        },
        "strategy_spec": spec,
    }


def test_root_cause_pydantic_refs_were_refused_by_the_adapter_validator(adapter):
    """Hosted 422: the draft schema carried `$defs`/`$ref`, which the adapter
    refuses (OPENCLAW_TOOL_ARGS_SCHEMA_MISMATCH) after the paid answer."""
    OpenClawProviderError = adapter.OpenClawProviderError
    _validate_extraction_arguments = adapter._validate_extraction_arguments
    unsupported_schema_keyword_paths = adapter.unsupported_schema_keyword_paths

    raw = SemanticReconstructionDraft.model_json_schema()
    assert unsupported_schema_keyword_paths(raw)
    with pytest.raises(OpenClawProviderError) as exc:
        _validate_extraction_arguments(hosted_sized_draft() | {"strategy_spec": None}, raw)
    assert (exc.value.status_code, exc.value.error_code) == (422, "OPENCLAW_TOOL_ARGS_SCHEMA_MISMATCH")


def test_sent_schema_passes_the_real_adapter_validator_and_agrees_with_jsonschema(adapter):
    from jsonschema import Draft202012Validator
    OpenClawProviderError = adapter.OpenClawProviderError
    _validate_extraction_arguments = adapter._validate_extraction_arguments
    unsupported_schema_keyword_paths = adapter.unsupported_schema_keyword_paths

    schema = provider.reconstruction_extraction_schema()
    assert unsupported_schema_keyword_paths(schema) == []
    assert "$ref" not in json.dumps(schema) and "$defs" not in json.dumps(schema)
    validator = Draft202012Validator(schema)
    full = hosted_sized_draft()
    for answer in (full, full | {"strategy_spec": None}, draft()):
        _validate_extraction_arguments(answer, schema)
        validator.validate(answer)
    for broken in (
        {k: v for k, v in full.items() if k != "next_best_question"},
        full | {"strategy_map": {"hypothesis": {"status": "certain"}}},
        full | {"completeness": {"grade": "trading_room_ready"}},
        full | {"strategy_spec": {k: v for k, v in full["strategy_spec"].items() if k != "market_scope"}},
    ):
        with pytest.raises(OpenClawProviderError):
            _validate_extraction_arguments(broken, schema)
        assert list(validator.iter_errors(broken))


def test_hosted_sized_conversation_validates_through_the_real_adapter_provider(monkeypatch, adapter):
    """The BFF request body runs through the real adapter `invoke_structured`
    (only the Gateway HTTP stream is faked) and the draft is published."""
    from unittest.mock import patch
    AssistantOpenClawProvider = adapter.AssistantOpenClawProvider

    answer_text = json.dumps(hosted_sized_draft(), ensure_ascii=False)
    sent = {}

    class _SSE:
        def __init__(self, events):
            self._lines = [("data: " + json.dumps(e) + "\n").encode() for e in events] + [b"data: [DONE]\n"]

        def __iter__(self):
            return iter(self._lines)

        def close(self):
            pass

    def gateway(req, timeout=None, deadline=None):
        sent["gateway_body"] = json.loads(req.data.decode("utf-8"))
        return _SSE([{"type": "response.output_text.done", "text": answer_text},
                     {"type": "response.completed", "response": {"status": "completed", "id": "r1",
                      "usage": {"input_tokens": 3, "output_tokens": 2300, "total_tokens": 9800}}}])

    adapter_provider = AssistantOpenClawProvider(gateway_url="ws://openclaw-gateway:18789", agent_id="main",
                                        token="t", _which_func=lambda _: None,
                                        _run_func=lambda *a, **k: pytest.fail("no subprocess"))

    def request(self, method, path, *, body, headers, **kwargs):
        sent["bff_body"] = body
        with patch.object(adapter, "_urlopen_with_deadline", gateway):
            result = adapter_provider.invoke_structured(
                body["prompt"], extraction_schema=body["extraction_schema"],
                operator_id=headers["X-Operator-Id"], trace_id=headers["X-Trace-Id"],
                max_output_tokens=body["max_output_tokens"],
            )
        return {"status": "ok", "data": result.to_dict()}

    monkeypatch.setattr(provider.OpenClawOpsClient, "_request", request)
    result = provider.reconstruct_strategy_with_agent(
        workshop_id="ws-hosted", sequence_no=1, events=[{"event_id": "e1", "sequence_no": 1}],
        messages_content=[HOSTED_SIZED_MESSAGE], tenant_id="tenant1", user_id="admintest",
    )
    assert result.strategy_map.hypothesis.status == "confirmed"
    assert result.next_best_question.question_id == "nbq-allocation"
    assert sent["gateway_body"]["max_output_tokens"] == provider.DEFAULT_RECONSTRUCTION_MAX_OUTPUT_TOKENS
    # The answer is well above the 1000-token interactive cap but inside the
    # reconstruction budget (bytes/3 is the repo's CJK token estimate).
    assert 1000 < len(answer_text.encode("utf-8")) // 3 < provider.DEFAULT_RECONSTRUCTION_MAX_OUTPUT_TOKENS


@pytest.mark.parametrize("raw,expected", [(None, 6144), ("3072", 3072), ("100", 1024), ("99999", 8192), ("x", 6144)])
def test_reconstruction_output_budget_is_sent_and_bounded(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("PANTHEON_WORKSHOP_RECONSTRUCTION_MAX_OUTPUT_TOKENS", raising=False)
    else:
        monkeypatch.setenv("PANTHEON_WORKSHOP_RECONSTRUCTION_MAX_OUTPUT_TOKENS", raw)
    bodies = []
    monkeypatch.setattr(provider.OpenClawOpsClient, "_request",
                        lambda self, *a, body, **kw: bodies.append(body) or envelope(draft()))
    invoke()
    assert bodies[0]["max_output_tokens"] == expected


@pytest.mark.parametrize("status,code,reason,bff_status", [
    (422, "OPENCLAW_TOOL_ARGS_SCHEMA_MISMATCH", "provider_output_schema_mismatch", 502),
    (422, "OPENCLAW_TOOL_ARGS_INVALID_JSON", "provider_output_invalid_json", 502),
    (422, "OPENCLAW_EXTRACTION_SCHEMA_UNSUPPORTED", "extraction_schema_unsupported", 502),
    (503, "OPENCLAW_STRUCTURED_POLICY_DENIED", "provider_policy_denied", 502),
    (504, "OPENCLAW_RESPONSES_TIMEOUT", "provider_timeout", 504),
    (503, "OPENCLAW_ADAPTER_UNREACHABLE", "provider_unavailable", 503),
    (500, "something odd with spaces", "provider_error", 502),
])
def test_adapter_error_codes_map_to_stable_reasons_without_upstream_text(monkeypatch, status, code, reason, bff_status):
    def fail(*a, **kw):
        raise provider.OpenClawOpsClientError("upstream secret text", status_code=status, error_code=code)
    monkeypatch.setattr(provider.OpenClawOpsClient, "_request", fail)
    with pytest.raises(provider.ReconstructionProviderError) as exc:
        invoke()
    assert (exc.value.reason, exc.value.status_code) == (reason, bff_status)
    assert exc.value.adapter_error_code == (None if " " in code else code)
    assert "secret" not in str(exc.value)
