"""Governed source ingestion primitives for the SD-03 source/evidence plane."""

from .ingest_manager import IngestManager
from .interaction_intent_classifier import (
    IntentClassification,
    InteractionIntentClassificationError,
    InteractionIntentClassifier,
    InteractionPrimaryIntent,
    classify_interaction_intent,
)
from .interaction_source_store import (
    InteractionActorType,
    InteractionRedactionStatus,
    InteractionSourceRecord,
    InteractionSourceRecordError,
    InteractionSourceRecordStore,
    InteractionSourceSurface,
    InteractionVisibility,
)
from .negative_memory import (
    NegativeMemoryKind,
    NegativeMemoryMatch,
    NegativeMemoryWarningLevel,
    match_negative_memory,
)
from .trainer_seed_bridge import (
    TrainerSeedBridge,
    TrainerSeedBridgeError,
    TrainerSeedBridgeResult,
    TrainerSeedExtractionRef,
    TrainerSeedKind,
    trainer_seed_kind_from_text,
)
from .scheduler import (
    CrawlFrontierItem,
    IngestionScheduler,
    IngestBatch,
    JsonlIngestScheduleStore,
    ScheduledIngestResult,
    SourceWatermark,
)

__all__ = [
    "CrawlFrontierItem",
    "IngestBatch",
    "IngestManager",
    "IntentClassification",
    "InteractionActorType",
    "InteractionIntentClassificationError",
    "InteractionIntentClassifier",
    "InteractionPrimaryIntent",
    "InteractionRedactionStatus",
    "InteractionSourceRecord",
    "InteractionSourceRecordError",
    "InteractionSourceRecordStore",
    "InteractionSourceSurface",
    "InteractionVisibility",
    "NegativeMemoryKind",
    "NegativeMemoryMatch",
    "NegativeMemoryWarningLevel",
    "TrainerSeedBridge",
    "TrainerSeedBridgeError",
    "TrainerSeedBridgeResult",
    "TrainerSeedExtractionRef",
    "TrainerSeedKind",
    "IngestionScheduler",
    "JsonlIngestScheduleStore",
    "match_negative_memory",
    "ScheduledIngestResult",
    "SourceWatermark",
    "classify_interaction_intent",
    "trainer_seed_kind_from_text",
]
