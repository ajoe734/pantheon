from services.memory.evaluation.hindsight_comparison import metrics as m


def test_off_subject_and_language():
    subj = {"strategy_family": "momentum", "underlyings": ["TXF"]}
    assert m.off_subject_rate([{"strategy_family": "momentum"}, {"underlyings": ["SPX"]}, {}], subj) == 2 / 3
    assert m.off_subject_rate([], subj) is None
    assert [m.query_language(x) for x in ("momentum TXF", "動能 策略", "動能 momentum")] == ["en", "zh-TW", "mixed"]


def test_duplicates_scope_latency_jaccard():
    e = [{"source_event_type": "a", "source_event_id": "1"}, {"source_event_type": "a", "source_event_id": "1"},
         {"source_event_type": "a", "source_event_id": "2"}]
    assert m.duplicate_rate(e) == 1 / 3
    assert m.duplicate_rate(e[1:], [[1, 0], [1, 0.01]]) == 0.5       # cosine >= .95
    assert m.scope_violations("a", [{"persona_id": "b", "relevance_scope": "persona_private"},
                                    {"persona_id": "b", "relevance_scope": "persona_and_committee"}]) == 1
    assert m.latency_summary([1, 2, 3, 4, 5])["p50"] == 3
    assert m.jaccard(["a", "b"], ["b", "c"]) == 1 / 3
    assert m.rebuild_stability({"d": ["a"]}, {"d": ["a"]}) == 1.0


def test_blind_sheet_has_no_arm_names_and_scorer():
    res = {"d1": {"current": ["x", "y"], "hs": ["y", "z"]}}
    sheet, key = m.build_blind_sheet(res, {"x": "tx", "y": "ty", "z": "tz"}, {"d1": {"decision_type": "consult"}})
    assert len(sheet) == 3 and "current" not in str(sheet) and "hs" not in str(sheet).replace("decision_summary", "")
    assert key["d1|y"] == ["current", "hs"]
    labels = {"d1|x": "relevant", "d1|y": "relevant", "d1|z": "irrelevant"}
    s = m.score_precision_at_k(res, labels, k=2)
    assert s["current"]["precision_at_k"] == 1.0 and s["hs"]["precision_at_k"] == 0.5
