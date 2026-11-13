"""Read-only persona pool composition; every value comes from an owner record.

Health and virtual books are the latest daily cycle's observation, pinned to
the current pool digest. No BFF portfolio calculations or historical fallback.
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


class PersonaPoolViewsPort:
    def __init__(self, *, read_store: Any, authorization: str | None) -> None:
        self.read_store = read_store
        self.authorization = authorization

    def _get(self, owner: str, path: str, tenant: str) -> Any:
        base = get_base_url(f"PANTHEON_{owner}_API_URL", f"PANTHEON_{owner}_SERVICE_URL")
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
            # Use the already-configured owner port, never open a second file store.
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
            if pool["pool_version"] != current["pool_version"] or pool["digest"] != current["digest"]:
                raise PoolReadUnavailable("cycle_pool_snapshot_mismatch")
            return bundle

        def health():
            bundle = observation()
            value = bundle.get("pool_health")
            keys = ("gross_utilisation", "drawdown_utilisation", "effective_bets", "idle_day_share")
            if (not isinstance(value, dict) or value.get("pool_version") != current["pool_version"]
                    or any(not isinstance(value.get(k), dict)
                           or value[k].get("status") not in {"known", "unknown"} for k in keys)):
                raise PoolReadUnavailable("pool_health_missing_or_invalid")
            return value

        pool_health = section("pool_health", "persona_cycle_owner", health)

        def books():
            bundle = observation()
            if "own_telemetry" in bundle.get("unavailable", []):
                raise PoolReadUnavailable("member_telemetry_unavailable")
            owned_books = bundle.get("books")
            if not isinstance(owned_books, dict):
                raise PoolReadUnavailable("member_books_missing")
            rows = []
            for member in current["members"]:
                ref = member["artifact_ref"]
                book = owned_books.get(ref)
                available = isinstance(book, dict) and book.get("checksum") == member["checksum"]
                rows.append({"artifact_ref": ref, "checksum": member["checksum"],
                             "stage": member["stage"], "risk_weight": member["risk_weight"],
                             "baseline_ref": member["baseline_ref"],
                             "status": "ok" if available else "unavailable",
                             "reason": None if available else "member_book_missing_or_checksum_mismatch",
                             "book": deepcopy(book) if available else None})
            return rows

        member_books = section("member_virtual_books", "persona_cycle_owner", books)
        if member_books is not None and any(row["status"] != "ok" for row in member_books):
            surfaces["member_virtual_books"].update(status="unavailable", reason="member_books_incomplete")
        for name in ("pool_health", "member_virtual_books"):
            if records:
                surfaces[name].update(packet_id=records[0]["packet_id"],
                                      observed_at=records[0].get("updated_at"))

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
             "pool_cycle": deepcopy(p["persona_cycle"].get("pool_cycle"))}
            for p in records]
        return {"current": current, "versions": versions, "memberVirtualBooks": member_books,
                "health": pool_health, "cycles": cycle_rows, "openDemands": open_demands}, surfaces
