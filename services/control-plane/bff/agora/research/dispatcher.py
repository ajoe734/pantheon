"""Governed dataset resolution retained for BFF request projection."""
from __future__ import annotations

from typing import Any, Dict, Optional


def resolve_governed_dataset(
    stage: Dict[str, Any], plan: Optional[Dict[str, Any]] = None, *,
    dataset_store: Optional[Any] = None, tenant_id: Optional[str] = None, user_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Resolve canonical input references into tenant-scoped dataset inputs."""
    if stage.get("dataset"): return stage["dataset"]
    if plan and plan.get("dataset"): return plan["dataset"]
    input_refs = stage.get("input_refs") or (plan.get("input_refs") if plan else None)
    if not isinstance(input_refs, (list, tuple)): return None
    refs = []
    for r in input_refs:
        v = (r.get("id") or r.get("ref") or r.get("uri")) if isinstance(r, dict) else r
        if v and str(v).strip(): refs.append(str(v).strip())
    if not refs: return None
    tenant = str(tenant_id or stage.get("tenant_id") or (plan or {}).get("tenant_id") or "").strip()
    user = str(user_id or stage.get("user_id") or (plan or {}).get("user_id") or "").strip()
    store = dataset_store
    if store is None:
        try:
            from ..dataset_extraction.router import _default_store
            store = _default_store()
        except Exception: return None
    if store is None: return None
    strategy = str((plan or {}).get("strategy_id") or stage.get("strategy_id") or "strategy-default")
    for ref in refs:
        clean = ref.split(":", 1)[-1] if ":" in ref else ref
        record = store.get_by_ref(ref, tenant_id=tenant, user_id=user) if hasattr(store, "get_by_ref") else (store.get(clean, tenant_id=tenant, user_id=user) or store.get(ref, tenant_id=tenant, user_id=user) if hasattr(store, "get") else None)
        if record is None and hasattr(store, "_records"):
            record = next((item for item in store._records.values()
                if (not tenant or getattr(item, "tenant_id", None) == tenant)
                and (not user or getattr(item, "user_id", None) == user)
                and (getattr(item, "evidence_id", None) in (ref, clean)
                     or getattr(item, "dataset_version_id", None) in (ref, clean))), None)
        if record is None: continue
        content = getattr(record, "content", {}) or {}
        dataset = dict(content) if isinstance(content, dict) else {"records": content}
        version = getattr(record, "dataset_version_id", clean)
        dataset.update({
            "dataset_id": ref if ref.startswith("dataset:") else f"dataset:{ref}",
            "strategy_id": strategy, "source_dataset_refs": refs, "dataset_version_id": version,
            "tenant_id": getattr(record, "tenant_id", tenant), "user_id": getattr(record, "user_id", user),
            "lineage_ref": f"lineage://agora/dataset/{version}",
        })
        if getattr(record, "learning_eligible", None) is not None: dataset.setdefault("learning_eligible", record.learning_eligible)
        return dataset
    return None
