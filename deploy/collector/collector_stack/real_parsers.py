"""Parsers for the markup the real sites actually return (captured 2026-11-10).

Each parser returns ``(records, skipped)``: malformed or incomplete entries are skipped and counted,
never allowed to abort the run.
"""

from __future__ import annotations

import html
import json
import re
from typing import Any

_TAG = re.compile(r"<[^>]+>")


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(_TAG.sub(" ", fragment))).strip()


# ---------------------------------------------------------------- PTT
_PTT_ENTRY = re.compile(r'<div class="r-ent">(.*?)(?=<div class="r-ent">|<div class="r-list-sep">|$)', re.S)
_PTT_HREF = re.compile(r'<div class="title">\s*<a href="([^"]+)">(.*?)</a>', re.S)
_PTT_AUTHOR = re.compile(r'<div class="author">([^<]*)</div>')
_PTT_PUSH = re.compile(r'<div class="nrec">(?:<span[^>]*>([^<]*)</span>)?</div>')
_PTT_DATE = re.compile(r'<div class="date">\s*([^<]*?)\s*</div>')


def parse_ptt_index(page: str) -> tuple[list[dict[str, Any]], int]:
    records: list[dict[str, Any]] = []
    skipped = 0
    seen_ids: set[str] = set()
    for match in _PTT_ENTRY.finditer(page):
        block = match.group(1)
        href = _PTT_HREF.search(block)
        author = _PTT_AUTHOR.search(block)
        author_id = author.group(1).strip() if author else ""
        if not href or not author_id or author_id == "-":
            skipped += 1  # deleted post or missing author
            continue
        article_id = href.group(1).rsplit("/", 1)[-1].removesuffix(".html")
        if article_id in seen_ids:
            continue  # the real index lists pinned posts twice; keep the first occurrence
        seen_ids.add(article_id)
        push_raw = (_PTT_PUSH.search(block) or [None, ""])[1] or ""
        push = int(push_raw) if push_raw.isdigit() else (100 if push_raw == "爆" else 0)
        date = _PTT_DATE.search(block)
        records.append(
            {
                "article_id": article_id,
                "href": href.group(1),
                "title": _text(href.group(2)),
                "author": author_id,
                "push_count": push,
                "date": date.group(1) if date else "",
            }
        )
    return records, skipped


# ---------------------------------------------------------------- Threads
_THREADS_POST = re.compile(r'href="/@([A-Za-z0-9._]+)/post/([A-Za-z0-9_-]+)"[^>]*>\s*<time[^>]*datetime="([^"]+)"')
_THREADS_TEXT = re.compile(r'<div lang="[^"]*"[^>]*>(.*?)</div></div>', re.S)


def parse_threads_search(page: str) -> tuple[list[dict[str, Any]], int]:
    records: list[dict[str, Any]] = []
    skipped = 0
    matches = list(_THREADS_POST.finditer(page))
    for idx, m in enumerate(matches):
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(page)
        seg = page[m.end() : end]
        tm = _THREADS_TEXT.search(seg)
        body = _text(tm.group(1)) if tm else ""
        if not body:
            skipped += 1
            continue
        records.append(
            {"post_id": m.group(2), "handle": m.group(1), "published_at": m.group(3), "text": body}
        )
    return records, skipped


# ---------------------------------------------------------------- Instagram
_IG_MARKER = '"xig_logged_out_popular_search_media_info":'


def parse_instagram_tag(page: str) -> tuple[list[dict[str, Any]], int]:
    start = page.find(_IG_MARKER)
    if start < 0:
        return [], 0
    try:
        blob, _ = json.JSONDecoder().raw_decode(page[start + len(_IG_MARKER) :])
    except ValueError:
        return [], 1
    records: list[dict[str, Any]] = []
    skipped = 0
    for edge in blob.get("edges") or []:
        node = (edge or {}).get("node") or {}
        code = node.get("code")
        username = (node.get("user") or {}).get("username")
        caption = (node.get("caption") or {}).get("text")
        if not (code and username and caption):
            skipped += 1
            continue
        records.append(
            {
                "post_id": str(code),
                "handle": str(username),
                "text": str(caption),
                "play_count": int(node.get("play_count") or 0),
            }
        )
    return records, skipped


# ---------------------------------------------------------------- CMoney
_CM_ARTICLE = re.compile(r'<article[^>]*\bid="art(\d+)"[^>]*class="articleContent"[^>]*>(.*?)(?=<article[^>]*\bid="art|$)', re.S)
_CM_USER = re.compile(r'href="/forum/user/(\d+)"[^>]*normal__text[^>]*>\s*<span[^>]*>(.*?)</span>', re.S)
_CM_LINE = re.compile(r'class="textRule__line"[^>]*><span[^>]*>(.*?)</span>', re.S)
_CM_STANCE = re.compile(r"stockTagLabel__stance--(bull|bear)")


def parse_cmoney_forum(page: str) -> tuple[list[dict[str, Any]], int]:
    records: list[dict[str, Any]] = []
    skipped = 0
    for m in _CM_ARTICLE.finditer(page):
        art_id, block = m.group(1), m.group(2)
        user = _CM_USER.search(block)
        lines = [_text(x) for x in _CM_LINE.findall(block)]
        body = "\n".join(x for x in lines if x)
        if not user or not body:
            skipped += 1
            continue
        stance = _CM_STANCE.search(block)
        records.append(
            {
                "article_id": art_id,
                "member_id": user.group(1),
                "display_name": _text(user.group(2)),
                "text": body,
                "sentiment": {"bull": "bullish", "bear": "bearish"}.get(stance.group(1), "neutral") if stance else "neutral",
            }
        )
    return records, skipped
