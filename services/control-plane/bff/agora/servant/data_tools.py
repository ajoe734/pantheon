"""Servant read-only data tools that return cited facts (AGORA-SERVANT-DATA-TOOLS-20261127).

SD_AGORA_SERVANT_CORE_2026-11-27 §4.3. The servant turn may ask for these tools; the BFF code
here resolves every call (the model never reaches an owner). Each tool returns **cited facts**::

    {id, tool, label, value, unit, source_id, connector_id | owner, as_of, freshness, query_hash}

``freshness`` is an AD-AG-07 state. Every number a trader sees comes from a fact; the language
model only writes prose around fact references (11/16 rule, enforced in ``turn_worker``).

The tool set is fixed and read-only: no tool writes an owner record, places an order or calls a
persona. ``propose_research`` returns an unsaved draft the trader may approve elsewhere.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence

logger = logging.getLogger("agora.servant.data_tools")

# AD-AG-07 freshness states (SA_AGORA_PRODUCT_CLOSURE_2026-08-27 §AD-AG-07).
FRESH = "fresh"
STALE = "stale"
EMPTY_FRESH = "empty_fresh"
UNAVAILABLE = "unavailable"
DEGRADED = "degraded"
NOT_CONFIGURED = "not_configured"
FRESHNESS_STATES = (FRESH, STALE, EMPTY_FRESH, UNAVAILABLE, DEGRADED, NOT_CONFIGURED)

# Turn and payload bounds (SD §4.3 proposals).
MAX_TOOL_ROUNDS = 4
MAX_CALLS_PER_ROUND = 4
MAX_FACTS_PER_TOOL = 40
MAX_FACTS_PER_ANSWER = 60
BROKER_TOOL_MAX_SYMBOLS = 5
BROKER_TOOL_TOP_PER_SIDE = 5
SOURCE_RECORD_PAGE_SIZE = 200
SOURCE_RECORD_MAX_PAGES = 3
TEXT_FACT_MAX_CHARS = 160

BROKER_CONNECTOR_ID = "tw-finmind-broker-daily-report"
MOPS_CONNECTOR_ID = "tw-mops-official-disclosures"
NEWS_CONNECTOR_ID = "collector-news-rss"
# SLA per source: broker reports are daily (weekend + holiday slack); MOPS polls every 10-30 min;
# collector news is hourly.
BROKER_SLA = timedelta(days=4)
MOPS_SLA = timedelta(days=1)
NEWS_SLA = timedelta(hours=6)
MARKET_BARS_SLA = timedelta(days=4)

QUICK_TASK_ANALYZE_BROKER_BRANCHES = "analyze_broker_branches"
# V6 §2.3 command-bar label -> quick task id.
QUICK_TASK_ALIASES = {"分析分點": QUICK_TASK_ANALYZE_BROKER_BRANCHES}

TOOL_CATALOG: List[Dict[str, Any]] = [
    {"name": "market_bars", "description": "Latest stored daily closes of one Taiwan symbol.",
     "arguments": {"symbol": "string, required"}},
    {"name": "candidate_pool", "description": "The trader's candidate pool size and lifecycle stage counts.",
     "arguments": {"strategy_id": "string, optional"}},
    {"name": "candidate_ranking", "description": "Top ranked candidates of the trader's pool.",
     "arguments": {"strategy_id": "string, optional", "limit": "integer 1-10"}},
    {"name": "broker_branch_flow", "description": "Broker-branch (分點) top buy/sell rows of stored sessions.",
     "arguments": {"symbols": "array of up to 5 symbols", "date_from": "YYYY-MM-DD, optional",
                   "date_to": "YYYY-MM-DD, optional", "side": "buy|sell|both"}},
    {"name": "disclosures", "description": "MOPS official disclosures of one symbol.",
     "arguments": {"symbol": "string, required", "since": "YYYY-MM-DD, optional"}},
    {"name": "news", "description": "Collector finance news matching a symbol or keyword.",
     "arguments": {"symbol": "string, optional", "keyword": "string, optional", "since": "YYYY-MM-DD, optional"}},
    {"name": "my_decisions", "description": "The trader's own locked decisions.",
     "arguments": {"limit": "integer 1-10"}},
    {"name": "my_journal", "description": "The trader's own decision journal entries.",
     "arguments": {"limit": "integer 1-10"}},
    {"name": "my_shadow", "description": "Shadow arms recorded on the trader's own decision locks.",
     "arguments": {"limit": "integer 1-10"}},
    {"name": "institutional_facts", "description": "Validated institutional facts (attribution, research, lessons).",
     "arguments": {"symbol": "string, optional", "strategy_id": "string, optional"}},
    {"name": "propose_research", "description": "Draft (not save) a research plan for a question.",
     "arguments": {"question": "string, required"}},
]
TOOL_NAMES = tuple(t["name"] for t in TOOL_CATALOG)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_time(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        if len(text) == 10:
            return datetime.combine(date.fromisoformat(text), datetime.min.time(), tzinfo=timezone(timedelta(hours=8)))
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def freshness_for(as_of: Any, sla: timedelta, now: datetime) -> str:
    """``fresh`` when the source timestamp is within its SLA, ``stale`` past it."""
    parsed = _parse_time(as_of)
    if parsed is None:
        return DEGRADED
    return FRESH if now - parsed <= sla else STALE


def query_hash(tool: str, arguments: Mapping[str, Any], scope: Sequence[str]) -> str:
    raw = json.dumps({"tool": tool, "arguments": arguments, "scope": list(scope)}, sort_keys=True, default=str)
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _symbol(value: Any) -> str:
    return str(value or "").strip().upper().split(".", 1)[0]


def _clip(text: Any, limit: int = TEXT_FACT_MAX_CHARS) -> str:
    value = " ".join(str(text or "").split())
    return value if len(value) <= limit else value[: limit - 1] + "…"


def _bounded_int(value: Any, default: int, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


@dataclass
class ToolContext:
    """Owner scope of one servant turn (AD-AG-08: the trader reads only their own records)."""

    tenant_id: str
    user_id: str
    operator_id: str = ""
    servant_persona_id: str = ""
    command_context: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolResult:
    tool: str
    arguments: Dict[str, Any]
    freshness: str
    query_hash: str
    facts: List[Dict[str, Any]] = field(default_factory=list)
    missing: Optional[str] = None
    note: Optional[str] = None

    def summary(self) -> Dict[str, Any]:
        """Compact trace stored with the answer (no fact bodies)."""
        return {
            "tool": self.tool,
            "arguments": self.arguments,
            "freshness": self.freshness,
            "query_hash": self.query_hash,
            "fact_count": len(self.facts),
            "missing": self.missing,
        }


class SourceIngestToolClient:
    """Bounded GET reads of source-ingest owner routes for the servant tools.

    Runs in ``agora-interaction-worker`` (no caller request), so it authenticates with a short
    service JWT carrying only the ``source_ingest_reader`` role and the turn's tenant.
    """

    URL_ENVS = (
        "PANTHEON_SOURCE_INGEST_API_URL",
        "PANTHEON_SOURCE_INGEST_URL",
        "SOURCE_INGEST_SERVICE_URL",
        "SOURCE_INGEST_URL",
        "SOURCE_MANAGEMENT_API_URL",
    )
    TOKEN_ENVS = ("PANTHEON_AGORA_SERVANT_SOURCE_INGEST_TOKEN",)
    SECRET_ENVS = ("PANTHEON_RUNTIME_JWT_SECRET",)

    def __init__(
        self,
        *,
        base_url: Optional[str] = None,
        token: Optional[str] = None,
        jwt_secret: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
        urlopen: Optional[Callable[..., Any]] = None,
    ) -> None:
        env_url = next((os.getenv(n, "").strip() for n in self.URL_ENVS if os.getenv(n, "").strip()), "")
        self.base_url = (base_url if base_url is not None else env_url).rstrip("/")
        self._token = token if token is not None else next(
            (os.getenv(n, "").strip() for n in self.TOKEN_ENVS if os.getenv(n, "").strip()), ""
        )
        self._secret = jwt_secret if jwt_secret is not None else next(
            (os.getenv(n, "").strip() for n in self.SECRET_ENVS if os.getenv(n, "").strip()), ""
        )
        self.timeout_seconds = float(
            timeout_seconds if timeout_seconds is not None
            else os.getenv("PANTHEON_AGORA_SERVANT_TOOL_TIMEOUT_SECONDS", "5")
        )
        self._urlopen = urlopen or urllib.request.urlopen

    @property
    def configured(self) -> bool:
        return bool(self.base_url and (self._token or self._secret))

    def _bearer(self, tenant_id: str) -> str:
        if self._token:
            return self._token
        from services.runtime_auth_inbound import encode_jwt_hs256

        now = int(time.time())
        return encode_jwt_hs256(
            {
                "sub": "agora-servant-tools",
                "roles": ["source_ingest_reader"],
                "tenant_id": tenant_id,
                "iat": now,
                "exp": now + 120,
            },
            secret=self._secret,
        )

    def get(self, path: str, params: Mapping[str, Any], *, tenant_id: str) -> Dict[str, Any]:
        query = urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")})
        request = urllib.request.Request(
            f"{self.base_url}{path}?{query}",
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self._bearer(tenant_id)}",
                "X-Tenant-Id": tenant_id,
            },
            method="GET",
        )
        with self._urlopen(request, timeout=self.timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8") or "{}")


class ServantDataTools:
    """The fixed read-only tool set of one servant (SD §4.3)."""

    def __init__(
        self,
        *,
        source_ingest: Optional[SourceIngestToolClient] = None,
        agora_owner: Any = None,
        research_store: Any = None,
        journal_reader: Any = None,
        institutional_reader: Any = None,
        research_drafter: Optional[Callable[[str, ToolContext], Mapping[str, Any]]] = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self.source_ingest = source_ingest if source_ingest is not None else SourceIngestToolClient()
        self.agora_owner = agora_owner
        self.research_store = research_store
        self.journal_reader = journal_reader
        self.institutional_reader = institutional_reader
        self.research_drafter = research_drafter
        self.clock = clock
        self._handlers: Dict[str, Callable[[Dict[str, Any], ToolContext], ToolResult]] = {
            "market_bars": self._market_bars,
            "candidate_pool": self._candidate_pool,
            "candidate_ranking": self._candidate_ranking,
            "broker_branch_flow": self._broker_branch_flow,
            "disclosures": self._disclosures,
            "news": self._news,
            "my_decisions": self._my_decisions,
            "my_journal": self._my_journal,
            "my_shadow": self._my_shadow,
            "institutional_facts": self._institutional_facts,
            "propose_research": self._propose_research,
        }
        assert tuple(self._handlers) == TOOL_NAMES

    @staticmethod
    def catalog() -> List[Dict[str, Any]]:
        return [dict(t) for t in TOOL_CATALOG]

    # -- dispatch ------------------------------------------------------------------

    def run(self, tool: str, arguments: Optional[Mapping[str, Any]], ctx: ToolContext) -> ToolResult:
        args = {k: v for k, v in dict(arguments or {}).items() if v not in (None, "", [])}
        qh = query_hash(tool, args, (ctx.tenant_id, ctx.user_id))
        handler = self._handlers.get(tool)
        if handler is None:
            return ToolResult(tool, args, NOT_CONFIGURED, qh, missing=f"no tool named {tool!r} exists")
        try:
            result = handler(args, ctx)
        except urllib.error.HTTPError as exc:
            logger.warning("servant tool %s owner HTTP %s", tool, exc.code)
            return ToolResult(tool, args, UNAVAILABLE, qh, missing=f"{tool}: owner returned HTTP {exc.code}")
        except Exception as exc:  # an owner outage is reported, never answered from memory
            logger.warning("servant tool %s unavailable: %s", tool, type(exc).__name__)
            return ToolResult(tool, args, UNAVAILABLE, qh, missing=f"{tool}: owner unavailable ({type(exc).__name__})")
        result.query_hash = qh
        result.facts = result.facts[:MAX_FACTS_PER_TOOL]
        for fact in result.facts:
            fact["query_hash"] = qh
            fact["tool"] = tool
        return result

    # -- helpers -------------------------------------------------------------------

    def _fact(
        self,
        *,
        label: str,
        value: Any,
        unit: str,
        as_of: Any,
        freshness: str,
        source_id: Optional[str] = None,
        connector_id: Optional[str] = None,
        owner: Optional[str] = None,
        symbol: Optional[str] = None,
    ) -> Dict[str, Any]:
        fact: Dict[str, Any] = {
            "label": label,
            "value": value,
            "unit": unit,
            "source_id": source_id,
            "as_of": str(as_of) if as_of not in (None, "") else None,
            "freshness": freshness,
        }
        if connector_id:
            fact["connector_id"] = connector_id
        if owner:
            fact["owner"] = owner
        if symbol:
            fact["symbol"] = symbol
        return fact

    def _require_source_ingest(self, tool: str, args: Dict[str, Any]) -> Optional[ToolResult]:
        if not self.source_ingest.configured:
            return ToolResult(tool, args, NOT_CONFIGURED, "", missing=f"{tool}: source-ingest read is not configured")
        return None

    def _source_record_page_scan(
        self, connector_id: str, ctx: ToolContext, keep: Callable[[Mapping[str, Any]], bool], since: Optional[str]
    ) -> tuple[List[Mapping[str, Any]], Optional[str], int]:
        """Newest-first bounded scan of one connector's records: at most 3 pages of 200."""
        kept: List[Mapping[str, Any]] = []
        newest: Optional[str] = None
        token: Optional[str] = None
        seen = 0
        for _ in range(SOURCE_RECORD_MAX_PAGES):
            page = self.source_ingest.get(
                "/api/source-ingest/source-records",
                {"connector_id": connector_id, "page_size": SOURCE_RECORD_PAGE_SIZE, "page_token": token},
                tenant_id=ctx.tenant_id,
            )
            records = [r for r in page.get("source_records") or [] if isinstance(r, Mapping)]
            seen += len(records)
            stop = False
            for record in records:
                when = _record_time(record)
                if newest is None and when:
                    newest = when
                if since and when and when[:10] < since:
                    stop = True
                    break
                if keep(record):
                    kept.append(record)
            token = page.get("next_page_token")
            if stop or not token or len(kept) >= MAX_FACTS_PER_TOOL:
                break
        return kept, newest, seen

    # -- tools ---------------------------------------------------------------------

    def _market_bars(self, args: Dict[str, Any], ctx: ToolContext) -> ToolResult:
        symbol = _symbol(args.get("symbol"))
        args = {"symbol": symbol}
        if not symbol:
            return ToolResult("market_bars", args, NOT_CONFIGURED, "", missing="market_bars needs a symbol")
        blocked = self._require_source_ingest("market_bars", args)
        if blocked:
            return blocked
        try:
            snap = self.source_ingest.get("/api/source-ingest/snapshots/latest", {"symbol": symbol}, tenant_id=ctx.tenant_id)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return ToolResult("market_bars", args, NOT_CONFIGURED, "", missing=f"no stored daily snapshot for {symbol}")
            raise
        closes = [c for c in snap.get("closes") or [] if isinstance(c, Mapping)]
        as_of = snap.get("event_time") or (closes[-1].get("event_time") if closes else None)
        fresh = freshness_for(as_of, MARKET_BARS_SLA, self.clock())
        facts = []
        for point in closes[-5:]:
            close = point.get("close")
            if close is None:
                continue
            facts.append(self._fact(
                label=f"{symbol} 收盤價 {str(point.get('event_time') or '')[:10]}", value=close, unit="TWD",
                as_of=point.get("event_time"), freshness=fresh, source_id=snap.get("snapshot_id"),
                owner="source-ingest.snapshots", symbol=symbol,
            ))
        return ToolResult("market_bars", args, fresh if facts else EMPTY_FRESH, "", facts)

    def _candidate_pools(self, ctx: ToolContext, strategy_id: Optional[str]) -> List[Mapping[str, Any]]:
        if self.research_store is None:
            return []
        return list(self.research_store.list_candidate_pools(
            user_id=ctx.user_id, tenant_id=ctx.tenant_id, strategy_id=strategy_id
        ) or [])

    def _candidate_pool(self, args: Dict[str, Any], ctx: ToolContext) -> ToolResult:
        strategy_id = args.get("strategy_id") or ctx.command_context.get("strategy_id")
        args = {"strategy_id": strategy_id} if strategy_id else {}
        if self.research_store is None:
            return ToolResult("candidate_pool", args, NOT_CONFIGURED, "", missing="candidate pool owner is not configured")
        pools = self._candidate_pools(ctx, strategy_id)
        if not pools:
            return ToolResult("candidate_pool", args, EMPTY_FRESH, "", missing="the trader has no candidate pool yet")
        pool = pools[0]
        members = [c for c in pool.get("candidates") or [] if isinstance(c, Mapping)]
        pool_id = str(pool.get("pool_id") or pool.get("id") or "")
        as_of = pool.get("snapshot_at")
        counts: Dict[str, int] = {}
        for member in members:
            stage = str(member.get("lifecycle_state") or "discovered")
            counts[stage] = counts.get(stage, 0) + 1
        common = dict(as_of=as_of, freshness=FRESH, source_id=pool_id, owner="agora.research.candidate_pools")
        facts = [self._fact(label="候選池檔數", value=len(members), unit="symbols", **common)]
        facts += [self._fact(label=f"候選池 {stage} 階段檔數", value=n, unit="symbols", **common) for stage, n in sorted(counts.items())]
        return ToolResult("candidate_pool", args, FRESH, "", facts)

    def _candidate_ranking(self, args: Dict[str, Any], ctx: ToolContext) -> ToolResult:
        strategy_id = args.get("strategy_id") or ctx.command_context.get("strategy_id")
        limit = _bounded_int(args.get("limit"), 5, 1, 10)
        out_args = {"limit": limit, **({"strategy_id": strategy_id} if strategy_id else {})}
        if self.research_store is None:
            return ToolResult("candidate_ranking", out_args, NOT_CONFIGURED, "", missing="candidate pool owner is not configured")
        pools = self._candidate_pools(ctx, strategy_id)
        if not pools:
            return ToolResult("candidate_ranking", out_args, EMPTY_FRESH, "", missing="the trader has no candidate pool yet")
        pool = pools[0]
        members = [c for c in pool.get("candidates") or [] if isinstance(c, Mapping)]

        def score(member: Mapping[str, Any]) -> float:
            for key in ("score", "rank_score", "composite_score"):
                try:
                    return float(member[key])
                except (KeyError, TypeError, ValueError):
                    continue
            return float("-inf")

        ranked = sorted(members, key=score, reverse=True)[:limit]
        facts = []
        for position, member in enumerate(ranked, start=1):
            symbol = _symbol(member.get("symbol") or member.get("instrument_id"))
            value = score(member)
            facts.append(self._fact(
                label=f"候選排名第 {position} 名 {symbol} 分數", value=None if value == float("-inf") else value,
                unit="score", as_of=pool.get("snapshot_at"), freshness=FRESH,
                source_id=str(pool.get("pool_id") or pool.get("id") or ""), owner="agora.research.candidate_pools",
                symbol=symbol,
            ))
        return ToolResult("candidate_ranking", out_args, FRESH if facts else EMPTY_FRESH, "", facts)

    def _broker_branch_flow(self, args: Dict[str, Any], ctx: ToolContext) -> ToolResult:
        raw_symbols = args.get("symbols") or ([args["symbol"]] if args.get("symbol") else [])
        if isinstance(raw_symbols, str):
            raw_symbols = raw_symbols.split(",")
        symbols = list(dict.fromkeys(_symbol(s) for s in raw_symbols if _symbol(s)))
        dropped = symbols[BROKER_TOOL_MAX_SYMBOLS:]
        symbols = symbols[:BROKER_TOOL_MAX_SYMBOLS]
        side = str(args.get("side") or "both").lower()
        side = side if side in {"buy", "sell", "both"} else "both"
        out_args = {"symbols": symbols, "side": side}
        for key in ("date_from", "date_to"):
            if args.get(key):
                out_args[key] = str(args[key])[:10]
        if not symbols:
            return ToolResult("broker_branch_flow", out_args, NOT_CONFIGURED, "", missing="broker_branch_flow needs at least one symbol")
        blocked = self._require_source_ingest("broker_branch_flow", out_args)
        if blocked:
            return blocked
        body = self.source_ingest.get(
            "/api/source-ingest/broker-branch-flow",
            {"symbols": ",".join(symbols), "side": side, "top": BROKER_TOOL_TOP_PER_SIDE,
             "date_from": out_args.get("date_from"), "date_to": out_args.get("date_to")},
            tenant_id=ctx.tenant_id,
        )
        latest = body.get("latest_stored_session")
        connector = str(body.get("connector_id") or BROKER_CONNECTOR_ID)
        if not latest:
            return ToolResult("broker_branch_flow", out_args, NOT_CONFIGURED, "",
                              missing="no broker-branch session is stored yet")
        fresh = freshness_for(latest, BROKER_SLA, self.clock())
        facts = []
        for row in body.get("rows") or []:
            side_label = "買超" if row.get("side") == "buy" else "賣超"
            facts.append(self._fact(
                label=f"{row.get('symbol')} {row.get('date')} {side_label}第 {row.get('rank')} 名 {row.get('broker')} 淨買賣",
                value=row.get("net_qty"), unit=str(row.get("unit") or "shares"), as_of=row.get("date"),
                freshness=fresh, source_id=row.get("source_id"), connector_id=connector, symbol=row.get("symbol"),
            ))
        missing_parts = []
        if body.get("symbols_without_rows"):
            missing_parts.append(f"no broker-branch rows for {', '.join(body['symbols_without_rows'])} in session(s) {', '.join(body.get('sessions') or [latest])}")
        if dropped:
            missing_parts.append(f"only the first {BROKER_TOOL_MAX_SYMBOLS} symbols were read; skipped {', '.join(dropped)}")
        state = fresh if facts else (EMPTY_FRESH if fresh == FRESH else fresh)
        return ToolResult("broker_branch_flow", out_args, state, "", facts,
                          missing="; ".join(missing_parts) or None,
                          note=f"sessions={','.join(body.get('sessions') or [])}")

    def _record_facts(
        self, tool: str, records: Iterable[Mapping[str, Any]], connector: str, freshness: str, unit: str
    ) -> List[Dict[str, Any]]:
        facts = []
        for record in records:
            meta = record.get("metadata") if isinstance(record.get("metadata"), Mapping) else {}
            facts.append(self._fact(
                label=f"{'公告' if tool == 'disclosures' else '新聞'}標題", value=_clip(record.get("title")),
                unit="text", as_of=_record_time(record), freshness=freshness,
                source_id=str(record.get("source_id") or ""), connector_id=connector,
                symbol=_symbol(meta.get("symbol") or meta.get("stock_id")) or None,
            ))
        return facts

    def _disclosures(self, args: Dict[str, Any], ctx: ToolContext) -> ToolResult:
        symbol = _symbol(args.get("symbol"))
        out_args = {"symbol": symbol, **({"since": str(args["since"])[:10]} if args.get("since") else {})}
        if not symbol:
            return ToolResult("disclosures", out_args, NOT_CONFIGURED, "", missing="disclosures needs a symbol")
        blocked = self._require_source_ingest("disclosures", out_args)
        if blocked:
            return blocked
        kept, newest, seen = self._source_record_page_scan(
            MOPS_CONNECTOR_ID, ctx, lambda r: _record_mentions(r, symbol), out_args.get("since")
        )
        if not seen:
            return ToolResult("disclosures", out_args, NOT_CONFIGURED, "", missing="no MOPS disclosure record is stored")
        fresh = freshness_for(newest, MOPS_SLA, self.clock())
        facts = self._record_facts("disclosures", kept, MOPS_CONNECTOR_ID, fresh, "text")
        state = fresh if facts else (EMPTY_FRESH if fresh == FRESH else fresh)
        return ToolResult("disclosures", out_args, state, "", facts,
                          note=f"connector newest record {newest}; scanned {seen}")

    def _news(self, args: Dict[str, Any], ctx: ToolContext) -> ToolResult:
        symbol = _symbol(args.get("symbol"))
        keyword = str(args.get("keyword") or "").strip()
        out_args = {k: v for k, v in {"symbol": symbol, "keyword": keyword,
                                       "since": str(args.get("since") or "")[:10]}.items() if v}
        if not (symbol or keyword):
            return ToolResult("news", out_args, NOT_CONFIGURED, "", missing="news needs a symbol or keyword")
        blocked = self._require_source_ingest("news", out_args)
        if blocked:
            return blocked
        terms = [t for t in (symbol, keyword) if t]
        kept, newest, seen = self._source_record_page_scan(
            NEWS_CONNECTOR_ID, ctx, lambda r: any(_record_mentions(r, t) for t in terms), out_args.get("since")
        )
        if not seen:
            return ToolResult("news", out_args, NOT_CONFIGURED, "", missing="no collector news record is stored")
        fresh = freshness_for(newest, NEWS_SLA, self.clock())
        facts = self._record_facts("news", kept, NEWS_CONNECTOR_ID, fresh, "text")
        state = fresh if facts else (EMPTY_FRESH if fresh == FRESH else fresh)
        return ToolResult("news", out_args, state, "", facts, note=f"connector newest record {newest}; scanned {seen}")

    def _own_locks(self, ctx: ToolContext) -> List[Mapping[str, Any]]:
        locks = self.agora_owner.list_decision_locks(
            tenant_id=ctx.tenant_id, user_id=ctx.user_id, actor_roles=("trader",)
        )
        return sorted(
            (lock for lock in locks or [] if str(lock.get("user_id")) == str(ctx.user_id)),
            key=lambda lock: str(lock.get("decision_time") or lock.get("created_at") or ""),
            reverse=True,
        )

    def _my_decisions(self, args: Dict[str, Any], ctx: ToolContext) -> ToolResult:
        limit = _bounded_int(args.get("limit"), 5, 1, 10)
        out_args = {"limit": limit}
        if self.agora_owner is None:
            return ToolResult("my_decisions", out_args, NOT_CONFIGURED, "", missing="decision lock owner is not configured")
        locks = self._own_locks(ctx)[:limit]
        facts = []
        for lock in locks:
            subject = lock.get("subject") if isinstance(lock.get("subject"), Mapping) else {}
            symbol = _symbol(subject.get("symbol") or lock.get("symbol"))
            facts.append(self._fact(
                label=f"我的決定 {symbol or ''}".strip(),
                value=str(lock.get("human_actual_action") or lock.get("decision") or lock.get("action") or "locked"),
                unit="decision", as_of=lock.get("decision_time") or lock.get("created_at"), freshness=FRESH,
                source_id=str(lock.get("human_decision_id") or lock.get("id") or ""), owner="agora.decision_locks",
                symbol=symbol or None,
            ))
        return ToolResult("my_decisions", out_args, FRESH if facts else EMPTY_FRESH, "", facts,
                          missing=None if facts else "the trader has no locked decision yet")

    def _my_shadow(self, args: Dict[str, Any], ctx: ToolContext) -> ToolResult:
        limit = _bounded_int(args.get("limit"), 5, 1, 10)
        out_args = {"limit": limit}
        if self.agora_owner is None:
            return ToolResult("my_shadow", out_args, NOT_CONFIGURED, "", missing="decision lock owner is not configured")
        facts = []
        for lock in self._own_locks(ctx)[:limit]:
            arms = lock.get("arms") if isinstance(lock.get("arms"), Mapping) else {}
            for arm_name, arm in sorted(arms.items()):
                if not isinstance(arm, Mapping):
                    continue
                facts.append(self._fact(
                    label=f"影子臂 {arm_name} 動作", value=str(arm.get("action") or arm.get("status") or "pending"),
                    unit="decision", as_of=arm.get("settled_at") or lock.get("decision_time"), freshness=FRESH,
                    source_id=str(lock.get("human_decision_id") or lock.get("id") or ""), owner="agora.decision_locks",
                ))
        return ToolResult("my_shadow", out_args, FRESH if facts else EMPTY_FRESH, "", facts,
                          missing=None if facts else "no shadow arm is recorded on the trader's decisions yet")

    def _my_journal(self, args: Dict[str, Any], ctx: ToolContext) -> ToolResult:
        limit = _bounded_int(args.get("limit"), 5, 1, 10)
        out_args = {"limit": limit}
        reader = getattr(self.journal_reader, "list_decision_journal_entries", None)
        if reader is None:
            return ToolResult("my_journal", out_args, NOT_CONFIGURED, "", missing="decision journal owner is not configured")
        try:
            entries = reader(tenant_id=ctx.tenant_id, user_id=ctx.user_id)
        except TypeError:
            entries = reader()
        own = [
            e for e in entries or []
            if isinstance(e, Mapping)
            and str(e.get("user_id") or e.get("userId") or "") == str(ctx.user_id)
            and str(e.get("tenant_id") or e.get("tenantId") or ctx.tenant_id) == str(ctx.tenant_id)
        ]
        own.sort(key=lambda e: str(e.get("created_at") or e.get("createdAt") or ""), reverse=True)
        facts = [
            self._fact(
                label="我的日誌", value=_clip(e.get("title") or e.get("summary") or e.get("content") or e.get("body")),
                unit="text", as_of=e.get("created_at") or e.get("createdAt"), freshness=FRESH,
                source_id=str(e.get("id") or e.get("entry_id") or ""), owner="governance.decision_journal",
            )
            for e in own[:limit]
        ]
        return ToolResult("my_journal", out_args, FRESH if facts else EMPTY_FRESH, "", facts,
                          missing=None if facts else "the trader has no journal entry yet")

    def _institutional_facts(self, args: Dict[str, Any], ctx: ToolContext) -> ToolResult:
        out_args = {k: v for k, v in {"symbol": _symbol(args.get("symbol")), "strategy_id": args.get("strategy_id")}.items() if v}
        if self.institutional_reader is None:
            return ToolResult("institutional_facts", out_args, NOT_CONFIGURED, "", missing="institutional knowledge owners are not configured")
        result = self.institutional_reader.read_servant_institutional_knowledge(
            tenant_id=ctx.tenant_id, user_id=ctx.user_id, servant_persona_id=ctx.servant_persona_id,
            operator_id=ctx.operator_id, symbol=out_args.get("symbol"), strategy_id=out_args.get("strategy_id"),
        )
        facts = [
            self._fact(
                label=f"機構事實 {item.get('fact_kind')}", value=_clip(item.get("title")), unit="text",
                as_of=item.get("written_at"), freshness=FRESH, source_id=str(item.get("fact_id") or ""),
                owner=str(item.get("source_owner") or "institutional_knowledge"),
            )
            for item in (result.get("items") or [])[:10]
            if isinstance(item, Mapping)
        ]
        return ToolResult("institutional_facts", out_args, FRESH if facts else EMPTY_FRESH, "", facts)

    def _propose_research(self, args: Dict[str, Any], ctx: ToolContext) -> ToolResult:
        question = _clip(args.get("question"), 500)
        out_args = {"question": question}
        if not question:
            return ToolResult("propose_research", out_args, NOT_CONFIGURED, "", missing="propose_research needs a question")
        if self.research_drafter is None:
            return ToolResult("propose_research", out_args, NOT_CONFIGURED, "", missing="research drafting is not configured")
        draft = dict(self.research_drafter(question, ctx) or {})
        fact = self._fact(
            label="研究提案草稿（未儲存，需交易員核准）", value=_clip(draft.get("title") or draft.get("objective") or question),
            unit="draft", as_of=_iso(self.clock()), freshness=FRESH, source_id=None, owner="agora.servant.research_proposal",
        )
        return ToolResult("propose_research", out_args, FRESH, "", [fact], note="draft only; not stored")


def _record_time(record: Mapping[str, Any]) -> Optional[str]:
    meta = record.get("metadata") if isinstance(record.get("metadata"), Mapping) else {}
    for key in ("published_at", "event_time", "available_time", "announced_at"):
        value = meta.get(key)
        if value not in (None, ""):
            return str(value)
    value = record.get("created_at")
    return str(value) if value not in (None, "") else None


def _record_mentions(record: Mapping[str, Any], term: str) -> bool:
    needle = str(term or "").strip().upper()
    if not needle:
        return False
    meta = record.get("metadata") if isinstance(record.get("metadata"), Mapping) else {}
    for key in ("symbol", "stock_id", "code"):
        if _symbol(meta.get(key)) == needle:
            return True
    for key in ("symbols", "tickers"):
        values = meta.get(key)
        if isinstance(values, (list, tuple)) and needle in {_symbol(v) for v in values}:
            return True
    haystack = " ".join(str(x or "") for x in (record.get("title"), meta.get("summary"), meta.get("description"))).upper()
    return needle in haystack


def normalize_command_context(raw: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """Bound the command-bar context (page, strategy, selected symbols, quick task)."""
    if not raw:
        return {}
    quick = str(raw.get("quick_task") or "").strip()
    quick = QUICK_TASK_ALIASES.get(quick, quick)
    symbols = raw.get("selected_symbols") or []
    if isinstance(symbols, str):
        symbols = symbols.split(",")
    context = {
        "page": str(raw.get("page") or "").strip()[:80] or None,
        "strategy_id": str(raw.get("strategy_id") or "").strip()[:120] or None,
        "widget_id": str(raw.get("widget_id") or "").strip()[:120] or None,
        "selected_symbols": list(dict.fromkeys(_symbol(s) for s in symbols if _symbol(s)))[:20],
        "quick_task": quick[:64] or None,
    }
    return {k: v for k, v in context.items() if v not in (None, [], "")}


def seeded_tool_calls(command_context: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """Deterministic first round for command-bar quick tasks (V6 §2.3).

    「分析分點」 runs ``broker_branch_flow`` on the selected symbols without asking the model.
    """
    if command_context.get("quick_task") == QUICK_TASK_ANALYZE_BROKER_BRANCHES and command_context.get("selected_symbols"):
        return [{"tool": "broker_branch_flow",
                 "arguments": {"symbols": list(command_context["selected_symbols"])[:BROKER_TOOL_MAX_SYMBOLS], "side": "both"}}]
    return []


__all__ = [
    "BROKER_TOOL_MAX_SYMBOLS",
    "FRESHNESS_STATES",
    "MAX_CALLS_PER_ROUND",
    "MAX_FACTS_PER_ANSWER",
    "MAX_TOOL_ROUNDS",
    "QUICK_TASK_ANALYZE_BROKER_BRANCHES",
    "ServantDataTools",
    "SourceIngestToolClient",
    "TOOL_CATALOG",
    "TOOL_NAMES",
    "ToolContext",
    "ToolResult",
    "freshness_for",
    "normalize_command_context",
    "seeded_tool_calls",
]
