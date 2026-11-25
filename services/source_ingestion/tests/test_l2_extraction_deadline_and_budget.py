"""L2-EXTRACTION-DEADLINE-AND-BUDGET-TRUTH-20261125.

Hosted 2026-11-25 15:24-15:26Z: 12 of 12 HYPOTHESIS_CARD calls ended in a 10 s
client timeout while the gateway answered in ~9-10 s on the second OpenAI key
after Anthropic and the first OpenAI key were skipped for billing. These tests
pin the separate deadline, the lease, per-call budget truth, the redaction
explanation and billing-only failures.
"""

from __future__ import annotations

import json
import time
from typing import Any

import pytest

from services.source_ingestion import hypothesis_card, semantic_extraction
from services.source_ingestion.distillation_worker import DistillationJobQueue, DistillationWorker
from services.source_ingestion.hypothesis_card import (
    MAX_RECORD_ATTEMPTS_PER_DAY,
    MAX_RECORD_ATTEMPTS_TOTAL,
    ExtractionLedger,
    HypothesisExtractor,
)
from services.source_ingestion.semantic_extraction import (
    ExtractionTaskType,
    SemanticExtractionAdmission,
    SemanticExtractionRequest,
    hypothesis_card_deadline_seconds,
)
from services.source_ingestion.semantic_extraction_client import SemanticExtractionClient
from services.source_ingestion.strategy_seed_store import StrategySpecSeedStore
from services.source_ingestion.tests.test_hypothesis_extraction import _card, _source

START = 1_800_000_000.0  # 2027-01-15T08:00:00Z


@pytest.fixture
def fake_monotonic(monkeypatch):
    """Model time spent in a call without sleeping: transports advance it."""
    offset = [0.0]
    real = time.monotonic
    monkeypatch.setattr(time, "monotonic", lambda: real() + offset[0])
    return offset


def _answer(payload: dict[str, Any] | None = None, **extra: Any) -> dict[str, Any]:
    return {"output": {"structured_data": {"is_abstained": False, "abstention_reason": None,
                                           "hypothesis_card": _card()},
                       "usage": {"input_tokens": 100, "output_tokens": 100}, **extra}}


class _Slow:
    """Adapter that answers after ``delay`` seconds of (fake) wall time."""

    def __init__(self, offset: list[float], delay: float) -> None:
        self.offset, self.delay, self.payloads = offset, delay, []

    def __call__(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.payloads.append(payload)
        self.offset[0] += self.delay
        return _answer()


def _queue_worker(tmp_path, transport, *, cap=400, clock=None, lease_seconds=30.0, client_kw=None):
    clock = clock if clock is not None else [START]
    queue = DistillationJobQueue(tmp_path / "q.sqlite", now=lambda: clock[0])
    ledger = ExtractionLedger(tmp_path / "l.sqlite", daily_cap=cap)
    client = SemanticExtractionClient(transport_fn=transport, **(client_kw or {}))
    extractor = HypothesisExtractor(client, ledger)
    worker = DistillationWorker(queue, StrategySpecSeedStore(tmp_path / "s.jsonl"),
                                hypothesis_extractor=extractor, lease_seconds=lease_seconds,
                                retry_base_seconds=5.0)
    return worker, queue, ledger, clock


# --- 2. deadline -----------------------------------------------------------

def test_hypothesis_deadline_is_its_own_bounded_setting(monkeypatch):
    monkeypatch.delenv(semantic_extraction.HYPOTHESIS_CARD_DEADLINE_ENV, raising=False)
    assert hypothesis_card_deadline_seconds() == 90.0
    monkeypatch.setenv(semantic_extraction.HYPOTHESIS_CARD_DEADLINE_ENV, "45")
    assert hypothesis_card_deadline_seconds() == 45.0
    monkeypatch.setenv(semantic_extraction.HYPOTHESIS_CARD_DEADLINE_ENV, "100000")
    assert hypothesis_card_deadline_seconds() == 180.0
    monkeypatch.setenv(semantic_extraction.HYPOTHESIS_CARD_DEADLINE_ENV, "0.1")
    assert hypothesis_card_deadline_seconds() == 5.0
    monkeypatch.setenv(semantic_extraction.HYPOTHESIS_CARD_DEADLINE_ENV, "nonsense")
    assert hypothesis_card_deadline_seconds() == 90.0


def test_adapter_answering_after_40s_yields_one_card(tmp_path, fake_monotonic):
    transport = _Slow(fake_monotonic, 40.0)
    worker, queue, ledger, _ = _queue_worker(tmp_path, transport)
    worker.enqueue_from_source_record(_source("src-1", "news"))
    result = worker.run_pending()
    assert result.created == 1 and len(transport.payloads) == 1
    # The same deadline is handed to the adapter (and from there to the gateway).
    assert transport.payloads[0]["timeout_seconds"] == pytest.approx(90.0, abs=0.5)
    assert ledger.calls_today() == 1 and ledger.summary()["timeout_calls"] == 0


def test_adapter_answering_after_the_deadline_ends_unavailable(tmp_path, fake_monotonic):
    transport = _Slow(fake_monotonic, 95.0)
    worker, queue, ledger, _ = _queue_worker(tmp_path, transport)
    worker.enqueue_from_source_record(_source("src-1", "news"))
    result = worker.run_pending()
    assert result.created == 0 and result.retried == 1
    job = queue.get("src-1")
    assert job.status == "retry_wait" and "hypothesis_extraction_unavailable: timeout" in job.error
    assert ledger.summary()["timeout_calls"] == 1
    assert [e["outcome"] for e in ledger.events("src-1")] == ["unavailable"]


def test_interactive_extraction_keeps_its_15s_clamp(fake_monotonic):
    transport = _Slow(fake_monotonic, 0.0)
    client = SemanticExtractionClient(transport_fn=transport)
    request = SemanticExtractionRequest(
        source_id="s", text="Buy 2330 on revenue momentum.", task_type=ExtractionTaskType.INTENT,
        timeout_seconds=600,
    )
    client.extract(request)
    assert transport.payloads[0]["timeout_seconds"] <= 15.0


def test_http_adapter_body_carries_the_deadline(monkeypatch):
    from services.source_ingestion import semantic_extraction_client as mod

    seen: dict[str, Any] = {}

    def fake_http(endpoint, *, body_bytes, headers, deadline_at, **_):
        seen["body"] = json.loads(body_bytes)
        seen["budget"] = deadline_at - time.monotonic()
        return 200, json.dumps({"data": _answer()}).encode(), {}

    monkeypatch.setattr(mod, "_perform_http_request", fake_http)
    client = SemanticExtractionClient(adapter_url="http://adapter.test", hypothesis_deadline_seconds=60)
    request = SemanticExtractionRequest(source_id="src-1", text="x", task_type=ExtractionTaskType.HYPOTHESIS_CARD,
                                        source_type="news", license_scope="collector_public_web")
    client.extract(request)
    assert seen["budget"] == pytest.approx(60.0, abs=1.0)
    assert seen["body"]["timeout_seconds"] == pytest.approx(58.0, abs=1.0)


# --- 3. lease ----------------------------------------------------------------

def test_call_longer_than_the_lease_is_never_a_lease_expiry(tmp_path):
    clock = [START]
    holder: dict[str, Any] = {}

    def slow_call(payload):
        # 60 s of wall time pass inside the call: twice the 30 s lease. Another
        # claimer runs lease recovery meanwhile.
        clock[0] += 60.0
        other = holder["queue"].claim_due(worker_id="other", lease_seconds=30.0, limit=10)
        holder["stolen"] = [job.job_id for job in other]
        return _answer()

    worker, queue, ledger, _ = _queue_worker(tmp_path, slow_call, clock=clock, lease_seconds=30.0)
    holder["queue"] = queue
    worker.enqueue_from_source_record(_source("src-1", "news"))
    result = worker.run_pending()
    assert holder["stolen"] == []
    assert result.created == 1 and not result.lease_lost
    job = queue.get("src-1")
    assert job.status == "done" and job.attempts == 1


# --- 4. budget -----------------------------------------------------------------

class _Timeout:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, payload):
        self.calls += 1
        raise TimeoutError("HTTP request timed out")


def test_record_is_called_3_times_a_day_6_in_total_then_closed_with_reason(tmp_path, monkeypatch):
    clock = [START]
    monkeypatch.setattr(hypothesis_card, "_today",
                        lambda: time.strftime("%Y-%m-%d", time.gmtime(clock[0])))
    transport = _Timeout()
    worker, queue, ledger, _ = _queue_worker(tmp_path, transport, clock=clock)
    worker.enqueue_from_source_record(_source("src-1", "news"))
    for _ in range(40):
        worker.run_pending()
        job = queue.get("src-1")
        if job.status not in ("retry_wait", "pending"):
            break
        due = _epoch(job.next_attempt_at) if job.next_attempt_at else clock[0]
        clock[0] = max(clock[0] + 1, due)
    assert transport.calls == MAX_RECORD_ATTEMPTS_TOTAL == 6
    job = queue.get("src-1")
    assert job.status == "skipped"
    assert job.skip_reason.startswith("l2_extraction_attempts_exhausted: 6 calls; last: timeout")
    days = [e["ts"][:10] for e in ledger.events("src-1") if e["outcome"] == "unavailable"]
    assert len(days) == 6
    assert ledger.record_attempts("src-1", job.source_digest or "")["total"] == 6
    assert ledger.summary()["timeout_calls"] == MAX_RECORD_ATTEMPTS_PER_DAY


def _epoch(text: str) -> float:
    from datetime import datetime
    return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()


def test_daily_cap_counts_retries_of_the_same_record(tmp_path):
    ledger = ExtractionLedger(tmp_path / "l.sqlite", daily_cap=4)
    assert [ledger.reserve_attempt("a", "d1") for _ in range(3)] == ["reserved"] * 3
    assert ledger.reserve_attempt("a", "d1") == "record_daily_limit"
    assert ledger.reserve_attempt("b", "d1") == "reserved"
    assert ledger.reserve_attempt("c", "d1") == "daily_cap"
    assert ledger.calls_today() == 4  # never above the cap, retries included


def test_failing_provider_cannot_spend_beyond_the_cap(tmp_path):
    transport = _Timeout()
    worker, queue, ledger, clock = _queue_worker(tmp_path, transport, cap=5)
    for i in range(4):
        worker.enqueue_from_source_record(_source(f"src-{i}", "news"))
    for _ in range(20):
        worker.run_pending()
        clock[0] += 700
    assert transport.calls == 5 == ledger.calls_today()
    summary = ledger.summary()
    assert summary["timeout_calls"] == 5 and summary["daily_cap"] == 5


# --- 5. redaction ----------------------------------------------------------------

NEWS = ("台積電月營收創高。Revenue rose to $3 billion, up 5 million units; "
        "stocks with rising monthly revenue tend to outperform over the next month.")


def _request(text: str, source_type: str = "news", task=ExtractionTaskType.HYPOTHESIS_CARD):
    return SemanticExtractionRequest(source_id="src-1", text=text, task_type=task, source_type=source_type,
                                     source_status="normalized", license_scope="collector_public_web")


def test_published_amounts_in_public_news_are_not_operator_capital():
    assert SemanticExtractionAdmission.check(_request(NEWS)).admitted
    # Interactive/internal text still treats amounts as operator capital.
    denied = SemanticExtractionAdmission.check(_request(NEWS, "internal_note", ExtractionTaskType.COMPREHENSIVE))
    assert denied.denial_code == "REDACTION_VIOLATION"
    assert {e["finding"] for e in denied.explanations} == {"capital_amount:dollar_amount", "capital_amount:amount_word"}
    assert all(e["secret_class"] is False for e in denied.explanations)


def test_real_secret_in_news_is_still_refused_and_explained_without_echo(tmp_path):
    secret = "sk-ABCDEFGHIJKLMNOPQRSTUVWX123456"
    text = NEWS + f" leaked key {secret}"
    decision = SemanticExtractionAdmission.check(_request(text))
    assert decision.denial_code == "REDACTION_VIOLATION"
    [match] = decision.explanations
    assert match["finding"] == "credential:api_key_sk" and match["secret_class"] is True
    assert match["offset"] == text.index(secret) and match["length"] == len(secret)

    ledger = ExtractionLedger(tmp_path / "l.sqlite")
    calls: list = []
    extractor = HypothesisExtractor(SemanticExtractionClient(transport_fn=calls.append), ledger)
    outcome = extractor.extract(_source("src-1", "news", body=text), digest="d1")
    assert outcome.status == "admission_denied" and not calls and ledger.calls_today() == 0
    [event] = ledger.events("src-1")
    recorded = json.loads(event["detail"])
    assert recorded["code"] == "REDACTION_VIOLATION"
    assert recorded["matches"][0]["rule"] == "api_key_sk" and recorded["matches"][0]["secret_class"] is True
    assert secret not in event["detail"] and secret[:6] not in event["detail"]


# --- 6. billing ------------------------------------------------------------------

def test_billing_skips_before_an_answer_are_not_no_hypothesis(tmp_path):
    def answered_after_skips(payload):
        return _answer(fallback={"skipped": [{"provider": "anthropic", "reason": "billing"},
                                             {"provider": "openai-1", "reason": "billing"}]})

    worker, queue, ledger, _ = _queue_worker(tmp_path, answered_after_skips)
    worker.enqueue_from_source_record(_source("src-1", "news"))
    assert worker.run_pending().created == 1
    assert "no_hypothesis" not in [e["outcome"] for e in ledger.events("src-1")]


def test_billing_only_failure_is_retried_with_backoff(tmp_path):
    calls = []

    def all_skipped(payload):
        calls.append(payload)
        raise RuntimeError("all providers failed: anthropic billing error; openai insufficient_quota")

    worker, queue, ledger, clock = _queue_worker(tmp_path, all_skipped)
    worker.enqueue_from_source_record(_source("src-1", "news"))
    first = worker.run_pending()
    job = queue.get("src-1")
    assert first.retried == 1 and job.status == "retry_wait"
    assert "hypothesis_extraction_unavailable: provider_billing" in job.error
    assert _epoch(job.next_attempt_at) - clock[0] >= 60  # long backoff, not an instant retry
    assert worker.run_pending().processed == 0  # not due yet
    assert ledger.summary()["billing_failed_calls"] == 1
    outcomes = [e["outcome"] for e in ledger.events("src-1")]
    assert outcomes == ["unavailable"] and "no_hypothesis" not in outcomes


def test_http_billing_error_maps_to_provider_billing(monkeypatch):
    from services.source_ingestion import semantic_extraction_client as mod

    monkeypatch.setattr(mod, "_perform_http_request", lambda *a, **k: (
        502, b'{"error_code":"OPENCLAW_GATEWAY_ERROR","message":"credit balance is too low (billing)"}', {}))
    client = SemanticExtractionClient(adapter_url="http://adapter.test")
    result = client.extract(_request("Revenue momentum predicts returns."))
    assert result.failure_code == "provider_billing"
    assert result.failure_code in hypothesis_card.UNAVAILABLE_FAILURE_CODES
