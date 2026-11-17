#!/usr/bin/env python3
"""Daily read-only measurement of hosted storage against docs/operations/storage-manifest.json.

RC3 WP3.3 of docs/operations/remediation-plan-20261117.md.

Two modes:

  collect   Runs on the CI runner. Sends ONE fixed read-only script to the hosted VM through
            scripts/dev_vm_ssh.sh (``bash -s`` on stdin) and writes the raw output to a file.
            The remote script lists file names, sizes and mtimes under /data of running containers
            (find -printf, never file contents, never container environment), runs SELECT-only SQL
            on catalog views through ``docker exec psql`` (default_transaction_read_only=on) and
            prints disk, docker and memory headline numbers. It writes nothing on hosted.
  report    Pure function of (raw collection, manifest, previous report). Exit 1 when a declared
            bound is exceeded or an orphan temp file exists; 0 otherwise.

Growth of the measurement itself: one JSON report per day, kept by workflow artifact retention.
"""
from __future__ import annotations

import argparse
import json
import posixpath
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = REPO_ROOT / "docs" / "operations" / "storage-manifest.json"
SCHEMA_VERSION = "hosted_storage_report.v1"
UNKNOWN_AGE_SECONDS = 7 * 86400
ORPHAN_TEMP_AGE_SECONDS = 86400
GB = 1000 ** 3
MB = 1000 ** 2

# Numeric budgets for manifest entries, keyed by manifest name. The manifest `cap` text is prose;
# these are the numbers the daily run enforces. max_bytes is a hosted-scale ceiling,
# max_growth_per_day is bytes/day compared with the change since the previous run.
# An unbounded entry with no budget here is reported as "unbounded", not exceeded.
BUDGETS: dict[str, dict[str, int]] = {
    "public.telemetry_events": {"max_bytes": 5 * GB, "max_growth_per_day": 100 * MB},
    "telemetry_events": {"max_bytes": 5 * GB, "max_growth_per_day": 100 * MB},
    "trade_journey_projection.event_receipts": {"max_bytes": 500 * MB, "max_growth_per_day": 50 * MB},
    "reconciliation_drift.work_claims": {"max_bytes": 50 * MB, "max_growth_per_day": 5 * MB},
    "downstream_health.sqlite3": {"max_bytes": 100 * MB, "max_growth_per_day": 10 * MB},
    "latest_market_snapshots.jsonl": {"max_bytes": 100 * MB, "max_growth_per_day": 10 * MB},
    "/data/source-ingest/latest_market_snapshots.jsonl": {"max_bytes": 100 * MB, "max_growth_per_day": 10 * MB},
    "distillation_job_queue.sqlite3": {"max_bytes": 500 * MB, "max_growth_per_day": 100 * MB},
    "job_queue.sqlite3": {"max_bytes": 500 * MB, "max_growth_per_day": 100 * MB},
    "/data/source-ingest/distill_job_queue.jsonl": {"max_bytes": 100 * MB, "max_growth_per_day": 50 * MB},
}

TEMP_RE = re.compile(r"(\.tmp($|[.\-_])|\.tmp[0-9a-z_]*$|\.part(ial)?$|\.new$|(^|/)\.?tmp[0-9a-z_]{4,}[^/]*$)", re.I)
SIDECAR_RE = re.compile(r"-(wal|shm|journal)$")

# ---------------------------------------------------------------------------- remote (read only)

REMOTE_SCRIPT = r"""#!/bin/bash
# Read-only hosted storage probe. Prints names, sizes, mtimes and counters. Writes nothing.
set -u
export LC_ALL=C
echo "@@time"; date -u +%s
echo "@@disk"; df -B1 --output=source,size,used,avail,target / /var/lib/docker 2>/dev/null
echo "@@free"; free -b 2>/dev/null
echo "@@docker_df"; docker system df --format '{{json .}}' 2>/dev/null
echo "@@docker_stats"; docker stats --no-stream --format '{{json .}}' 2>/dev/null
echo "@@files"
for c in $(docker ps --format '{{.Names}}' 2>/dev/null); do
  docker exec "$c" find /data -xdev -type f -printf "$c\t%s\t%T@\t%p\n" 2>/dev/null
done
echo "@@lines"
for c in $(docker ps --format '{{.Names}}' 2>/dev/null); do
  docker exec "$c" sh -c 'for f in $(find /data -xdev -type f -name "*.jsonl" -size -2000000k 2>/dev/null); do printf "%s\t%s\n" "$f" "$(wc -l < "$f")"; done' 2>/dev/null
done
echo "@@tables"
pg="$(docker ps --filter label=com.docker.compose.service=postgres --format '{{.Names}}' 2>/dev/null | head -n 1)"
if [ -n "$pg" ]; then
  docker exec -i -e PGOPTIONS='-c default_transaction_read_only=on -c statement_timeout=30000' "$pg" \
    sh -c 'psql -X -At -F "|" -v ON_ERROR_STOP=1 -U "${POSTGRES_USER:-postgres}" -d "${POSTGRES_DB:-pantheon}"' <<'SQL' 2>/dev/null
SELECT n.nspname || '.' || c.relname, pg_total_relation_size(c.oid), GREATEST(c.reltuples, 0)::bigint
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind IN ('r', 'p', 'm') AND n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast');
SQL
fi
echo "@@end"
"""


def collect(output: Path, transport: Path) -> None:
    proc = subprocess.run(
        [str(transport), "exec", "bash -s"], input=REMOTE_SCRIPT, text=True,
        capture_output=True, timeout=600, check=False,
    )
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr[-2000:])
        raise SystemExit(f"hosted collection failed with exit {proc.returncode}")
    output.write_text(proc.stdout, encoding="utf-8")


# ---------------------------------------------------------------------------- parsing

def _sections(raw: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    cur: list[str] | None = None
    for line in raw.splitlines():
        if line.startswith("@@"):
            cur = out.setdefault(line[2:].strip(), [])
        elif cur is not None and line.strip():
            cur.append(line)
    return out


_UNITS = {"b": 1, "kb": 1000, "mb": 1000 ** 2, "gb": 1000 ** 3, "tb": 1000 ** 4,
          "kib": 1024, "mib": 1024 ** 2, "gib": 1024 ** 3, "tib": 1024 ** 4}


def parse_size(text: str) -> int:
    m = re.match(r"\s*([0-9.]+)\s*([A-Za-z]*)", text or "")
    if not m:
        return 0
    return int(float(m.group(1)) * _UNITS.get(m.group(2).lower() or "b", 1))


def parse_collection(raw: str) -> dict:
    sec = _sections(raw)
    now = int(float((sec.get("time") or ["0"])[0]))
    files, seen = [], set()
    for line in sec.get("files", []):
        parts = line.split("\t")
        if len(parts) != 4 or parts[3] in seen:
            continue  # the same volume path mounted in several containers is counted once
        seen.add(parts[3])
        files.append({"container": parts[0], "size": int(parts[1]), "mtime": int(float(parts[2])), "path": parts[3]})
    lines = {}
    for line in sec.get("lines", []):
        parts = line.split("\t")
        if len(parts) == 2 and parts[1].strip().isdigit():
            lines[parts[0]] = int(parts[1])
    tables = {}
    for line in sec.get("tables", []):
        parts = line.split("|")
        if len(parts) == 3:
            tables[parts[0]] = {"bytes": int(parts[1]), "rows": int(parts[2])}
    disk = []
    for line in sec.get("disk", [])[1:]:
        p = line.split()
        if len(p) >= 5:
            disk.append({"source": p[0], "size": int(p[1]), "used": int(p[2]), "free": int(p[3]), "mount": p[4]})
    docker_df = []
    for line in sec.get("docker_df", []):
        try:
            d = json.loads(line)
            docker_df.append({"type": d.get("Type"), "size": d.get("Size"), "reclaimable": d.get("Reclaimable")})
        except ValueError:
            pass
    containers = []
    for line in sec.get("docker_stats", []):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        containers.append({"name": d.get("Name"), "memory_bytes": parse_size(str(d.get("MemUsage", "")).split("/")[0])})
    containers.sort(key=lambda c: c["memory_bytes"], reverse=True)
    mem = {}
    for line in sec.get("free", []):
        p = line.split()
        if p and p[0] == "Mem:" and len(p) >= 7:
            mem = {"total": int(p[1]), "used": int(p[2]), "available": int(p[6])}
    return {"collected_at": now, "files": files, "line_counts": lines, "tables": tables,
            "disk": disk, "docker_df": docker_df, "top_containers_by_memory": containers[:10], "memory": mem}


# ---------------------------------------------------------------------------- analysis

def entry_id(entry: dict) -> str:
    return f"{entry['kind']}:{entry.get('name') or entry.get('pattern')}"


def _entry_matches(entry: dict, key: str) -> bool:
    if "pattern" in entry:
        return re.fullmatch(entry["pattern"], key) is not None
    return entry.get("name") == key


def _finding(kind: str, entry: dict | None, **extra) -> dict:
    base = {"finding": kind}
    if entry is not None:
        base.update({"entry_id": entry_id(entry), "owner": entry.get("owner"),
                     "register_class": entry.get("register_class"), "task": entry.get("task")})
    base.update(extra)
    return base


def build_report(raw: str, manifest: dict, previous: dict | None = None) -> dict:
    data = parse_collection(raw)
    now = data["collected_at"]
    entries = manifest["entries"]
    prev_m = (previous or {}).get("measurements", {})
    prev_t = (previous or {}).get("collected_at")
    elapsed_days = max((now - prev_t) / 86400, 1 / 24) if prev_t else None

    # Attribute every file to a manifest entry (compose path first, then basename).
    sums: dict[str, dict] = {}
    unmatched, orphans = [], []
    compose = [e for e in entries if e["kind"] == "compose_path"]
    named = [e for e in entries if e["kind"] in ("jsonl", "sqlite")]
    for f in data["files"]:
        path = f["path"]
        base_path = SIDECAR_RE.sub("", path)
        if TEMP_RE.search(path) and now - f["mtime"] > ORPHAN_TEMP_AGE_SECONDS:
            orphans.append(f)
            continue
        owner = next((e for e in compose if _entry_matches(e, base_path)), None) \
            or next((e for e in named if _entry_matches(e, posixpath.basename(base_path))), None)
        if owner is None:
            if now - f["mtime"] > UNKNOWN_AGE_SECONDS:
                unmatched.append(f)
            continue
        m = sums.setdefault(entry_id(owner), {"entry": owner, "bytes": 0, "files": 0, "lines": 0})
        m["bytes"] += f["size"]
        m["files"] += 1
        m["lines"] += data["line_counts"].get(path, 0)

    rel_counts: dict[str, int] = {}
    for t in data["tables"]:
        rel_counts[t.split(".", 1)[1]] = rel_counts.get(t.split(".", 1)[1], 0) + 1
    for e in entries:
        if e["kind"] not in ("table", "schema_table"):
            continue
        key = e.get("name") or ""
        table = data["tables"].get(key)
        if table is None and "." not in key and rel_counts.get(key) == 1:
            table = next(v for k, v in data["tables"].items() if k.split(".", 1)[1] == key)
        if table is not None:
            sums[entry_id(e)] = {"entry": e, "bytes": table["bytes"], "files": 0, "lines": 0, "rows": table["rows"]}

    measurements, findings = {}, []
    for eid, m in sums.items():
        e = m["entry"]
        row = {"bytes": m["bytes"], "files": m["files"], "lines": m["lines"], "rows": m.get("rows")}
        old = prev_m.get(eid)
        if old is not None and elapsed_days:
            row["growth_per_day"] = int((m["bytes"] - old["bytes"]) / elapsed_days)
        measurements[eid] = row
        budget = BUDGETS.get(e.get("name") or "") or {}
        if budget.get("max_bytes") is not None and m["bytes"] > budget["max_bytes"]:
            findings.append(_finding("exceeded_size", e, bytes=m["bytes"], limit=budget["max_bytes"],
                                     severity="red"))
        grow = row.get("growth_per_day")
        if budget.get("max_growth_per_day") is not None and grow is not None and grow > budget["max_growth_per_day"]:
            findings.append(_finding("exceeded_growth", e, growth_per_day=grow,
                                     limit=budget["max_growth_per_day"], severity="red"))
        if not budget and e.get("bound_status") == "unbounded":
            findings.append(_finding("unbounded", e, bytes=m["bytes"], severity="warning"))

    if orphans:
        by_dir: dict[str, dict] = {}
        for f in orphans:
            d = by_dir.setdefault(posixpath.dirname(f["path"]), {"count": 0, "bytes": 0})
            d["count"] += 1
            d["bytes"] += f["size"]
        for d, v in sorted(by_dir.items(), key=lambda kv: -kv[1]["bytes"]):
            findings.append({"finding": "orphan_temp_files", "directory": d, "count": v["count"],
                             "bytes": v["bytes"], "older_than_seconds": ORPHAN_TEMP_AGE_SECONDS,
                             "register_class": "RC3", "severity": "red"})
    unknown_by_dir: dict[str, dict] = {}
    for f in unmatched:
        d = unknown_by_dir.setdefault(posixpath.dirname(f["path"]), {"count": 0, "bytes": 0, "example": f["path"]})
        d["count"] += 1
        d["bytes"] += f["size"]
    for d, v in sorted(unknown_by_dir.items(), key=lambda kv: -kv[1]["bytes"])[:50]:
        findings.append({"finding": "unknown_state_file", "directory": d, "count": v["count"], "bytes": v["bytes"],
                         "example": v["example"], "older_than_seconds": UNKNOWN_AGE_SECONDS,
                         "register_class": "RC3", "severity": "warning"})

    red = [f for f in findings if f.get("severity") == "red"]
    return {
        "schema_version": SCHEMA_VERSION,
        "collected_at": now,
        "collected_at_iso": datetime.fromtimestamp(now, timezone.utc).isoformat() if now else None,
        "status": "red" if red else "green",
        "red_count": len(red),
        "findings": findings,
        "measurements": measurements,
        "coverage": {"manifest_entries": len(entries), "measured": len(measurements),
                     "files_seen": len(data["files"]), "tables_seen": len(data["tables"])},
        "headline": {k: data[k] for k in ("disk", "memory", "docker_df", "top_containers_by_memory")},
        "previous_collected_at": prev_t,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect")
    c.add_argument("--output", required=True, type=Path)
    c.add_argument("--transport", type=Path, default=REPO_ROOT / "scripts" / "dev_vm_ssh.sh")
    r = sub.add_parser("report")
    r.add_argument("--raw", required=True, type=Path)
    r.add_argument("--output", required=True, type=Path)
    r.add_argument("--manifest", type=Path, default=MANIFEST_PATH)
    r.add_argument("--previous", type=Path)
    args = ap.parse_args(argv)
    if args.cmd == "collect":
        collect(args.output, args.transport)
        return 0
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    previous = None
    if args.previous and args.previous.is_file():
        try:
            previous = json.loads(args.previous.read_text(encoding="utf-8"))
        except ValueError:
            previous = None
    report = build_report(args.raw.read_text(encoding="utf-8"), manifest, previous)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(f"hosted storage: {report['status']} ({report['red_count']} red findings, "
          f"{report['coverage']['measured']} entries measured)")
    return 1 if report["status"] == "red" else 0


if __name__ == "__main__":
    sys.exit(main())
