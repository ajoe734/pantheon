"""One deterministic jieba tokenizer shared by PostgreSQL writes and queries.

dict.txt.big is fetched and checksum-pinned at image build, never at runtime.
The market dictionary contains aliases only, not product knowledge or memories.
"""

from functools import lru_cache
import os
from pathlib import Path
import re

from services.search.filters import SearchCapabilityUnavailableError


@lru_cache(maxsize=1)
def _tokenizer():
    import jieba

    dictionary = Path(os.getenv("SEARCH_JIEBA_DICTIONARY", "/opt/search/dict.txt.big"))
    if not dictionary.is_file():
        raise SearchCapabilityUnavailableError("jieba dict.txt.big is missing")
    tokenizer = jieba.Tokenizer(str(dictionary))
    tokenizer.load_userdict(str(Path(__file__).parent / "dictionaries/tw_market_terms.txt"))
    return tokenizer


def lexical_text(text: str) -> str:
    """Segment Traditional Chinese and preserve English words and ticker IDs."""
    return " ".join(
        token.lower() for token in _tokenizer().cut(str(text or ""), HMM=False)
        if re.search(r"[\w]", token, re.UNICODE)
    )
