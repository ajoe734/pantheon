"""Connector projection: map a SourceConnector into exactly one product registry role.

Each SourceConnector has a ``natural_role`` derived from its ``source_type``.
``project_connector`` always emits entries for that natural role only.  If a
vendor genuinely belongs in both registries (e.g. TEJ as market data AND as a
strategy seed source), the caller must register two connectors with distinct
``connector_id`` values — one per role — then call ``project_connector`` for
each.  The module will raise ``ConnectorProjectionError`` if asked to project a
connector into its non-natural role without an explicit ``force_role`` override
that carries an audit note.

SourceType → registry role:
  MARKET, FILING, NEWS, SOCIAL, MACRO  → data_source
  PAPER, REPO, INTERNAL_NOTE, TELEMETRY, ALPHA_DB → strategy_seed_source
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Mapping, Sequence

from ..connectors.base import SourceConnector, SourceType
from .data_source_registry import (
    DataSourceClass,
    DataSourceEntry,
    DataSourceLifecycleState,
    DataSourceRegistryError,
)
from .strategy_seed_source_registry import (
    StrategySeedSourceClass,
    StrategySeedSourceEntry,
    StrategySeedSourceLifecycleState,
    StrategySeedSourceRegistryError,
)


class ConnectorProjectionError(ValueError):
    """Raised when a connector cannot be projected into the requested role."""


class RegistryRole(str, Enum):
    DATA_SOURCE = "data_source"
    STRATEGY_SEED_SOURCE = "strategy_seed_source"


# Natural role for each SourceType.
_NATURAL_ROLE: dict[SourceType, RegistryRole] = {
    SourceType.MARKET: RegistryRole.DATA_SOURCE,
    SourceType.FILING: RegistryRole.DATA_SOURCE,
    SourceType.NEWS: RegistryRole.DATA_SOURCE,
    SourceType.SOCIAL: RegistryRole.DATA_SOURCE,
    SourceType.MACRO: RegistryRole.DATA_SOURCE,
    SourceType.PAPER: RegistryRole.STRATEGY_SEED_SOURCE,
    SourceType.REPO: RegistryRole.STRATEGY_SEED_SOURCE,
    SourceType.INTERNAL_NOTE: RegistryRole.STRATEGY_SEED_SOURCE,
    SourceType.TELEMETRY: RegistryRole.STRATEGY_SEED_SOURCE,
    SourceType.ALPHA_DB: RegistryRole.STRATEGY_SEED_SOURCE,
}

# Source-type to DataSourceClass for data-side projection.
_SOURCE_TYPE_TO_DATA_CLASS: dict[SourceType, DataSourceClass] = {
    SourceType.MARKET: DataSourceClass.MARKET_DAILY,
    SourceType.FILING: DataSourceClass.FILING_EVENT,
    SourceType.NEWS: DataSourceClass.NEWS,
    SourceType.SOCIAL: DataSourceClass.NEWS,
    SourceType.MACRO: DataSourceClass.MACRO,
}

# Source-type to StrategySeedSourceClass for seed-side projection.
_SOURCE_TYPE_TO_SEED_CLASS: dict[SourceType, StrategySeedSourceClass] = {
    SourceType.PAPER: StrategySeedSourceClass.PAPER,
    SourceType.REPO: StrategySeedSourceClass.REPO,
    SourceType.INTERNAL_NOTE: StrategySeedSourceClass.INTERNAL_NOTE,
    SourceType.TELEMETRY: StrategySeedSourceClass.TELEMETRY,
    SourceType.ALPHA_DB: StrategySeedSourceClass.ALPHA_DB,
}


def _coerce_source_type(value: SourceType | str) -> SourceType:
    if isinstance(value, SourceType):
        return value
    return SourceType(str(value).strip())


def natural_role(connector: SourceConnector) -> RegistryRole:
    """Return the natural registry role for a connector based on its source_type."""
    source_type = _coerce_source_type(connector.source_type)
    role = _NATURAL_ROLE.get(source_type)
    if role is None:
        raise ConnectorProjectionError(
            f"No natural registry role defined for source_type '{source_type.value}'"
        )
    return role


def project_to_data_source(
    connector: SourceConnector,
    *,
    data_source_id: str,
    datasets: Sequence[Mapping[str, Any]],
    allowed_use: Sequence[str],
    update_frequency: str,
    lifecycle_state: DataSourceLifecycleState | str = DataSourceLifecycleState.CANDIDATE,
    entitlement_tags: Sequence[str] = (),
    universe_policy_ref: str | None = None,
    metadata: Mapping[str, Any] | None = None,
    force_role: bool = False,
    force_role_note: str = "",
) -> DataSourceEntry:
    """Project a SourceConnector into a DataSourceEntry.

    If the connector's natural role is not ``data_source``, the call is
    rejected unless ``force_role=True`` and ``force_role_note`` is non-empty.
    """
    role = natural_role(connector)
    if role != RegistryRole.DATA_SOURCE:
        if not force_role:
            raise ConnectorProjectionError(
                f"Connector '{connector.connector_id}' has natural role "
                f"'{role.value}', not 'data_source'. "
                "Register a separate connector for each role or pass "
                "force_role=True with a force_role_note."
            )
        if not str(force_role_note).strip():
            raise ConnectorProjectionError(
                "force_role=True requires a non-empty force_role_note explaining the dual-role intent."
            )

    source_type = _coerce_source_type(connector.source_type)
    source_class = _SOURCE_TYPE_TO_DATA_CLASS.get(source_type)
    if source_class is None:
        if not force_role:
            raise ConnectorProjectionError(
                f"source_type '{source_type.value}' has no default DataSourceClass mapping; "
                "provide force_role=True or use a dedicated connector."
            )
        source_class = DataSourceClass.MARKET_DAILY

    return DataSourceEntry(
        data_source_id=data_source_id,
        provider=connector.provider,
        source_class=source_class.value,
        datasets=list(datasets),
        license_scope=connector.license_scope,
        allowed_use=list(allowed_use),
        update_frequency=update_frequency,
        lifecycle_state=lifecycle_state.value if isinstance(lifecycle_state, DataSourceLifecycleState) else str(lifecycle_state),
        entitlement_tags=list(entitlement_tags),
        universe_policy_ref=universe_policy_ref,
        connector_id=connector.connector_id,
        metadata=dict(metadata or {}),
    )


def project_to_strategy_seed_source(
    connector: SourceConnector,
    *,
    strategy_seed_source_id: str,
    source_scope: str,
    allowed_use: Sequence[str],
    lifecycle_state: StrategySeedSourceLifecycleState | str = StrategySeedSourceLifecycleState.CANDIDATE,
    entitlement_tags: Sequence[str] = (),
    crawler_policy_ref: str | None = None,
    expected_outputs: Sequence[str] = (),
    metadata: Mapping[str, Any] | None = None,
    force_role: bool = False,
    force_role_note: str = "",
) -> StrategySeedSourceEntry:
    """Project a SourceConnector into a StrategySeedSourceEntry.

    If the connector's natural role is not ``strategy_seed_source``, the call
    is rejected unless ``force_role=True`` and ``force_role_note`` is non-empty.
    """
    role = natural_role(connector)
    if role != RegistryRole.STRATEGY_SEED_SOURCE:
        if not force_role:
            raise ConnectorProjectionError(
                f"Connector '{connector.connector_id}' has natural role "
                f"'{role.value}', not 'strategy_seed_source'. "
                "Register a separate connector for each role or pass "
                "force_role=True with a force_role_note."
            )
        if not str(force_role_note).strip():
            raise ConnectorProjectionError(
                "force_role=True requires a non-empty force_role_note explaining the dual-role intent."
            )

    source_type = _coerce_source_type(connector.source_type)
    source_class = _SOURCE_TYPE_TO_SEED_CLASS.get(source_type)
    if source_class is None:
        if not force_role:
            raise ConnectorProjectionError(
                f"source_type '{source_type.value}' has no default StrategySeedSourceClass mapping; "
                "provide force_role=True or use a dedicated connector."
            )
        source_class = StrategySeedSourceClass.INTERNAL_NOTE

    return StrategySeedSourceEntry(
        strategy_seed_source_id=strategy_seed_source_id,
        provider=connector.provider,
        source_class=source_class.value,
        source_scope=source_scope,
        license_scope=connector.license_scope,
        allowed_use=list(allowed_use),
        lifecycle_state=lifecycle_state.value if isinstance(lifecycle_state, StrategySeedSourceLifecycleState) else str(lifecycle_state),
        entitlement_tags=list(entitlement_tags),
        crawler_policy_ref=crawler_policy_ref,
        expected_outputs=list(expected_outputs),
        connector_id=connector.connector_id,
        metadata=dict(metadata or {}),
    )
