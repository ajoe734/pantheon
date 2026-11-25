"""Production wiring of the hypothesis-card admission path of the alpha replication worker.

CARD-ADMISSION-PRODUCTION-WIRING-20261115 (design section 6A of
SD_DISCOVERY_DEMAND_AND_EVOLUTION_TRIGGERS_2026-11-12). The card admission logic
(``CardSpecAdmissionOwner``, ``register_card_pass``) takes four injected collaborators; this module
builds them from the deployed environment so the compose service entry point runs them:

* ``registrar`` - the template path's ``TemplateCandidateRegistrar`` (owner-call plumbing, Governance
  and Registry addresses, the research proposer principal and the distinct paper gate principal);
* ``matcher`` - the existing persona discovery scorer ``discover_for_demand`` (no second scorer);
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


def build_matcher(env: Mapping[str, str]) -> Callable[[Mapping[str, Any], Mapping[str, Any]], bool]:
    """The existing discovery scorer, asked whether this one spec fits the demand's persona.

    The persona is read from the persona owner. Route policy and capability snapshot are taken only from
    the persona record itself; a persona that references a route policy it does not carry inline cannot be
    scored without its constraints, so the match fails closed with that reason (never scored unconstrained).
    """
    persona_url = env["PERSONA_API_URL"].rstrip("/")

    def match(entry: Mapping[str, Any], demand: Mapping[str, Any]) -> bool:
        tenant_id = str(demand.get("tenant_id") or "")
        persona_id = str(demand.get("persona_id") or "")
        persona = _owner_request(
            step="read_persona", service="persona", method="GET", env=env, tenant_id=tenant_id,
            url=f"{persona_url}/api/personas/{urllib.parse.quote(persona_id, safe='')}",
            token_variable="ALPHA_REPLICATION_PERSONA_SERVICE_TOKEN")
        if not isinstance(persona, Mapping) or persona.get("persona_id") != persona_id:
            raise OwnerCallError(f"read_persona: persona owner returned no exact record for {persona_id}")
        persona_meta = persona.get("metadata") if isinstance(persona.get("metadata"), Mapping) else {}
        route_policy = persona.get("route_policy") or persona_meta.get("route_policy")
        snapshot = persona.get("capability_snapshot") or persona_meta.get("capability_snapshot")
        policy_id = str(persona.get("route_policy_id") or "").strip()
        if policy_id and not isinstance(route_policy, Mapping):
            raise OwnerCallError(f"route_policy_unavailable:{policy_id}")
        found = _discovery_module().discover_for_demand(
            dict(persona), demand,
            route_policy=route_policy if isinstance(route_policy, Mapping) else None,
            capability_snapshot=snapshot if isinstance(snapshot, Mapping) else None,
            strategy_specs=[_strategy_spec_view(entry)],
            created_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            discovery_session_id="card-admission-" + str(demand.get("demand_id")))
        return bool(found.get("matches"))

    return match


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
    drops the binding (fail closed). No ``candidate_pool_market`` attestation is added here.
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
        return {**dataset, "dataset_id": dataset_id, "snapshot_id": snapshot,
                "source_dataset_refs": [f"daily_bars:{snapshot}"]}

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
