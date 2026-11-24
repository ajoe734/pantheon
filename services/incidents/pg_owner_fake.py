"""Row-backed stand-in for PostgresJsonOwnerStore used by incidents tests.

It understands only the code-defined predicates ``PostgresIncidentStore``
emits (``payload ->> 'k' = %s``, the open-status ``IN`` list, ``LIKE`` on a
column/payload text, ``ORDER BY`` and ``LIMIT``) and records every row it
hands back, so tests can prove memory bounds from rows materialised rather
than from wall-clock time.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

_OPEN_IN = "payload ->> 'status' IN ('open', 'investigating')"
_EQ = re.compile(r"^payload ->> '(\w+)' = %s$")
_LIKE_FIELD = re.compile(r"^payload ->> '(\w+)' LIKE %s$")


def _like_regex(pattern: str) -> "re.Pattern[str]":
    out, i = [], 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "\\" and i + 1 < len(pattern):
            out.append(re.escape(pattern[i + 1]))
            i += 2
            continue
        out.append(".*" if ch == "%" else "." if ch == "_" else re.escape(ch))
        i += 1
    return re.compile("^" + "".join(out) + "$", re.S)


class FakeOwnerTable:
    """Instances share one table registry keyed by table name."""

    tables: Dict[str, List[Tuple[str, Dict[str, Any]]]] = {}
    stats: Dict[str, int] = {}

    def __init__(self, *, dsn: str, table: str, owner_service: str, bootstrap: bool = True) -> None:
        self.dsn = dsn
        self.table = table
        self.owner_service = owner_service
        self.tables.setdefault(table, [])

    @classmethod
    def reset(cls, rows_by_table: Dict[str, List[Dict[str, Any]]], id_key: Dict[str, str]) -> None:
        cls.tables = {
            t: [(r[id_key[t]], dict(r)) for r in rows] for t, rows in rows_by_table.items()
        }
        cls.stats = {"select_calls": 0, "rows_returned": 0, "list_all_calls": 0, "max_rows_in_call": 0}

    # -- API used by the store ------------------------------------------
    def list_all(self) -> List[Dict[str, Any]]:
        self.stats["list_all_calls"] += 1
        rows = [dict(p) for _, p in self.tables[self.table]]
        self._note(len(rows))
        return rows

    def get(self, record_id: str) -> Optional[Dict[str, Any]]:
        for rid, payload in self.tables[self.table]:
            if rid == record_id:
                self._note(1)
                return dict(payload)
        return None

    def count_where(self, where_sql: str = "TRUE", params: tuple = ()) -> int:
        return len(self._filter(where_sql, params))

    def select_where(self, where_sql: str, params: tuple = (), *, tail_sql: str = "") -> List[Dict[str, Any]]:
        rows = self._filter(where_sql, params)
        tail = " ".join(tail_sql.split())
        limit_match = re.search(r"LIMIT (\d+)$", tail)
        if "ORDER BY payload ->> 'created_at' DESC" in tail:
            rows.sort(key=lambda r: (str(r[1].get("created_at")), r[0]), reverse=True)
        elif "ORDER BY updated_at DESC" in tail:
            rows.reverse()
        if limit_match:
            rows = rows[: int(limit_match.group(1))]
        self.stats["select_calls"] += 1
        self._note(len(rows))
        return [dict(p) for _, p in rows]

    # -- internals --------------------------------------------------------
    def _note(self, n: int) -> None:
        self.stats["rows_returned"] += n
        self.stats["max_rows_in_call"] = max(self.stats["max_rows_in_call"], n)

    def _filter(self, where_sql: str, params: tuple) -> List[Tuple[str, Dict[str, Any]]]:
        rest = list(params)
        preds = []
        for clause in [c.strip() for c in where_sql.split(" AND ")]:
            if clause == "TRUE":
                continue
            if clause == _OPEN_IN:
                preds.append(lambda rid, p: p.get("status") in ("open", "investigating"))
            elif _EQ.match(clause):
                key, val = _EQ.match(clause).group(1), rest.pop(0)
                preds.append(lambda rid, p, key=key, val=val: p.get(key) == val)
            elif _LIKE_FIELD.match(clause):
                key, rx = _LIKE_FIELD.match(clause).group(1), _like_regex(rest.pop(0))
                preds.append(lambda rid, p, key=key, rx=rx: bool(rx.match(str(p.get(key) or ""))))
            elif clause == "record_id LIKE %s":
                rx = _like_regex(rest.pop(0))
                preds.append(lambda rid, p, rx=rx: bool(rx.match(rid)))
            elif clause == "payload::text LIKE %s":
                rx = _like_regex(rest.pop(0))
                preds.append(lambda rid, p, rx=rx: bool(rx.match(json.dumps(p, sort_keys=True))))
            else:  # pragma: no cover - guards against silently ignoring a new predicate
                raise AssertionError(f"fake owner table does not understand: {clause!r}")
        return [(rid, p) for rid, p in self.tables[self.table] if all(f(rid, p) for f in preds)]
