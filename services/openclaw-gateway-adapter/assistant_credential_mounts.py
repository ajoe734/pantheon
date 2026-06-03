"""Credential mount contract for assistant provider runtimes.

The adapter reports whether dedicated service-user OAuth directories are
configured and usable. It intentionally never exposes raw host paths in API
metadata because those paths can reveal operator usernames or secret layout.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
import pwd
import stat
from typing import Any, Callable, Mapping


DEFAULT_CODEX_HOST_HOME = "/srv/pantheon-assistant/.codex"
DEFAULT_CODEX_CONTAINER_HOME = "/home/pantheon-assistant/.codex"
DEFAULT_CLAUDE_HOST_CONFIG_DIR = "/srv/pantheon-assistant/.claude"
DEFAULT_CLAUDE_CONTAINER_CONFIG_DIR = "/home/pantheon-assistant/.claude"
DEFAULT_OWNER_NAME = "pantheon-assistant"

ALLOWED_MOUNT_MODES = {"ro", "rw"}
SERVICE_HOST_PREFIXES = ("/srv/pantheon-assistant/", "/home/pantheon-assistant/")
SERVICE_CONTAINER_PREFIX = "/home/pantheon-assistant/"
FORBIDDEN_CONTAINER_PATHS = {"/", "/bin", "/dev", "/etc", "/root", "/sbin", "/tmp", "/usr", "/var"}


@dataclass(frozen=True)
class CredentialMountContract:
    provider: str
    host_env: str
    container_env: str
    host_path: str
    container_path: str
    expected_suffix: str
    container_target: str


@dataclass(frozen=True)
class CredentialMountValidation:
    provider: str
    ready: bool
    status: str
    configured: bool
    host_source: str
    container_target: str
    mount_mode: str
    owner_check: str

    def to_metadata(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "status": self.status,
            "configured": self.configured,
            "host_source": self.host_source,
            "container_target": self.container_target,
            "mount_mode": self.mount_mode,
            "owner_check": self.owner_check,
        }


class AssistantCredentialMounts:
    """Validate the dedicated service-user credential mount contract."""

    def __init__(
        self,
        environ: Mapping[str, str] | None = None,
        *,
        stat_func: Callable[[str], os.stat_result] | None = None,
        expected_uid: int | None = None,
    ) -> None:
        self._environ = dict(environ if environ is not None else os.environ)
        self._stat = stat_func or os.stat
        self.mount_mode = self._environ.get("PANTHEON_ASSISTANT_CREDENTIAL_MOUNT_MODE", "rw").strip()
        self.expected_owner_name = self._environ.get(
            "PANTHEON_ASSISTANT_CREDENTIAL_OWNER", DEFAULT_OWNER_NAME
        ).strip() or DEFAULT_OWNER_NAME
        self._expected_uid = expected_uid if expected_uid is not None else self._owner_uid_from_env()

    def _owner_uid_from_env(self) -> int | None:
        raw_uid = self._environ.get("PANTHEON_ASSISTANT_CREDENTIAL_OWNER_UID", "").strip()
        if raw_uid:
            try:
                return int(raw_uid)
            except ValueError:
                return None
        try:
            return pwd.getpwnam(self.expected_owner_name).pw_uid
        except KeyError:
            return None

    def _contracts(self) -> tuple[CredentialMountContract, CredentialMountContract]:
        return (
            CredentialMountContract(
                provider="codex",
                host_env="PANTHEON_ASSISTANT_CODEX_HOST_HOME",
                container_env="PANTHEON_ASSISTANT_CODEX_CONTAINER_HOME",
                host_path=self._environ.get("PANTHEON_ASSISTANT_CODEX_HOST_HOME", DEFAULT_CODEX_HOST_HOME).strip(),
                container_path=self._environ.get(
                    "PANTHEON_ASSISTANT_CODEX_CONTAINER_HOME", DEFAULT_CODEX_CONTAINER_HOME
                ).strip(),
                expected_suffix="/.codex",
                container_target="codex_home",
            ),
            CredentialMountContract(
                provider="claude",
                host_env="PANTHEON_ASSISTANT_CLAUDE_HOST_CONFIG_DIR",
                container_env="PANTHEON_ASSISTANT_CLAUDE_CONTAINER_CONFIG_DIR",
                host_path=self._environ.get(
                    "PANTHEON_ASSISTANT_CLAUDE_HOST_CONFIG_DIR", DEFAULT_CLAUDE_HOST_CONFIG_DIR
                ).strip(),
                container_path=self._environ.get(
                    "PANTHEON_ASSISTANT_CLAUDE_CONTAINER_CONFIG_DIR", DEFAULT_CLAUDE_CONTAINER_CONFIG_DIR
                ).strip(),
                expected_suffix="/.claude",
                container_target="claude_config",
            ),
        )

    def _invalid_mount_mode(self) -> bool:
        return self.mount_mode not in ALLOWED_MOUNT_MODES

    def _host_policy_status(self, contract: CredentialMountContract) -> str:
        host_path = os.path.normpath(contract.host_path)
        if not contract.host_path or not os.path.isabs(contract.host_path):
            return "host_path_not_configured"
        if contract.host_path.startswith("~") or "$HOME" in contract.host_path:
            return "forbidden_human_home"
        if host_path.startswith("/Users/"):
            return "forbidden_human_home"
        if host_path == "/root" or host_path.startswith("/root/"):
            return "forbidden_human_home"
        if host_path.startswith("/home/") and not host_path.startswith("/home/pantheon-assistant/"):
            return "forbidden_human_home"
        if not host_path.endswith(contract.expected_suffix):
            return "unexpected_host_directory"
        if not any(host_path.startswith(prefix) for prefix in SERVICE_HOST_PREFIXES):
            return "host_path_not_service_user"
        return "ok"

    def _container_policy_status(self, contract: CredentialMountContract) -> str:
        container_path = os.path.normpath(contract.container_path)
        if not contract.container_path or not os.path.isabs(contract.container_path):
            return "container_path_not_configured"
        if container_path in FORBIDDEN_CONTAINER_PATHS:
            return "forbidden_container_path"
        if not container_path.endswith(contract.expected_suffix):
            return "unexpected_container_directory"
        if not container_path.startswith(SERVICE_CONTAINER_PREFIX):
            return "container_path_not_service_user"
        return "ok"

    def _validate_contract(self, contract: CredentialMountContract) -> CredentialMountValidation:
        host_status = self._host_policy_status(contract)
        if host_status != "ok":
            return self._metadata(contract, False, host_status, "not_checked")

        container_status = self._container_policy_status(contract)
        if container_status != "ok":
            return self._metadata(contract, False, container_status, "not_checked")

        if self._invalid_mount_mode():
            return self._metadata(contract, False, "invalid_mount_mode", "not_checked")

        # The host path is validated as policy, but the running container can
        # only prove readability through the mounted container target path.
        try:
            mount_stat = self._stat(os.path.normpath(contract.container_path))
        except FileNotFoundError:
            return self._metadata(contract, False, "missing_host_mount", "not_checked")
        except PermissionError:
            return self._metadata(contract, False, "host_mount_unreadable", "not_checked")
        except OSError:
            return self._metadata(contract, False, "host_mount_unavailable", "not_checked")

        if not stat.S_ISDIR(mount_stat.st_mode):
            return self._metadata(contract, False, "host_mount_not_directory", "not_checked")

        if self._expected_uid is None:
            return self._metadata(contract, False, "owner_unverified", "unverified")

        if mount_stat.st_uid != self._expected_uid:
            return self._metadata(contract, False, "wrong_owner", "mismatch")

        if mount_stat.st_mode & 0o077:
            return self._metadata(contract, False, "permissions_too_open", "matched")

        return self._metadata(contract, True, "ready", "matched")

    def _metadata(
        self,
        contract: CredentialMountContract,
        ready: bool,
        status: str,
        owner_check: str,
    ) -> CredentialMountValidation:
        host_source = (
            "rejected"
            if status
            in {
                "forbidden_human_home",
                "host_path_not_configured",
                "host_path_not_service_user",
                "unexpected_host_directory",
            }
            else "dedicated_service_user"
        )
        return CredentialMountValidation(
            provider=contract.provider,
            ready=ready,
            status=status,
            configured=bool(contract.host_path and contract.container_path),
            host_source=host_source,
            container_target=contract.container_target,
            mount_mode=self.mount_mode if self.mount_mode in ALLOWED_MOUNT_MODES else "invalid",
            owner_check=owner_check,
        )

    def validate_mounts(self) -> dict[str, CredentialMountValidation]:
        """Return internal validation objects keyed by provider name."""

        return {contract.provider: self._validate_contract(contract) for contract in self._contracts()}

    def get_readiness_metadata(self) -> dict[str, Any]:
        """Return sanitized mount readiness metadata for health/capabilities APIs."""

        validations = self.validate_mounts()
        ready = all(item.ready for item in validations.values())
        return {
            "status": "ready" if ready else "degraded",
            "ready": ready,
            "host_policy": "dedicated_service_user_only",
            "required_owner": self.expected_owner_name,
            "mounts": {
                provider: validation.to_metadata()
                for provider, validation in validations.items()
            },
        }
