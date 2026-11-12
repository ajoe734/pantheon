"""Durable underlying-return attribution; no broker, capital or ledger writes."""
from __future__ import annotations

import copy
import json
import math
import os
import statistics
import uuid
from datetime import datetime
from pathlib import Path


def virtual_book_metrics(returns, *, periods_per_year=252):
    equity = peak = 1.0
    drawdown = 0.0
    for value in returns:
        if not math.isfinite(value):
            raise ValueError("virtual returns must be finite")
        equity *= 1 + value
        peak = max(peak, equity)
        drawdown = max(drawdown, (peak - equity) / peak)
    deviation = statistics.stdev(returns) if len(returns) > 1 else 0.0
    return {"return_series": list(returns), "total_return": equity - 1,
            "sharpe_ratio": statistics.mean(returns) / deviation * math.sqrt(periods_per_year)
            if deviation > 0 else None,
            "sharpe_undefined_reason": None if deviation > 0 else "insufficient_or_constant_returns",
            "max_drawdown": drawdown, "observation_count": len(returns),
            "basis": "underlying_gross_virtual", "is_real_order": False}


class MemberVirtualBooks:
    def __init__(self, path=None):
        self.path = Path(path) if path else None
        self.books = {}
        if self.path and self.path.exists():
            state = json.loads(self.path.read_text())
            if state.get("schema_version") != "member_virtual_books.v1":
                raise ValueError("unsupported virtual book schema")
            self.books = state["books"]
            self.snapshot()  # reject corrupt metrics before accepting new observations

    def observe(self, *, binding_id, envelope_checksum, symbol, event_time,
                price, source_ref, members, bar_frequency):
        if not source_ref or not event_time or not members:
            raise ValueError("virtual book requires source, time and pinned member views")
        at = datetime.fromisoformat(str(event_time).replace("Z", "+00:00"))
        if at.tzinfo is None:
            raise ValueError("virtual book mark requires timezone")
        if isinstance(price, bool) or not isinstance(price, (int, float)) or not math.isfinite(price) or price <= 0:
            raise ValueError("virtual book mark must be positive and finite")
        # Annualisation is only defined for the currently supported daily bars.
        if bar_frequency not in {"1d", "d", "daily", "1day"}:
            raise ValueError("virtual book annualisation requires daily bar cadence")
        candidate = copy.deepcopy(self.books)
        for member_id, member in members.items():
            key = json.dumps([binding_id, envelope_checksum, member_id, symbol])
            book = candidate.setdefault(key, {"binding_id": binding_id,
                "envelope_checksum": envelope_checksum, "member_id": member_id,
                "artifact_id": member["artifact_id"], "version": member["version"],
                "checksum": member["checksum"], "virtual_only": member["virtual_only"],
                "symbol": symbol, "underlying_count": member.get("underlying_count", 1),
                "observations": []})
            view = member["view"]
            strength = 1.0 if view["strength"] is None else view["strength"]
            if not isinstance(strength, (int, float)) or not math.isfinite(strength) or not 0 <= strength <= 1:
                raise ValueError("invalid member virtual strength")
            exposure = {"LONG": 1, "SHORT": -1, "FLAT": 0}[view["direction"]] * strength
            observation = {"event_time": event_time, "price": price,
                           "source_ref": source_ref, "exposure": exposure}
            rows = book["observations"]
            if rows:
                previous_at = datetime.fromisoformat(rows[-1]["event_time"].replace("Z", "+00:00"))
                if at <= previous_at:
                    prior = next((row for row in rows if row["event_time"] == event_time), None)
                    if prior is not None:
                        if prior != observation:
                            raise ValueError("conflicting virtual mark for the same bar")
                        continue
                    raise ValueError("out-of-order virtual mark")
            rows.append(observation)
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(self.path.name + "." + uuid.uuid4().hex + ".tmp")
            try:
                with temporary.open("w") as handle:
                    json.dump({"schema_version": "member_virtual_books.v1", "books": candidate},
                              handle, allow_nan=False)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, self.path)
                directory_fd = os.open(self.path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            finally:
                temporary.unlink(missing_ok=True)
        self.books = candidate
        return self.snapshot(binding_id=binding_id, envelope_checksum=envelope_checksum)

    def snapshot(self, *, binding_id=None, envelope_checksum=None):
        groups = {}
        for book in self.books.values():
            if binding_id and book["binding_id"] != binding_id:
                continue
            if envelope_checksum and book["envelope_checksum"] != envelope_checksum:
                continue
            rows = book["observations"]
            returns = {(previous["event_time"], row["event_time"]):
                       previous["exposure"] * (row["price"] / previous["price"] - 1)
                       for previous, row in zip(rows, rows[1:])}
            key = (book["binding_id"], book["envelope_checksum"], book["member_id"])
            groups.setdefault(key, []).append((book, returns))
        result = []
        for group in groups.values():
            book = group[0][0]
            count = book["underlying_count"]
            if isinstance(count, bool) or not isinstance(count, int) or count < 1:
                raise ValueError("invalid virtual book underlying count")
            all_underlyings = len(group) == count
            complete = all_underlyings and len({b["observations"][-1]["event_time"] for b, _ in group}) == 1
            # Only common, observed return intervals enter member metrics. A
            # missing underlying mark never becomes a fabricated zero return.
            dates = set.intersection(*(set(returns) for _, returns in group)) if all_underlyings else set()
            dates = sorted(dates)
            returns = [math.fsum(series[day] for _, series in group) / count for day in dates]
            underlyings = [{"symbol": subbook["symbol"], **virtual_book_metrics(list(series.values())),
                            "as_of": subbook["observations"][-1]["event_time"]}
                           for subbook, series in group]
            result.append({**{k: v for k, v in book.items()
                              if k not in {"observations", "symbol", "underlying_count"}},
                           **virtual_book_metrics(returns), "underlyings": underlyings,
                           "aggregation": "equal_notional_per_underlying",
                           "marks_complete": complete,
                           "as_of": dates[-1][1] if dates else None})
        return result
