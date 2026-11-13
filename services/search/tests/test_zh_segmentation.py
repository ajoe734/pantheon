from pathlib import Path

import pytest

from services.search.filters import SearchCapabilityUnavailableError
from services.search.zh_segmentation import _tokenizer, lexical_text


@pytest.fixture
def dictionary(tmp_path, monkeypatch):
    path = tmp_path / "dict.txt.big"
    path.write_text("晶圓 20 n\n代工 20 n\n風險 20 n\n管理 20 n\n", encoding="utf-8")
    monkeypatch.setenv("SEARCH_JIEBA_DICTIONARY", str(path))
    _tokenizer.cache_clear()
    yield path
    _tokenizer.cache_clear()


def test_taiwan_aliases_and_tickers_remain_whole(dictionary):
    tokens = lexical_text("2330 台積電晶圓代工 Risk Management").split()
    assert tokens == ["2330", "台積電", "晶圓代工", "risk", "management"]


def test_index_and_query_share_exact_segmentation(dictionary):
    from services.search.pg_retrieval import _format_lexical_query

    text = "聯發科2454，台股停損"
    assert _format_lexical_query(text) == lexical_text(text)
    assert "聯發科" in lexical_text(text).split()
    assert "停損" in lexical_text(text).split()


def test_missing_dictionary_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("SEARCH_JIEBA_DICTIONARY", str(tmp_path / "absent"))
    _tokenizer.cache_clear()
    with pytest.raises(SearchCapabilityUnavailableError, match="dict.txt.big"):
        lexical_text("台積電")
    _tokenizer.cache_clear()
