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
            text="今天天氣真好，大家一起去散步踏青，不要討論任何交易話題。",
            task_type=ExtractionTaskType.INTENT,
        )
        res = DeterministicBaselineExtractor.extract(req)
        assert res.is_abstained is True
        assert res.abstention_reason in (
            AbstentionReason.INSUFFICIENT_EVIDENCE.value,
            AbstentionReason.CONFIDENCE_BELOW_THRESHOLD.value,
        )

    def test_non_strategy_abstention(self):
        req = _base_req(
            text="今日伺服器例行性維護公告：系統將於午夜12點進行重啟，預計耗時30分鐘。",
            task_type=ExtractionTaskType.INTENT,
        )
        res = DeterministicBaselineExtractor.extract(req)
        assert res.is_abstained is True
        assert res.abstention_reason in (
            AbstentionReason.INSUFFICIENT_EVIDENCE.value,
            AbstentionReason.AMBIGUOUS_INTENT.value,
        )

    def test_production_baseline_extracts_strategy_seed(self):
        req = _base_req(
            text="台股期貨動能突破策略：當台指期突破20日高點且成交量放大時買進，停損2%，使用日K與價量資料。",
            task_type=ExtractionTaskType.COMPREHENSIVE,
        )
        res = DeterministicBaselineExtractor.extract(req)
        assert res.strategy_seed is not None
        assert res.strategy_seed.hypothesis == "台股期貨動能突破策略"
        assert "futures" in res.strategy_seed.asset_class
        assert "tw" in res.strategy_seed.market_scope
        assert "ohlcv" in res.strategy_seed.required_data
        assert "strategy_seed.hypothesis" in res.supported_fields


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

        client = SemanticExtractionClient(transport_fn=excessive_tokens_transport, default_model="gpt-4o")
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

        client = SemanticExtractionClient(transport_fn=successful_transport, default_model="gpt-4o")
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

    def test_loopback_http_keepalive_content_length_does_not_stall(self):
        payload_data = {
            "status": "completed",
            "output": {
                "structured_data": {
                    "is_abstained": True,
                    "abstention_reason": "insufficient_evidence",
                    "source_spans": [],
                }
            },
        }
        raw_bytes = json.dumps({"status": "ok", "data": payload_data}).encode("utf-8")

        class KeepAliveHandler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw_bytes)))
                self.end_headers()
                self.wfile.write(raw_bytes)
                self.wfile.flush()
                # Server keeps connection open and sleeps without closing
                time.sleep(0.8)
                self.close_connection = True

            def log_message(self, format, *args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), KeepAliveHandler)
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        try:
            client = SemanticExtractionClient(adapter_url=f"http://127.0.0.1:{server.server_port}")
            req = _base_req(timeout_seconds=1.0)
            t0 = time.monotonic()
            res = client.extract(req)
            elapsed = time.monotonic() - t0
            assert res.status == "abstained"
            assert res.failure_code is None
            # Must complete well under the 0.8s keep-alive sleep
            assert elapsed < 0.4
        finally:
            server.shutdown()
            t.join()
            server.server_close()

    def test_loopback_http_chunked_transfer_decoding(self):
        payload_data = {
            "status": "completed",
            "output": {
                "structured_data": {
                    "is_abstained": True,
                    "abstention_reason": "insufficient_evidence",
                    "source_spans": [],
                }
            },
        }
        raw_bytes = json.dumps({"status": "ok", "data": payload_data}).encode("utf-8")

        class ChunkedHandler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                # Emit chunked response
                chunk_header = f"{len(raw_bytes):x}\r\n".encode("utf-8")
                self.wfile.write(chunk_header + raw_bytes + b"\r\n0\r\n\r\n")
                self.wfile.flush()
                self.close_connection = True

            def log_message(self, format, *args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), ChunkedHandler)
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        try:
            client = SemanticExtractionClient(adapter_url=f"http://127.0.0.1:{server.server_port}")
            req = _base_req(timeout_seconds=1.0)
            res = client.extract(req)
            assert res.status == "abstained"
            assert res.failure_code is None
        finally:
            server.shutdown()
            t.join()
            server.server_close()

    def test_loopback_http_error_trickle_deadline_timeout(self):
        class TrickleErrorHandler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                self.send_response(500)
                self.send_header("Content-Length", "20")
                self.end_headers()
                try:
                    for _ in range(20):
                        self.wfile.write(b" ")
                        self.wfile.flush()
                        time.sleep(0.01)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, format, *args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), TrickleErrorHandler)
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        try:
            client = SemanticExtractionClient(adapter_url=f"http://127.0.0.1:{server.server_port}")
            req = _base_req(timeout_seconds=0.05, max_retries=0)
            t0 = time.monotonic()
            res = client.extract(req)
            elapsed = time.monotonic() - t0
            assert res.is_abstained is True
            assert res.failure_code == ExtractionFailureCode.TIMEOUT.value
            # Must timeout near 0.05s rather than taking >= 0.20s
            assert elapsed < 0.15
        finally:
            server.shutdown()
            t.join()
            server.server_close()

    def test_provider_remaining_timeout_and_deadline_enforcement(self):
        passed_timeouts = []

        class MockProvider:
            def _gateway_call(self, *args, **kw):
                time.sleep(0.06)
                return {"valid": True, "config": {"agents": {"list": [{"id": "main", "tools": {"deny": ["*"]}}]}}}

            def invoke_structured(self, *args, **kw):
                passed_timeouts.append(kw.get("timeout_seconds"))
                time.sleep(0.06)
                return type("R", (), {
                    "to_dict": lambda _: {
                        "status": "completed",
                        "output": {
                            "structured_data": {
                                "is_abstained": True,
                                "abstention_reason": "insufficient_evidence",
                                "source_spans": [],
                            }
                        },
                    }
                })()

        provider = MockProvider()
        client = SemanticExtractionClient(provider=provider)
        req = _base_req(timeout_seconds=0.1, max_retries=0)
        t0 = time.monotonic()
        res = client.extract(req)
        elapsed = time.monotonic() - t0

        # invoke_structured must have received remaining budget (~0.04s), not the original 0.1s
        assert len(passed_timeouts) == 1
        assert passed_timeouts[0] < 0.08
        # Since policy (0.06s) + invoke (0.06s) = 0.12s > 0.1s budget, result must be timeout
        assert res.is_abstained is True
        assert res.failure_code == ExtractionFailureCode.TIMEOUT.value
        assert elapsed < 0.25

    def test_strict_support_validation_rejects_non_canonical_and_invalid_spans(self):
        client = SemanticExtractionClient(
            transport_fn=lambda _: {
                "status": "completed",
                "output": {
                    "structured_data": {
                        "is_abstained": False,
                        "strategy_seed": {
                            "hypothesis": "Buy unrelated lunar rocks",
                            "asset_class": ["equities"],
                            "market_scope": ["us"],
                            "required_data": ["ohlcv"],
                            "confidence": 0.9,
                        },
                        "source_spans": [
                            # Non-canonical field path
                            {"field_name": "bogus_hypothesis_suffix", "start_char": 0, "end_char": 10, "exact_text": "Momentum i"},
                            # Invalid offsets span
                            {"field_name": "strategy_seed.asset_class", "start_char": 999, "end_char": 1005, "exact_text": "equities"},
                        ],
                    }
                },
            }
        )
        req = _base_req(text="Momentum in US equities produces excess returns.")
        res = client.extract(req)
        # Non-canonical / invalid spans must not be silently ignored or accepted
        assert res.is_abstained is True
        assert res.abstention_reason == AbstentionReason.MISSING_CRITICAL_SUPPORT.value
        assert res.failure_code == ExtractionFailureCode.MISSING_SUPPORT.value

    def test_pricing_unknown_model_preserves_none_and_roundtrip(self):
        client = SemanticExtractionClient()
        # Verified cataloged model returns honest cost
        known_cost = client.calculate_cost(1000, 1000, model_id="gpt-4o")
        assert known_cost == 0.0125

        # Routing alias openclaw/main returns None (not synthetic rate)
        alias_cost = client.calculate_cost(1000, 1000, model_id="openclaw/main")
        assert alias_cost is None

        # Unknown model preserves None (honest tracking)
        unknown_cost = client.calculate_cost(1000, 1000, model_id="unknown-subscription-model")
        assert unknown_cost is None

        # None input tokens preserves None
        assert client.calculate_cost(None, 1000) is None

        # Result with unknown usage safely serializes and deserializes
        res = SemanticExtractionClient(
            transport_fn=lambda _: {
                "status": "completed",
                "output": {
                    "structured_data": {
                        "is_abstained": True,
                        "abstention_reason": "insufficient_evidence",
                        "source_spans": [],
                    }
                },
            }
        ).extract(_base_req())
        assert res.usage is None
        assert res.cost_usd is None
        data = res.to_dict()
        assert data["usage"] is None
        assert data["cost_usd"] is None
        roundtrip = SemanticExtractionResult.from_dict(data)
        assert roundtrip.usage is None
        assert roundtrip.cost_usd is None

    def test_corrupted_output_evaluator_rejects_bad_fields_and_identities(self, tmp_path):
        from services.source_ingestion.evaluation.run_semantic_extraction_eval import run_evaluation, DEFAULT_CASES_PATH

        cases = [json.loads(l) for l in DEFAULT_CASES_PATH.read_text().splitlines()]
        by_source = {c["input"]["source_id"]: c for c in cases}

        class CorruptedOutputExtractor:
            def extract(self, req):
                c = by_source[req.source_id]
                exp = c["expected"]
                abstain = exp.get("is_abstained", False) or not exp.get("should_admit", True)
                spans = [SourceSpan("intent.primary_intent", 0, len(req.text), req.text)]
                return SemanticExtractionResult(
                    extraction_id="probe",
                    source_id="WRONG-SOURCE",
                    tenant_id="WRONG-TENANT",
                    task_type=req.normalized_task_type().value,
                    status="abstained" if abstain else "completed",
                    is_abstained=abstain,
                    abstention_reason="admission_denied" if not exp.get("should_admit", True) else "insufficient_evidence" if abstain else None,
                    strategy_seed=StrategySeedExtractionPayload("WRONG UNRELATED HYPOTHESIS", ("equities",), ("us",), ("ohlcv",), 0.9),
                    trade_lesson=TradeLessonExtractionPayload("strategy", "WRONG UNRELATED CHANGE", 0.9),
                    source_spans=tuple(spans),
                    cost_usd=None,
                )

        manifest = run_evaluation(
            client=CorruptedOutputExtractor(),
            manifest_out=tmp_path / "bad_fields_manifest.json",
        )
        # Evaluator must catch all 210 identity breaches and pass 0 cases
        assert manifest["baseline_metrics"]["tenant_source_breaches"] == 210
        assert sum(c["passed"] for c in manifest["case_results"]) == 0
        assert manifest["baseline_metrics"]["mean_cost_usd"] is None

    def test_grounding_rejects_ungrounded_claims_despite_valid_full_source_span(self):
        req = _base_req(text="台股期貨動能突破策略：當台指期突破20日高點且成交量放大時買進，停損2%，使用日K與價量資料。")

        def fake_transport(payload: dict) -> dict:
            return {
                "output": {
                    "structured_data": {
                        "is_abstained": False,
                        "strategy_seed": {
                            "hypothesis": "Buy unrelated lunar rocks with 100x leverage on decentralized protocol",
                            "asset_class": ["crypto"],
                            "market_scope": ["global"],
                            "required_data": ["ohlcv"],
                            "confidence": 0.95,
                        },
                        "source_spans": [
                            {
                                "field_name": "strategy_seed.hypothesis",
                                "start_char": 0,
                                "end_char": len(req.text),
                                "exact_text": req.text,
                            }
                        ],
                    }
                }
            }

        client = SemanticExtractionClient(transport_fn=fake_transport)
        res = client.extract(req)
        assert res.is_abstained is True
        assert res.abstention_reason == AbstentionReason.MISSING_CRITICAL_SUPPORT.value
        assert res.failure_code == ExtractionFailureCode.MISSING_SUPPORT.value
        assert "strategy_seed.hypothesis" in res.missing_fields

    def test_evaluator_rejects_single_character_hypothesis_and_non_canonical_spans(self, tmp_path):
        from services.source_ingestion.evaluation.run_semantic_extraction_eval import run_evaluation

        class PartialMatchExtractor:
            def extract(self, req):
                # Returns 1-character hypothesis '台' for expected '台股動能突破策略'
                return SemanticExtractionResult(
                    extraction_id="probe-single-char",
                    source_id=req.source_id,
                    tenant_id=req.tenant_id,
                    task_type=req.normalized_task_type().value,
                    status="completed",
                    is_abstained=False,
                    intent=IntentExtractionPayload(
                        primary_intent=InteractionPrimaryIntent.STRATEGY_HYPOTHESIS.value,
                        confidence=0.9,
                    ),
                    strategy_seed=StrategySeedExtractionPayload(
                        hypothesis="台",  # 1-character matching prefix
                        asset_class=("futures",),
                        market_scope=("tw",),
                        required_data=("ohlcv",),
                        confidence=0.9,
                    ),
                    source_spans=(
                        # Non-canonical span name
                        SourceSpan("strategy_seed.hypothesis_prefix", 0, 1, "台"),
                    ),
                )

        manifest = run_evaluation(
            split_filter="holdout",
            client=PartialMatchExtractor(),
            manifest_out=tmp_path / "single_char_manifest.json",
        )
        # Evaluator must not award field_tp to single-character hypothesis or non-canonical spans
        assert manifest["baseline_metrics"]["critical_support_pct"] == 0.0
        assert manifest["baseline_metrics"]["source_validity_pct"] == 0.0

    def test_usage_metadata_parsing_and_failure_preservation(self):
        # 1. Missing output_tokens preserves None (does not fabricate 0)
        def partial_usage_transport(payload: dict) -> dict:
            return {
                "output": {
                    "structured_data": {
                        "is_abstained": True,
                        "abstention_reason": "insufficient_evidence",
                        "source_spans": [],
                    },
                    "usage": {"input_tokens": 500},
                }
            }

        client = SemanticExtractionClient(transport_fn=partial_usage_transport)
        res = client.extract(_base_req())
        assert res.usage is not None
        assert res.usage["input_tokens"] == 500
        assert res.usage["output_tokens"] is None
        assert res.usage["total_tokens"] is None

        # 2. String 'unknown' usage emits INVALID_SCHEMA without ValueError
        def malformed_usage_transport(payload: dict) -> dict:
            return {
                "output": {
                    "structured_data": {"is_abstained": True, "source_spans": []},
                    "usage": {"input_tokens": "unknown", "output_tokens": 100},
                }
            }

        client2 = SemanticExtractionClient(transport_fn=malformed_usage_transport)
        res2 = client2.extract(_base_req())
        assert res2.status == "failed"
        assert res2.failure_code == ExtractionFailureCode.INVALID_SCHEMA.value

        # 3. Usage preserved on explicit model refusal
        def refusal_transport(payload: dict) -> dict:
            return {
                "status": "refusal",
                "output": {
                    "usage": {"input_tokens": 200, "output_tokens": 10},
                },
            }

        client3 = SemanticExtractionClient(transport_fn=refusal_transport, default_model="gpt-4o")
        res3 = client3.extract(_base_req())
        assert res3.is_abstained is True
        assert res3.abstention_reason == AbstentionReason.MODEL_REFUSAL.value
        assert res3.usage is not None
        assert res3.usage["input_tokens"] == 200
        assert res3.usage["output_tokens"] == 10
        assert res3.cost_usd is not None
        assert res3.cost_usd > 0.0

    def test_corpus_zero_split_leakage_and_pairwise_grouping(self):
        from collections import defaultdict
        from services.source_ingestion.evaluation.run_semantic_extraction_eval import DEFAULT_CASES_PATH, _compute_sha256

        cases = [json.loads(line) for line in DEFAULT_CASES_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert len(cases) == 210

        # Check SHA256 of frozen corpus
        sha = _compute_sha256(DEFAULT_CASES_PATH)
        assert sha == "83a1fc0d6ae6aac771eb4c904f20b282e7d663137a503e11bf7c0122559c6868"

        # Check zero split leakage across template groups
        group_to_splits = defaultdict(set)
        for c in cases:
            group_to_splits[c["dedup_group"]].add(c["split"])
        for group_id, splits in group_to_splits.items():
            assert len(splits) == 1, f"Group {group_id} leaks across splits: {splits}"

        # Check exact split distribution
        split_counts = defaultdict(int)
        for c in cases:
            split_counts[c["split"]] += 1
        assert split_counts["train"] == 126
        assert split_counts["validation"] == 42
        assert split_counts["holdout"] == 42

