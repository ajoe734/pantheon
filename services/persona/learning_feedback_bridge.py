"""Learning feedback bridge between executed evolution decisions / postmortems and persona memory."""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

# Setup logs
log = logging.getLogger("learning-feedback-bridge")
if not log.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s [%(name)s]: %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)


def parse_persona_id_from_pcb(pcb_id: str) -> str:
    """Extract persona_id from persona_capital_binding_id or similar strings.
    e.g. pcb-persona-crypto-paper -> persona-crypto
         pcb-usability-persona-crypto -> persona-crypto
         pcb-persona-crypto -> persona-crypto
    """
    if not pcb_id:
        return ""
    s = str(pcb_id).strip()
    if s.startswith("pcb-usability-"):
        s = s[len("pcb-usability-"):]
    elif s.startswith("pcb-"):
        s = s[len("pcb-"):]
    if s.endswith("-paper"):
        s = s[:-len("-paper")]
    return s


def load_local_decisions(storage_path: Path) -> List[Dict[str, Any]]:
    """Load decisions from a local EvolutionDecisionStore JSON file."""
    if not storage_path.exists():
        return []
    try:
        text = storage_path.read_text(encoding="utf-8")
        if not text.strip():
            return []
        data = json.loads(text)
        if isinstance(data, dict):
            return list(data.values())
        if isinstance(data, list):
            return data
    except Exception as exc:
        log.warning("Failed to load local decisions from %s: %s", storage_path, exc)
    return []


def load_local_postmortems(storage_path: Path) -> List[Dict[str, Any]]:
    """Load postmortems from a local IncidentStore JSON file."""
    if not storage_path.exists():
        return []
    try:
        text = storage_path.read_text(encoding="utf-8")
        if not text.strip():
            return []
        data = json.loads(text)
        if isinstance(data, dict) and "postmortems" in data:
            return list(data["postmortems"])
    except Exception as exc:
        log.warning("Failed to load local postmortems from %s: %s", storage_path, exc)
    return []


def run_learning_feedback_bridge(
    *,
    evolution_api_url: Optional[str] = None,
    postmortems_api_url: Optional[str] = None,
    memory_api_url: Optional[str] = None,
    decisions_store_path: Optional[Path] = None,
    incidents_store_path: Optional[Path] = None,
    persona_store: Optional[Any] = None,
    institutional_store: Optional[Any] = None,
    skip_openclaw_sync: bool = False,
) -> Dict[str, Any]:
    """Poll executed decisions and published postmortems, post them to memory writeback,

    and run OpenClaw sync to materialize them to persona agent workspaces.
    """
    # 1. Resolve URLs and Paths
    evo_url = (evolution_api_url or os.getenv("EVOLUTION_API_URL", "http://localhost:8093")).strip().rstrip("/")
    pm_url = (postmortems_api_url or os.getenv("POSTMORTEMS_API_URL", "http://localhost:8091")).strip().rstrip("/")
    mem_url = (memory_api_url or os.getenv("MEMORY_API_URL", "http://localhost:8086")).strip().rstrip("/")

    evo_dir = os.getenv("EVOLUTION_DATA_DIR", "/tmp/pantheon/evolution")
    inc_dir = os.getenv("INCIDENT_DATA_DIR", "/tmp/pantheon/incident")
    default_decisions_path = Path(evo_dir) / "decisions.json"
    default_incidents_path = Path(inc_dir) / "incidents.json"

    dec_path = decisions_store_path or default_decisions_path
    inc_path = incidents_store_path or default_incidents_path

    # 2. Fetch/Load Executed Decisions
    decisions: List[Dict[str, Any]] = []
    try:
        url = f"{evo_url}/api/evolution/proposals"
        req = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            decisions = data.get("proposals") if isinstance(data, dict) else data
        log.info("Fetched %d decisions from evolution API %s", len(decisions), evo_url)
    except Exception as exc:
        log.warning("Could not fetch decisions from evolution API: %s. Falling back to local file %s", exc, dec_path)
        decisions = load_local_decisions(dec_path)
        log.info("Loaded %d decisions from local file %s", len(decisions), dec_path)

    # Filter executed decisions with non-empty outcome_summary
    executed_decisions: List[Dict[str, Any]] = []
    for d in decisions:
        # Check decision state
        state = str(d.get("decision_state") or "").lower()
        if state != "executed":
            continue
        # Check execution result
        exec_res = d.get("execution_result")
        if not exec_res:
            continue
        status = str(exec_res.get("status") or "").lower()
        # Fail-closed: "沒有結果不得造記憶" -> only complete or submitted with result
        outcome = str(exec_res.get("outcome_summary") or "").strip()
        if not outcome:
            continue
        executed_decisions.append(d)

    # 3. Fetch/Load Published Postmortems
    postmortems: List[Dict[str, Any]] = []
    try:
        url = f"{pm_url}/api/postmortems?status=published"
        req = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            postmortems = json.loads(resp.read().decode("utf-8"))
        log.info("Fetched %d published postmortems from postmortem API %s", len(postmortems), pm_url)
    except Exception as exc:
        log.warning("Could not fetch postmortems from postmortem API: %s. Falling back to local file %s", exc, inc_path)
        pm_all = load_local_postmortems(inc_path)
        postmortems = [p for p in pm_all if str(p.get("status") or "").lower() == "published"]
        log.info("Loaded %d published postmortems from local file %s", len(postmortems), inc_path)

    report = {
        "processed_decisions": 0,
        "processed_postmortems": 0,
        "written_decisions": [],
        "written_postmortems": [],
        "errors": [],
    }

    # 4. Process Decisions → write feedback to memory plane
    for d in executed_decisions:
        decision_id = d["decision_id"]
        persona_id = d.get("persona_id") or parse_persona_id_from_pcb(d.get("persona_capital_binding_id") or "")
        if not persona_id:
            log.warning("Decision %s has no persona_id or valid PCB; skipping", decision_id)
            continue
        
        exec_res = d["execution_result"]
        outcome = exec_res["outcome_summary"]
        
        payload = {
            "source_event_type": "evolution_decision_approved",
            "source_event_id": decision_id,
            "write_authority": "evolution-svc",
            "sponsor_persona_id": persona_id,
            "contributing_persona_ids": [persona_id],
            "summary": f"Executed evolution decision: {outcome}",
            "runtime_telemetry_evidence": [
                {
                    "type": "evolution_decision",
                    "id": decision_id,
                    "target_id": d.get("target_id"),
                    "action_type": d.get("action_type"),
                    "outcome_summary": outcome,
                }
            ],
            "proposal_ids": [decision_id],
            "tags": ["evolution", "executed_decision", d.get("action_type", "")],
        }

        # Write to memory (either via HTTP or local writeback function)
        try:
            if persona_store and institutional_store:
                from services.memory.learn_feedback_writeback import write_learn_feedback
                res = write_learn_feedback(
                    payload,
                    persona_store=persona_store,
                    institutional_store=institutional_store,
                )
                created = res.get("created", False)
            else:
                req_url = f"{mem_url}/api/memory/writebacks/learn-feedback"
                req_body = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(
                    req_url,
                    data=req_body,
                    headers={"Content-Type": "application/json"},
                    method="POST"
                )
                with urllib.request.urlopen(req, timeout=5.0) as resp:
                    res = json.loads(resp.read().decode("utf-8"))
                    created = res.get("created", False)
            
            report["processed_decisions"] += 1
            if created:
                report["written_decisions"].append(decision_id)
                log.info("Learning feedback written for executed decision %s", decision_id)
            else:
                log.info("Learning feedback already exists for decision %s (idempotent)", decision_id)
        except Exception as exc:
            msg = f"Failed to write learning feedback for decision {decision_id}: {exc}"
            log.error(msg)
            report["errors"].append(msg)

    # 5. Process Postmortems → write feedback to memory plane
    for pm in postmortems:
        pm_id = pm["postmortem_id"]
        persona_id = parse_persona_id_from_pcb(pm.get("persona_capital_binding_id") or "")
        if not persona_id:
            log.warning("Postmortem %s has no valid persona_capital_binding_id; skipping", pm_id)
            continue
        
        root_cause = pm.get("root_cause") or pm.get("title") or "No details"
        payload = {
            "source_event_type": "postmortem_published",
            "source_event_id": pm_id,
            "write_authority": "incident-svc",
            "sponsor_persona_id": persona_id,
            "contributing_persona_ids": [persona_id],
            "summary": f"Published postmortem: {root_cause}",
            "runtime_telemetry_evidence": [
                {
                    "type": "postmortem",
                    "id": pm_id,
                    "incident_id": pm.get("incident_id"),
                    "root_cause": root_cause,
                }
            ],
            "proposal_ids": [pm_id],
            "tags": ["postmortem", "incident", persona_id],
        }

        try:
            if persona_store and institutional_store:
                from services.memory.learn_feedback_writeback import write_learn_feedback
                res = write_learn_feedback(
                    payload,
                    persona_store=persona_store,
                    institutional_store=institutional_store,
                )
                created = res.get("created", False)
            else:
                req_url = f"{mem_url}/api/memory/writebacks/learn-feedback"
                req_body = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(
                    req_url,
                    data=req_body,
                    headers={"Content-Type": "application/json"},
                    method="POST"
                )
                with urllib.request.urlopen(req, timeout=5.0) as resp:
                    res = json.loads(resp.read().decode("utf-8"))
                    created = res.get("created", False)
            
            report["processed_postmortems"] += 1
            if created:
                report["written_postmortems"].append(pm_id)
                log.info("Learning feedback written for postmortem %s", pm_id)
            else:
                log.info("Learning feedback already exists for postmortem %s (idempotent)", pm_id)
        except Exception as exc:
            msg = f"Failed to write learning feedback for postmortem {pm_id}: {exc}"
            log.error(msg)
            report["errors"].append(msg)

    # 6. Trigger OpenClaw Sync (reconcile) if configured and any new entries written
    if not skip_openclaw_sync and (report["written_decisions"] or report["written_postmortems"]):
        # Run sync-persona-agents to force materialization to MEMORY.md
        log.info("New learning feedback written to memory plane. Triggering OpenClaw agent synchronization...")
        try:
            # We can retrieve all personas from bff
            bff_url = os.getenv("BFF_API_URL", "http://localhost:18001").rstrip("/")
            req_url = f"{bff_url}/bff/personas"
            req = urllib.request.Request(req_url, headers={"Authorization": "Bearer op-b3-evolution:operator,reviewer"}, method="GET")
            
            personas_list = []
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                personas_list = json.loads(resp.read().decode("utf-8"))
            
            if personas_list:
                # Run the openclaw-sync-persona-agents.py script via docker exec or subprocess
                script_path = Path(__file__).resolve().parents[2] / "scripts" / "openclaw-sync-persona-agents.py"
                if script_path.exists():
                    p = subprocess.Popen(
                        [sys.executable, str(script_path)],
                        stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                    )
                    stdout, stderr = p.communicate(input=json.dumps(personas_list))
                    log.info("OpenClaw sync script exit=%d. stdout: %s. stderr: %s", p.returncode, stdout.strip(), stderr.strip())
        except Exception as exc:
            log.warning("Could not run OpenClaw agent synchronization: %s", exc)

    return report


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Learning feedback bridge for OpenClaw.")
    parser.add_argument("--once", action="store_true", help="Run once and exit.")
    parser.add_argument("--poll", type=int, default=0, help="Poll interval in seconds (0 to disable).")
    args = parser.parse_args()

    if args.poll > 0:
        import time
        log.info("Running learning feedback bridge daemon, polling every %ds", args.poll)
        while True:
            try:
                run_learning_feedback_bridge()
            except Exception as exc:
                log.error("Unhandled error in bridge tick: %s", exc)
            time.sleep(args.poll)
    else:
        run_learning_feedback_bridge()
    return 0


if __name__ == "__main__":
    sys.exit(main())
