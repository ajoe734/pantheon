"""Read-only persona pool composition; every value comes from an owner record.

Health and virtual books are the latest daily cycle's observation. They are
valid while the observed pool has the same non-removed members as the current
pool (artifact_ref, checksum, stage, risk_weight); a refused reweight appends a
new version with unchanged members and must not invalidate them. Member rules
are read from the Registry owner and checksum-verified. No BFF portfolio
calculations or historical fallback.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any
from urllib.error import HTTPError
from urllib.parse import quote, urlencode

from ..command_adapters.base import get_base_url, http_request_json
from ..core.owner_reads import remaining_management_read_budget, selected_tenant


class PoolReadUnavailable(ValueError):
    pass


def _membership(pool: dict) -> list:
    return sorted((m["artifact_ref"], m["checksum"], m["stage"], m["risk_weight"])
                  for m in pool["members"] if m["stage"] != "removed")


class PersonaPoolViewsPort:
    def __init__(self, *, read_store: Any, authorization: str | None) -> None:
        self.read_store = read_store
        self.authorization = authorization

    def _get(self, owner: str, path: str, tenant: str) -> Any:
        # The operator BFF on hosted carries the Research owner URL only as
        # PANTHEON_RESEARCH_ORCHESTRATOR_API_URL; the older names stay fallbacks.
        names = [f"PANTHEON_{owner}_API_URL", f"PANTHEON_{owner}_SERVICE_URL"]
        if owner == "RESEARCH":
            names = ["PANTHEON_RESEARCH_ORCHESTRATOR_API_URL", *names,
                     "RESEARCH_ORCHESTRATOR_URL", "RESEARCH_ORCHESTRATOR_API_URL"]
        base = get_base_url(*names)
        return http_request_json(
            base + path, auth_token=self.authorization, tenant_id=tenant,
            timeout=remaining_management_read_budget(2.0),
        )

    def read(self, persona_id: str, tenant_id: str, *, snapshot_at: str) -> tuple[dict, dict]:
        surfaces: dict[str, dict] = {}

        def section(name, source, fn):
            try:
                value = fn()
            except Exception as exc:
                # Never echo owner bodies, URLs, tokens or another tenant's IDs.
                reason = (str(exc) if isinstance(exc, PoolReadUnavailable)
                          else f"owner_http_{exc.code}" if isinstance(exc, HTTPError)
                          else f"owner_read_failed:{type(exc).__name__}")
                surfaces[name] = {"status": "unavailable", "source": source,
                                  "reason": reason, "snapshot_at": snapshot_at}
                return None
            surfaces[name] = {"status": "ok", "source": source, "reason": None,
                              "snapshot_at": snapshot_at}
            return deepcopy(value)

        def scoped_pool(value):
            if (not isinstance(value, dict) or value.get("tenant_id") != tenant_id
                    or value.get("persona_id") != persona_id):
                raise PoolReadUnavailable("pool_scope_missing_or_mismatched")
            if (type(value.get("pool_version")) is not int or value["pool_version"] < 1
                    or not value.get("digest") or not isinstance(value.get("members"), list)
                    or any(not isinstance(m, dict) or not m.get("artifact_ref")
                           or not m.get("checksum") or m.get("stage") not in {"trial", "active", "removed"}
                           or "risk_weight" not in m or "baseline_ref" not in m
                           for m in value["members"])):
                raise PoolReadUnavailable("pool_payload_invalid")
            return value

        path = f"/api/personas/{quote(persona_id, safe='')}/strategy-pool"
        current = section("strategy_pool", "persona_owner", lambda: scoped_pool(
            self._get("PERSONA", path, tenant_id)))

        def history():
            payload = self._get("PERSONA", path + "/versions", tenant_id)
            if not isinstance(payload, dict) or not isinstance(payload.get("versions"), list):
                raise PoolReadUnavailable("pool_versions_payload_invalid")
            return [scoped_pool(v) for v in payload["versions"]]

        versions = section("strategy_pool_versions", "persona_owner", history)

        def cycles():
            port = self.read_store.ooda_management.ooda
            state = port.get_surface_status()
            if state.get("status") == "unavailable":
                raise PoolReadUnavailable("cycle_owner_unavailable")
            token = selected_tenant.set(tenant_id)
            try:
                packets = port.list_ooda_packets()
            finally:
                selected_tenant.reset(token)
            return [p for p in packets if p.get("tenant_id") == tenant_id
                    and persona_id in p.get("persona_ids", [])
                    and p.get("environment") == "paper"
                    and isinstance(p.get("persona_cycle"), dict)][:20]

        records = section("autonomy_cycles", "persona_cycle_owner", cycles)

        def observation():
            if current is None:
                raise PoolReadUnavailable("current_pool_unavailable")
            if not records:
                raise PoolReadUnavailable("latest_cycle_observation_missing")
            bundle = records[0]["persona_cycle"].get("observe_bundle")
            if (not isinstance(bundle, dict) or bundle.get("tenant_id") != tenant_id
                    or bundle.get("persona_id") != persona_id):
                raise PoolReadUnavailable("cycle_observation_scope_missing_or_mismatched")
            pool = scoped_pool(bundle.get("pool"))
            if _membership(pool) != _membership(current):
                raise PoolReadUnavailable("cycle_pool_snapshot_mismatch")
            return bundle, pool["pool_version"]

        def health():
            bundle, observed_version = observation()
            value = bundle.get("pool_health")
            keys = ("gross_utilisation", "drawdown_utilisation", "effective_bets", "idle_day_share")
            if (not isinstance(value, dict) or value.get("pool_version") != observed_version
                    or any(not isinstance(value.get(k), dict)
                           or value[k].get("status") not in {"known", "unknown"} for k in keys)):
                raise PoolReadUnavailable("pool_health_missing_or_invalid")
            return value

        pool_health = section("pool_health", "persona_cycle_owner", health)

        def books():
            bundle, _ = observation()
            if "own_telemetry" in bundle.get("unavailable", []):
                raise PoolReadUnavailable("member_telemetry_unavailable")
            owned_books = bundle.get("books")
            if not isinstance(owned_books, dict):
                raise PoolReadUnavailable("member_books_missing")
            rows = []
            for member in current["members"]:
                ref = member["artifact_ref"]
                book = owned_books.get(ref)
                available = (isinstance(book, dict) and book.get("checksum") == member["checksum"]
                             and book.get("member_id") == ref and bool(book.get("source_ref"))
                             and book.get("marks_complete") is True
                             and isinstance(book.get("net_of_costs"), dict)
                             and all(k in book["net_of_costs"] for k in
                                     ("total_return", "sharpe_ratio", "max_drawdown", "observation_count", "basis")))
                rows.append({"artifact_ref": ref, "checksum": member["checksum"],
                             "stage": member["stage"], "risk_weight": member["risk_weight"],
                             "baseline_ref": member["baseline_ref"],
                             "status": "ok" if available else "unavailable",
                             "reason": None if available else "member_book_missing_incomplete_or_mismatched",
                             "book": deepcopy(book) if available else None})
            return rows

        member_books = section("member_virtual_books", "persona_cycle_owner", books)
        if member_books is not None and any(row["status"] != "ok" for row in member_books):
            surfaces["member_virtual_books"].update(status="unavailable", reason="member_books_incomplete")
        observed_version = None
        if records:
            observed_pool = ((records[0]["persona_cycle"].get("observe_bundle") or {}).get("pool"))
            version = observed_pool.get("pool_version") if isinstance(observed_pool, dict) else None
            observed_version = version if type(version) is int and version >= 1 else None
        for name in ("pool_health", "member_virtual_books"):
            if records:
                surfaces[name].update(packet_id=records[0]["packet_id"],
                                      observed_at=records[0].get("updated_at"))
                if observed_version is not None:
                    surfaces[name]["observed_pool_version"] = observed_version

        def rules():
            if current is None and versions is None:
                raise PoolReadUnavailable("pool_unavailable")
            named: dict[str, str] = {}
            for pool in ([current] if current else []) + list(reversed(versions or [])):
                for member in pool["members"]:
                    named.setdefault(member["artifact_ref"], member["checksum"])
            rows = []
            for ref, checksum in named.items():  # one Registry read per distinct ref
                row = {"artifact_ref": ref, "checksum": checksum, "status": "unavailable",
                       "reason": None}
                try:
                    payload = self._get("REGISTRY", "/api/registry/strategy-artifacts/"
                                        + quote(ref, safe=""), tenant_id)
                except HTTPError as exc:
                    if exc.code != 404:
                        raise
                    row["reason"] = "registry_entry_missing"
                    rows.append(row)
                    continue
                entry = payload.get("entry") if isinstance(payload, dict) and isinstance(
                    payload.get("entry"), dict) else payload
                entry = entry.get("data") if isinstance(entry, dict) and isinstance(
                    entry.get("data"), dict) else entry
                if not isinstance(entry, dict) or not entry:
                    raise PoolReadUnavailable("registry_payload_invalid")
                artifact = (entry.get("metadata") or {}).get("strategy_artifact") \
                    if isinstance(entry.get("metadata"), dict) else None
                if entry.get("registry_id") not in (None, ref) or entry.get("owner_tenant") != tenant_id:
                    row["reason"] = "registry_entry_missing_or_not_tenant_owned"
                elif entry.get("checksum") != checksum:
                    row["reason"] = "registry_checksum_mismatch"
                elif not isinstance(artifact, dict):
                    row["reason"] = "registry_strategy_artifact_missing"
                else:
                    surface = artifact.get("mutation_surface")
                    surface = surface if isinstance(surface, dict) else {}
                    row.update(status="ok", reason=None, strategy_id=artifact.get("strategy_id"),
                               version=artifact.get("version"),
                               strategy_logic=deepcopy(artifact.get("strategy_logic")),
                               parameters=deepcopy(artifact.get("parameters")),
                               adjustable_controls=deepcopy(surface.get("controls")),
                               immutable_parameters=deepcopy(surface.get("immutable_parameters")),
                               expression_policy=deepcopy(artifact.get("expression_policy")))
                rows.append(row)
            return rows

        member_rules = section("member_rules", "registry_owner", rules)

        def demands():
            payload = self._get("RESEARCH", "/api/research/persona-demands?" + urlencode(
                {"persona_id": persona_id, "status": "open"}), tenant_id)
            if not isinstance(payload, dict) or not isinstance(payload.get("demands"), list):
                raise PoolReadUnavailable("demands_payload_invalid")
            rows = payload["demands"]
            if any(not isinstance(d, dict) or d.get("tenant_id") != tenant_id
                   or d.get("persona_id") != persona_id or d.get("status") != "open" for d in rows):
                raise PoolReadUnavailable("demand_scope_or_status_mismatch")
            return rows

        open_demands = section("open_demands", "research_owner", demands)
        # Summarize cycles without exposing private case files, staged memory or prompts.
        cycle_rows = None if records is None else [
            {"packet_id": p["packet_id"], "status": p.get("status"),
             "created_at": p.get("created_at"), "updated_at": p.get("updated_at"),
             "phase": p["persona_cycle"].get("phase"),
             "failure_reason": p["persona_cycle"].get("failure_reason"),
             "decisions": deepcopy(p["persona_cycle"].get("decisions")),
             "pool_autonomy": deepcopy(p.get("pool_autonomy"))}
            for p in records]
        return {"current": current, "versions": versions, "memberVirtualBooks": member_books,
                "health": pool_health, "cycles": cycle_rows, "openDemands": open_demands,
                "memberRules": member_rules}, surfaces
