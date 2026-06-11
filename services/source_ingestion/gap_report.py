"""Market-data gap report generation."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from .active_universe import DEFAULT_SOURCE_UPDATE_RULES, build_active_universe_job_fanout
from .source_health import SourceHealth, SourceHealthStore


GAP_CLASSES = ("credential", "quota", "provider_stale", "schema", "parse", "not-in-universe")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def classify_gap(*, health: SourceHealth | None = None, reason: str | None = None) -> str:
    text = " ".join(
        str(value or "")
        for value in (
            reason,
            health.status if health else "",
            health.metadata.get("source_error") if health else "",
            health.metadata.get("last_error") if health else "",
        )
    ).lower()
    if "not-in-universe" in text or "not in universe" in text:
        return "not-in-universe"
    if "credential" in text or "secret" in text or "api key" in text or "token" in text:
        return "credential"
    if "quota" in text or "rate limit" in text or "429" in text or "throttle" in text:
        return "quota"
    if "schema" in text:
        return "schema"
    if "parse" in text or "decode" in text or "invalid json" in text:
        return "parse"
    return "provider_stale"


def generate_market_data_gap_report(
    *,
    members: Sequence[Mapping[str, Any]],
    rules: Sequence[Mapping[str, Any]] | None = None,
    health_records: Sequence[SourceHealth] = (),
    run_date: str,
    default_max_symbols_per_job: int = 50,
    generated_at: str | None = None,
) -> dict[str, Any]:
    fanout = build_active_universe_job_fanout(
        members,
        rules=rules or DEFAULT_SOURCE_UPDATE_RULES,
        run_date=run_date,
        default_max_symbols_per_job=default_max_symbols_per_job,
    )
    health_by_source = {record.source_id: record for record in health_records}
    gaps: list[dict[str, Any]] = []

    for skipped in fanout["skipped"]:
        gaps.append(
            {
                "connector_id": skipped["connector_id"],
                "dataset": skipped["dataset"],
                "symbol": skipped["symbol"],
                "date": run_date,
                "gap_class": "not-in-universe",
                "reason": skipped["reason"],
            }
        )

    for job in fanout["jobs"]:
        health = health_by_source.get(job["connector_id"])
        if health is None:
            gaps.append(
                {
                    "connector_id": job["connector_id"],
                    "dataset": job["dataset"],
                    "symbols": list(job["symbols"]),
                    "date": run_date,
                    "gap_class": "provider_stale",
                    "reason": "source health missing",
                }
            )
            continue
        if health.status != "ok":
            gaps.append(
                {
                    "connector_id": job["connector_id"],
                    "dataset": job["dataset"],
                    "symbols": list(job["symbols"]),
                    "date": run_date,
                    "gap_class": classify_gap(health=health),
                    "reason": str(health.metadata.get("source_error") or health.status),
                    "latest_watermark": health.latest_watermark,
                }
            )
            continue
        if health.latest_watermark and str(health.latest_watermark)[:10] < run_date:
            gaps.append(
                {
                    "connector_id": job["connector_id"],
                    "dataset": job["dataset"],
                    "symbols": list(job["symbols"]),
                    "date": run_date,
                    "gap_class": "provider_stale",
                    "reason": f"latest_watermark={health.latest_watermark}",
                    "latest_watermark": health.latest_watermark,
                }
            )

    summary = {gap_class: 0 for gap_class in GAP_CLASSES}
    for gap in gaps:
        summary[gap["gap_class"]] = summary.get(gap["gap_class"], 0) + 1

    return {
        "schema_version": "market_data_gap_report.v1",
        "generated_at": generated_at or utc_now_iso(),
        "run_date": run_date,
        "fanout_summary": fanout["summary"],
        "gap_summary": summary,
        "gaps": gaps,
    }


def render_gap_report_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        f"# Market Data Gap Report {report['run_date']}",
        "",
        f"Generated: {report['generated_at']}",
        "",
        "## Summary",
        "",
    ]
    for key, value in sorted(dict(report["gap_summary"]).items()):
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## Gaps", ""])
    if not report["gaps"]:
        lines.append("No gaps detected.")
        return "\n".join(lines) + "\n"
    lines.append("| Connector | Dataset | Date | Class | Symbols | Reason |")
    lines.append("|---|---|---|---|---|---|")
    for gap in report["gaps"]:
        symbols = gap.get("symbol") or ",".join(str(item) for item in gap.get("symbols") or [])
        lines.append(
            "| {connector_id} | {dataset} | {date} | {gap_class} | {symbols} | {reason} |".format(
                connector_id=gap.get("connector_id", ""),
                dataset=gap.get("dataset", ""),
                date=gap.get("date", ""),
                gap_class=gap.get("gap_class", ""),
                symbols=symbols,
                reason=str(gap.get("reason", "")).replace("|", "/"),
            )
        )
    return "\n".join(lines) + "\n"


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate a market-data gap report.")
    parser.add_argument("--members-json", required=True, help="Path to JSON array of active-universe members.")
    parser.add_argument("--health-store", required=True, help="Path to source_health JSONL store.")
    parser.add_argument("--run-date", required=True, help="Report date, YYYY-MM-DD.")
    parser.add_argument("--output", required=True, help="Markdown output path.")
    parser.add_argument("--json-output", help="Optional JSON report output path.")
    args = parser.parse_args(argv)

    members = _load_json(Path(args.members_json))
    if not isinstance(members, list):
        raise SystemExit("--members-json must contain a JSON array")
    health = SourceHealthStore.from_jsonl(Path(args.health_store)).list()
    report = generate_market_data_gap_report(members=members, health_records=health, run_date=args.run_date)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_gap_report_markdown(report), encoding="utf-8")
    if args.json_output:
        json_path = Path(args.json_output)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
