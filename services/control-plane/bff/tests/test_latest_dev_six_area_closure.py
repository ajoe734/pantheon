"""Guard the six-area closure record: every declared predecessor has a recorded identity."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
DOC = ROOT / "docs/deployment/latest-dev-six-area-closure.md"
EVIDENCE = ROOT / "docs/deployment/evidence/L12-SOURCE-CLOSURE-20261002/evidence.json"


def test_closure_doc_records_identities_and_non_real_boundary():
    text = DOC.read_text(encoding="utf-8")
    rows = [line for line in text.splitlines() if re.match(r"\| [A-Z0-9-]+-\d+ \|", line)]
    assert len(rows) >= 40
    for line in rows:
        assert re.search(r"#\d+ \| `[0-9a-f]{9}`", line) or "not verifiable from BE source" in line
    assert "`is_real=false`" in text
    assert "DIRECT-RAY-BASELINE-001 was NOT DELIVERED" in text
    assert "Zero production delta" in text


def test_evidence_declares_zero_production_delta():
    assert json.loads(EVIDENCE.read_text(encoding="utf-8"))["production_delta"] == 0
