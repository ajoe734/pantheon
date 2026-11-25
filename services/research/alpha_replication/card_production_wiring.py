"""Production wiring of the hypothesis-card admission path of the alpha replication worker.

CARD-ADMISSION-PRODUCTION-WIRING-20261115 (design section 6A of
SD_DISCOVERY_DEMAND_AND_EVOLUTION_TRIGGERS_2026-11-12). The card admission logic
(``CardSpecAdmissionOwner``, ``register_card_pass``) takes four injected collaborators; this module
builds them from the deployed environment so the compose service entry point runs them:

* ``registrar`` - the template path's ``TemplateCandidateRegistrar`` (owner-call plumbing, Governance
  and Registry addresses, the research proposer principal and the distinct paper gate principal);
* ``matcher`` - the existing persona discovery scorer ``discover_for_demand`` (no second scorer), with the
  persona's route policy and capability snapshot resolved exactly as the persona daily cycle resolves them
  (``services/control-plane/persona/main.py`` ``_route_policy`` / ``_capability_snapshot``;
  HYPOTHESIS-CARD-GOVERNED-BINDING-20261125);
* ``demands_provider`` - the research owner's open persona demand read (HTTP, service credential);
* ``dataset_resolver`` - the governed whole-market dataset read from the stored daily bars
  (``services.research.governed_market_dataset``), never synthesised and never waiting for a persona
  candidate evaluation (DISCOVERY-GOVERNED-MARKET-DATASET-20261125).

Nothing here falls back: a missing address, credential or dataset pointer disables the card path with a
readable reason that the worker publishes on its health file; the rest of the worker is unaffected.
Credentials are read from their mounted files at call time so rotation needs no restart.
"""
from __future__ import annotations

import importlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from services.service_token_file import configured_service_token

SOURCE_SERVICE = "alpha-replication-worker"
HTTP_TIMEOUT_SECONDS = 15.0

# (environment name, role) - every name the card path reads; compose passes each one.
REQUIRED_ADDRESSES: tuple[str, ...] = (
    "PANTHEON_REGISTRY_URL",
    "PANTHEON_GOVERNANCE_URL",
    "RESEARCH_API_URL",
    "PERSONA_API_URL",
)
REQUIRED_IDENTITIES: tuple[str, ...] = ("RESEARCH_GOVERNANCE_PROPOSER_ID", "RESEARCH_GOVERNANCE_GATE_ID")
REQUIRED_CREDENTIALS: tuple[str, ...] = (
    "RESEARCH_GOVERNANCE_PROPOSER_TOKEN",
    "RESEARCH_GOVERNANCE_GATE_TOKEN",
    "RESEARCH_REGISTRY_SERVICE_TOKEN",
    "ALPHA_REPLICATION_RESEARCH_SERVICE_TOKEN",
    "ALPHA_REPLICATION_PERSONA_SERVICE_TOKEN",
)
# A card binding names the dataset either by a pinned snapshot id (``gmd-...``, reproduced exactly) or by one
# of these logical ids (the newest snapshot; the resolved dataset keeps the binding's id and carries the
# snapshot id in ``snapshot_id`` and ``source_dataset_refs``).
GOVERNED_MARKET_DATASET_ID = "governed-market:tw-daily"
LOGICAL_DATASET_IDS = (GOVERNED_MARKET_DATASET_ID, "tw_price_daily")
WHOLE_MARKET_SCOPE = "all_governed_tw"  # the distillation controller's whole governed Taiwan boundary binding


def _whole_market_binding(binding: Mapping[str, Any]) -> bool:
    """A card rule bound to the whole governed boundary: the conversion's ``governed_reference`` to a pinned
    ``gmd-`` snapshot (symbols are the governed dataset's own), or an explicit ``scope``."""
    if binding.get("scope") == WHOLE_MARKET_SCOPE:
        return True
    return binding.get("binding") == "governed_reference" and str(binding.get("dataset_id") or "").startswith("gmd-")


class OwnerCallError(RuntimeError):
    """An owner call failed; ``status`` is the HTTP status when the owner answered."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class CardWiring:
    """The four collaborators, or the readable reason the card path is disabled."""

    registrar: Any = None
    matcher: Callable[[Mapping[str, Any], Mapping[str, Any]], bool] | None = None
    demands_provider: Callable[[str], list[dict[str, Any]]] | None = None
    dataset_resolver: Callable[[Mapping[str, Any]], Mapping[str, Any] | None] | None = None
    reason: str | None = None
    missing: list[str] = field(default_factory=list)

    @property
    def enabled(self) -> bool:
        return self.reason is None and all(
            (self.registrar, self.matcher, self.demands_provider, self.dataset_resolver))


def _present(env: Mapping[str, str], name: str) -> bool:
    """True when the value (or its mounted credential file) is configured and readable."""
    try:
        return bool(configured_service_token(name, env))
    except RuntimeError:
        return False


def missing_configuration(env: Mapping[str, str]) -> list[str]:
    """Every required environment name that is unset, empty or unreadable (a readable list)."""
    missing = [n for n in REQUIRED_ADDRESSES + REQUIRED_IDENTITIES if not str(env.get(n, "") or "").strip()]
    missing += [n for n in REQUIRED_CREDENTIALS if not _present(env, n)]
    from services.research.governed_market_dataset import STORE_ROOT_ENV, store_root

    if not (store_root(env) / "daily_bars" / "_manifest").is_dir():
        missing.append(f"{STORE_ROOT_ENV}(daily bars store not found)")
    return missing


def _owner_request(*, step: str, service: str, method: str, url: str, tenant_id: str, token_variable: str,
                   env: Mapping[str, str], body: Any = None, extra_headers: Mapping[str, str] | None = None,
                   timeout: float = HTTP_TIMEOUT_SECONDS) -> Any:
    """One authenticated owner call. Raises ``OwnerCallError`` with a readable reason, never falls back."""
    try:
        token = configured_service_token(token_variable, env)
    except RuntimeError as exc:
        raise OwnerCallError(f"{step}: {service} credential unavailable ({token_variable}): {exc}") from None
    if not token:
        raise OwnerCallError(f"{step}: {service} credential is not configured ({token_variable})")
    headers = {
        "Accept": "application/json",
        "Authorization": token if token.startswith("Bearer ") else f"Bearer {token}",
        "X-Tenant-Id": tenant_id,
        "X-Pantheon-Service": SOURCE_SERVICE,
    }
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    headers.update(extra_headers or {})
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            parsed = json.loads(exc.read().decode("utf-8"))
            raw_detail = parsed.get("detail") if isinstance(parsed, dict) else None
            detail = raw_detail if isinstance(raw_detail, str) else json.dumps(raw_detail, sort_keys=True)
        except Exception:  # noqa: BLE001 - the status line is enough
            detail = str(exc.reason or "")
        raise OwnerCallError(f"{step}: {service} returned HTTP {exc.code}: {(detail or '')[:300]}",
                             status=exc.code) from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise OwnerCallError(f"{step}: {service} request failed: {type(exc).__name__}: {exc}") from None


def _strategy_spec_view(entry: Mapping[str, Any]) -> dict[str, Any]:
    """The registry entry as the discovery scorer's StrategySpec candidate (same shape the cycle reads)."""
    metadata = dict(entry.get("metadata") or {})
    stored = metadata.get("strategy_spec")
    spec = dict(stored) if isinstance(stored, Mapping) else {}
    for key in ("mechanism", "mechanism_status", "mechanism_support", "negative_memory_match"):
        if key in metadata:
            spec.setdefault(key, metadata[key])
    embedded = spec.get("metadata") if isinstance(spec.get("metadata"), Mapping) else {}
    spec.update({
        "strategy_id": spec.get("strategy_id") or entry.get("strategy_id"),
        "strategy_spec_registry_id": entry.get("registry_id"),
        "lifecycle_state": entry.get("artifact_state"),
        "metadata": {**embedded, **{k: v for k, v in metadata.items() if k != "strategy_spec"}},
        "lineage": entry.get("lineage") if isinstance(entry.get("lineage"), Mapping) else {},
    })
    return spec


def _discovery_module() -> Any:
    return importlib.import_module("services.control_plane.persona.persona_strategy_discovery")


def route_policy_catalog() -> Any:
    """The route policy owner catalog the persona daily cycle reads.

    The daily cycle reads ``ReadSurfacePorts().list_route_policies()``, which delegates to the workflow catalog
    port built by ``create_operations_consultation_port()`` (``DomainWorkflowCatalogPort``). The worker builds
    that same port the same way; it reads no environment for route policies.
    """
    from services.control_plane.bff.ports.operations_consultation import DomainWorkflowCatalogPort

    return DomainWorkflowCatalogPort()


def _persona_capability_snapshot(env: Mapping[str, str], persona_url: str, tenant_id: str,
                                 persona_id: str) -> Mapping[str, Any] | None:
    """The persona owner's capability snapshot for the persona (``None`` when the owner has none)."""
    try:
        snapshot = _owner_request(
            step="read_capability_snapshot", service="persona", method="GET", env=env, tenant_id=tenant_id,
            url=f"{persona_url}/api/personas/{urllib.parse.quote(persona_id, safe='')}/capability-snapshot",
            token_variable="ALPHA_REPLICATION_PERSONA_SERVICE_TOKEN")
    except OwnerCallError as exc:
        if exc.status == 404:
            return None
        raise
    return snapshot if isinstance(snapshot, Mapping) else None


def _resolve_route_policy(persona: Mapping[str, Any], list_route_policies: Callable[[], Any]) -> dict[str, Any]:
    """The persona daily cycle's ``_route_policy`` (services/control-plane/persona/main.py), line for line:
    inline wins; a referenced policy is selected from the owner catalog by its own ``policy_id``/``id`` (never a
    persona-scoped lookup that may return a stale, different policy); unreadable or mismatched -> raise."""
    inline = persona.get("route_policy")
    if isinstance(inline, Mapping) and inline:
        return dict(inline)
    policy_id = str(persona.get("route_policy_id") or "").strip()
    if not policy_id:
        return {}
    for policy in list_route_policies() or []:
        if isinstance(policy, Mapping) and policy and policy_id in {
                str(policy.get("policy_id") or "").strip(), str(policy.get("id") or "").strip()} - {""}:
            return dict(policy)
    # referenced policy unreadable/mismatched: never score without (or with other) constraints
    raise RuntimeError(f"route_policy_unavailable:{policy_id}")


def _resolve_capability_snapshot(persona: Mapping[str, Any],
                                 snapshot_for_persona: Callable[[], Mapping[str, Any] | None]) -> dict[str, Any]:
    """The persona daily cycle's ``_capability_snapshot``, line for line: inline wins, else the owner's snapshot
    for the persona; a referenced ``capability_snapshot_id`` the owner does not return raises."""
    inline = persona.get("capability_snapshot")
    if isinstance(inline, Mapping) and inline:
        return dict(inline)
    snapshot = snapshot_for_persona()
    snapshot_id = str(persona.get("capability_snapshot_id") or "").strip()
    if not snapshot and snapshot_id:
        raise RuntimeError(f"capability_snapshot_unavailable:{snapshot_id}")
    return dict(snapshot or {})


def build_matcher(env: Mapping[str, str], *,
                  catalog_factory: Callable[[], Any] | None = None) -> Callable[[Mapping[str, Any], Mapping[str, Any]], bool]:
    """The existing discovery scorer, asked whether this one spec fits the demand's persona.

    The persona is read from the persona owner. Its route policy and capability snapshot are resolved as the
    persona daily cycle resolves them (``_resolve_route_policy`` / ``_resolve_capability_snapshot``, parity-tested
    against the daily cycle's loader): inline wins; a referenced ``route_policy_id`` is selected from the route
    policy owner catalog the daily cycle reads by its own id and the capability snapshot is read from the persona
    owner. A referenced policy or snapshot the owner cannot return fails the match with
    ``route_policy_unavailable:<id>`` / ``capability_snapshot_unavailable:<id>``; it is never scored without its
    constraints.
    """
    persona_url = env["PERSONA_API_URL"].rstrip("/")
    catalogs = catalog_factory or route_policy_catalog

    def match(entry: Mapping[str, Any], demand: Mapping[str, Any]) -> bool:
        tenant_id = str(demand.get("tenant_id") or "")
        persona_id = str(demand.get("persona_id") or "")
        persona = _owner_request(
            step="read_persona", service="persona", method="GET", env=env, tenant_id=tenant_id,
            url=f"{persona_url}/api/personas/{urllib.parse.quote(persona_id, safe='')}",
            token_variable="ALPHA_REPLICATION_PERSONA_SERVICE_TOKEN")
        if not isinstance(persona, Mapping) or persona.get("persona_id") != persona_id:
            raise OwnerCallError(f"read_persona: persona owner returned no exact record for {persona_id}")
        # The daily cycle resolves from the persona owner record as read (top-level inline values only).
        view = dict(persona)
        view.setdefault("tenant_id", tenant_id)
        try:
            route_policy = _resolve_route_policy(view, lambda: _catalog_policies(catalogs, tenant_id))
            snapshot = _resolve_capability_snapshot(
                view, lambda: _persona_capability_snapshot(env, persona_url, tenant_id, persona_id))
        except RuntimeError as exc:
            if isinstance(exc, OwnerCallError):
                raise
            raise OwnerCallError(str(exc)) from None
        found = _discovery_module().discover_for_demand(
            view, demand, route_policy=route_policy, capability_snapshot=snapshot,
            strategy_specs=[_strategy_spec_view(entry)],
            created_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            discovery_session_id="card-admission-" + str(demand.get("demand_id")))
        return bool(found.get("matches"))

    return match


def _catalog_policies(catalogs: Callable[[], Any], tenant_id: str) -> list[Any]:
    """Read the route policy catalog bound to the persona's tenant, as the daily cycle's owner read does."""
    from services.control_plane.bff.core.owner_reads import selected_tenant

    mark = selected_tenant.set(tenant_id)
    try:
        return list(catalogs().list_route_policies() or [])
    except Exception as exc:  # noqa: BLE001 - an unreadable catalog is never an empty one
        raise OwnerCallError(f"read_route_policies: route policy owner unavailable: {type(exc).__name__}: {exc}")
    finally:
        selected_tenant.reset(mark)


def build_demands_provider(env: Mapping[str, str]) -> Callable[[str], list[dict[str, Any]]]:
    """Open persona demands from the research owner (the owner of ``research_demand.v1``)."""
    research_url = env["RESEARCH_API_URL"].rstrip("/")

    def demands(tenant_id: str) -> list[dict[str, Any]]:
        out = _owner_request(
            step="read_open_demands", service="research", method="GET", env=env, tenant_id=tenant_id,
            url=f"{research_url}/api/research/persona-demands?status=open",
            token_variable="ALPHA_REPLICATION_RESEARCH_SERVICE_TOKEN")
        rows = out.get("demands") if isinstance(out, Mapping) else None
        if not isinstance(rows, list):
            raise OwnerCallError("read_open_demands: research owner returned no demands list")
        return [dict(r) for r in rows if isinstance(r, Mapping) and r.get("status") == "open"]

    return demands


def build_dataset_resolver(env: Mapping[str, str],
                           now: Callable[[], datetime] | None = None) -> Callable[[Mapping[str, Any]], Mapping[str, Any] | None]:
    """The governed whole-market dataset, read from the stored daily bars only.

    ``binding`` carries ``dataset_id`` and, for the symbols the rule trades, ``symbols``. A pinned ``gmd-``
    id is reproduced exactly (or refused); a logical id resolves the newest snapshot. Anything else, a
    missing or stale store, or a symbol outside the market boundary raises a readable reason; the worker
    drops the binding (fail closed).

    A whole-market card binding (HYPOTHESIS-CARD-GOVERNED-BINDING-20261125: a ``governed_reference`` to a pinned
    ``gmd-`` snapshot, which the distillation controller writes for every compiled card rule) also gets the
    screen's ``candidate_pool_market`` built from the same stored bars by the template search's own
    ``market_from_dataset`` (no copy). It carries no survivorship attestation (``includes_delisted`` and
    ``includes_suspended`` stay false), so the gate refuses a screened rule as ``survivorship_limited`` until
    the source owner attests coverage: a correct refusal, never bypassed.
    """
    from services.research import governed_market_dataset as governed

    def resolve(binding: Mapping[str, Any]) -> Mapping[str, Any] | None:
        dataset_id = str(binding.get("dataset_id") or "")
        pinned = dataset_id if dataset_id.startswith("gmd-") else None
        if not pinned and dataset_id not in LOGICAL_DATASET_IDS:
            raise governed.GovernedMarketDatasetError(
                "dataset_id_not_governed_market", f"{dataset_id!r} is neither a gmd- snapshot nor {LOGICAL_DATASET_IDS}")
        symbols = binding.get("symbols")
        dataset = governed.resolve_governed_dataset(
            symbols=list(symbols) if symbols else None, snapshot_id=pinned, strategy_id=dataset_id,
            now=now, env=env)
        snapshot = dataset["dataset_id"]
        resolved = {**dataset, "dataset_id": dataset_id, "snapshot_id": snapshot,
                    "source_dataset_refs": [f"daily_bars:{snapshot}"]}
        if _whole_market_binding(binding):
            from services.research.template_demand_search import market_from_dataset

            resolved["candidate_pool_market"] = market_from_dataset(resolved)
        return resolved

    return resolve


def build_card_wiring(*, data_dir: Path, admission_store: Any, queue_store: Any = None,
                      env: Mapping[str, str] | None = None) -> CardWiring:
    """Build the four collaborators from the environment, or a disabled wiring with the reason."""
    source = os.environ if env is None else env
    missing = missing_configuration(source)
    if missing:
        return CardWiring(reason="card_path_disabled:unconfigured:" + ",".join(missing), missing=missing)
    from services.research.candidate_registration import TemplateCandidateRegistrar

    def owner_call(*, step: str, service: str, method: str, url: str, tenant_id: str, token_variable: str,
                   body: Any = None, extra_headers: Mapping[str, str] | None = None,
                   timeout: float = HTTP_TIMEOUT_SECONDS) -> Any:
        return _owner_request(step=step, service=service, method=method, url=url, tenant_id=tenant_id,
                              token_variable=token_variable, env=source, body=body,
                              extra_headers=extra_headers, timeout=timeout)

    try:
        registrar = TemplateCandidateRegistrar(
            data_dir=data_dir, owner_call=owner_call,
            registry_url=source["PANTHEON_REGISTRY_URL"], governance_url=source["PANTHEON_GOVERNANCE_URL"],
            proposer_id=source["RESEARCH_GOVERNANCE_PROPOSER_ID"], gate_id=source["RESEARCH_GOVERNANCE_GATE_ID"],
            admission_store=admission_store, queue_store=queue_store)
        if registrar.proposer_id == registrar.gate_id:
            return CardWiring(reason="card_path_disabled:proposer_and_gate_principals_must_differ")
        return CardWiring(
            registrar=registrar, matcher=build_matcher(source), demands_provider=build_demands_provider(source),
            dataset_resolver=build_dataset_resolver(source))
    except Exception as exc:  # noqa: BLE001 - a wiring error disables the path with its reason
        return CardWiring(reason=f"card_path_disabled:wiring_error:{type(exc).__name__}:{str(exc)[:160]}")
