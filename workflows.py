"""
OC-002: OpenClaw Workflow Adapter.
Adapter that maps Pantheon orchestration requests to upstream OpenClaw workflows.

Note on naming:
  - "OpenClaw" here refers to the upstream OSS framework being integrated.
  - "Pantheon" is the name of this overall multi-persona trading system.
  - This class is an integration adapter, not Pantheon's own workflow engine.
"""
from typing import Any, Dict


class OpenClawAdapter:
    """Adapter for upstream OpenClaw framework workflow entrypoints."""

    def __init__(self):
        # Pinning upstream OpenClaw version/ref (OSS_INTEGRATION_CHECKLIST.md)
        self.upstream_ref = "github.com/openclaw/openclaw-core"
        self.upstream_version = "v0.1.0-alpha"  # TODO: confirm once source is selected

    def create_ingest_payload(self, source_url: str, spec_hints: Dict[str, Any]) -> Dict[str, Any]:
        """Maps raw research source to upstream OpenClaw's ingestion workflow parameters."""
        return {
            "workflow": "research_ingestion",
            "params": {
                "source": source_url,
                "depth": "full",
                "metadata_hints": spec_hints,
            },
            "governance": {
                "target_state": "draft",
                "adapter_version": self.upstream_version,
            },
        }

    def create_deploy_payload(self, strategy_id: str, version: str, mode: str) -> Dict[str, Any]:
        """Maps a Pantheon registry artifact to an upstream OpenClaw deployment workflow."""
        return {
            "workflow": "strategy_deployment",
            "params": {
                "strategy_id": strategy_id,
                "version": version,
                "execution_mode": mode,  # paper or live
            },
        }
