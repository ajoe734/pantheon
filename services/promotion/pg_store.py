from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

_CP_GOV = Path(__file__).resolve().parent.parent / "control-plane" / "governance"
if str(_CP_GOV) not in sys.path:
    sys.path.insert(0, str(_CP_GOV))

from approval_decision import ApprovalDecision, ApprovalDecisionStore  # type: ignore
from deployment_plan import DeploymentPlan, DeploymentPlanStore  # type: ignore

from services.foundation.postgres_json_store import PostgresJsonOwnerStore


class PostgresPromotionApprovalStore(ApprovalDecisionStore):
    """Postgres owner store for promotion-local ApprovalDecision records."""

    def __init__(
        self,
        dsn: str,
        table: str = "promotion.approval_decisions",
        bootstrap: bool = True,
    ) -> None:
        self._records = PostgresJsonOwnerStore(
            dsn=dsn,
            table=table,
            owner_service="promotion-svc",
            bootstrap=bootstrap,
        )
        super().__init__(storage_path=None)
        self._refresh_from_postgres()

    def put(self, decision: ApprovalDecision) -> None:
        self._refresh_from_postgres()
        super().put(decision)

    def get(self, decision_id: str) -> ApprovalDecision | None:
        self._refresh_from_postgres()
        return super().get(decision_id)

    def list_all(self) -> list[ApprovalDecision]:
        self._refresh_from_postgres()
        return super().list_all()

    def _save(self) -> None:
        for decision in self._decisions.values():
            self._records.put(decision.decision_id, decision.to_dict())

    def _refresh_from_postgres(self) -> None:
        self._decisions = {}
        for record in self._records.list_all():
            decision = ApprovalDecision.from_dict(record)
            self._decisions[decision.decision_id] = decision


class PostgresDeploymentPlanStore(DeploymentPlanStore):
    """Postgres owner store for DeploymentPlan records created by promotion-svc."""

    def __init__(
        self,
        dsn: str,
        table: str = "promotion.deployment_plans",
        bootstrap: bool = True,
    ) -> None:
        self._records = PostgresJsonOwnerStore(
            dsn=dsn,
            table=table,
            owner_service="promotion-svc",
            bootstrap=bootstrap,
        )
        super().__init__(storage_path=None)
        self._refresh_from_postgres()

    def put(self, plan: DeploymentPlan) -> None:
        self._refresh_from_postgres()
        super().put(plan)

    def get(self, plan_id: str) -> DeploymentPlan | None:
        self._refresh_from_postgres()
        return super().get(plan_id)

    def list_all(self) -> list[DeploymentPlan]:
        self._refresh_from_postgres()
        return super().list_all()

    def _save(self) -> None:
        for plan in self._plans.values():
            self._records.put(plan.plan_id, plan.to_dict())

    def _refresh_from_postgres(self) -> None:
        self._plans = {}
        for record in self._records.list_all():
            plan = DeploymentPlan.from_dict(record)
            self._plans[plan.plan_id] = plan


class JsonPromotionExtensionStore:
    def __init__(self, path: Path):
        self._path = path
        self._items: dict[str, dict[str, Any]] = {}
        if self._path.exists():
            text = self._path.read_text(encoding="utf-8")
            if text.strip():
                self._items = json.loads(text)

    def get(self, key: str) -> dict[str, Any]:
        return dict(self._items.get(key, {}))

    def put(self, key: str, value: dict[str, Any]) -> None:
        self._items[key] = dict(value)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._items, indent=2), encoding="utf-8")


class PostgresPromotionExtensionStore:
    """Postgres owner store for promotion deployment extension payloads."""

    def __init__(
        self,
        dsn: str,
        table: str = "promotion.deployment_plan_extensions",
        bootstrap: bool = True,
    ) -> None:
        self._records = PostgresJsonOwnerStore(
            dsn=dsn,
            table=table,
            owner_service="promotion-svc",
            bootstrap=bootstrap,
        )

    def get(self, key: str) -> dict[str, Any]:
        payload = self._records.get(key)
        return dict(payload or {})

    def put(self, key: str, value: dict[str, Any]) -> None:
        self._records.put(key, dict(value))


def _promotion_backend() -> str:
    return os.getenv("PROMOTION_STORE_BACKEND", "json").strip().lower()


def _promotion_dsn() -> str:
    dsn = os.getenv("PROMOTION_STORE_DSN") or os.getenv("DATABASE_URL")
    if not dsn:
        raise ValueError("PROMOTION_STORE_DSN or DATABASE_URL is required for Postgres promotion store")
    return dsn


def _bootstrap() -> bool:
    return os.getenv("PROMOTION_STORE_BOOTSTRAP", "1").strip().lower() not in ("0", "false", "no")


def build_promotion_approval_store(path: Path) -> ApprovalDecisionStore | PostgresPromotionApprovalStore:
    backend = _promotion_backend()
    if backend in ("", "json"):
        return ApprovalDecisionStore(str(path))
    if backend != "postgres":
        raise ValueError("PROMOTION_STORE_BACKEND must be json or postgres")
    return PostgresPromotionApprovalStore(
        dsn=_promotion_dsn(),
        table=os.getenv("PROMOTION_APPROVAL_STORE_TABLE", "promotion.approval_decisions"),
        bootstrap=_bootstrap(),
    )


def build_promotion_deployment_store(path: Path) -> DeploymentPlanStore | PostgresDeploymentPlanStore:
    backend = _promotion_backend()
    if backend in ("", "json"):
        return DeploymentPlanStore(str(path))
    if backend != "postgres":
        raise ValueError("PROMOTION_STORE_BACKEND must be json or postgres")
    return PostgresDeploymentPlanStore(
        dsn=_promotion_dsn(),
        table=os.getenv("PROMOTION_DEPLOYMENT_STORE_TABLE", "promotion.deployment_plans"),
        bootstrap=_bootstrap(),
    )


def build_promotion_extension_store(
    path: Path,
) -> JsonPromotionExtensionStore | PostgresPromotionExtensionStore:
    backend = _promotion_backend()
    if backend in ("", "json"):
        return JsonPromotionExtensionStore(path)
    if backend != "postgres":
        raise ValueError("PROMOTION_STORE_BACKEND must be json or postgres")
    return PostgresPromotionExtensionStore(
        dsn=_promotion_dsn(),
        table=os.getenv("PROMOTION_DEPLOYMENT_EXT_STORE_TABLE", "promotion.deployment_plan_extensions"),
        bootstrap=_bootstrap(),
    )
