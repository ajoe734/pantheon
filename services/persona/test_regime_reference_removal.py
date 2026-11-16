"""No regime reference is built from a literal or a persona stage (SD-REGIME-STATE-ENGINE §3, R13)."""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FILES = ("services/control-plane/ooda/paper_loop_packet.py", "services/persona/ooda_cycle_runtime.py",
         "services/persona/cron_ooda_closure.py")


def _regime_ref_writes(path: str) -> list[int]:
    hits = []
    for node in ast.walk(ast.parse((ROOT / path).read_text(encoding="utf-8"))):
        if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(t, ast.Attribute) and t.attr == "regime_state_ref" for t in targets):
                hits.append(node.lineno)
        if isinstance(node, ast.keyword) and node.arg == "regime_state_ref":
            hits.append(node.value.lineno)
    return hits


def test_fabricated_regime_references_are_gone():
    for path in FILES:
        assert _regime_ref_writes(path) == [], path
        text = (ROOT / path).read_text(encoding="utf-8")
        assert "regime-state://tw-paper-neutral" not in text and "regime://persona-ooda15" not in text


def test_only_a_stored_registry_record_id_can_set_the_cycle_regime_ref():
    controller = (ROOT / "services/persona/persona_cycle_controller.py").read_text(encoding="utf-8")
    assert controller.count('["regime_state_ref"] =') == 1
    consumer = (ROOT / "services/persona/regime_consumer.py").read_text(encoding="utf-8")
    assert consumer.count("REF_PREFIX + rec[\"regime_id\"]") == 1 and consumer.count('transport("POST"') == 1
