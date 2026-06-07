from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ASK_PERSONAS = ROOT / "execute-plans" / "src" / "agora" / "pages" / "AskPersonas.tsx"


def _source() -> str:
    return ASK_PERSONAS.read_text(encoding="utf-8")


def test_sa_sd_result_exposes_repo_local_dispatch_handoff() -> None:
    source = _source()

    assert "function buildAssistantDevDispatchCommand" in source
    assert "python3 scripts/dispatch_assistant_dev_task_packet.py <<'JSON'" in source
    assert "JSON.stringify({ taskPacket: packet }, null, 2)" in source
    assert 'data-testid="assistant-dev-handoff"' in source
    assert "Copy Dispatch Command" in source


def test_handoff_surfaces_bridge_policy_and_task_scope() -> None:
    source = _source()

    assert "repo_local_required" in source
    assert "noDirectShellFromWeb" in source
    assert "web shell disabled" in source
    assert "scoped artifacts" in source
    assert "devDocArtifactPaths" in source


def test_generated_packet_prefills_repair_task_and_scope() -> None:
    source = _source()

    assert "const generatedTaskId = firstPacketTaskId(res)" in source
    assert "setRepairTaskId(generatedTaskId)" in source
    assert "const generatedScope = devDocArtifactPaths(res)" in source
    assert 'setRepairScope(generatedScope.join("\\n"))' in source
