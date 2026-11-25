"""BFF-owned persona and strategy display names for management projections.

Frontends must not look names up from ids themselves (KW-02 research notes:
``display_name`` is resolved by the BFF). A ``DisplayNameResolver`` maps persona
ids and strategy ids to display names for one request:

* each directory (personas, strategy labels) is read at most once per resolver,
  and at most once per request across resolvers (``cached_owner_call``);
* a directory read failure or an unknown id falls back to the id itself and the
  annotated row carries ``<prefix>_name_missing: true``;
* annotation is additive: existing fields are never rewritten.
"""
from __future__ import annotations

import logging
import os
import re
import threading
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Set, Tuple

from .owner_reads import cached_owner_call, selected_tenant, shared_persona_directory_list

log = logging.getLogger(__name__)

_source_lock = threading.Lock()
_source: Optional[Any] = None


def configure_display_name_source(read_store: Optional[Any]) -> None:
    """Register the composed read store (``list_personas`` / ``get_strategy_labels``)."""
    global _source
    with _source_lock:
        _source = read_store


def _composed_read_store() -> Optional[Any]:
    """The read store of the composed persona service (set by the app composition root)."""
    try:
        from ..personas import service as persona_service

        svc = getattr(persona_service, "_composed_persona_service", None)
        return svc.get_read_store() if svc is not None else None
    except Exception:  # noqa: BLE001 - no composed store means names fall back to ids
        return None


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _persona_label(record: Mapping[str, Any]) -> str:
    meta = record.get("metadata") if isinstance(record.get("metadata"), Mapping) else {}
    for candidate in (
        record.get("display_name"),
        record.get("displayName"),
        record.get("name"),
        meta.get("display_name"),
        meta.get("name"),
    ):
        text = _text(candidate)
        if text:
            return text
    return ""


def _strategy_labels_configured(store: Any) -> bool:
    """True when ``store.get_strategy_labels`` is backed by a configured or injected source."""
    if store is None or not hasattr(store, "get_strategy_labels"):
        return False
    if not hasattr(store, "research_knowledge_source"):
        return True
    if "get_strategy_labels" in getattr(store, "__dict__", {}):
        return True
    rks = getattr(store, "research_knowledge_source", None)
    if getattr(rks, "_has_injected_strategy_specs", False):
        return True
    return bool(os.getenv("PANTHEON_REGISTRY_API_URL") or os.getenv("PANTHEON_REGISTRY_URL"))


class DisplayNameResolver:
    """Resolve persona and strategy display names with one read per directory."""

    def __init__(
        self,
        read_store: Optional[Any] = None,
        *,
        persona_loader: Optional[Callable[[], Iterable[Mapping[str, Any]]]] = None,
        strategy_loader: Optional[Callable[[], Mapping[str, Any]]] = None,
        binding_loader: Optional[Callable[[], Iterable[Mapping[str, Any]]]] = None,
    ) -> None:
        composed = _composed_read_store()
        if _source is not None:
            p_store = s_store = b_store = _source
        else:
            p_store = (
                read_store
                if read_store is not None and hasattr(read_store, "list_personas")
                else (composed or read_store)
            )
            s_store = (
                read_store
                if read_store is not None and _strategy_labels_configured(read_store)
                else (composed if _strategy_labels_configured(composed) else None)
            )
            b_store = (
                read_store
                if read_store is not None and hasattr(read_store, "list_runtime_bindings")
                else (composed or read_store)
            )
        self._store = p_store
        if persona_loader is None and p_store is not None and hasattr(p_store, "list_personas"):
            persona_loader = lambda: shared_persona_directory_list(p_store, p_store.list_personas)  # noqa: E731
        if strategy_loader is None and s_store is not None and hasattr(s_store, "get_strategy_labels"):
            strategy_loader = lambda: cached_owner_call(  # noqa: E731
                ("display_names_strategy_labels", id(s_store), selected_tenant.get()),
                s_store.get_strategy_labels,
            )
        if binding_loader is None and b_store is not None and hasattr(b_store, "list_runtime_bindings"):
            binding_loader = lambda: cached_owner_call(  # noqa: E731
                ("display_names_runtime_bindings", id(b_store), selected_tenant.get()),
                b_store.list_runtime_bindings,
            )
        self._binding_loader = binding_loader
        self._bindings: Optional[Dict[str, Tuple[str, str]]] = None
        self._persona_loader = persona_loader
        self._strategy_loader = strategy_loader
        self._personas: Optional[Dict[str, str]] = None
        self._strategies: Optional[Dict[str, str]] = None
        self._persona_error: Optional[str] = None
        self._strategy_error: Optional[str] = None

    # -- directory reads (each at most once per resolver) --------------------
    def _persona_names(self) -> Dict[str, str]:
        if self._personas is None:
            names: Dict[str, str] = {}
            if self._persona_loader is not None:
                try:
                    for record in self._persona_loader() or []:
                        if not isinstance(record, Mapping):
                            continue
                        pid = _text(record.get("persona_id") or record.get("id"))
                        label = _persona_label(record)
                        if pid and label and label != pid:
                            names[pid] = label
                    p_port = getattr(getattr(self._store, "persona_capital_runtime", None), "persona", None)
                    if p_port is not None and callable(getattr(p_port, "get_surface_status", None)):
                        if p_port.get_surface_status().get("source") == "unavailable":
                            self._persona_error = "persona_store_unavailable"
                except Exception as exc:  # noqa: BLE001 - names degrade to ids, rows stay visible
                    self._persona_error = f"{type(exc).__name__}: {exc}"
                    log.warning("persona display-name read failed: %s", exc)
            self._personas = names
        return self._personas

    def _strategy_names(self) -> Dict[str, str]:
        if self._strategies is None:
            names: Dict[str, str] = {}
            if self._strategy_loader is not None:
                try:
                    for sid, label in (self._strategy_loader() or {}).items():
                        clean_sid = _text(sid)
                        text = _text(
                            (label.get("title") or label.get("name"))
                            if isinstance(label, Mapping)
                            else label
                        )
                        if clean_sid and text and text != clean_sid:
                            names[clean_sid] = text
                except Exception as exc:  # noqa: BLE001
                    self._strategy_error = f"{type(exc).__name__}: {exc}"
                    log.warning("strategy display-name read failed: %s", exc)
            self._strategies = names
        return self._strategies

    @property
    def errors(self) -> Dict[str, str]:
        out: Dict[str, str] = {}
        if self._persona_error:
            out["personas"] = self._persona_error
        if self._strategy_error:
            out["strategies"] = self._strategy_error
        return out

    @property
    def failed(self) -> bool:
        return bool(self._persona_error or self._strategy_error)

    def surface(self) -> Dict[str, Any]:
        errors = self.errors
        if errors:
            return {
                "status": "degraded",
                "source": "display_name_resolver",
                "reason": "; ".join(f"{k}: {v}" for k, v in sorted(errors.items())),
                "errors": errors,
            }
        return {
            "status": "ok",
            "source": "display_name_resolver",
        }

    def _binding_ids(self) -> Dict[str, Tuple[str, str]]:
        """``binding_id -> (persona_id, strategy_id)`` from one runtime-binding read."""
        if self._bindings is None:
            mapping: Dict[str, Tuple[str, str]] = {}
            if self._binding_loader is not None:
                try:
                    for rec in self._binding_loader() or []:
                        if not isinstance(rec, Mapping):
                            continue
                        pair = (_text(rec.get("persona_id")), _text(rec.get("strategy_id")))
                        for key in ("binding_id", "id", "runtime_binding_id"):
                            bid = _text(rec.get(key))
                            if bid:
                                mapping.setdefault(bid, pair)
                except Exception as exc:  # noqa: BLE001
                    log.warning("runtime binding display-name read failed: %s", exc)
            self._bindings = mapping
        return self._bindings

    def binding(self, binding_id: Any) -> Tuple[str, str]:
        """Return ``(persona_id, strategy_id)`` for a binding id, empty strings if unknown."""
        return self._binding_ids().get(_text(binding_id), ("", ""))

    # -- lookups --------------------------------------------------------------
    def persona(self, persona_id: Any) -> Tuple[str, bool]:
        """Return ``(display_name, name_missing)``; falls back to the id."""
        clean = _text(persona_id)
        name = self._persona_names().get(clean)
        return (name, False) if name else (clean, True)

    def strategy(self, strategy_id: Any) -> Tuple[str, bool]:
        clean = _text(strategy_id)
        name = self._strategy_names().get(clean)
        return (name, False) if name else (clean, True)

    # -- projection helpers ---------------------------------------------------
    def annotate(
        self,
        row: Any,
        *,
        persona_keys: Iterable[str] = ("persona_id",),
        strategy_keys: Iterable[str] = ("strategy_id",),
    ) -> Any:
        """Add ``<x>_display_name`` (and ``<x>_name_missing`` on fallback) next to each id.

        ``persona_id`` -> ``persona_display_name``; ``chair_persona_id`` ->
        ``chair_persona_display_name``. Existing keys are never overwritten.
        """
        if not isinstance(row, dict):
            return row
        for keys, lookup in ((persona_keys, self.persona), (strategy_keys, self.strategy)):
            for key in keys:
                raw = row.get(key)
                if not _text(raw) or isinstance(raw, (dict, list)):
                    continue
                base = key[:-3] if key.endswith("_id") else key
                name, missing = lookup(raw)
                row.setdefault(f"{base}_display_name", name)
                if missing:
                    row.setdefault(f"{base}_name_missing", True)
        return row

    def present_title(
        self,
        title: Any,
        *,
        persona_id: Any = None,
        strategy_id: Any = None,
        binding_id: Any = None,
    ) -> Optional[str]:
        """Return ``title`` with embedded persona/strategy ids replaced by names.

        The stored title is not rewritten; ``None`` means nothing changed.
        """
        text = _text(title)
        if not text:
            return None
        out = text
        bid = _text(binding_id)
        if bid and bid in out:
            b_persona, b_strategy = self.binding(bid)
            persona_id = persona_id or b_persona
            strategy_id = strategy_id or b_strategy
            parts = [
                n
                for n, missing in (
                    self.persona(persona_id) if _text(persona_id) else ("", True),
                    self.strategy(strategy_id) if _text(strategy_id) else ("", True),
                )
                if n and not missing
            ]
            if parts:
                label = " / ".join(parts)
                out = re.sub(re.escape(bid), lambda _m: label, out)
        for raw, lookup in ((persona_id, self.persona), (strategy_id, self.strategy)):
            clean = _text(raw)
            if not clean or clean not in out:
                continue
            name, missing = lookup(clean)
            if not missing:
                out = re.sub(re.escape(clean), lambda _m, n=name: n, out)
        return out if out != text else None

    def annotate_lineage_nodes(
        self,
        nodes: Iterable[Any],
        edges: Iterable[Any] = (),
    ) -> List[Dict[str, Any]]:
        """Annotate lineage nodes with ``display_name`` and ``name_missing``.

        * ``Persona``, ``Strategy`` and ``Artifact`` nodes resolve through the
          persona/strategy directory (one bounded read per directory per request).
        * A ``CapitalPool`` or run (``Experiment``) node connected to exactly one
          owning strategy gets ``"{strategy_name} ({kind})"``.
        * Any node the resolver cannot name has ``display_name: None`` and
          ``name_missing: True``; ``label`` always stays the node ``id``.
        """
        node_list: List[Dict[str, Any]] = [
            dict(n) for n in (nodes or ()) if isinstance(n, Mapping)
        ]
        direct_strategy_by_node: Dict[str, str] = {}
        owning_strategies: Dict[str, Set[str]] = {}
        for node in node_list:
            nid = _text(node.get("id") or node.get("artifact_id"))
            if not nid:
                continue
            raw_type = _text(node.get("type") or node.get("artifact_type"))
            kind = raw_type.lower()
            sid = _text(node.get("strategy_id"))
            if not sid and kind in ("strategy", "strategy_spec") and not nid.startswith("reg-"):
                sid = nid
            if sid:
                direct_strategy_by_node[nid] = sid
                owning_strategies.setdefault(nid, set()).add(sid)

        for edge in edges or ():
            if not isinstance(edge, Mapping):
                continue
            frm = _text(edge.get("from") or edge.get("from_artifact_id"))
            to = _text(edge.get("to") or edge.get("to_artifact_id"))
            if not frm or not to:
                continue
            if frm in direct_strategy_by_node:
                owning_strategies.setdefault(to, set()).add(direct_strategy_by_node[frm])
            if to in direct_strategy_by_node:
                owning_strategies.setdefault(frm, set()).add(direct_strategy_by_node[to])

        for node in node_list:
            nid = _text(node.get("id") or node.get("artifact_id"))
            if nid:
                node.setdefault("id", nid)
                node["label"] = nid
            raw_type = _text(node.get("type") or node.get("artifact_type"))
            kind = raw_type.lower()
            display_name: Optional[str] = None
            name_missing = True
            if kind == "persona":
                pid = _text(node.get("persona_id") or nid)
                if pid:
                    resolved, missing = self.persona(pid)
                    if not missing and resolved:
                        display_name = resolved
                        name_missing = False
            elif kind in ("strategy", "strategy_spec", "artifact", "execution_bundle", "strategy_bundle"):
                sid = _text(node.get("strategy_id") or nid)
                if sid:
                    resolved, missing = self.strategy(sid)
                    if not missing and resolved:
                        display_name = resolved
                        name_missing = False
            elif kind in ("capitalpool", "capital_pool", "experiment", "run", "source_run"):
                owners = owning_strategies.get(nid, set())
                if len(owners) == 1:
                    sid = next(iter(owners))
                    resolved, missing = self.strategy(sid)
                    if not missing and resolved:
                        kind_label = raw_type or ("CapitalPool" if "pool" in kind else "Experiment")
                        display_name = f"{resolved} ({kind_label})"
                        name_missing = False
            node["display_name"] = display_name
            node["name_missing"] = name_missing
        return node_list


def annotate_subject_row(names: "DisplayNameResolver", row: Any, *, title_key: str = "title") -> Any:
    """Annotate an incident/inbox row with persona and strategy names and a presented title.

    Ids come from the row itself or, when only ``binding_id`` is present, from the
    runtime-binding directory (one read per request). The stored title is left as is;
    ``title_display`` carries the presented form when a name replaced an id.
    """
    if not isinstance(row, dict):
        return row
    persona_id = _text(row.get("persona_id"))
    strategy_id = _text(row.get("strategy_id"))
    binding_id = _text(row.get("binding_id") or row.get("runtime_binding_id"))
    if binding_id and not (persona_id and strategy_id):
        b_persona, b_strategy = names.binding(binding_id)
        persona_id = persona_id or b_persona
        strategy_id = strategy_id or b_strategy
    for kind, raw, lookup in (("persona", persona_id, names.persona), ("strategy", strategy_id, names.strategy)):
        if raw:
            name, missing = lookup(raw)
            row.setdefault(f"{kind}_display_name", name)
            if missing:
                row.setdefault(f"{kind}_name_missing", True)
    presented = names.present_title(
        row.get(title_key), persona_id=persona_id, strategy_id=strategy_id, binding_id=binding_id
    )
    if presented is not None:
        row.setdefault(f"{title_key}_display", presented)
    return row


def current_resolver(read_store: Optional[Any] = None) -> DisplayNameResolver:
    """New resolver for the current request, bound to the composed read store."""
    return DisplayNameResolver(read_store)
