import json
from pathlib import Path

from services.source_ingestion.l2_relevance import (
    DROP_BODY_ONLY_WEAK,
    DROP_NO_FINANCE_SIGNAL,
    DROP_NO_TEXT,
    DROP_PAPER_NOT_FINANCE,
    PASS_BODY_EVIDENCE,
    PASS_SECURITY_CODE,
    assess_relevance,
)

FIXTURE = Path(__file__).parent / "fixtures" / "l2_relevance_labelled_titles.json"


def _decide(row, **kw):
    return assess_relevance(row["source_type"], row["title"], {}, **kw)


def _score(rows):
    tp = fp = fn = tn = 0
    for row in rows:
        got = _decide(row).relevant
        tp += got and row["relevant"]
        fp += got and not row["relevant"]
        fn += (not got) and row["relevant"]
        tn += (not got) and not row["relevant"]
    return tp, fp, fn, tn


def test_heldout_precision_recall_on_real_titles():
    rows = json.loads(FIXTURE.read_text(encoding="utf-8"))["records"]
    held = [r for r in rows if r["split"] == "heldout"]
    assert len(held) >= 100 and len(rows) - len(held) >= 100
    tp, fp, fn, tn = _score(held)
    precision, recall = tp / (tp + fp), tp / (tp + fn)
    # Measured on the held-out part only (title-only; bodies are not committed).
    assert precision >= 0.85, (precision, tp, fp, fn, tn)
    assert recall >= 0.55, (recall, tp, fp, fn, tn)
    assert tn >= 80


def test_real_false_passes_stay_dropped():
    # Real records the first version passed; body text here is a short stand-in
    # with the incidental finance word the real body carried.
    cases = [
        ("今彩539第115273期 頭獎槓龜", "頭獎槓龜，獲利 新台幣 3億 滾入下期"),
        ("高雄某里長參選人涉藉宮廟活動行賄 15萬交保", "檢方指其以新台幣15萬元交保，另涉股票與投資人糾紛"),
        ("中選會調增投開票所工作人員支給額 盼提高擔任誘因", "預算增列，新台幣數億元"),
        ("空軍官校流感群聚95人有症狀 未通報最高罰1.5萬元", "新台幣1.5萬元罰鍰"),
        ("中國男子攀登富士山失蹤 父懸賞250萬尋人引疑慮", "懸賞新台幣250萬，人民幣匯率"),
    ]
    for title, body in cases:
        got = assess_relevance("news", title, {"body": body})
        assert not got.relevant, (title, got)


def test_bare_currency_never_qualifies():
    assert not assess_relevance("news", "路邊攤漲價 一碗麵新台幣80元", {}).relevant
    assert not assess_relevance("news", "獎金新台幣100萬元 美元 日圓", {}).relevant
    assert assess_relevance("news", "新台幣升值 匯率走強", {}).relevant


def test_names_do_not_match_inside_other_words():
    assert not assess_relevance("news", "東南亞旅遊展登場", {}).relevant
    assert not assess_relevance("news", "統一7-ELEVEN獅隊逆轉勝", {}).relevant
    assert not assess_relevance("news", "兩岸統一議題再起", {}).relevant
    assert assess_relevance("news", "南亞 法說會釋出展望", {}).relevant


def test_body_only_evidence_needs_several_distinct_hits():
    one = assess_relevance("news", "社區活動預告", {"body": "活動由聯電前董事長出席，現場有股票攤位"})
    assert (one.relevant, one.reason) == (False, DROP_BODY_ONLY_WEAK)
    many = assess_relevance(
        "news", "產業動態", {"body": "台積電與聯發科公布財報，營收創高，外資調升目標價"}
    )
    assert (many.relevant, many.reason) == (True, PASS_BODY_EVIDENCE)
    # the same single term in the title is enough
    assert assess_relevance("news", "股票攤位登場", {}).relevant


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
