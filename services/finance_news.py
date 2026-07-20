"""
Live finance / crypto news from free public RSS feeds (no API key).

Sources: CoinDesk, Cointelegraph, Yahoo Finance (BTC/markets), Fed press,
Reuters business (when available), CryptoPanic public (best-effort).
"""

from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from email.utils import parsedate_to_datetime
from html import unescape
from typing import Any, Dict, List, Optional, Tuple

import httpx

# Free RSS / Atom endpoints — no keys
_FEEDS: List[Tuple[str, str, str]] = [
    ("CoinDesk", "crypto", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("Cointelegraph", "crypto", "https://cointelegraph.com/rss"),
    ("Yahoo BTC", "crypto", "https://feeds.finance.yahoo.com/rss/2.0/headline?s=BTC-USD&region=US&lang=en-US"),
    ("Yahoo Markets", "markets", "https://feeds.finance.yahoo.com/rss/2.0/headline?s=%5EGSPC,%5EIXIC,%5EDJI&region=US&lang=en-US"),
    ("Fed Press", "macro", "https://www.federalreserve.gov/feeds/press_all.xml"),
    ("BBC Business", "markets", "https://feeds.bbci.co.uk/news/business/rss.xml"),
    ("CNBC Top", "markets", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114"),
]

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

# light in-process cache so SSE + UI don't hammer RSS
_CACHE: Dict[str, Any] = {"ts": 0.0, "items": [], "errors": []}
_CACHE_TTL = 45.0  # seconds


def _strip_html(text: str) -> str:
    if not text:
        return ""
    t = unescape(_TAG_RE.sub(" ", text))
    return _WS_RE.sub(" ", t).strip()


def _parse_date(raw: Optional[str]) -> Optional[float]:
    if not raw:
        return None
    try:
        return parsedate_to_datetime(raw).timestamp()
    except Exception:
        pass
    # ISO-ish
    try:
        from datetime import datetime

        return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _local(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag


def _text(el: Optional[ET.Element]) -> str:
    if el is None:
        return ""
    return (el.text or "").strip() or "".join(el.itertext()).strip()


def _parse_rss_or_atom(xml_bytes: bytes, source: str, category: str) -> List[Dict[str, Any]]:
    root = ET.fromstring(xml_bytes)
    items: List[Dict[str, Any]] = []

    # RSS 2.0
    for item in root.iter():
        if _local(item.tag) != "item":
            continue
        title = link = desc = pub = ""
        for child in item:
            name = _local(child.tag)
            if name == "title":
                title = _text(child)
            elif name == "link":
                link = _text(child) or (child.get("href") or "")
            elif name in ("description", "summary", "content"):
                desc = _text(child)
            elif name in ("pubDate", "published", "updated", "date"):
                pub = _text(child)
        if not title:
            continue
        items.append(
            {
                "title": _strip_html(title)[:280],
                "url": link.strip(),
                "summary": _strip_html(desc)[:420],
                "source": source,
                "category": category,
                "published_at": _parse_date(pub),
                "published": pub or None,
            }
        )

    # Atom entries
    if not items:
        for entry in root.iter():
            if _local(entry.tag) != "entry":
                continue
            title = summary = pub = ""
            link = ""
            for child in entry:
                name = _local(child.tag)
                if name == "title":
                    title = _text(child)
                elif name == "link":
                    href = child.get("href") or _text(child)
                    if href and (child.get("rel") in (None, "alternate") or not link):
                        link = href
                elif name in ("summary", "content"):
                    summary = _text(child)
                elif name in ("published", "updated"):
                    pub = _text(child)
            if not title:
                continue
            items.append(
                {
                    "title": _strip_html(title)[:280],
                    "url": link.strip(),
                    "summary": _strip_html(summary)[:420],
                    "source": source,
                    "category": category,
                    "published_at": _parse_date(pub),
                    "published": pub or None,
                }
            )
    return items


def _fetch_feed(source: str, category: str, url: str, timeout: float = 6.0) -> List[Dict[str, Any]]:
    headers = {
        "User-Agent": "SniperTrades-News/6.2 (local; free RSS)",
        "Accept": "application/rss+xml, application/xml, text/xml, */*",
    }
    with httpx.Client(timeout=timeout, headers=headers, follow_redirects=True) as client:
        r = client.get(url)
        r.raise_for_status()
        return _parse_rss_or_atom(r.content, source, category)


def get_news(
    limit: int = 30,
    category: Optional[str] = None,
    force: bool = False,
) -> Dict[str, Any]:
    """
    Aggregate free finance/crypto headlines.
    category: crypto | markets | macro | None (all)
    """
    now = time.time()
    if not force and _CACHE["items"] and (now - float(_CACHE["ts"])) < _CACHE_TTL:
        items = list(_CACHE["items"])
        errors = list(_CACHE["errors"])
    else:
        items = []
        errors: List[str] = []
        # RSS feeds are independent; parallel fetch keeps the dashboard bounded by
        # the slowest feed instead of the sum of every provider timeout.
        with ThreadPoolExecutor(max_workers=len(_FEEDS)) as pool:
            futures = {
                pool.submit(_fetch_feed, source, cat, url): source
                for source, cat, url in _FEEDS
            }
            for future in as_completed(futures):
                source = futures[future]
                try:
                    items.extend(future.result())
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{source}: {exc}")
        # de-dupe by title
        seen = set()
        uniq: List[Dict[str, Any]] = []
        for it in items:
            key = it["title"].lower()
            if key in seen:
                continue
            seen.add(key)
            uniq.append(it)
        uniq.sort(key=lambda x: x.get("published_at") or 0, reverse=True)
        _CACHE["items"] = uniq
        _CACHE["errors"] = errors
        _CACHE["ts"] = now
        items = uniq
        errors = list(_CACHE["errors"])

    if category:
        cat = category.lower().strip()
        items = [i for i in items if i.get("category") == cat]

    limit = max(1, min(int(limit), 80))
    sliced = items[:limit]
    return {
        "source": "free_rss",
        "count": len(sliced),
        "updated_at": _CACHE["ts"] or now,
        "items": sliced,
        "feeds": [{"name": n, "category": c, "url": u} for n, c, u in _FEEDS],
        "errors": errors[:6],
    }


def list_news_sources() -> Dict[str, Any]:
    return {
        "feeds": [{"name": n, "category": c, "url": u} for n, c, u in _FEEDS],
        "auth": "none",
        "note": "Public RSS — no API keys required",
    }
