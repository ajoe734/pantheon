import json
from pathlib import Path

from services.source_ingestion.l2_relevance import (
    DROP_NO_FINANCE_SIGNAL,
    DROP_NO_TEXT,
    DROP_PAPER_NOT_FINANCE,
    PASS_SECURITY_CODE,
    assess_relevance,
)

FIXTURE = Path(__file__).parent / "fixtures" / "l2_relevance_labelled_titles.json"


def _decide(row, **kw):
    meta = dict(row["metadata"])
    if row["category"]:
        meta["category"] = row["category"]
    return assess_relevance(row["source_type"], row["title"], meta, **kw)


def test_labelled_titles_precision_recall():
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    rows = data["records"]
    assert len(rows) >= 40
    tp = fp = fn = tn = 0
    wrong = []
    for row in rows:
        got = _decide(row).relevant
        if got and row["relevant"]:
            tp += 1
        elif got:
            fp += 1
            wrong.append(("fp", row["title"]))
        elif row["relevant"]:
            fn += 1
            wrong.append(("fn", row["title"]))
        else:
            tn += 1
    precision, recall = tp / (tp + fp), tp / (tp + fn)
    assert not wrong, wrong
    assert precision == 1.0 and recall == 1.0
    assert tn >= 15 and tp >= 20


def test_dropped_record_says_why_and_never_calls_a_model():
    assert assess_relevance("news", "大樂透開獎", {}).reason == DROP_NO_FINANCE_SIGNAL
    assert assess_relevance("news", "", {}).reason == DROP_NO_TEXT
    assert assess_relevance("paper", "Protein folding", {"category": "q-bio"}).reason == DROP_PAPER_NOT_FINANCE


def test_bare_year_is_not_a_security_code_but_a_marked_listed_code_is():
    listed = frozenset({"2330", "2024"})
    assert not assess_relevance("news", "2024 年的旅遊指南", {}, listed_codes=listed).relevant
    hit = assess_relevance("news", "今日焦點 (2330) 動態", {}, listed_codes=frozenset({"2330"}))
    assert (hit.relevant, hit.reason) == (True, PASS_SECURITY_CODE)
    assert not assess_relevance("news", "社區活動 (5566) 報名", {}, listed_codes=frozenset({"2330"})).relevant


def test_finance_paper_category_passes_without_term():
    assert assess_relevance("paper", "Untitled", {"categories": ["q-fin.PM"]}).relevant
