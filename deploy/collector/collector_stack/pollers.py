"""Official API, RSSHub, and Docling book library pollers (SD §5.1, §5.2, §5.3, §5.4, §5.5, §6.2)."""

from __future__ import annotations

import csv
import hashlib
import math
import tempfile
import io
import json
import os
import re
import xml.etree.ElementTree as ET
import zipfile
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, unquote, urlencode, urlparse
from typing import Any, Mapping, Sequence

from .bundle import CollectedItem, utc_iso
from .config import (DISABLED_BY_BUILD_TOKENS, GDELT_BASE_URL, GDELT_INDEX_URL,
                     CollectorConfigError, SourceFetchPolicy, validate_query_terms)
from .fetchers import (
    DoclingBookAdapter,
    FetchAccessBlockedError,
    FetchResponse,
    FetchTransport,
    PolicyEnforcedFetcher,
    ScraplingHttpFetcher,
)
from .privacy import AuthorHasher, scrub_raw_html_or_json
from .state import CollectorStateStore


def _policy_query(policy: SourceFetchPolicy, *, arxiv: bool = False) -> str:
    terms = validate_query_terms(policy.source_id, policy.raw_config.get("query_terms"))
    expression = " OR ".join(f'all:"{term}"' if arxiv else f'"{term}"' for term in terms)
    if arxiv:
        return f"(cat:q-fin.* OR cat:stat.ML) AND ({expression})"
    return f"({expression})" if len(terms) > 1 else expression


def _configured_url(override: str | None, configured: str) -> str:
    if override is not None and override != configured:
        raise CollectorConfigError("query URL must match committed fetch-policy query_terms")
    return configured


def read_secret(env_name: str) -> str | None:
    """Read a secret from $<env_name> or from the file named by $<env_name>_FILE (compose secrets)."""
    direct = os.environ.get(env_name)
    if direct:
        return direct.strip()
    path = os.environ.get(f"{env_name}_FILE")
    if path and Path(path).is_file():
        return Path(path).read_text(encoding="utf-8").strip() or None
    return None


def _api_key_headers(env_name: str) -> dict[str, str]:
    key = read_secret(env_name)
    return {"x-goog-api-key": key} if key else {}


_RFC2822_RE = re.compile(
    r"^(?:(?P<dow>Mon|Tue|Wed|Thu|Fri|Sat|Sun), )?(?P<d>\d{1,2}) "
    r"(?P<mon>Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) (?P<y>\d{4}) "
    r"(?P<H>\d{2}):(?P<M>\d{2}):(?P<S>\d{2}) (?:(?P<utc>GMT|UTC?)|(?P<sign>[+-])(?P<oh>\d{2})(?P<om>\d{2}))$"
)
_RFC2822_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_RFC2822_DOWS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _strict_rfc2822_utc(text: str) -> datetime | None:
    """Strictly parse a full RFC 2822 date with GMT/UT/UTC or a 4-digit numeric offset.

    Anything else (no zone, -0000, short/oversized offsets, named zones, trailing text,
    impossible calendar values, weekday mismatch) returns None; never guesses.
    """
    m = _RFC2822_RE.match(text)
    if not m:
        return None
    if m["utc"]:
        offset = timedelta(0)
    else:
        oh, om = int(m["oh"]), int(m["om"])
        if oh > 23 or om > 59 or (m["sign"] == "-" and oh == 0 and om == 0):
            return None
        offset = (1 if m["sign"] == "+" else -1) * timedelta(hours=oh, minutes=om)
    try:
        local = datetime(int(m["y"]), _RFC2822_MONTHS.index(m["mon"]) + 1, int(m["d"]),
                         int(m["H"]), int(m["M"]), int(m["S"]))
    except ValueError:
        return None
    if m["dow"] and _RFC2822_DOWS[local.weekday()] != m["dow"]:
        return None
    return (local - offset).replace(tzinfo=timezone.utc)


def _rss_pub_iso(raw: str) -> str:
    """Canonical UTC ISO for a strictly valid RFC 2822 RSS pubDate; anything else is
    returned unchanged so the verified reader keeps its truthful verdict."""
    parsed = _strict_rfc2822_utc(raw)
    if parsed is None:
        return raw
    return parsed.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _detect_tw_symbols(text: str) -> list[str]:
    return sorted(set(re.findall(r"\b(?:00\d{2,3}|[1-9]\d{3})\b", text or "")))


class CbcMacroPoller:
    """Central Bank (CBC) Open Data API poller (SD §5.1)."""

    source_id = "collector-tw-macro-cbc"
    MAX_ROWS_PER_SERIES = 30
    skipped_count = 0

    def __init__(
        self,
        policy: SourceFetchPolicy,
        *,
        transport: FetchTransport,
        contact_user_agent: str = "PantheonCollector/1.0 (+mailto:collector-ops@example.invalid)",
    ) -> None:
        self.policy = policy
        self.fetcher = PolicyEnforcedFetcher(
            policy, transport=transport, contact_user_agent=contact_user_agent
        )

    def collect(
        self,
        *,
        series_codes: Sequence[str] = ("BP01D01",),
        fetched_at: datetime | str | None = None,
    ) -> list[CollectedItem]:
        fetched_iso = fetched_at if isinstance(fetched_at, str) else utc_iso(fetched_at)
        items: list[CollectedItem] = []

        for code in series_codes:
            url = f"https://cpx.cbc.gov.tw/API/DataAPI/Get?FileName={quote(code, safe='')}"
            response = self.fetcher.fetch(url)
            # The CBC API answers JSON with content-type text/plain; parse the body ourselves.
            try:
                payload = json.loads(response.text)
            except ValueError:
                self.skipped_count += 1
                continue
            if not isinstance(payload, dict):
                self.skipped_count += 1
                continue
            data = payload.get("data")
            data_sets = data.get("dataSets") if isinstance(data, dict) else None
            if not isinstance(data_sets, list):
                self.skipped_count += 1
                continue
            columns = _cbc_columns(data)
            meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
            for row in data_sets[-self.MAX_ROWS_PER_SERIES :]:
                if not isinstance(row, list) or not row:
                    self.skipped_count += 1
                    continue
                period = _cbc_period(row[0])
                if period is None:
                    self.skipped_count += 1
                    continue
                for idx, cell in enumerate(row[1:]):
                    try:
                        val = float(str(cell).replace(",", ""))
                    except ValueError:
                        continue  # "-" means not published for this currency/period
                    name = columns[idx] if idx < len(columns) else f"col{idx + 1}"
                    raw_row = json.dumps(
                        {"series": code, "column": name, "period": period, "value": val},
                        sort_keys=True,
                        ensure_ascii=False,
                    ).encode("utf-8")
                    items.append(
                        CollectedItem(
                            item_id=f"cbc-{code.lower()}-{idx + 1:02d}-{period}",
                            url=url,
                            fetched_at=fetched_iso,
                            published_at=f"{period}T08:30:00Z",
                            text=f"CBC {code} {name} {period}: {val}",
                            lang="zh-TW",
                            hashed_author=None,
                            metrics={"value": val, "series_code": code, "period": period},
                            raw_bytes=raw_row,
                            raw_extension="json",
                            http_status=response.http_status,
                            extra_fields={
                                "title": f"CBC {code} {name} {period}",
                                "series_id": f"CBC:{code}:{idx + 1:02d}",
                                "value": val,
                                "unit": str(meta.get("units") or ""),
                            },
                        )
                    )
        return items


def _cbc_columns(data: Mapping[str, Any]) -> list[str]:
    structure = data.get("structure")
    if isinstance(structure, dict):
        for cols in structure.values():
            if isinstance(cols, list):
                return [str(c.get("data", "")).strip() if isinstance(c, dict) else str(c) for c in cols]
    return []


def _cbc_period(raw: Any) -> str | None:
    """CBC row keys are YYYYMMDD (daily), YYYYMM (monthly) or YYYY (yearly)."""
    text = str(raw).strip()
    if re.fullmatch(r"\d{8}", text):
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    if re.fullmatch(r"\d{6}", text):
        return f"{text[:4]}-{text[4:]}-01"
    if re.fullmatch(r"\d{4}", text):
        return f"{text}-01-01"
    return None


class NdcIndicatorsPoller:
    """NDC business indicators (景氣燈號, data.gov.tw dataset 6099) monthly ZIP poller (SD §5.1)."""

    source_id = "collector-tw-macro-ndc"

    def __init__(
        self,
        policy: SourceFetchPolicy,
        *,
        transport: FetchTransport,
        contact_user_agent: str = "PantheonCollector/1.0 (+mailto:collector-ops@example.invalid)",
    ) -> None:
        self.policy = policy
        self.fetcher = PolicyEnforcedFetcher(
            policy, transport=transport, contact_user_agent=contact_user_agent
        )

    DATASET_URL = "https://data.gov.tw/api/v2/rest/dataset/6099"
    INDICATOR_MEMBER = "景氣指標與燈號.csv"
    MAX_ROWS = 24
    skipped_count = 0

    def _resolve_zip_url(self) -> str:
        meta = self.fetcher.fetch(self.DATASET_URL)
        payload = json.loads(meta.text)
        for dist in (payload.get("result") or {}).get("distribution") or []:
            url = str(dist.get("resourceDownloadUrl") or "")
            if str(dist.get("resourceFormat") or "").upper() == "ZIP" and url:
                return url
        raise CollectorConfigError("data.gov.tw dataset 6099 lists no ZIP distribution")

    @staticmethod
    def _num(raw: Any) -> float | None:
        try:
            return float(str(raw).strip())
        except ValueError:
            return None

    def collect(
        self,
        *,
        zip_url: str | None = None,
        fetched_at: datetime | str | None = None,
    ) -> list[CollectedItem]:
        fetched_iso = fetched_at if isinstance(fetched_at, str) else utc_iso(fetched_at)
        zip_url = zip_url or self._resolve_zip_url()
        response = self.fetcher.fetch(zip_url)

        csv_text = ""
        if zipfile.is_zipfile(io.BytesIO(response.body)):
            with zipfile.ZipFile(io.BytesIO(response.body), "r") as zf:
                names = zf.namelist()
                # Exact basename: "schema-景氣指標與燈號.csv" also ends with the member name and must not match.
                member = next((n for n in names if n.rsplit("/", 1)[-1] == self.INDICATOR_MEMBER), None)
                if member is None:
                    member = next(
                        (n for n in sorted(names) if n.lower().endswith(".csv") and not n.startswith(("manifest", "schema"))),
                        None,
                    )
                if member is not None:
                    csv_text = zf.read(member).decode("utf-8-sig", errors="replace")
        else:
            csv_text = response.text

        rows = list(csv.DictReader(io.StringIO(csv_text)))[-self.MAX_ROWS :]
        items: list[CollectedItem] = []
        for row in rows:
            raw_period = (row.get("Date") or row.get("期間") or "").strip()
            if re.fullmatch(r"\d{6}", raw_period):
                period = f"{raw_period[:4]}-{raw_period[4:]}"
            elif re.fullmatch(r"\d{4}-\d{2}", raw_period):
                period = raw_period
            else:
                self.skipped_count += 1
                continue
            score = self._num(row.get("景氣對策信號綜合分數") or row.get("Monitoring_Score") or "")
            leading = self._num(row.get("領先指標綜合指數") or row.get("Leading_Index") or "")
            coincident = self._num(row.get("同時指標綜合指數") or row.get("Coincident_Index") or "")
            light = (row.get("景氣對策信號") or row.get("Signal_Light") or "").strip()
            if score is None:
                self.skipped_count += 1  # older rows carry no signal score
                continue
            pub_at = f"{period}-01T00:00:00Z"

            raw_row = json.dumps(row, sort_keys=True, ensure_ascii=False).encode("utf-8")
            items.append(
                CollectedItem(
                    item_id=f"ndc-6099-{period}",
                    url=self.DATASET_URL,
                    fetched_at=fetched_iso,
                    published_at=pub_at,
                    text=(
                        f"NDC 景氣指標 ({period}): 綜合分數={score} ({light}), "
                        f"領先指標={leading}, 同時指標={coincident}"
                    ),
                    lang="zh-TW",
                    hashed_author=None,
                    metrics={
                        "monitoring_score": score,
                        "leading_index": leading,
                        "coincident_index": coincident,
                        "signal_light": light,
                    },
                    raw_bytes=raw_row,
                    raw_extension="json",
                    http_status=response.http_status,
                    extra_fields={
                        "title": f"NDC Business Indicators {period}",
                        "series_id": "NDC:6099:MONITORING_SCORE",
                        "value": score,
                        "unit": "score",
                    },
                )
            )
        return items


class TwseOpenApiPoller:
    """TWSE OpenAPI fundamentals poller (SD §5.1; disabled by default on collector as Pantheon fetches P)."""

    source_id = "collector-tw-twse-openapi"

    def __init__(
        self,
        policy: SourceFetchPolicy,
        *,
        transport: FetchTransport,
        contact_user_agent: str = "PantheonCollector/1.0 (+mailto:collector-ops@example.invalid)",
    ) -> None:
        self.policy = policy
        self.fetcher = PolicyEnforcedFetcher(
            policy, transport=transport, contact_user_agent=contact_user_agent
        )

    def collect(
        self,
        *,
        endpoint: str = "https://openapi.twse.com.tw/v1/opendata/t187ap05_L",
        fetched_at: datetime | str | None = None,
    ) -> list[CollectedItem]:
        fetched_iso = fetched_at if isinstance(fetched_at, str) else utc_iso(fetched_at)
        response = self.fetcher.fetch(endpoint)
        rows = json.loads(response.text)
        items: list[CollectedItem] = []

        for row in rows:
            code = str(row.get("公司代號") or row.get("Code") or "").strip()
            ym = str(row.get("資料年月") or row.get("YearMonth") or "11510").strip()
            yoy = float(row.get("去年同月增減(%)") or row.get("YoY") or 0.0)
            rev = float(row.get("營業收入-當月營收") or row.get("MonthlyRevenue") or 0.0)
            pub_at = str(row.get("出表日期") or fetched_iso)
            raw_row = json.dumps(row, sort_keys=True, ensure_ascii=False).encode("utf-8")

            items.append(
                CollectedItem(
                    item_id=f"twse-rev-{code}-{ym}",
                    url=endpoint,
                    fetched_at=fetched_iso,
                    published_at=pub_at,
                    text=f"TWSE OpenAPI monthly revenue {code} ({ym}): YoY={yoy}%",
                    lang="zh-TW",
                    hashed_author=None,
                    metrics={"monthly_revenue": rev, "yoy_pct": yoy, "year_month": ym},
                    raw_bytes=raw_row,
                    raw_extension="json",
                    http_status=response.http_status,
                    extra_fields={
                        "title": f"TWSE {code} Monthly Revenue {ym}",
                        "symbols": [code] if code else [],
                        "series_id": "TWSE:t187ap05_L:yoy",
                        "value": yoy,
                        "unit": "percent",
                    },
                )
            )
        return items


class GdeltRawFeedPoller:
    """Hourly latest-export snapshot; verified ZIP cache and post-bundle receipts.

    Export has structured events, not article titles/text. Vocabulary matching uses
    actor/geographic names and decoded source URLs only. No DOC API fallback.
    """

    source_id = "collector-gdelt"
    MAX_ZIP_BYTES = 64 * 1024 * 1024
    MAX_CSV_BYTES = 128 * 1024 * 1024

    def __init__(self, policy: SourceFetchPolicy, *, transport: FetchTransport,
                 cache_dir: Path | str,
                 contact_user_agent: str = "PantheonCollector/1.0 (+mailto:collector-ops@example.invalid)") -> None:
        self.policy = policy
        self.cache_dir = Path(cache_dir)
        self.fetcher = PolicyEnforcedFetcher(policy, transport=transport, contact_user_agent=contact_user_agent)
        self.terms = validate_query_terms(policy.source_id, policy.raw_config.get("query_terms"))
        self.skipped_count = 0
        self.empty_reason = "no_matching_records"
        self._pending_receipt: Path | None = None

    def _fetch(self, url: str) -> FetchResponse:
        try:
            response = self.fetcher.fetch(url)
        except FetchAccessBlockedError:
            raise
        except Exception as exc:
            raise FetchAccessBlockedError(
                f"gdelt_download_failed:{url}:{type(exc).__name__}",
                FetchResponse(url, url, 0, b""),
            ) from exc
        if response.http_status != 200:
            raise FetchAccessBlockedError(f"gdelt_http_{response.http_status}:{url}", response)
        # Live HTTP transports follow redirects; reject payloads redirected off-site.
        final = urlparse(response.final_url)
        if final.scheme != "https" or final.hostname != "data.gdeltproject.org" or final.port not in (None, 443):
            raise FetchAccessBlockedError(f"gdelt_unexpected_redirect:{response.final_url}", response)
        return response

    @staticmethod
    def _atomic_bytes(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".gdelt-", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def acknowledge_bundle(self) -> None:
        """Called only after BundleWriter has published the output successfully."""
        if self._pending_receipt is not None:
            self._atomic_bytes(self._pending_receipt, b"bundled\n")

    def collect(self, *, fetched_at: datetime | str | None = None) -> list[CollectedItem]:
        response = self._fetch(GDELT_INDEX_URL)
        try:
            entries = []
            for line in response.text.splitlines():
                parts = line.split()
                if len(parts) != 3:
                    raise ValueError("invalid index row")
                size, md5, listed_url = parts
                if not listed_url.endswith(".export.CSV.zip"):
                    continue
                if not re.fullmatch(r"https?://data\.gdeltproject\.org/gdeltv2/\d{14}\.export\.CSV\.zip", listed_url):
                    raise ValueError("invalid export URL")
                if not re.fullmatch(r"[a-fA-F0-9]{32}", md5) or not 0 < int(size) <= self.MAX_ZIP_BYTES:
                    raise ValueError("invalid export size/md5")
                entries.append((int(size), md5.lower(), listed_url))
            if len(entries) != 1:
                raise ValueError("index must list exactly one export")
            size, expected_md5, listed_url = entries[0]
        except ValueError as exc:
            raise FetchAccessBlockedError(f"gdelt_invalid_index:{exc}", response) from exc

        name = listed_url.rsplit("/", 1)[1]
        cached = self.cache_dir / name
        receipt = self.cache_dir / f"{name}.{self.policy.policy_hash}.bundled"
        download_url = GDELT_BASE_URL + name  # HTTPS for the official HTTP listing
        if cached.is_file():
            body = cached.read_bytes()
        else:
            response = self._fetch(download_url)
            body = response.body
        if len(body) != size or hashlib.md5(body).hexdigest() != expected_md5:
            # Never keep a corrupt cache entry; the next scheduled run can retry.
            cached.unlink(missing_ok=True)
            raise FetchAccessBlockedError(f"gdelt_integrity_mismatch:{listed_url}", response)

        try:
            with zipfile.ZipFile(io.BytesIO(body)) as archive:
                members = archive.infolist()
                if (len(members) != 1 or members[0].filename != name[:-4]
                        or members[0].file_size > self.MAX_CSV_BYTES):
                    raise ValueError("unexpected ZIP member/size")
                raw_csv = archive.read(members[0])
            items = self._parse_export(raw_csv, listed_url, fetched_at)
        except (ValueError, zipfile.BadZipFile, RuntimeError, csv.Error, EOFError, zlib.error) as exc:
            cached.unlink(missing_ok=True)
            raise FetchAccessBlockedError(f"gdelt_invalid_export:{name}:{exc}", response) from exc
        if not cached.is_file():
            self._atomic_bytes(cached, body)
        if receipt.is_file():
            self.empty_reason = "no_new_feed"
            return []
        self._pending_receipt = receipt
        return items

    def _parse_export(self, raw: bytes, feed_url: str,
                      fetched_at: datetime | str | None) -> list[CollectedItem]:
        fetched_iso = fetched_at if isinstance(fetched_at, str) else utc_iso(fetched_at)
        items = []
        # Normalize URL word separators, keeping phrase matching within the fixed terms.
        def normalize(text: str) -> str:
            return re.sub(r"[\W_]+", " ", unquote(text).casefold()).strip()

        for row in csv.reader(io.StringIO(raw.decode("utf-8")), delimiter="\t", quoting=csv.QUOTE_NONE):
            if len(row) != 61:
                raise ValueError(f"expected 61 event columns, got {len(row)}")
            if not row[0].isdigit():
                raise ValueError("invalid GlobalEventID")
            event_day = datetime.strptime(row[1], "%Y%m%d").date().isoformat()
            added_at = datetime.strptime(row[59], "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
            source = urlparse(row[60])
            if source.scheme not in ("http", "https") or not source.netloc:
                raise ValueError("invalid SOURCEURL")
            context = " " + normalize(" ".join(row[i] for i in (6, 16, 36, 44, 52, 60))) + " "
            matched = [term for term in self.terms if " " + normalize(term) + " " in context]
            if not matched:
                continue
            metrics = {
                "event_code": row[26], "event_base_code": row[27], "event_root_code": row[28],
                "quad_class": int(row[29]), "goldstein_scale": float(row[30]),
                "num_mentions": int(row[31]), "num_sources": int(row[32]),
                "num_articles": int(row[33]), "avg_tone": float(row[34]),
                "actor1_name": row[6], "actor1_country_code": row[7],
                "actor2_name": row[16], "actor2_country_code": row[17],
                "action_geo_name": row[52], "action_country_code": row[53],
            }
            if not all(math.isfinite(metrics[k]) for k in ("goldstein_scale", "avg_tone")):
                raise ValueError("non-finite event feature")
            title = f"GDELT event {row[0]} ({event_day}): CAMEO {row[26]}"
            text = (f"{title}; actor1={row[6]}; actor2={row[16]}; location={row[52]}; "
                    f"Goldstein={row[30]}; tone={row[34]}; source={row[60]}")
            items.append(CollectedItem(
                item_id=f"gdelt-{row[0]}", url=row[60], fetched_at=fetched_iso,
                published_at=utc_iso(added_at), text=text, lang="und", hashed_author=None,
                metrics=metrics, raw_bytes=("\t".join(row) + "\n").encode("utf-8"), raw_extension="tsv",
                extra_fields={"title": title, "gdelt_record_id": row[0], "feed_url": feed_url,
                              "event_time": event_day + "T00:00:00Z", "event_time_precision": "day",
                              "gdelt_date_added": utc_iso(added_at), "published_at_basis": "gdelt_date_added",
                              "matched_query_terms": matched, "publisher": source.netloc,
                              "attribution": self.policy.attribution},
            ))
        return items


class DbnomicsMacroPoller:
    """DBnomics v22 API poller for US/EU/JP/CN macro context series (SD §5.1)."""

    source_id = "collector-macro-dbnomics"

    def __init__(
        self,
        policy: SourceFetchPolicy,
        *,
        transport: FetchTransport,
        contact_user_agent: str = "PantheonCollector/1.0 (+mailto:collector-ops@example.invalid)",
    ) -> None:
        self.policy = policy
        self.fetcher = PolicyEnforcedFetcher(
            policy, transport=transport, contact_user_agent=contact_user_agent
        )

    MAX_OBSERVATIONS_PER_SERIES = 60
    skipped_count = 0

    def collect(
        self,
        *,
        series_ids: Sequence[str] | None = None,
        fetched_at: datetime | str | None = None,
    ) -> list[CollectedItem]:
        ids = list(series_ids or self.policy.raw_config.get("series_ids") or ())
        fetched_iso = fetched_at if isinstance(fetched_at, str) else utc_iso(fetched_at)
        items: list[CollectedItem] = []
        seen_ids: set[str] = set()
        for series_id in ids:
            if series_id.upper().startswith(("TW/", "TAIWAN/", "CBC/", "DGBAS/")):
                raise CollectorConfigError(
                    "DBnomics has almost no Taiwan series (SD §5.1); use DBnomics only for US/EU/JP/CN context"
                )
            # Documented form: /v22/series/{provider}/{dataset}/{series}?observations=1
            target_url = (
                f"https://api.db.nomics.world/v22/series/{quote(series_id, safe='/.')}"
                "?observations=1&metadata=0"
            )
            response = self.fetcher.fetch(target_url)
            try:
                payload = json.loads(response.text)
            except ValueError:
                self.skipped_count += 1
                continue
            docs = (payload.get("series") or {}).get("docs") or []
            if response.http_status >= 400 or not docs:
                self.skipped_count += 1
                continue
            for item in self._items_from_docs(docs, series_id, target_url, fetched_iso, response.http_status):
                if item.item_id in seen_ids:
                    self.skipped_count += 1
                    continue
                seen_ids.add(item.item_id)
                items.append(item)
        return items

    def _items_from_docs(
        self,
        docs: Sequence[Mapping[str, Any]],
        series_id: str,
        target_url: str,
        fetched_iso: str,
        http_status: int,
    ) -> list[CollectedItem]:
        items: list[CollectedItem] = []
        for doc in docs:
            sid = "/".join(
                str(doc.get(k) or "") for k in ("provider_code", "dataset_code", "series_code")
            ).strip("/") or series_id
            periods = list(doc.get("period") or [])[-self.MAX_OBSERVATIONS_PER_SERIES :]
            values = list(doc.get("value") or [])[-self.MAX_OBSERVATIONS_PER_SERIES :]
            unit = str(doc.get("unit") or "")
            for period, val in zip(periods, values):
                if val is None or val == "NA":
                    self.skipped_count += 1
                    continue
                try:
                    fval = float(val)
                except (TypeError, ValueError):
                    self.skipped_count += 1
                    continue
                obs_obj = {"series_id": sid, "period": period, "value": fval, "unit": unit}
                items.append(
                    CollectedItem(
                        item_id=f"dbnomics-{sid.replace('/', '-').lower()}-{period}",
                        url=target_url,
                        fetched_at=fetched_iso,
                        published_at=f"{period}T00:00:00Z" if len(str(period)) == 10 else fetched_iso,
                        text=f"DBnomics {sid} ({period}): {fval} {unit}".rstrip(),
                        lang="en",
                        hashed_author=None,
                        metrics={"value": fval, "period": str(period)},
                        raw_bytes=json.dumps(obs_obj, sort_keys=True).encode("utf-8"),
                        raw_extension="json",
                        http_status=http_status,
                        extra_fields={
                            "title": f"DBnomics {sid} {period}",
                            "series_id": f"DBNOMICS:{sid}",
                            "value": fval,
                            "unit": unit,
                        },
                    )
                )
        return items


class ArxivPaperPoller:
    """arXiv export API poller filtered to q-fin.* and stat.ML (SD §5.2)."""

    source_id = "collector-paper-arxiv"
    ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}

    def __init__(
        self,
        policy: SourceFetchPolicy,
        *,
        transport: FetchTransport,
        contact_user_agent: str = "PantheonCollector/1.0 (+mailto:collector-ops@example.invalid)",
    ) -> None:
        if policy.min_interval_seconds < 3.0:
            raise CollectorConfigError("arXiv export API requires min_interval_seconds >= 3.0")
        self.policy = policy
        self.fetcher = PolicyEnforcedFetcher(
            policy, transport=transport, contact_user_agent=contact_user_agent
        )

    @staticmethod
    def _is_allowed_category(cat: str) -> bool:
        return cat.startswith("q-fin.") or cat == "q-fin" or cat == "stat.ML"

    def collect(
        self,
        *,
        url: str | None = None,
        fetched_at: datetime | str | None = None,
    ) -> list[CollectedItem]:
        fetched_iso = fetched_at if isinstance(fetched_at, str) else utc_iso(fetched_at)
        target_url = _configured_url(url, "https://export.arxiv.org/api/query?" + urlencode({
            "search_query": _policy_query(self.policy, arxiv=True), "max_results": 10,
        }))
        response = self.fetcher.fetch(target_url)
        root = ET.fromstring(response.text)
        items: list[CollectedItem] = []

        for entry in root.findall("atom:entry", self.ATOM_NS):
            cats = [
                str(c.attrib.get("term") or "")
                for c in entry.findall("atom:category", self.ATOM_NS)
            ]
            if not any(self._is_allowed_category(c) for c in cats):
                continue

            raw_id = (entry.findtext("atom:id", default="", namespaces=self.ATOM_NS) or "").strip()
            arxiv_id = raw_id.rsplit("/", 1)[-1]
            title = " ".join(
                (entry.findtext("atom:title", default="", namespaces=self.ATOM_NS) or "").split()
            )
            summary = " ".join(
                (entry.findtext("atom:summary", default="", namespaces=self.ATOM_NS) or "").split()
            )
            published = (
                entry.findtext("atom:published", default=fetched_iso, namespaces=self.ATOM_NS)
                or fetched_iso
            ).strip()
            authors = [
                (a.findtext("atom:name", default="", namespaces=self.ATOM_NS) or "").strip()
                for a in entry.findall("atom:author", self.ATOM_NS)
                if (a.findtext("atom:name", default="", namespaces=self.ATOM_NS) or "").strip()
            ]
            raw_entry = ET.tostring(entry, encoding="utf-8")

            items.append(
                CollectedItem(
                    item_id=f"arxiv-{arxiv_id}",
                    url=raw_id or target_url,
                    fetched_at=fetched_iso,
                    published_at=published,
                    text=f"{title}\n\n{summary}",
                    lang="en",
                    hashed_author=None,
                    metrics={
                        "categories": cats,
                        "redistribution_allowed": False,
                        "pdf_storage_policy": "internal_extraction_only_never_reserved",
                    },
                    raw_bytes=raw_entry,
                    raw_extension="xml",
                    http_status=response.http_status,
                    extra_fields={
                        "title": title,
                        "arxiv_id": arxiv_id,
                        "authors": authors,
                    },
                )
            )
        return items


class OpenAlexPaperPoller:
    """OpenAlex REST API works poller (SD §5.2)."""

    source_id = "collector-paper-openalex"

    def __init__(
        self,
        policy: SourceFetchPolicy,
        *,
        transport: FetchTransport,
        contact_user_agent: str = "PantheonCollector/1.0 (+mailto:collector-ops@example.invalid)",
    ) -> None:
        self.policy = policy
        self.fetcher = PolicyEnforcedFetcher(
            policy, transport=transport, contact_user_agent=contact_user_agent
        )

    @staticmethod
    def _reconstruct_abstract(inverted_index: Mapping[str, Sequence[int]] | None) -> str:
        if not inverted_index:
            return ""
        positions: dict[int, str] = {}
        for word, indices in inverted_index.items():
            for pos in indices:
                positions[int(pos)] = str(word)
        return " ".join(positions[idx] for idx in sorted(positions))

    def collect(
        self,
        *,
        url: str | None = None,
        fetched_at: datetime | str | None = None,
    ) -> list[CollectedItem]:
        fetched_iso = fetched_at if isinstance(fetched_at, str) else utc_iso(fetched_at)
        target_url = _configured_url(url, "https://api.openalex.org/works?" + urlencode({
            "search": _policy_query(self.policy), "per-page": 10,
        }))
        response = self.fetcher.fetch(target_url)
        payload = json.loads(response.text)
        works = payload.get("results") or []
        items: list[CollectedItem] = []

        for work in works:
            work_id = str(work.get("id") or "").rsplit("/", 1)[-1]
            title = str(work.get("title") or work_id)
            abstract = str(work.get("abstract") or "") or self._reconstruct_abstract(
                work.get("abstract_inverted_index")
            )
            pub_date = str(work.get("publication_date") or "")
            if not pub_date:
                pub_iso = fetched_iso
            else:
                pub_iso = f"{pub_date}T00:00:00Z" if "T" not in pub_date else pub_date
            doi = str(work.get("doi") or "")
            cited_by = int(work.get("cited_by_count") or 0)

            raw_work = json.dumps(work, sort_keys=True, ensure_ascii=False).encode("utf-8")
            items.append(
                CollectedItem(
                    item_id=f"openalex-{work_id.lower()}",
                    url=str(work.get("id") or target_url),
                    fetched_at=fetched_iso,
                    published_at=pub_iso,
                    text=f"{title}\n\n{abstract}".strip(),
                    lang="en",
                    hashed_author=None,
                    metrics={"cited_by_count": cited_by, "license": "CC0-1.0"},
                    raw_bytes=raw_work,
                    raw_extension="json",
                    http_status=response.http_status,
                    extra_fields={"title": title, "doi": doi} if doi else {"title": title},
                )
            )
        return items


class RssHubPaperPoller:
    """NBER and journal paper RSS poller via unmodified RSSHub container (SD §5.2)."""

    source_id = "collector-paper-nber-rss"

    def __init__(
        self,
        policy: SourceFetchPolicy,
        *,
        transport: FetchTransport,
        contact_user_agent: str = "PantheonCollector/1.0 (+mailto:collector-ops@example.invalid)",
    ) -> None:
        self.policy = policy
        self.fetcher = ScraplingHttpFetcher(
            policy, transport=transport, contact_user_agent=contact_user_agent
        )

    def collect(
        self,
        *,
        route: str = "/nber/papers",
        fetched_at: datetime | str | None = None,
    ) -> list[CollectedItem]:
        base = self.policy.base_urls[0].rstrip("/")
        url = f"{base}/{route.lstrip('/')}"
        fetched_iso = fetched_at if isinstance(fetched_at, str) else utc_iso(fetched_at)
        response = self.fetcher.fetch(url)
        root = ET.fromstring(response.text)
        items: list[CollectedItem] = []

        for item_el in root.findall(".//item"):
            guid = (item_el.findtext("guid") or item_el.findtext("link") or "").strip()
            paper_id = guid.rsplit("/", 1)[-1] or "nber-paper"
            title = (item_el.findtext("title") or "").strip()
            link = (item_el.findtext("link") or url).strip()
            desc = (item_el.findtext("description") or "").strip()
            pub_date = _rss_pub_iso((item_el.findtext("pubDate") or fetched_iso).strip())

            raw_xml = ET.tostring(item_el, encoding="utf-8")
            items.append(
                CollectedItem(
                    item_id=f"rsshub-paper-{paper_id}",
                    url=link,
                    fetched_at=fetched_iso,
                    published_at=pub_date,
                    text=f"{title}\n\n{desc}".strip(),
                    lang="en",
                    hashed_author=None,
                    metrics={"route": route, "origin": "rsshub"},
                    raw_bytes=raw_xml,
                    raw_extension="xml",
                    http_status=response.http_status,
                    extra_fields={"title": title},
                )
            )
        return items


class RssHubNewsPoller:
    """Taiwan news RSSHub poller (cna, udn, ltn, chinatimes, cw); Yahoo and Anue/cnyes DISABLED_BY_BUILD (SD §5.5)."""

    source_id = "collector-news-rss"
    VERIFIED_ROUTES = frozenset(
        {
            "/cna/aall",
            "/udn/news/breakingnews/2",
            "/ltn/news/business",
            "/chinatimes/realtimenews/260410",
            "/cw/master",
        }
    )

    def __init__(
        self,
        policy: SourceFetchPolicy,
        *,
        transport: FetchTransport,
        contact_user_agent: str = "PantheonCollector/1.0 (+mailto:collector-ops@example.invalid)",
    ) -> None:
        self.policy = policy
        self.fetcher = ScraplingHttpFetcher(
            policy, transport=transport, contact_user_agent=contact_user_agent
        )

    @classmethod
    def validate_route(cls, route: str) -> str:
        norm = "/" + str(route or "").strip().lstrip("/")
        lowered = norm.lower()
        for forbidden in DISABLED_BY_BUILD_TOKENS:
            if forbidden in lowered:
                raise CollectorConfigError(
                    f"RSSHub news route {route!r} is DISABLED_BY_BUILD per 2026-09-02 licence decision"
                )
        if "/ptt" in lowered:
            raise CollectorConfigError("RSSHub has no ptt route (SD §5.5); use PttStockSpider instead")
        return norm

    def collect(
        self,
        *,
        routes: Sequence[str] = ("/cna/aall",),
        fetched_at: datetime | str | None = None,
    ) -> list[CollectedItem]:
        fetched_iso = fetched_at if isinstance(fetched_at, str) else utc_iso(fetched_at)
        base = self.policy.base_urls[0].rstrip("/")
        items: list[CollectedItem] = []

        for route in routes:
            norm_route = self.validate_route(route)
            publisher = norm_route.strip("/").split("/", 1)[0]
            url = f"{base}{norm_route}"
            response = self.fetcher.fetch(url)
            root = ET.fromstring(response.text)

            for item_el in root.findall(".//item"):
                guid = (item_el.findtext("guid") or item_el.findtext("link") or "").strip()
                slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", guid.rsplit("/", 1)[-1]).strip("-") or "news"
                title = (item_el.findtext("title") or "").strip()
                link = (item_el.findtext("link") or url).strip()
                desc = (item_el.findtext("description") or "").strip()
                pub_date = _rss_pub_iso((item_el.findtext("pubDate") or fetched_iso).strip())
                combined = f"{title} — {desc}".strip()

                raw_xml = ET.tostring(item_el, encoding="utf-8")
                items.append(
                    CollectedItem(
                        item_id=f"news-{publisher}-{slug}",
                        url=link,
                        fetched_at=fetched_iso,
                        published_at=pub_date,
                        text=combined,
                        lang="zh-TW",
                        hashed_author=None,
                        metrics={"route": norm_route, "publisher": publisher},
                        raw_bytes=raw_xml,
                        raw_extension="xml",
                        http_status=response.http_status,
                        extra_fields={
                            "title": title,
                            "publisher": publisher,
                            "symbols": _detect_tw_symbols(combined),
                        },
                    )
                )
        return items


class YouTubeDataApiPoller:
    """YouTube Data API v3 poller with quota accounting and captions.download prohibition (SD §5.3)."""

    source_id = "collector-youtube-finance"
    SEARCH_COST_UNITS = 100
    VIDEOS_COST_UNITS = 1
    DAILY_QUOTA_LIMIT_UNITS = 10000

    def __init__(
        self,
        policy: SourceFetchPolicy,
        *,
        transport: FetchTransport,
        hasher: AuthorHasher,
        state_store: CollectorStateStore | None = None,
        contact_user_agent: str = "PantheonCollector/1.0 (+mailto:collector-ops@example.invalid)",
    ) -> None:
        self.policy = policy
        self.fetcher = PolicyEnforcedFetcher(
            policy, transport=transport, contact_user_agent=contact_user_agent
        )
        self.hasher = hasher
        self.state_store = state_store

    def _charge_quota(self, date_str: str, units: int) -> None:
        if self.state_store is None:
            return
        used = self.state_store.get_daily_quota_usage("youtube_data_api_units", date_str)
        if used + units > self.DAILY_QUOTA_LIMIT_UNITS:
            raise CollectorConfigError(
                f"YouTube Data API daily quota limit ({self.DAILY_QUOTA_LIMIT_UNITS} units) exceeded"
            )
        self.state_store.add_daily_quota_usage("youtube_data_api_units", date_str, units)

    def collect(
        self,
        *,
        query: str = "台股 量化 選股",
        search_url: str | None = None,
        videos_url: str | None = None,
        attempt_captions_download: bool = False,
        fetched_at: datetime | str | None = None,
    ) -> list[CollectedItem]:
        if attempt_captions_download:
            raise CollectorConfigError(
                "YouTube captions.download is permitted only for the video's editor and is forbidden on the collector (SD §5.3)"
            )
        fetched_iso = fetched_at if isinstance(fetched_at, str) else utc_iso(fetched_at)
        date_str = fetched_iso[:10]

        s_url = (
            search_url
            or "https://www.googleapis.com/youtube/v3/search?"
            + urlencode({"part": "snippet", "type": "video", "q": query, "maxResults": 5})
        )
        self._charge_quota(date_str, self.SEARCH_COST_UNITS)
        search_resp = self.fetcher.fetch(s_url, headers=_api_key_headers("YOUTUBE_DATA_API_KEY"))
        search_data = json.loads(search_resp.text)
        video_ids = [
            str((it.get("id") or {}).get("videoId") or "")
            for it in (search_data.get("items") or [])
            if (it.get("id") or {}).get("videoId")
        ]

        v_url = (
            videos_url
            or f"https://www.googleapis.com/youtube/v3/videos?part=snippet,statistics,contentDetails&id={','.join(video_ids)}"
        )
        self._charge_quota(date_str, self.VIDEOS_COST_UNITS)
        videos_resp = self.fetcher.fetch(v_url, headers=_api_key_headers("YOUTUBE_DATA_API_KEY"))
        videos_data = json.loads(videos_resp.text)

        items: list[CollectedItem] = []
        for vid in videos_data.get("items") or []:
            vid_id = str(vid.get("id") or "")
            snippet = dict(vid.get("snippet") or {})
            stats = dict(vid.get("statistics") or {})
            raw_channel_id = str(snippet.get("channelId") or "unknown_channel")
            raw_channel_title = str(snippet.get("channelTitle") or "")
            title = str(snippet.get("title") or vid_id)
            description = str(snippet.get("description") or "")
            published_at = str(snippet.get("publishedAt") or fetched_iso)

            if raw_channel_title:
                title = title.replace(raw_channel_title, "").strip()
                description = description.replace(raw_channel_title, "").strip()

            hashed_author = self.hasher.hash_author("youtube", raw_channel_id)
            sanitized_vid = {
                "id": vid_id,
                "publishedAt": published_at,
                "title": title,
                "description": description,
                "hashed_author": hashed_author,
                "statistics": stats,
            }
            raw_bytes = scrub_raw_html_or_json(
                json.dumps(sanitized_vid, sort_keys=True, ensure_ascii=False).encode("utf-8"),
                platform="youtube",
                hasher=self.hasher,
                handles=[raw_channel_id],
                display_names=[raw_channel_title] if raw_channel_title else (),
            )
            forbidden = tuple(x for x in (raw_channel_id, raw_channel_title) if x)
            combined_text = f"{title}\n\n{description}".strip()

            items.append(
                CollectedItem(
                    item_id=f"yt-video-{vid_id}",
                    url=f"https://www.youtube.com/watch?v={vid_id}",
                    fetched_at=fetched_iso,
                    published_at=published_at,
                    text=combined_text,
                    lang="zh-TW",
                    hashed_author=hashed_author,
                    metrics={
                        "view_count": int(stats.get("viewCount") or 0),
                        "like_count": int(stats.get("likeCount") or 0),
                        "comment_count": int(stats.get("commentCount") or 0),
                        "quota_units_spent": self.SEARCH_COST_UNITS + self.VIDEOS_COST_UNITS,
                        "platform": "youtube",
                    },
                    raw_bytes=raw_bytes,
                    raw_extension="json",
                    http_status=videos_resp.http_status,
                    extra_fields={
                        "title": title,
                        "symbols": _detect_tw_symbols(combined_text),
                    },
                    forbidden_literals=forbidden,
                    require_hashed_author=True,
                )
            )
        return items


DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_MODEL_ENV = "COLLECTOR_GEMINI_MODEL"


def gemini_endpoint_url(model: str | None = None) -> str:
    """Model comes from the argument, then $COLLECTOR_GEMINI_MODEL, then the default."""
    name = (model or os.environ.get(GEMINI_MODEL_ENV) or DEFAULT_GEMINI_MODEL).strip()
    if not re.fullmatch(r"[A-Za-z0-9._-]+", name):
        raise CollectorConfigError(f"invalid Gemini model name: {name!r}")
    return f"https://generativelanguage.googleapis.com/v1beta/models/{name}:generateContent"


class GeminiVideoNotesPoller:
    """Gemini API video understanding poller for public YouTube finance URLs (SD §5.3: <= 8h/day)."""

    source_id = "collector-youtube-gemini-notes"
    DAILY_VIDEO_SECONDS_LIMIT = 8 * 3600

    def __init__(
        self,
        policy: SourceFetchPolicy,
        *,
        transport: FetchTransport,
        state_store: CollectorStateStore | None = None,
        contact_user_agent: str = "PantheonCollector/1.0 (+mailto:collector-ops@example.invalid)",
    ) -> None:
        self.policy = policy
        self.fetcher = PolicyEnforcedFetcher(
            policy, transport=transport, contact_user_agent=contact_user_agent
        )
        self.state_store = state_store

    def collect(
        self,
        *,
        video_id: str | None = None,
        youtube_url: str | None = None,
        duration_seconds: int = 900,
        endpoint_url: str | None = None,
        model: str | None = None,
        fetched_at: datetime | str | None = None,
    ) -> list[CollectedItem]:
        fetched_iso = fetched_at if isinstance(fetched_at, str) else utc_iso(fetched_at)
        if endpoint_url is None:
            endpoint_url = gemini_endpoint_url(model)
        if not video_id and not youtube_url:
            # Nothing queued: never invent a video. Videos are supplied by the caller.
            return []
        video_id = video_id or "unknown"
        date_str = fetched_iso[:10]
        yt_url = youtube_url or f"https://www.youtube.com/watch?v={video_id}"

        if self.state_store is not None:
            used_sec = self.state_store.get_daily_quota_usage("gemini_video_seconds", date_str)
            if used_sec + duration_seconds > self.DAILY_VIDEO_SECONDS_LIMIT:
                raise CollectorConfigError(
                    f"Gemini video understanding daily limit ({self.DAILY_VIDEO_SECONDS_LIMIT}s) exceeded"
                )
            self.state_store.add_daily_quota_usage(
                "gemini_video_seconds", date_str, duration_seconds
            )

        request_body = json.dumps(
            {
                "contents": [
                    {
                        "parts": [
                            {"file_data": {"file_uri": yt_url, "mime_type": "video/*"}},
                            {
                                "text": "Summarize quantitative trading hypotheses, cited indicators, and Taiwan stock symbols mentioned in this video."
                            },
                        ]
                    }
                ]
            }
        ).encode("utf-8")
        response = self.fetcher.fetch(
            endpoint_url,
            method="POST",
            headers={"Content-Type": "application/json", **_api_key_headers("GEMINI_API_KEY")},
            body=request_body,
        )
        payload = json.loads(response.text)
        candidates = payload.get("candidates") or []
        summary_text = ""
        if candidates:
            parts = ((candidates[0].get("content") or {}).get("parts")) or []
            if parts:
                summary_text = str(parts[0].get("text") or "").strip()
        if not summary_text:
            summary_text = str(payload.get("summary") or "").strip()

        published_at = str(payload.get("published_at") or fetched_iso)
        return [
            CollectedItem(
                item_id=f"gemini-video-note-{video_id}",
                url=yt_url,
                fetched_at=fetched_iso,
                published_at=published_at,
                text=summary_text,
                lang="zh-TW",
                hashed_author=None,
                metrics={
                    "video_duration_seconds": duration_seconds,
                    "origin": "video",
                },
                raw_bytes=response.body,
                raw_extension="json",
                http_status=response.http_status,
                extra_fields={
                    "title": f"Gemini Video Note {video_id}",
                    "symbols": _detect_tw_symbols(summary_text),
                },
            )
        ]


class DoclingLibraryPoller:
    """Operator-supplied book library parser using Docling (SD §5.4, §8.7)."""

    source_id = "collector-library-books"
    ALLOWED_EXTENSIONS = frozenset({".pdf", ".epub", ".md"})

    def __init__(
        self,
        policy: SourceFetchPolicy,
        *,
        library_dir: Path | str,
        docling_adapter: DoclingBookAdapter | None = None,
    ) -> None:
        self.policy = policy
        self.library_dir = Path(library_dir)
        self.adapter = docling_adapter or DoclingBookAdapter()

    def collect(
        self,
        *,
        book_files: Sequence[Path | str] | None = None,
        fetched_at: datetime | str | None = None,
    ) -> list[CollectedItem]:
        fetched_iso = fetched_at if isinstance(fetched_at, str) else utc_iso(fetched_at)
        targets: list[Path] = []
        if book_files is not None:
            for bf in book_files:
                bf_str = str(bf)
                if "://" in bf_str and not bf_str.startswith("file://"):
                    raise CollectorConfigError(
                        f"collector-library-books forbids remote downloads ({bf_str!r}); only local operator-supplied files are allowed"
                    )
                p = Path(bf_str.removeprefix("file://"))
                targets.append(p if p.is_absolute() else (self.library_dir / p))
        elif self.library_dir.is_dir():
            targets = [
                p
                for p in sorted(self.library_dir.iterdir())
                if p.is_file() and p.suffix.lower() in self.ALLOWED_EXTENSIONS
            ]

        items: list[CollectedItem] = []
        for path in targets:
            self.policy.validate_target_url(f"file://{path.resolve()}")
            raw_bytes, sections = self.adapter.parse_book_file(path)
            ext = path.suffix.lstrip(".").lower() or "pdf"
            for sec in sections:
                sec_payload = {
                    "book_id": sec.book_id,
                    "section_index": sec.section_index,
                    "section_title": sec.section_title,
                    "page_start": sec.page_start,
                    "page_end": sec.page_end,
                    "text": sec.text,
                }
                items.append(
                    CollectedItem(
                        item_id=f"book-{sec.book_id}-sec-{sec.section_index:02d}",
                        url=f"file:///data/library/books/{path.name}#page={sec.page_start}-{sec.page_end}",
                        fetched_at=fetched_iso,
                        published_at=fetched_iso,
                        text=sec.text,
                        lang="en",
                        hashed_author=None,
                        metrics={
                            "page_start": sec.page_start,
                            "page_end": sec.page_end,
                            "section_index": sec.section_index,
                            "section_title": sec.section_title,
                            "origin": "book",
                            "redistribution_allowed": False,
                        },
                        raw_bytes=raw_bytes if len(sections) == 1 else json.dumps(sec_payload, sort_keys=True, ensure_ascii=False).encode("utf-8"),
                        raw_extension=ext if len(sections) == 1 else "json",
                        http_status=200,
                        extra_fields={"title": f"{sec.book_id}: {sec.section_title}"},
                    )
                )
        return items
