"""Registry split layer: product-level DataSourceRegistry and StrategySeedSourceRegistry.

These two registries enforce the semantic boundary between data supply sources
(market, filing, news, macro) and strategy idea/seed sources (papers, repos,
internal notes, alpha DBs).  The underlying source_ingestion connector plane
is unchanged; the registries add product-level classification on top.
"""

from .data_source_registry import (
    DataSourceEntry,
    DataSourceLifecycleState,
    DataSourceRegistry,
    DataSourceRegistryError,
)
from .strategy_seed_source_registry import (
    StrategySeedSourceEntry,
    StrategySeedSourceLifecycleState,
    StrategySeedSourceRegistry,
    StrategySeedSourceRegistryError,
)
from .jsonl_store import JsonlRegistryStore
from .connector_projection import (
    ConnectorProjectionError,
    RegistryRole,
    natural_role,
    project_to_data_source,
    project_to_strategy_seed_source,
)

__all__ = [
    "DataSourceEntry",
    "DataSourceLifecycleState",
    "DataSourceRegistry",
    "DataSourceRegistryError",
    "StrategySeedSourceEntry",
    "StrategySeedSourceLifecycleState",
    "StrategySeedSourceRegistry",
    "StrategySeedSourceRegistryError",
    "JsonlRegistryStore",
    "ConnectorProjectionError",
    "RegistryRole",
    "natural_role",
    "project_to_data_source",
    "project_to_strategy_seed_source",
]
