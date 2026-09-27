"""Unit and contract tests for semantic extraction contract and client.

SIMPLIFY-EXTRACTION-CONTRACT-FOUNDATION-001:
Tests typed semantic extraction contract, deterministic baseline, span verification,
bounded failure semantics (invalid schema, wrong tool, missing support, refusal,
incomplete response, timeout, budget breach), and bounded retry policy.
"""

from __future__ import annotations

import http.server
import json
import threading
import time
from typing import Any, Dict
import uuid
import pytest

from services.source_ingestion.interaction_intent_classifier import (
    InteractionPrimaryIntent,
)
from services.source_ingestion.semantic_extraction import (
    _MAX_TURN_DEADLINE_SECONDS,
    _TARGET_P95_SECONDS,
    AbstentionReason,
    DeterministicBaselineExtractor,
    ExtractionFailureCode,
    ExtractionTaskType,
    IntentExtractionPayload,
    SemanticExtractionAdmission,
    SemanticExtractionRequest,
    SemanticExtractionResult,
    SourceSpan,
    StrategySeedExtractionPayload,
    TradeLessonExtractionPayload,
    compute_schema_id,
    get_semantic_extraction_json_schema,
)
from services.source_ingestion.semantic_extraction_client import (
    SemanticExtractionClient,
    SemanticExtractionClientError,
)


def _base_req(**overrides) -> SemanticExtractionRequest:
    data = {
        "source_id": "test-src-001",
        "text": "台股動能策略：突破20日均線且成交量大於5日均量時買進台指期，停損設為2%，持有期5天。",
        "task_type": ExtractionTaskType.COMPREHENSIVE,
        "tenant_id": "tenant_test",
        "source_type": "internal_note",
        "source_status": "raw",
        "license_scope": "internal",
    }
    data.update(overrides)
    return SemanticExtractionRequest(**data)


class TestSemanticExtractionContract:
    def test_schema_generation_and_id_stability(self):
        schema_comprehensive = get_semantic_extraction_json_schema(ExtractionTaskType.COMPREHENSIVE)
        id1 = compute_schema_id(schema_comprehensive)
        id2 = compute_schema_id(schema_comprehensive)
        assert id1 == id2
        assert len(id1) == 16
        assert "intent" in schema_comprehensive["properties"]
        assert "strategy_seed" in schema_comprehensive["properties"]
        assert "trade_lesson" in schema_comprehensive["properties"]
        assert "source_spans" in schema_comprehensive["properties"]

        schema_intent_only = get_semantic_extraction_json_schema(ExtractionTaskType.INTENT)
        assert "intent" in schema_intent_only["properties"]
        assert "strategy_seed" not in schema_intent_only["properties"]

    def test_source_span_validation(self):
        text = "Hello world from Pantheon extraction"
        valid_span = SourceSpan(field_name="test", start_char=6, end_char=11, exact_text="world")
        assert valid_span.is_valid(text) is True

        mismatch_span = SourceSpan(field_name="test", start_char=6, end_char=11, exact_text="earth")
        assert mismatch_span.is_valid(text) is False

        out_of_bounds = SourceSpan(field_name="test", start_char=50, end_char=60, exact_text="none")
        assert out_of_bounds.is_valid(text) is False

        inverted_indices = SourceSpan(field_name="test", start_char=10, end_char=5, exact_text="none")
        assert inverted_indices.is_valid(text) is False

    def test_result_dict_roundtrip(self):
        res = SemanticExtractionResult(
            extraction_id="ext-123",
            source_id="src-001",
            tenant_id="tenant_a",
            task_type="comprehensive",
            status="completed",
            is_abstained=False,
            intent=IntentExtractionPayload(
                primary_intent=InteractionPrimaryIntent.STRATEGY_HYPOTHESIS.value,
                confidence=0.92,
                reason="Clear trend hypothesis",
            ),
            strategy_seed=StrategySeedExtractionPayload(
                hypothesis="突破20日均線買進",
                asset_class=("futures",),
                market_scope=("tw",),
                required_data=("ohlcv",),
                confidence=0.88,
            ),
            trade_lesson=TradeLessonExtractionPayload(
                scope="risk",
                proposed_change="停損設為2%",
                confidence=0.85,
            ),
            supported_fields=("intent.primary_intent", "strategy_seed.hypothesis"),
            source_spans=(
                SourceSpan("strategy_seed.hypothesis", 0, 8, "突破20日均線買進"),
            ),
            usage={"input_tokens": 120, "output_tokens": 45, "total_tokens": 165},
            cost_usd=0.00075,
            latency_ms=150.0,
        )

        d = res.to_dict()
        assert d["extraction_id"] == "ext-123"
        assert d["intent"]["primary_intent"] == "strategy_hypothesis"
        assert d["strategy_seed"]["asset_class"] == ["futures"]
        assert len(d["source_spans"]) == 1

        restored = SemanticExtractionResult.from_dict(d)
        assert restored.extraction_id == res.extraction_id
        assert restored.intent.primary_intent == res.intent.primary_intent
        assert restored.strategy_seed.asset_class == res.strategy_seed.asset_class
        assert restored.source_spans[0].exact_text == "突破20日均線買進"


class TestDeterministicBaselineExtractor:
    def test_english_mean_reversion_intent_extraction(self):
        req = _base_req(
            text="US equities mean reversion strategy: buy S&P 500 stocks when RSI < 25, exit when RSI > 50, using daily OHLCV candlestick data.",
            task_type=ExtractionTaskType.INTENT,
        )
        res = DeterministicBaselineExtractor.extract(req)
        assert res.status == "completed"
        assert res.is_abstained is False
        assert res.intent is not None
        assert res.intent.primary_intent == InteractionPrimaryIntent.STRATEGY_HYPOTHESIS.value
        assert res.strategy_seed is None
        assert len(res.source_spans) > 0
        for span in res.source_spans:
            assert span.is_valid(req.text) is True

    def test_traditional_chinese_unmatched_abstains(self):
        req = _base_req(
            text="台股期貨動能突破策略：當台指期突破20日高點且成交量放大時買進，停損2%，使用日K與價量資料。"
        )
        res = DeterministicBaselineExtractor.extract(req)
        # Production baseline has no Traditional Chinese keywords, faithfully returns low confidence / abstention
        assert res.is_abstained is True
        assert res.abstention_reason in (
            AbstentionReason.INSUFFICIENT_EVIDENCE.value,
            AbstentionReason.CONFIDENCE_BELOW_THRESHOLD.value,
        )

    def test_non_strategy_abstention(self):
        req = _base_req(
            text="今日伺服器例行性維護公告：系統將於午夜12點進行重啟，預計耗時30分鐘。",
            task_type=ExtractionTaskType.STRATEGY_SEED,
        )
        res = DeterministicBaselineExtractor.extract(req)
        assert res.is_abstained is True
        assert res.abstention_reason == AbstentionReason.UNSUPPORTED_SOURCE.value


class TestSemanticExtractionClientBoundedFailures:
    def test_invalid_schema_mapped_to_failure(self):
        # Transport returns invalid tool arguments that violate schema
        def bad_schema_transport(payload: dict) -> dict:
            raise SemanticExtractionClientError(
                "tool call arguments are not valid JSON",
                ExtractionFailureCode.INVALID_SCHEMA,
                422,
            )

        client = SemanticExtractionClient(transport_fn=bad_schema_transport)
        req = _base_req()
        res = client.extract(req)
        assert res.status == "failed"
        assert res.failure_code == ExtractionFailureCode.INVALID_SCHEMA.value

    def test_wrong_tool_mapped_to_failure(self):
        # Transport returns wrong tool name
        def wrong_tool_transport(payload: dict) -> dict:
            raise SemanticExtractionClientError(
                "tool call name 'execute_shell' does not match 'emit_extraction'",
                ExtractionFailureCode.WRONG_TOOL,
                502,
            )

        client = SemanticExtractionClient(transport_fn=wrong_tool_transport)
        req = _base_req()
        res = client.extract(req)
        assert res.status == "failed"
        assert res.failure_code == ExtractionFailureCode.WRONG_TOOL.value

    def test_timeout_deadline_mapped_to_failure(self):
        def slow_transport(payload: dict) -> dict:
            time.sleep(0.05)
            raise SemanticExtractionClientError(
                "Invocation deadline exhausted",
                ExtractionFailureCode.TIMEOUT,
                504,
            )

        client = SemanticExtractionClient(
            transport_fn=slow_transport,
            target_timeout_seconds=0.01,
            max_deadline_seconds=0.02,
        )
        req = _base_req(timeout_seconds=0.01)
        res = client.extract(req)
        assert res.is_abstained is True
        assert res.abstention_reason == AbstentionReason.TIMEOUT.value
        assert res.failure_code == ExtractionFailureCode.TIMEOUT.value

    def test_budget_breach_mapped_to_abstention(self):
        # Model produced excessive tokens
        def excessive_tokens_transport(payload: dict) -> dict:
            return {
                "output": {
                    "structured_data": {
                        "is_abstained": False,
                        "intent": {
                            "primary_intent": "strategy_hypothesis",
                            "confidence": 0.95,
                            "reason": "Clear hypothesis",
                        },
                        "source_spans": [],
                    },
                    "usage": {
                        "input_tokens": 10000,  # exceeds 8000 cap
                        "output_tokens": 200,
                    },
                }
            }

        client = SemanticExtractionClient(transport_fn=excessive_tokens_transport)
        req = _base_req()
        res = client.extract(req)
        assert res.is_abstained is True
        assert res.abstention_reason == AbstentionReason.BUDGET_BREACH.value
        assert res.failure_code == ExtractionFailureCode.BUDGET_BREACH.value
        assert res.cost_usd > 0.0

    def test_missing_critical_support_triggers_abstention(self):
        # Transport returns extracted hypothesis but zero valid supporting spans
        def unsupported_transport(payload: dict) -> dict:
            return {
                "output": {
                    "structured_data": {
                        "is_abstained": False,
                        "strategy_seed": {
                            "hypothesis": "Imaginary Arbitrage Strategy",
                            "asset_class": ["crypto"],
                            "market_scope": ["global"],
                            "required_data": ["orderbook"],
                            "confidence": 0.9,
                        },
                        # Invalid span pointing nowhere in the text
                        "source_spans": [
                            {"field_name": "strategy_seed.hypothesis", "start_char": 999, "end_char": 1020, "exact_text": "Imaginary Arbitrage Strategy"}
                        ],
                    },
                    "usage": {"input_tokens": 100, "output_tokens": 50},
                }
            }

        client = SemanticExtractionClient(transport_fn=unsupported_transport)
        req = _base_req()
        res = client.extract(req)
        assert res.is_abstained is True
        assert res.abstention_reason == AbstentionReason.MISSING_CRITICAL_SUPPORT.value
        assert res.failure_code == ExtractionFailureCode.MISSING_SUPPORT.value
        assert "strategy_seed.hypothesis" in res.missing_fields

    def test_bounded_retry_policy_retries_once_then_fails(self):
        call_count = 0

        def flaky_transport(payload: dict) -> dict:
            nonlocal call_count
            call_count += 1
            raise RuntimeError("Transient gateway network drop")

        client = SemanticExtractionClient(transport_fn=flaky_transport)
        req = _base_req(max_retries=1)
        res = client.extract(req)
        assert call_count == 2  # exactly 1 initial attempt + 1 bounded retry
        assert res.status == "failed"
        assert res.retry_count == 1
        assert res.failure_code == ExtractionFailureCode.TRANSPORT_ERROR.value

    def test_successful_turn_returns_valid_result(self):
        def successful_transport(payload: dict) -> dict:
            prompt_text = payload["prompt"]
            # Ground the span in the actual source text
            span_text = "突破20日均線"
            start_idx = prompt_text.find(span_text)
            return {
                "output": {
                    "structured_data": {
                        "is_abstained": False,
                        "intent": {
                            "primary_intent": "strategy_hypothesis",
                            "confidence": 0.94,
                            "reason": "Quantitative breakout momentum",
                        },
                        "strategy_seed": {
                            "hypothesis": "突破20日均線買進台指期",
                            "asset_class": ["futures"],
                            "market_scope": ["tw"],
                            "required_data": ["ohlcv"],
                            "confidence": 0.91,
                        },
                        "source_spans": [
                            {"field_name": "intent.primary_intent", "start_char": req.text.find("突破20日均線"), "end_char": req.text.find("突破20日均線") + len("突破20日均線"), "exact_text": "突破20日均線"},
                            {"field_name": "strategy_seed.hypothesis", "start_char": req.text.find("突破20日均線"), "end_char": req.text.find("突破20日均線") + len("突破20日均線"), "exact_text": "突破20日均線"},
                        ],
                    },
                    "usage": {"input_tokens": 150, "output_tokens": 65},
                }
            }

        client = SemanticExtractionClient(transport_fn=successful_transport)
        req = _base_req()
        res = client.extract(req)
        assert res.status == "completed"
        assert res.is_abstained is False
        assert res.intent.primary_intent == "strategy_hypothesis"
        assert res.strategy_seed.asset_class == ("futures",)
        assert res.cost_usd > 0.0
        assert len(res.source_spans) == 2

    def test_real_transport_shape_empty_structured_data_fails_closed(self):
        def empty_transport(payload: dict) -> dict:
            return {"output": {"structured_data": {}}}

        client = SemanticExtractionClient(transport_fn=empty_transport)
        req = _base_req()
        res = client.extract(req)
        assert res.status == "failed"
        assert res.failure_code == ExtractionFailureCode.INVALID_SCHEMA.value

    def test_real_transport_shape_non_abstained_empty_spans_fails_closed(self):
        def empty_spans_transport(payload: dict) -> dict:
            return {
                "output": {
                    "structured_data": {
                        "is_abstained": False,
                        "intent": {
                            "primary_intent": "strategy_hypothesis",
                            "confidence": 0.9,
                        },
                        "source_spans": [],
                    }
                }
            }

        client = SemanticExtractionClient(transport_fn=empty_spans_transport)
        req = _base_req(task_type=ExtractionTaskType.INTENT)
        res = client.extract(req)
        assert res.status == "failed"
        assert res.failure_code == ExtractionFailureCode.INVALID_SCHEMA.value

    def test_real_transport_shape_invalid_intent_enum_fails_closed(self):
        def bad_enum_transport(payload: dict) -> dict:
            return {
                "output": {
                    "structured_data": {
                        "is_abstained": False,
                        "intent": {
                            "primary_intent": "INVALID_ENUM",
                            "confidence": 2.5,
                        },
                        "source_spans": [
                            {"field_name": "intent.primary_intent", "start_char": 0, "end_char": 4, "exact_text": "台股動能"}
                        ],
                    }
                }
            }

        client = SemanticExtractionClient(transport_fn=bad_enum_transport)
        req = _base_req(task_type=ExtractionTaskType.INTENT)
        res = client.extract(req)
        assert res.status == "failed"
        assert res.failure_code == ExtractionFailureCode.INVALID_SCHEMA.value

    def test_real_transport_shape_missing_payload_fails_closed(self):
        def missing_payload_transport(payload: dict) -> dict:
            return {
                "output": {
                    "structured_data": {
                        "is_abstained": False,
                        "source_spans": [
                            {"field_name": "intent.primary_intent", "start_char": 0, "end_char": 4, "exact_text": "台股動能"}
                        ],
                    }
                }
            }

        client = SemanticExtractionClient(transport_fn=missing_payload_transport)
        req = _base_req(task_type=ExtractionTaskType.INTENT)
        res = client.extract(req)
        assert res.status == "failed"
        assert res.failure_code == ExtractionFailureCode.INVALID_SCHEMA.value

    def test_real_transport_shape_refusal_status_maps_to_abstained(self):
        def refusal_transport(payload: dict) -> dict:
            return {
                "status": "refusal",
                "output": {
                    "refusal": "I cannot fulfill this request due to financial advice safety boundaries.",
                },
            }

        client = SemanticExtractionClient(transport_fn=refusal_transport)
        req = _base_req()
        res = client.extract(req)
        assert res.status == "abstained"
        assert res.is_abstained is True
        assert res.abstention_reason == AbstentionReason.MODEL_REFUSAL.value
        assert res.failure_code == ExtractionFailureCode.REFUSAL.value

    def test_real_transport_shape_absent_usage_preserves_none(self):
        def no_usage_transport(payload: dict) -> dict:
            return {
                "output": {
                    "structured_data": {
                        "is_abstained": True,
                        "abstention_reason": "insufficient_evidence",
                        "source_spans": [],
                    }
                }
            }

        client = SemanticExtractionClient(transport_fn=no_usage_transport)
        req = _base_req()
        res = client.extract(req)
        assert res.is_abstained is True
        assert res.usage is None
        assert res.cost_usd is None

    def test_loopback_http_slow_trickle_wall_clock_timeout(self):
        class SlowTrickleHandler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                for _ in range(20):
                    time.sleep(0.01)
                    try:
                        self.wfile.write(b" ")
                        self.wfile.flush()
                    except Exception:
                        break
                self.wfile.write(b"{}")
                self.wfile.flush()

            def log_message(self, format, *args):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), SlowTrickleHandler)
        port = server.server_port
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()

        client = SemanticExtractionClient(
            adapter_url=f"http://127.0.0.1:{port}",
            target_timeout_seconds=0.04,
            max_deadline_seconds=0.08,
        )
        req = _base_req(timeout_seconds=0.04, max_retries=0)
        t0 = time.monotonic()
        res = client.extract(req)
        elapsed = time.monotonic() - t0
        server.shutdown()

        assert res.is_abstained is True
        assert res.abstention_reason == AbstentionReason.TIMEOUT.value
        assert res.failure_code == ExtractionFailureCode.TIMEOUT.value
        assert elapsed < 0.2

    def test_always_abstain_client_known_answer_eval_metrics(self, tmp_path):
        from services.source_ingestion.evaluation.run_semantic_extraction_eval import run_evaluation

        def always_abstain_transport(payload: dict) -> dict:
            return {
                "output": {
                    "structured_data": {
                        "is_abstained": True,
                        "abstention_reason": "insufficient_evidence",
                        "source_spans": [],
                    }
                }
            }

        client = SemanticExtractionClient(transport_fn=always_abstain_transport)
        # Evaluate holdout with always-abstain client
        manifest = run_evaluation(
            split_filter="holdout",
            client=client,
            manifest_out=tmp_path / "always_abstain_manifest.json",
        )
        metrics = manifest["baseline_metrics"]
        # Must score 0.0 for field F1, 0.0 for critical support, 0.0 for source validity (NOT 1.0 / 100%)
        assert metrics["field_f1"] == 0.0
        assert metrics["critical_support_pct"] == 0.0
        assert metrics["source_validity_pct"] == 0.0

