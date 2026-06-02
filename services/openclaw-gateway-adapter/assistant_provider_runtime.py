"""Assistant provider runtime redaction boundary for OpenClaw gateway adapter."""
from __future__ import annotations

import sys
import shutil
import subprocess
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

try:
    from assistant.redaction import RedactionError, RedactionResult, redact_assistant_payload
except ModuleNotFoundError:  # pragma: no cover - exercised in service import contexts
    _BFF_DIR = Path(__file__).resolve().parents[1] / "control-plane" / "bff"
    if str(_BFF_DIR) not in sys.path:
        sys.path.insert(0, str(_BFF_DIR))
    from assistant.redaction import RedactionError, RedactionResult, redact_assistant_payload

from assistant_credential_mounts import AssistantCredentialMounts

ProviderRunner = Callable[[Mapping[str, Any]], Any]
TranscriptSink = Callable[[Mapping[str, Any]], None]
Redactor = Callable[..., RedactionResult]

_PROVIDER_BINARY_ENVS = {
    "claude": "PANTHEON_ASSISTANT_CLAUDE_BIN",
    "claude_cli": "PANTHEON_ASSISTANT_CLAUDE_BIN",
    "codex": "PANTHEON_ASSISTANT_CODEX_BIN",
    "codex_cli": "PANTHEON_ASSISTANT_CODEX_BIN",
}
_PROVIDER_BINARY_NAMES = {
    "claude_cli": "claude",
    "codex_cli": "codex",
}
_PROVIDER_MOUNT_KEYS = {
    "claude_cli": "claude",
    "codex_cli": "codex",
}


class AssistantProviderRuntimeError(RuntimeError):
    """Raised when provider runtime cannot safely continue."""

    def __init__(self, code: str, message: str, *, stage: str) -> None:
        super().__init__(message)
        self.code = code
        self.stage = stage


@dataclass(frozen=True)
class ProviderInvocationRequest:
    provider: str
    mode: str
    prompt: str
    context_pack: Mapping[str, Any] | None = None
    metadata: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class ProviderInvocationResult:
    provider: str
    mode: str
    status: str
    output: Any
    invocation_payload: Mapping[str, Any]
    transcript_payload: Mapping[str, Any]
    redaction: Mapping[str, Any]


class AssistantProviderRuntime:
    """Runs assistant providers behind the redaction boundary.

    The runner receives only the redacted provider invocation payload.  The
    transcript sink receives only the redacted transcript payload.  User mode
    fails closed when either redaction pass fails.
    """

    def __init__(
        self,
        runner: ProviderRunner,
        *,
        transcript_sink: TranscriptSink | None = None,
        redactor: Redactor = redact_assistant_payload,
        redaction_enabled: bool = True,
        kernel_redaction_override: bool = False,
    ) -> None:
        self._runner = runner
        self._transcript_sink = transcript_sink
        self._redactor = redactor
        self._redaction_enabled = redaction_enabled
        self._kernel_redaction_override = kernel_redaction_override
        self._credential_mounts = AssistantCredentialMounts()

    def check_readiness(self, provider: str) -> Dict[str, Any]:
        """Probes the provider binary and auth mount readiness."""
        binary_path = _resolve_provider_binary(provider)
        if not binary_path:
            return {"ready": False, "reason": f"Binary {provider} not found"}

        try:
            version = subprocess.check_output([binary_path, "--version"], stderr=subprocess.STDOUT).decode().strip()
        except Exception as e:
            version = "unknown"
            return {"ready": False, "reason": f"Binary {provider} probe failed: {e}"}

        mounts = self._credential_mounts.validate_mounts()
        mount_validation = mounts.get(_provider_mount_key(provider))
        
        if not mount_validation:
            return {"ready": False, "reason": f"No mount configuration for {provider}"}
        
        if not mount_validation.ready:
            return {"ready": False, "reason": f"Mount validation failed: {mount_validation.status}", "mount_status": mount_validation.status}

        return {
            "ready": True,
            "binary_path": binary_path,
            "version": version,
            "mount_mode": mount_validation.mount_mode
        }

    def invoke(self, request: ProviderInvocationRequest) -> ProviderInvocationResult:
        invocation_payload = {
            "provider": request.provider,
            "mode": request.mode,
            "prompt": request.prompt,
            "context_pack": request.context_pack if request.context_pack is not None else {},
            "metadata": request.metadata if request.metadata is not None else {},
        }
        invocation_redaction = self._redact(
            invocation_payload,
            mode=request.mode,
            stage="provider_invocation",
        )

        raw_output = self._runner(invocation_redaction.value)

        transcript_payload = {
            "provider": request.provider,
            "mode": request.mode,
            "request": invocation_redaction.value,
            "response": raw_output,
        }
        transcript_redaction = self._redact(
            transcript_payload,
            mode=request.mode,
            stage="transcript_persistence",
        )
        safe_transcript = _ensure_mapping(transcript_redaction.value, stage="transcript_persistence")
        if self._transcript_sink:
            self._transcript_sink(safe_transcript)

        return ProviderInvocationResult(
            provider=request.provider,
            mode=request.mode,
            status="completed",
            output=safe_transcript.get("response"),
            invocation_payload=_ensure_mapping(invocation_redaction.value, stage="provider_invocation"),
            transcript_payload=safe_transcript,
            redaction={
                "provider_invocation": invocation_redaction.summary.to_dict(),
                "transcript_persistence": transcript_redaction.summary.to_dict(),
            },
        )

    def _redact(self, payload: Any, *, mode: str, stage: str) -> RedactionResult:
        try:
            return self._redactor(
                payload,
                mode=mode,
                stage=stage,
                enabled=self._redaction_enabled,
                allow_kernel_override=self._kernel_redaction_override,
            )
        except RedactionError as exc:
            raise AssistantProviderRuntimeError(
                "REDACTION_FAILED",
                f"Assistant provider runtime failed closed during {stage}: {exc}",
                stage=stage,
            ) from exc


def _ensure_mapping(value: Any, *, stage: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise AssistantProviderRuntimeError(
            "REDACTION_SCHEMA_ERROR",
            f"Assistant provider runtime expected mapping payload after {stage} redaction.",
            stage=stage,
        )
    return value


def _resolve_provider_binary(provider: str) -> str | None:
    normalized = str(provider or "").strip().lower()
    configured = os.getenv(_PROVIDER_BINARY_ENVS.get(normalized, ""), "").strip()
    if configured:
        if os.path.isabs(configured):
            return configured if os.path.isfile(configured) and os.access(configured, os.X_OK) else None
        return shutil.which(configured)
    return shutil.which(_PROVIDER_BINARY_NAMES.get(normalized, normalized))


def _provider_mount_key(provider: str) -> str:
    normalized = str(provider or "").strip().lower()
    return _PROVIDER_MOUNT_KEYS.get(normalized, normalized)
