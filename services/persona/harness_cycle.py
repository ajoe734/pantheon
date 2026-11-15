"""Production callers of the persona harness self-improvement loop (paper only).

One step per daily cycle, driven from the persona cycle controller:
propose (harness_patch decisions) -> precheck -> shadow comparison on the
deny-all replay agent -> consultation (harness_patch panel) -> adoption with the
published memo -> observation facts in the first trading-day packets -> observe.

Everything is delegated to the existing owners (harness, shadow evaluator,
adoption, consultation client). This module adds no gate of its own: a missing
provider, store, consultation or evidence source yields a readable ``degraded``
reason on the cycle record and never a silent keep-base or approval.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any, Callable, Mapping
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from services.persona.harness import (
    EvidenceReference, HarnessError, ObservationRequest, PersistentHarnessOwner, PersonaPatch,
)

PROVIDER_REASONS = ("provider_unavailable", "deny_all_provider_unverified")
CONSULT_FAILED_STATES = {"rejected", "cancelled", "failed", "expired"}


# ---------------------------------------------------------------- evidence ---
def _ranking_reader_from_env(persona: Any, moment: str) -> list[dict[str, Any]] | None:
    """Rows of the ranking snapshot ``moment`` (current or previous quarter); None if unconfigured."""
    base = (os.getenv("PANTHEON_RANKING_BFF_URL") or "").rstrip("/")
    token = os.getenv("PANTHEON_RANKING_BFF_TOKEN") or ""
    if not base or not token:
        return None
    now = datetime.now(timezone.utc)
    quarters = [f"{now.year}-Q{(now.month - 1) // 3 + 1}"]
    prev = (now.month - 1) // 3
    quarters.append(f"{now.year}-Q{prev}" if prev else f"{now.year - 1}-Q4")
    headers = {"Authorization": f"Bearer {token}", "X-Tenant-Id": persona.tenant_id}
    for quarter in quarters:
        rows, page = [], ""
        for _ in range(100):
            query = urlencode({"quarter": quarter, "page_size": 200, "page_token": page})
            with urlopen(Request(f"{base}/bff/management/quarterly-ranking?{query}", headers=headers),
                         timeout=15) as response:
                body = json.load(response)
            data = body["data"]
            if str(data.get("ranking_snapshot_id")) != moment:
                rows = None
                break
            rows.extend(data["items"])
            page = (body.get("page_info") or {}).get("next_page_token")
            if not page:
                return rows
    return []


def production_evidence_verifier(base: Callable[[Any, EvidenceReference], bool], *,
                                 packet_reader: Callable[[], Mapping[str, Any]] | None = None,
                                 ranking_reader: Callable[[Any, str], Any] = _ranking_reader_from_env) -> Callable:
    """Resolve ``packet`` and ``virtual_book`` refs on top of the reflection/lesson resolvers.

    packet: an own daily-cycle OODA packet (moment = packet id) or a ranking evidence ref
    of this persona inside the ranking snapshot named by ``moment``.
    virtual_book: a member virtual book in an own terminal packet's frozen Observe bundle.
    memo refs stay unresolved here (they bind through the adoption verifier).
    """
    def packets() -> Mapping[str, Any]:
        if packet_reader is not None:
            return packet_reader()
        from services.persona.cron_ooda_closure import resolve_ooda_packet_store_path
        from services.persona.persona_cycle_controller import read_packets
        return read_packets(resolve_ooda_packet_store_path())

    def own(persona: Any, packet: Mapping[str, Any]) -> bool:
        return (packet.get("tenant_id") == persona.tenant_id
                and packet.get("environment") == "paper"
                and persona.persona_id in packet.get("persona_ids", []))

    def verify(persona: Any, ref: EvidenceReference) -> bool:
        if ref.kind not in {"packet", "virtual_book"}:
            return base(persona, ref)
        try:
            if ref.kind == "virtual_book":
                packet = packets().get(ref.moment)
                books = ((packet or {}).get("persona_cycle") or {}).get("observe_bundle", {}).get("books") or {}
                return bool(packet) and own(persona, packet) and ref.ref in books
            packet = packets().get(ref.ref)
            if packet is not None:
                return own(persona, packet) and ref.moment in {packet["packet_id"], packet.get("updated_at")}
            rows = ranking_reader(persona, ref.moment)
            return any(r.get("persona_id") == persona.persona_id and any(
                ref.ref == str(e.get("refId") or e.get("ref_id") or e.get("id"))
                for e in r.get("evidence_refs") or [] if isinstance(e, dict)) for r in rows or [])
        except Exception:
            return False
    return verify


# ------------------------------------------------------------ observation ---
def _utility(books: Mapping[str, Any]) -> tuple[float | None, list[str]]:
    """Sum of own paper-book net-of-costs total returns; None when any live book lacks a measure."""
    members = sorted(books)
    values = []
    for member in members:
        book = books[member]
        value = book.get("total_return") if isinstance(book, Mapping) else None
        if (not isinstance(book, Mapping) or book.get("is_real_order") is not False
                or type(value) not in (int, float) or isinstance(value, bool)):
            return None, members
        values.append(float(value))
    return (sum(values) if values else None), members


def attach_harness_observation(persona: Mapping[str, Any], observe: dict[str, Any],
                               packets: Mapping[str, Mapping[str, Any]], packet_id: str, today: datetime) -> None:
    """Write ``observe_bundle.harness_observation`` while a committed harness experiment is active.

    Candidate utility is today's own paper-book measure; the base is the same measure from the
    newest recorded terminal packet before adoption. Missing measures or a changed member set are
    recorded as confounders (never zero-filled) so the observe step reports ``confounded``.
    """
    state = ((persona.get("metadata") or {}).get("persona_harness")) or {}
    patch_id = state.get("active_experiment")
    row = (state.get("patches") or {}).get(patch_id) if patch_id else None
    if not row or row.get("status") != "committed" or observe.get("unavailable"):
        return
    adopted = datetime.fromisoformat(row["adopted_at"])
    if today.date() <= adopted.date() or observe.get("harness_version") != row["version"]:
        return
    base = None
    for pid, packet in sorted(packets.items(), key=lambda kv: kv[1].get("updated_at", ""), reverse=True):
        bundle = (packet.get("persona_cycle") or {}).get("observe_bundle") or {}
        if (packet.get("tenant_id") == persona["tenant_id"] and persona["persona_id"] in packet.get("persona_ids", [])
                and (packet.get("persona_cycle") or {}).get("phase") == "terminal" and bundle.get("books")
                and not bundle.get("unavailable") and bundle.get("harness_version") == row["proposal"]["base_version"]
                and datetime.fromisoformat(packet["updated_at"]) < adopted):
            base = (pid, bundle)
            break
    candidate, members = _utility(observe.get("books") or {})
    base_value, base_members = _utility(base[1]["books"]) if base else (None, [])
    confounders = []
    if base is None or base_value is None:
        confounders.append("base_measure_unavailable")
    if candidate is None:
        confounders.append("candidate_measure_unavailable")
    if base and members != base_members:
        confounders.append("pool_membership_changed")
    observe["harness_observation"] = {
        "patch_id": patch_id, "base_version": row["proposal"]["base_version"], "candidate_version": row["version"],
        "trading_date": today.date().isoformat(), "criterion": row["observation_criterion"],
        "base_utility": base_value, "candidate_utility": candidate, "confounders": confounders,
        "evidence_refs": [f"ooda://{packet_id}", *(["ooda://" + base[0]] if base else []),
                          *observe.get("telemetry_refs", [])],
        "confidence": 0.5 if confounders else 1.0}


# ------------------------------------------------------------------- loop ---
class HarnessLoop:
    def __init__(self, harness: PersistentHarnessOwner, *, now: Callable[[], datetime] | None = None):
        self.harness = harness
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.consultation_factory = harness.adoption.consultation_factory

    # proposals ---------------------------------------------------------
    def _stage(self, persona_id: str, packet: dict[str, Any], degraded: list[str]) -> None:
        cycle = packet.get("persona_cycle") or {}
        proposals = (cycle.get("output") or {}).get("proposals") or []
        decisions = cycle.get("decisions") or []
        for index, proposal in enumerate(proposals):
            if proposal.get("kind") not in {"harness_patch", "consult_request"} or index >= len(decisions):
                continue
            decision = decisions[index]
            params = proposal.get("parameters") or {}
            if proposal["kind"] == "consult_request":
                # Consultations are opened by the loop for a recorded patch only.
                target = params.get("patch_id")
                known = target in (self.harness.read(persona_id).get("patches") or {})
                decision.update(status="consult_tracked" if known else "requires_owner_gate",
                                reason="harness_loop_opens_patch_consultation" if known else
                                "consult_request_has_no_recorded_patch")
                continue
            try:
                refs = [EvidenceReference.model_validate(r) for r in params.get("evidence_refs") or []]
                state = self.harness.read(persona_id)
                if not state.get("active_version"):
                    raise HarnessError("harness_not_initialized", 409)
                key = hashlib.sha256(json.dumps([packet["packet_id"], index]).encode()).hexdigest()[:32]
                patch = PersonaPatch(
                    patch_id="cycle-" + key, persona_id=persona_id, session_id=packet["packet_id"],
                    base_version=state["active_version"], rollback_target=state["active_version"],
                    patch_type=params.get("patch_type"), proposed_change=params.get("proposed_change") or {},
                    evidence_refs=refs, expected_effect=str(params.get("expected_effect") or proposal.get("reason")))
                self.harness.propose(persona_id, patch, "persona-cycle")
                decision.update(status="patch_recorded", patch_id=patch.patch_id, reason="harness_patch_recorded")
            except Exception as exc:  # pydantic validation or owner refusal: readable, not silent
                reason = str(exc).splitlines()[0][:120] if isinstance(exc, HarnessError) else "harness_patch_invalid:" + type(exc).__name__
                decision.update(status="patch_rejected", reason=reason)
                degraded.append(reason)

    # one patch -------------------------------------------------------------
    def _consult(self, persona: Any, row: dict[str, Any], comparison: dict[str, Any]) -> dict[str, Any]:
        from services.persona.consult_triggers import required_consultation
        proposal, tenant = row["proposal"], persona.tenant_id
        requirement = required_consultation(
            "harness_patch", proposal["patch_id"], initiator_persona_id=persona.persona_id,
            touches=proposal["proposed_change"], target_version=str(proposal["base_version"]),
            candidate_digest=comparison["candidate_digest"])
        reader = self.consultation_factory(tenant)
        found = [r for r in reader.list_requests(target_type="harness_patch", target_id=proposal["patch_id"],
                                                 tenant_id=tenant) or []
                 if r.get("from_persona_id") == persona.persona_id]
        if not found:
            from services.consultation.client import ConsultationServiceClient
            created = ConsultationServiceClient(tenant_id=tenant, service_actor=f"persona:{persona.persona_id}") \
                .open_persona_panel(
                    requirement, persona_id=persona.persona_id, trace_id="harness://" + proposal["patch_id"],
                    tenant_id=tenant, request_id="consult-" + requirement.decision_id,
                    task="Review harness patch " + proposal["patch_id"] + ": " + proposal["expected_effect"],
                    evidence_refs=[r["ref"] for r in proposal["evidence_refs"]])
            return {"state": "opened", "request_id": created["request_id"]}
        request = found[-1]
        if str(request.get("status") or "") in CONSULT_FAILED_STATES:
            return {"state": "operator", "reason": "consultation_" + str(request["status"])}
        if request.get("status") != "published":
            return {"state": "pending", "request_id": request["request_id"]}
        memos = reader.list_memos(request_id=request["request_id"], status="published")
        return {"state": "memo" if memos else "pending", "request_id": request["request_id"],
                "memo_id": memos[0]["memo_id"] if memos else None}

    def _advance(self, persona_id: str, patch_id: str, degraded: list[str], actions: list[dict]) -> None:
        harness, persona = self.harness, self.harness.personas.get(persona_id)
        row = harness.read(persona_id)["patches"][patch_id]
        status = row["status"]
        if status == "draft":
            result = harness.operate(persona_id, patch_id, "precheck", "persona-cycle")
            actions.append({"patch_id": patch_id, "step": "precheck", "status": result.get("status")})
            if result.get("status") == "refused":
                degraded.append("precheck_refused:" + ",".join(result.get("reasons", [])))
                return
            row = harness.read(persona_id)["patches"][patch_id]
            status = row["status"]
        if status == "previewed":
            comparisons = row.get("comparisons") or []
            if not comparisons or comparisons[-1].get("status") != "complete":
                from services.persona.harness_shadow_eval import run_shadow_comparison
                from services.persona.cron_ooda_closure import resolve_ooda_packet_store_path
                store = harness.packet_store_path() if harness.packet_store_path else resolve_ooda_packet_store_path()
                record = run_shadow_comparison(harness, persona_id, patch_id, packet_store_path=store,
                                               provider=harness.shadow_provider(persona_id), actor="persona-cycle")
                actions.append({"patch_id": patch_id, "step": "shadow", "status": record["status"],
                                "decision": record["decision"], "reasons": record["reasons"]})
                if record["status"] != "complete":
                    degraded.extend(("shadow_" + r) if any(r.startswith(p) for p in PROVIDER_REASONS) else r
                                    for r in record["reasons"])
                    return
                comparisons = [record]
            if comparisons[-1].get("decision") != "CANDIDATE_BETTER":
                actions.append({"patch_id": patch_id, "step": "keep_base", "status": "complete"})
                return
            consult = self._consult(persona, row, comparisons[-1])
            actions.append({"patch_id": patch_id, "step": "consultation", **consult})
            if consult["state"] == "operator":
                degraded.append(consult["reason"])
            if consult["state"] != "memo":
                return
            try:
                result = harness.operate(persona_id, patch_id, "activate", "persona-cycle", consult["memo_id"])
                actions.append({"patch_id": patch_id, "step": "adopted", "version": result.get("version")})
            except HarnessError as exc:
                actions.append({"patch_id": patch_id, "step": "adopt_refused", "reason": str(exc)})
            return
        if status == "committed" and harness.read(persona_id)["active_experiment"] == patch_id:
            window = self._observation_window(persona, row, patch_id)
            if window is None:
                return
            result = harness.adoption.observe(persona_id, patch_id, ObservationRequest(packet_ids=window),
                                              "persona-cycle")
            actions.append({"patch_id": patch_id, "step": "observed", "status": result.get("status"),
                            "outcome": (result.get("experience") or {}).get("outcome"),
                            "reasons": result.get("reasons")})

    def _observation_window(self, persona: Any, row: dict[str, Any], patch_id: str) -> list[str] | None:
        adopted = datetime.fromisoformat(row["adopted_at"])
        available = []
        for pid, packet in self.harness.adoption.packet_reader().items():
            cycle = packet.get("persona_cycle") or {}
            raw = (cycle.get("observe_bundle") or {}).get("harness_observation") or {}
            if (packet.get("tenant_id") == persona.tenant_id and packet.get("persona_ids") == [persona.persona_id]
                    and cycle.get("phase") == "terminal" and raw.get("patch_id") == patch_id
                    and raw.get("candidate_version") == row["version"]):
                try:
                    if datetime.fromisoformat(raw["trading_date"] + "T00:00:00+00:00").date() > adopted.date():
                        available.append((raw["trading_date"], pid))
                except (ValueError, TypeError, KeyError):
                    continue
        if len(available) < row["observation_trading_days"]:
            return None
        return [pid for _, pid in sorted(available)[:row["observation_trading_days"]]]

    # entry ---------------------------------------------------------------
    def run(self, persona: Mapping[str, Any], packet: dict[str, Any]) -> dict[str, Any]:
        """Never raises: any failure is a degraded reason on the cycle record."""
        persona_id = persona["persona_id"]
        degraded: list[str] = []
        actions: list[dict[str, Any]] = []
        try:
            if persona.get("lifecycle_state") != "paper_owner":
                return {"status": "not_applicable", "reasons": ["persona_not_paper_owner"], "actions": []}
            if not self.harness.read(persona_id).get("active_version"):
                return {"status": "degraded", "reasons": ["harness_not_initialized"], "actions": []}
            self._stage(persona_id, packet, degraded)
            for patch_id, row in list(self.harness.read(persona_id)["patches"].items()):
                if row["status"] not in {"draft", "previewed", "committed"}:
                    continue
                if row["status"] == "draft" and len(row.get("checks") or []) >= 3:
                    continue  # three refused prechecks: leave it for review instead of re-checking daily
                try:
                    self._advance(persona_id, patch_id, degraded, actions)
                except Exception as exc:
                    degraded.append(f"{patch_id}:{type(exc).__name__}:{str(exc)[:80]}")
        except Exception as exc:
            degraded.append(type(exc).__name__ + ":" + str(exc)[:80])
        return {"status": "degraded" if degraded else ("ok" if actions else "idle"),
                "reasons": sorted(set(degraded)), "actions": actions}


def default_loop(persona_owner: Any) -> HarnessLoop:
    """Production wiring: the loop reads and writes through the persona owner's own harness state."""
    harness = PersistentHarnessOwner(persona_owner)
    try:
        from services.persona.lesson_rule_proposals import build_lesson_rule_converter
        build_lesson_rule_converter(harness)  # composes lesson evidence into the verifier
    except Exception:
        pass  # lesson refs then fail closed; other refs still resolve
    return HarnessLoop(harness)


class ControllerHarnessLoop:
    """Adapter the cycle controller calls; builds the production loop on first use, never raises."""

    def __init__(self, loop: HarnessLoop | None, owners: Any):
        self._loop, self._owners = loop, owners

    def _get(self) -> HarnessLoop:
        if self._loop is None:
            self._loop = default_loop(self._owners.persona_owner)
        return self._loop

    def observation(self, persona: Mapping[str, Any], observe: dict[str, Any],
                    snapshots: Mapping[str, Mapping[str, Any]], packet_id: str) -> None:
        try:
            attach_harness_observation(persona, observe, snapshots, packet_id, self._get().now())
        except Exception as exc:
            observe.setdefault("harness_observation_error", type(exc).__name__)

    def run(self, persona: Mapping[str, Any], packet: dict[str, Any]) -> dict[str, Any]:
        try:
            return self._get().run(persona, packet)
        except Exception as exc:
            return {"status": "degraded", "reasons": ["harness_loop_unavailable:" + type(exc).__name__], "actions": []}
