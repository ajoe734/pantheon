from pathlib import Path


def test_gateway_dockerfile_installs_assistant_clis_from_npm() -> None:
    dockerfile = Path(__file__).resolve().parents[1] / "Dockerfile"
    content = dockerfile.read_text(encoding="utf-8")

    assert "nodejs npm" in content
    assert "ARG PANTHEON_ASSISTANT_UID=10001" in content
    assert "useradd" in content
    assert "pantheon-assistant" in content
    assert "@openai/codex@" in content
    assert "@anthropic-ai/claude-code@" in content
    assert "claude-cli" not in content
    assert "codex-cli" not in content
