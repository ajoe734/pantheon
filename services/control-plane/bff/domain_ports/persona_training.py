"""Domain port for Persona reads, Training Session trainer/replay, and rapid-eval.

This module provides typed domain ports for:
- Persona, session, teaching, and capability reads owned by the Persona
  Registry read surface
- Trainer session, controls, message, preview, and replay flows owned by
  the existing Training Session service
- Rapid-evaluation caller-evidence ownership assignment (ACG-02-017)

None of these ports reimplement persistence or HTTP transport. Every method
delegates to an injected store/backend that already owns that
responsibility -- ``read_store.ReadSurfaceStore`` in the running BFF, or a
service-backed double in tests. This module intentionally does not import
``main`` or ``read_store`` so it stays composable and does not change the
behavior of either file.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional


# ---------------------------------------------------------------------------
# Persona Registry Reads Port
# ---------------------------------------------------------------------------

class PersonaRegistryReadsPort:
    """Port for persona, session, teaching, and capability reads.

    Delegates to the injected ``store`` (duck-typed to the Persona
    Registry-backed methods already implemented by
    ``read_store.ReadSurfaceStore``: ``list_personas``, ``get_persona``,
    ``get_bindings_for_persona``, ``list_sessions_for_persona``,
    ``list_teaching_sessions_for_persona``, and
    ``get_capability_snapshot_for_persona``).
    """

    def __init__(self, *, store: Optional[Any] = None) -> None:
        self._store = store

    def _require_store(self) -> Any:
        if self._store is None:
            raise RuntimeError(
                "PersonaRegistryReadsPort requires a Persona Registry-backed store"
            )
        return self._store

    def list_personas(
        self,
        *,
        lifecycle_state: Optional[str] = None,
        mandate: Optional[str] = None,
        strategy_family: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        store = self._require_store()
        return (
            store.list_personas(
                lifecycle_state=lifecycle_state,
                mandate=mandate,
                strategy_family=strategy_family,
            )
            or []
        )

    def get_persona(self, persona_id: Optional[str]) -> Optional[Dict[str, Any]]:
        store = self._require_store()
        persona = store.get_persona(persona_id)
        if not persona:
            return None
        payload = dict(persona)
        payload["bindings"] = store.get_bindings_for_persona(persona_id) or []
        return payload

    def list_persona_sessions(
        self, persona_id: str, *, status: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        store = self._require_store()
        return store.list_sessions_for_persona(persona_id, status=status) or []

    def list_persona_teaching_sessions(
        self, persona_id: str, *, status: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        store = self._require_store()
        return store.list_teaching_sessions_for_persona(persona_id, status=status) or []

    def get_persona_capabilities(self, persona_id: str) -> Optional[Dict[str, Any]]:
        store = self._require_store()
        return store.get_capability_snapshot_for_persona(persona_id)


# ---------------------------------------------------------------------------
# Training Session Trainer / Replay Port
# ---------------------------------------------------------------------------

class TrainingSessionTrainerPort:
    """Port for trainer session, controls, message, preview, and replay flows.

    Delegates to the injected ``training`` store, which is expected to be
    backed by the Training Session service -- see
    ``read_store.ReadSurfaceStore.create_trainer_session`` and the
    neighboring trainer/replay methods, which already call the Training
    Session HTTP API at ``PANTHEON_TRAINING_SESSION_API_URL``. This port owns
    no HTTP transport or local persistence of its own.
    """

    def __init__(self, *, training: Optional[Any] = None) -> None:
        self._training = training

    def _require_training(self) -> Any:
        if self._training is None:
            raise RuntimeError(
                "TrainingSessionTrainerPort requires a Training Session-backed store"
            )
        return self._training

    def create_trainer_session(self, **kwargs: Any) -> Optional[Dict[str, Any]]:
        return self._require_training().create_trainer_session(**kwargs)

    def list_trainer_sessions(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self._require_training().list_trainer_sessions(**kwargs) or []

    def get_trainer_session(self, session_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return self._require_training().get_trainer_session(session_id)

    def get_trainer_controls(self, session_id: str) -> Optional[Dict[str, Any]]:
        return self._require_training().get_trainer_controls(session_id)

    def patch_trainer_controls(self, session_id: str, **kwargs: Any) -> Optional[Dict[str, Any]]:
        return self._require_training().patch_trainer_controls(session_id, **kwargs)

    def append_trainer_message(self, session_id: str, **kwargs: Any) -> Optional[Dict[str, Any]]:
        return self._require_training().append_trainer_message(session_id, **kwargs)

    def get_trainer_preview(self, session_id: str, **kwargs: Any) -> Optional[Dict[str, Any]]:
        return self._require_training().get_trainer_preview(session_id, **kwargs)

    def refresh_trainer_preview(self, session_id: str, **kwargs: Any) -> Optional[Dict[str, Any]]:
        return self._require_training().refresh_trainer_preview(session_id, **kwargs)

    def list_trainer_replays(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self._require_training().list_trainer_replays(**kwargs) or []

    def get_trainer_replay(self, session_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return self._require_training().get_trainer_replay(session_id)

    def commit_trainer_replay(self, session_id: str, **kwargs: Any) -> Optional[Dict[str, Any]]:
        return self._require_training().commit_trainer_replay(session_id, **kwargs)

    def discard_trainer_replay(self, session_id: str, **kwargs: Any) -> Optional[Dict[str, Any]]:
        return self._require_training().discard_trainer_replay(session_id, **kwargs)


# ---------------------------------------------------------------------------
# Rapid Evaluation Ownership (ACG-02-017)
# ---------------------------------------------------------------------------

RAPID_EVAL_OWNER = "training-session"
RAPID_EVAL_OWNER_EVIDENCE = (
    "services/training-session/rapid_eval_integration.py defines run_rapid_eval "
    "and is the only implementation module for rapid-evaluation execution in "
    "this repository, with its own dedicated test suite "
    "(services/training-session/test_rapid_eval_integration.py). "
    "services/control-plane/persona/persona_strategy_discovery.py only emits a "
    "run_rapid_eval RecommendedAction label; it does not import "
    "rapid_eval_integration or implement an evaluation backend, so it is not a "
    "competing owner. No implementation exists under services/research."
)


@dataclass(frozen=True)
class RapidEvaluationOwnership:
    """Caller-evidence record for the rapid-evaluation canonical owner.

    ACG-02-017 requires tracing production/test callers and selecting an
    existing owner before migration, and forbids inventing a new owner
    solely to retain unused JSON behavior. See ``RAPID_EVAL_OWNER_EVIDENCE``
    for the traced evidence backing this assignment.
    """

    owner: str = RAPID_EVAL_OWNER
    evidence: str = RAPID_EVAL_OWNER_EVIDENCE
    implementation_module: str = "services/training-session/rapid_eval_integration.py"
    implementation_symbol: str = "run_rapid_eval"


class RapidEvaluationPort:
    """Port for rapid-evaluation requests, bound to the Training Session owner.

    This port does not implement rapid-evaluation execution or persistence.
    It delegates to injected ``create``/``get`` callables that a caller binds
    to the Training Session owner (``rapid_eval_integration.run_rapid_eval``
    or an HTTP client for the Training Session service). Construction
    without a backend still exposes the resolved ownership so callers can
    assert it without invoking execution.
    """

    ownership: RapidEvaluationOwnership = RapidEvaluationOwnership()

    def __init__(
        self,
        *,
        create: Optional[Callable[..., Optional[Dict[str, Any]]]] = None,
        get: Optional[Callable[..., Optional[Dict[str, Any]]]] = None,
    ) -> None:
        self._create = create
        self._get = get

    @property
    def owner(self) -> str:
        return self.ownership.owner

    def create_rapid_eval(self, session_id: str, **kwargs: Any) -> Optional[Dict[str, Any]]:
        if self._create is None:
            raise RuntimeError(
                f"RapidEvaluationPort has no backend bound to its owner ({self.owner})"
            )
        return self._create(session_id, **kwargs)

    def get_rapid_eval(self, eval_id: Optional[str], **kwargs: Any) -> Optional[Dict[str, Any]]:
        if self._get is None:
            raise RuntimeError(
                f"RapidEvaluationPort has no backend bound to its owner ({self.owner})"
            )
        return self._get(eval_id, **kwargs)


# ---------------------------------------------------------------------------
# Composed Domain Port
# ---------------------------------------------------------------------------

class PersonaTrainingDomainPort:
    """Consolidated domain port for Persona, Trainer/Replay, and Rapid-Eval."""

    def __init__(
        self,
        *,
        persona_port: Optional[PersonaRegistryReadsPort] = None,
        trainer_port: Optional[TrainingSessionTrainerPort] = None,
        rapid_eval_port: Optional[RapidEvaluationPort] = None,
    ) -> None:
        self.persona = persona_port or PersonaRegistryReadsPort()
        self.trainer = trainer_port or TrainingSessionTrainerPort()
        self.rapid_eval = rapid_eval_port or RapidEvaluationPort()

    # Persona delegates
    def list_personas(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.persona.list_personas(**kwargs)

    def get_persona(self, persona_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return self.persona.get_persona(persona_id)

    def list_persona_sessions(self, persona_id: str, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.persona.list_persona_sessions(persona_id, **kwargs)

    def list_persona_teaching_sessions(self, persona_id: str, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.persona.list_persona_teaching_sessions(persona_id, **kwargs)

    def get_persona_capabilities(self, persona_id: str) -> Optional[Dict[str, Any]]:
        return self.persona.get_persona_capabilities(persona_id)

    # Trainer delegates
    def create_trainer_session(self, **kwargs: Any) -> Optional[Dict[str, Any]]:
        return self.trainer.create_trainer_session(**kwargs)

    def list_trainer_sessions(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.trainer.list_trainer_sessions(**kwargs)

    def get_trainer_session(self, session_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return self.trainer.get_trainer_session(session_id)

    def get_trainer_controls(self, session_id: str) -> Optional[Dict[str, Any]]:
        return self.trainer.get_trainer_controls(session_id)

    def patch_trainer_controls(self, session_id: str, **kwargs: Any) -> Optional[Dict[str, Any]]:
        return self.trainer.patch_trainer_controls(session_id, **kwargs)

    def append_trainer_message(self, session_id: str, **kwargs: Any) -> Optional[Dict[str, Any]]:
        return self.trainer.append_trainer_message(session_id, **kwargs)

    def get_trainer_preview(self, session_id: str, **kwargs: Any) -> Optional[Dict[str, Any]]:
        return self.trainer.get_trainer_preview(session_id, **kwargs)

    def refresh_trainer_preview(self, session_id: str, **kwargs: Any) -> Optional[Dict[str, Any]]:
        return self.trainer.refresh_trainer_preview(session_id, **kwargs)

    def list_trainer_replays(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.trainer.list_trainer_replays(**kwargs)

    def get_trainer_replay(self, session_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return self.trainer.get_trainer_replay(session_id)

    def commit_trainer_replay(self, session_id: str, **kwargs: Any) -> Optional[Dict[str, Any]]:
        return self.trainer.commit_trainer_replay(session_id, **kwargs)

    def discard_trainer_replay(self, session_id: str, **kwargs: Any) -> Optional[Dict[str, Any]]:
        return self.trainer.discard_trainer_replay(session_id, **kwargs)

    # Rapid-eval delegates
    @property
    def rapid_eval_owner(self) -> str:
        return self.rapid_eval.owner

    def create_rapid_eval(self, session_id: str, **kwargs: Any) -> Optional[Dict[str, Any]]:
        return self.rapid_eval.create_rapid_eval(session_id, **kwargs)

    def get_rapid_eval(self, eval_id: Optional[str], **kwargs: Any) -> Optional[Dict[str, Any]]:
        return self.rapid_eval.get_rapid_eval(eval_id, **kwargs)
