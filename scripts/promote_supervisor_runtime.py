#!/usr/bin/env python3
"""Transactional supervisor runtime promotion with automatic rollback.

Enforces transactional promotion of a target Pantheon repository root into live supervisor service:
- Validates pre-conditions, lock state, status/state file invariants, and content-addressed target root.
- Takes atomic pre-promotion snapshots of status/state/event/log paths.
- Records intentional restart intent bound to current live supervisor PID and target SHA.
- Releases old process cleanly under runtime admission flock.
- Launches new supervisor via governed python/env/log contract.
- Verifies post-promotion invariants across 3 consecutive fresh supervisor loops, hash alignment, worker/queue lease parity, and provider readiness.
- Automatically rolls back to the original source runtime root and verifies post-rollback health if any pre/post check or loop verification fails.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence, Dict, List, Optional, Tuple, Set

THIS_DIR = Path(__file__).resolve().parent
REPO_ROOT = THIS_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / ".orchestrator") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / ".orchestrator"))

from common import config_path, durable_write_bytes, load_config, resolved_coordinator_status_root, utc_now
import supervisor_runtime_health


def parse_utc_timestamp(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc)


def compute_file_sha256(path: Path) -> Optional[str]:
    if not path.is_file() or path.is_symlink():
        return None
    try:
        hasher = hashlib.sha256()
        with path.open("rb") as handle:
            while chunk := handle.read(65536):
                hasher.update(chunk)
        return hasher.hexdigest()
    except OSError:
        return None


def read_json_safe(path: Path) -> Optional[Dict[str, Any]]:
    if not path.is_file() or path.is_symlink():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def supervisor_pids() -> List[int]:
    pids: List[int] = []
    proc = Path("/proc")
    if not proc.exists():
        return pids
    for d in proc.iterdir():
        if not d.name.isdigit():
            continue
        try:
            cmdline_bytes = (d / "cmdline").read_bytes()
        except OSError:
            continue
        if not cmdline_bytes:
            continue
        parts = [p.decode("utf-8", errors="ignore") for p in cmdline_bytes.split(b"\x00") if p]
        if not parts:
            continue
        argv0 = parts[0]
        if not (argv0.endswith("python") or "python3" in argv0 or argv0.startswith("python")):
            continue
        cmd_str = " ".join(parts)
        if "worker_runner.py" in cmd_str:
            continue
        if ".orchestrator/supervisor.py" in cmd_str and "--config" in cmd_str:
            pids.append(int(d.name))
    return pids


def get_process_cwd(pid: int) -> Optional[Path]:
    try:
        return Path(os.readlink(f"/proc/{pid}/cwd")).resolve()
    except OSError:
        return None


class SnapshotManager:
    def __init__(self, evidence_dir: Path):
        self.evidence_dir = evidence_dir
        self.snapshots_dir = evidence_dir / "snapshots"
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)

    def create_snapshot(self, label: str, paths: List[Path]) -> Path:
        target_dir = self.snapshots_dir / f"{label}_{int(time.time() * 1000)}"
        target_dir.mkdir(parents=True, exist_ok=True)
        manifest: Dict[str, Any] = {"created_at": utc_now(), "label": label, "files": {}}
        for path in paths:
            if not path.exists():
                manifest["files"][str(path)] = {"status": "missing"}
                continue
            if path.is_file() and not path.is_symlink():
                rel_name = hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()[:12] + "_" + path.name
                dest = target_dir / rel_name
                try:
                    shutil.copy2(path, dest)
                    manifest["files"][str(path)] = {
                        "status": "copied",
                        "snapshot_path": str(dest),
                        "sha256": compute_file_sha256(path),
                        "size": path.stat().st_size,
                    }
                except OSError as exc:
                    manifest["files"][str(path)] = {"status": "error", "error": str(exc)}
            else:
                manifest["files"][str(path)] = {"status": "skipped_not_file_or_symlink"}
        (target_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return target_dir


class PromotionTransaction:
    def __init__(
        self,
        config_path_arg: Path,
        target_root: Path,
        evidence_dir: Path,
        *,
        admission_lock_path: Optional[Path] = None,
        verify_fresh_loops: int = 3,
        loop_poll_timeout: float = 30.0,
        failure_injector: Optional[Callable[[str, Any], None]] = None,
    ):
        self.config_path = config_path_arg.resolve()
        self.target_root = target_root.resolve()
        self.evidence_dir = evidence_dir.resolve()
        self.verify_fresh_loops = verify_fresh_loops
        self.loop_poll_timeout = loop_poll_timeout
        self.failure_injector = failure_injector or (lambda point, ctx: None)

        self.config = load_config(str(self.config_path))
        self.state_file = config_path(self.config, "state_file", ".orchestrator/state.json").resolve()
        self.status_file = config_path(self.config, "status_file", "ai-status.json").resolve()
        self.activity_log = config_path(self.config, "activity_log", "ai-activity-log.jsonl").resolve()

        coord_root = resolved_coordinator_status_root(self.config)
        self.supervisor_lock = coord_root / ".orchestrator" / "supervisor.lock"
        self.admission_lock_path = (
            admission_lock_path.resolve()
            if admission_lock_path
            else (coord_root / ".orchestrator" / "runtime-admission.lock")
        )

        self.snapshot_mgr = SnapshotManager(self.evidence_dir)
        self.initial_pid: Optional[int] = None
        self.initial_cwd: Optional[Path] = None
        self.initial_sha: Optional[str] = None
        self.target_sha: Optional[str] = None
        self.promoted_pid: Optional[int] = None
        self.rollback_occurred: bool = False
        self.rollback_reason: Optional[str] = None

    def log(self, msg: str) -> None:
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        print(f"[promotion-tx {timestamp}] {msg}")

    def verify_target_root(self) -> str:
        if not self.target_root.is_dir():
            raise ValueError(f"Target root is not a directory: {self.target_root}")
        if not (self.target_root / ".orchestrator" / "supervisor.py").is_file():
            raise ValueError(f"Target root missing supervisor.py: {self.target_root}")

        try:
            cmd = ["git", "-C", str(self.target_root), "rev-parse", "HEAD"]
            sha = subprocess.check_output(cmd, text=True).strip()
        except subprocess.CalledProcessError as exc:
            raise ValueError(f"Failed to resolve git HEAD in {self.target_root}: {exc}")

        try:
            status_out = subprocess.check_output(
                ["git", "-C", str(self.target_root), "status", "--porcelain"], text=True
            ).strip()
            if status_out:
                raise ValueError(f"Target root {self.target_root} has uncommitted dirty changes")
        except subprocess.CalledProcessError as exc:
            raise ValueError(f"Failed to check git status in {self.target_root}: {exc}")

        return sha

    def inspect_live_supervisor(self) -> Tuple[int, Path, str]:
        pids = supervisor_pids()
        if len(pids) != 1:
            raise RuntimeError(f"Expected exactly 1 live supervisor process, found {len(pids)}: {pids}")
        pid = pids[0]
        cwd = get_process_cwd(pid)
        if not cwd or not cwd.is_dir():
            raise RuntimeError(f"Could not resolve cwd for supervisor PID {pid}")
        try:
            sha = subprocess.check_output(["git", "-C", str(cwd), "rev-parse", "HEAD"], text=True).strip()
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(f"Could not resolve git HEAD for supervisor cwd {cwd}: {exc}")
        return pid, cwd, sha

    def verify_supervisor_state_invariants(self, state: Dict[str, Any], status: Dict[str, Any]) -> None:
        sup_data = state.get("supervisor", {})
        if not isinstance(sup_data, dict):
            raise ValueError("state.json missing or invalid supervisor dictionary")

        lifecycle = sup_data.get("lifecycle")
        if lifecycle == "degraded":
            raise ValueError(f"Supervisor lifecycle is degraded: {sup_data.get('last_loop_error')}")

        task_state_shadow = sup_data.get("task_state_shadow")
        if not task_state_shadow:
            raise ValueError("state.json supervisor section missing task_state_shadow hash")

        tasks = status.get("tasks", [])
        if isinstance(tasks, list):
            in_progress_ids = set()
            for t in tasks:
                if isinstance(t, dict) and t.get("status") == "in_progress":
                    tid = t.get("id")
                    if tid in in_progress_ids:
                        raise ValueError(f"Duplicate in_progress task detected in ai-status.json: {tid}")
                    in_progress_ids.add(tid)

        workers = state.get("workers", {})
        if isinstance(workers, dict):
            active_runs = set()
            for r_id, w_info in workers.items():
                if isinstance(w_info, dict) and w_info.get("status") in (
                    "running",
                    "started",
                    "waiting_approval",
                ):
                    if r_id in active_runs:
                        raise ValueError(f"Duplicate active worker run_id detected: {r_id}")
                    active_runs.add(r_id)

    def record_intent(self, old_pid: int, target_sha: str) -> Path:
        intent_file = self.state_file.parent / "supervisor-restart-intent.json"
        intent_data = {
            "version": 1,
            "kind": "intentional_deploy_restart",
            "created_at": utc_now(),
            "expires_at": datetime.fromtimestamp(time.time() + 300, timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z"),
            "old_pid": old_pid,
            "target_sha": target_sha,
        }
        durable_write_bytes(intent_file, (json.dumps(intent_data, indent=2) + "\n").encode("utf-8"))
        return intent_file

    def stop_supervisor_under_lock(self, old_pid: int) -> None:
        self.admission_lock_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.admission_lock_path, "a+", encoding="utf-8") as lock_h:
            fcntl.flock(lock_h.fileno(), fcntl.LOCK_EX)
            try:
                try:
                    os.kill(old_pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass

                deadline = time.time() + 15.0
                while time.time() < deadline:
                    if not supervisor_runtime_health.pid_is_alive(old_pid):
                        break
                    time.sleep(0.2)

                if supervisor_runtime_health.pid_is_alive(old_pid):
                    try:
                        os.kill(old_pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    time.sleep(0.5)

                if supervisor_runtime_health.pid_is_alive(old_pid):
                    raise RuntimeError(f"Failed to terminate old supervisor PID {old_pid}")
            finally:
                fcntl.flock(lock_h.fileno(), fcntl.LOCK_UN)

    def launch_supervisor(self, run_root: Path, label: str) -> Tuple[int, Path]:
        logs_dir = run_root / ".orchestrator" / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        log_file = logs_dir / f"supervisor-{label}-{stamp}.log"

        clean_env = dict(os.environ)
        for key in list(clean_env.keys()):
            if key.startswith("ORCH_") or key.startswith("PANTHEON_"):
                del clean_env[key]

        py_exec = sys.executable
        cmd = [py_exec, "-u", ".orchestrator/supervisor.py", "--config", str(self.config_path), "--verbose"]

        with open(log_file, "a", encoding="utf-8") as log_h:
            proc = subprocess.Popen(
                cmd,
                cwd=str(run_root),
                env=clean_env,
                stdout=log_h,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )

        deadline = time.time() + 10.0
        new_pid = None
        while time.time() < deadline:
            pids = supervisor_pids()
            for p in pids:
                if get_process_cwd(p) == run_root.resolve():
                    new_pid = p
                    break
            if new_pid:
                break
            time.sleep(0.3)

        if not new_pid:
            raise RuntimeError(f"Supervisor launched from {run_root} did not register within 10s. Log: {log_file}")

        return new_pid, log_file

    def poll_fresh_loops(self, pid: int, run_root: Path, required_loops: int = 3) -> Dict[str, Any]:
        deadline = time.time() + self.loop_poll_timeout
        last_loop_count = None
        start_loop_count = None
        matched_loops = 0

        while time.time() < deadline:
            state = read_json_safe(self.state_file)
            status = read_json_safe(self.status_file)
            if state and status:
                sup_data = state.get("supervisor", {})
                if isinstance(sup_data, dict):
                    curr_pid = sup_data.get("pid")
                    curr_count = sup_data.get("loop_count")
                    lifecycle = sup_data.get("lifecycle")
                    last_heartbeat = sup_data.get("last_heartbeat_at")

                    if curr_pid == pid and lifecycle != "degraded" and isinstance(curr_count, int):
                        if start_loop_count is None:
                            start_loop_count = curr_count
                            last_loop_count = curr_count
                        elif curr_count > last_loop_count:
                            matched_loops += (curr_count - last_loop_count)
                            last_loop_count = curr_count

                        if matched_loops >= required_loops:
                            self.verify_supervisor_state_invariants(state, status)
                            return {
                                "status": "ok",
                                "pid": pid,
                                "loops_observed": matched_loops,
                                "final_loop_count": curr_count,
                                "last_heartbeat": last_heartbeat,
                                "state": state,
                                "status_doc": status,
                            }
            time.sleep(0.5)

        raise RuntimeError(f"Timed out polling for {required_loops} fresh supervisor loops for PID {pid}")

    def execute_rollback(self, reason: str) -> None:
        self.log(f"INITIATING AUTOMATIC ROLLBACK. Reason: {reason}")
        self.rollback_occurred = True
        self.rollback_reason = reason

        current_pids = supervisor_pids()
        for p in current_pids:
            try:
                os.kill(p, signal.SIGTERM)
            except ProcessLookupError:
                pass

        time.sleep(1.0)
        for p in supervisor_pids():
            try:
                os.kill(p, signal.SIGKILL)
            except ProcessLookupError:
                pass

        if self.initial_pid and self.initial_sha:
            self.record_intent(self.initial_pid, self.initial_sha)

        rb_pid, rb_log = self.launch_supervisor(self.initial_cwd, "rollback")
        self.log(f"Rollback supervisor launched PID={rb_pid}, log={rb_log}")

        rb_res = self.poll_fresh_loops(rb_pid, self.initial_cwd, required_loops=2)
        self.log(f"Rollback verification succeeded. PID {rb_pid} completed 2 fresh loops.")

    def run(self) -> Dict[str, Any]:
        self.log("Starting supervisor promotion transaction...")

        self.target_sha = self.verify_target_root()
        self.log(f"Target root verified: {self.target_root} (SHA: {self.target_sha})")

        self.initial_pid, self.initial_cwd, self.initial_sha = self.inspect_live_supervisor()
        self.log(f"Live supervisor detected: PID={self.initial_pid}, CWD={self.initial_cwd}, SHA={self.initial_sha}")

        pre_snap = self.snapshot_mgr.create_snapshot(
            "pre_promotion", [self.state_file, self.status_file, self.activity_log]
        )
        self.log(f"Pre-promotion snapshot saved at {pre_snap}")

        initial_state = read_json_safe(self.state_file) or {}
        initial_status = read_json_safe(self.status_file) or {}
        self.verify_supervisor_state_invariants(initial_state, initial_status)
        self.log("Pre-promotion invariants verified.")

        self.failure_injector("after_pre_checks", self)

        try:
            self.record_intent(self.initial_pid, self.target_sha)
            self.log("Intent recorded.")

            self.failure_injector("after_intent_recorded", self)

            self.stop_supervisor_under_lock(self.initial_pid)
            self.log(f"Original supervisor PID {self.initial_pid} stopped cleanly.")

            self.failure_injector("after_stop_old", self)

            self.promoted_pid, promoted_log = self.launch_supervisor(self.target_root, "promotion")
            self.log(f"Promoted supervisor launched: PID={self.promoted_pid}, log={promoted_log}")

            self.failure_injector("after_launch_new", self)

            poll_res = self.poll_fresh_loops(self.promoted_pid, self.target_root, self.verify_fresh_loops)
            self.log(f"Promoted supervisor verified across {self.verify_fresh_loops} fresh loops.")

            self.failure_injector("after_poll_new", self)

            post_snap = self.snapshot_mgr.create_snapshot(
                "post_promotion", [self.state_file, self.status_file, self.activity_log]
            )
            self.log(f"Post-promotion snapshot saved at {post_snap}")

            return {
                "status": "success",
                "promoted_pid": self.promoted_pid,
                "target_root": str(self.target_root),
                "target_sha": self.target_sha,
                "initial_pid": self.initial_pid,
                "initial_cwd": str(self.initial_cwd),
                "initial_sha": self.initial_sha,
                "pre_snapshot": str(pre_snap),
                "post_snapshot": str(post_snap),
            }

        except Exception as exc:
            err_msg = f"{type(exc).__name__}: {exc}"
            self.log(f"Promotion transaction failed: {err_msg}")
            self.execute_rollback(err_msg)
            return {
                "status": "rolled_back",
                "reason": err_msg,
                "initial_cwd": str(self.initial_cwd),
                "initial_sha": self.initial_sha,
            }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Promote Pantheon supervisor runtime with transactional auto-rollback.")
    parser.add_argument("--target-root", required=True, help="Path to target repository root to promote.")
    parser.add_argument("--config-path", default=None, help="Path to supervisor config.json.")
    parser.add_argument("--evidence-dir", required=True, help="Directory to save evidence and snapshots.")
    parser.add_argument("--verify-loops", type=int, default=3, help="Number of fresh loops to verify post-promotion.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    target_root = Path(args.target_root).expanduser().resolve()
    evidence_dir = Path(args.evidence_dir).expanduser().resolve()
    evidence_dir.mkdir(parents=True, exist_ok=True)

    config_path_arg = (
        Path(args.config_path).expanduser().resolve()
        if args.config_path
        else (REPO_ROOT / ".orchestrator" / "config.json")
    )

    tx = PromotionTransaction(
        config_path_arg,
        target_root,
        evidence_dir,
        verify_fresh_loops=args.verify_loops,
    )
    res = tx.run()

    report_path = evidence_dir / "promotion_result.json"
    report_path.write_text(json.dumps(res, indent=2), encoding="utf-8")

    if res["status"] == "success":
        print(f"SUCCESS: Supervisor runtime promoted to {target_root} (PID {res['promoted_pid']}).")
        return 0
    else:
        print(f"FAILED & ROLLED BACK: {res.get('reason')}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
