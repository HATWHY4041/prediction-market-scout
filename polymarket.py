"""Read-only Polymarket client.

Talks to Polymarket's public, keyless endpoints only (Gamma for market metadata,
CLOB for price history). Standard library only. Never places orders, never needs
a key, never touches a wallet.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Optional

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"

# The only hosts this plugin will ever contact.
_ALLOWED_HOSTS = frozenset({"gamma-api.polymarket.com", "clob.polymarket.com"})
_USER_AGENT = "hermes-prediction-market-scout/1.0"
_MAX_BYTES = 4_000_000
_CACHE_TTL = 30.0
_CACHE_MAX = 64

_cache: dict[str, tuple[float, Any]] = {}
_cache_lock = threading.Lock()


class PolymarketError(Exception):
    """Raised for any network or data problem; the message is safe to show a user."""


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #

def _fmt_param(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _get_json(base: str, path: str, params: Optional[dict] = None,
              timeout: float = 10.0, ttl: float = _CACHE_TTL) -> Any:
    query = urllib.parse.urlencode(
        {k: _fmt_param(v) for k, v in (params or {}).items() if v is not None}
    )
    url = f"{base}{path}" + (f"?{query}" if query else "")
    if urllib.parse.urlparse(url).hostname not in _ALLOWED_HOSTS:
        raise PolymarketError("blocked: host is not on the allowlist")

    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(url)
        if hit and now - hit[0] < ttl:
            return hit[1]

    for attempt in (0, 1):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": _USER_AGENT, "Accept": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read(_MAX_BYTES + 1)
            if len(raw) > _MAX_BYTES:
                raise PolymarketError("Polymarket response was too large")
            data = json.loads(raw.decode("utf-8"))
            with _cache_lock:
                _cache[url] = (now, data)
                while len(_cache) > _CACHE_MAX:
                    _cache.pop(next(iter(_cache)))
            return data
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 500, 502, 503, 504) and attempt == 0:
                time.sleep(0.8)
                continue
            raise PolymarketError(f"Polymarket returned HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            if attempt == 0:
                time.sleep(0.5)
                continue
            reason = getattr(exc, "reason", exc)
            raise PolymarketError(f"could not reach Polymarket ({reason})") from exc
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise PolymarketError("Polymarket sent data that could not be read") from exc
    raise PolymarketError("Polymarket request failed")  # pragma: no cover


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


# --------------------------------------------------------------------------- #
# Normalisation
# --------------------------------------------------------------------------- #

def _jlist(value: Any) -> list:
    """Gamma returns some arrays as JSON-encoded strings; accept either form."""
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except ValueError:
            return []
    return []


def _num(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out else None  # drop NaN


def _parse_dt(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def normalize_market(raw: dict, event: Optional[dict] = None,
                     now: Optional[datetime] = None) -> Optional[dict]:
    """Flatten a Gamma market into the few fields the analysis needs."""
    if not isinstance(raw, dict) or not raw.get("question"):
        return None
    now = now or datetime.now(timezone.utc)

    outcomes = [str(o) for o in _jlist(raw.get("outcomes"))]
    prices = [_num(p) for p in _jlist(raw.get("outcomePrices"))]
    tokens = [str(t) for t in _jlist(raw.get("clobTokenIds"))]

    yes_idx = 0
    for i, name in enumerate(outcomes):
        if name.strip().lower() == "yes":
            yes_idx = i
            break
    yes = prices[yes_idx] if yes_idx < len(prices) else None
    if yes is None:
        yes = _num(raw.get("lastTradePrice"))

    if event is None:
        events = raw.get("events")
        if isinstance(events, list) and events and isinstance(events[0], dict):
            event = events[0]

    slug = raw.get("slug") or ""
    if event and event.get("slug"):
        url = f"https://polymarket.com/event/{event['slug']}"
    elif slug:
        url = f"https://polymarket.com/market/{slug}"
    else:
        url = ""

    end = _parse_dt(raw.get("endDate") or raw.get("endDateIso"))
    hours_to_end = (end - now).total_seconds() / 3600 if end else None

    volume24 = _num(raw.get("volume24hr"))
    if volume24 is None:
        volume24 = _num(raw.get("volume24hrClob"))

    return {
        "id": str(raw.get("id") or ""),
        "slug": slug,
        "question": str(raw["question"]),
        "url": url,
        "outcomes": outcomes,
        "prices": prices,
        "token_ids": tokens,
        "yes_index": yes_idx,
        "yes": yes,
        "is_binary": len(outcomes) == 2,
        "volume_24h": volume24 or 0.0,
        "volume_1w": _num(raw.get("volume1wk")),
        "volume_total": _num(raw.get("volumeNum")) or _num(raw.get("volume")) or 0.0,
        "liquidity": _num(raw.get("liquidityNum")) or _num(raw.get("liquidity")) or 0.0,
        "change_1d": _num(raw.get("oneDayPriceChange")),
        "change_1w": _num(raw.get("oneWeekPriceChange")),
        "best_bid": _num(raw.get("bestBid")),
        "best_ask": _num(raw.get("bestAsk")),
        "spread": _num(raw.get("spread")),
        "end_date": end.isoformat() if end else None,
        "hours_to_end": hours_to_end,
        "closed": bool(raw.get("closed")),
        "active": raw.get("active") is not False,
    }


def _normalize_all(rows: Any, now: Optional[datetime] = None) -> list[dict]:
    out = []
    if not isinstance(rows, list):
        return out
    for raw in rows:
        market = normalize_market(raw, now=now)
        if market and not market["closed"] and market["active"]:
            out.append(market)
    return out


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #

def list_markets(order: str = "volume24hr", ascending: bool = False, limit: int = 50,
                 volume_num_min: Optional[float] = None,
                 end_date_min: Optional[str] = None,
                 end_date_max: Optional[str] = None,
                 now: Optional[datetime] = None) -> list[dict]:
    data = _get_json(GAMMA, "/markets", {
        "closed": False, "order": order, "ascending": ascending,
        "limit": max(1, min(int(limit), 100)),
        "volume_num_min": volume_num_min,
        "end_date_min": end_date_min, "end_date_max": end_date_max,
    })
    return _normalize_all(data, now)


def search_markets(query: str, limit: int = 10,
                   now: Optional[datetime] = None) -> list[dict]:
    data = _get_json(GAMMA, "/public-search", {
        "q": query, "limit_per_type": max(1, min(int(limit), 20)),
    })
    events = data.get("events") if isinstance(data, dict) else None
    out: list[dict] = []
    seen: set[str] = set()
    for event in events or []:
        if not isinstance(event, dict):
            continue
        for raw in event.get("markets") or []:
            market = normalize_market(raw, event=event, now=now)
            if market and not market["closed"] and market["slug"] not in seen:
                seen.add(market["slug"])
                out.append(market)
    out.sort(key=lambda m: m["volume_24h"], reverse=True)
    return out[: max(1, int(limit))]


def get_market(ref: str, now: Optional[datetime] = None) -> Optional[dict]:
    """Look a market up by slug, numeric id, or a polymarket.com URL."""
    ref = (ref or "").strip()
    if not ref:
        return None
    if ref.startswith("http"):
        path = urllib.parse.urlparse(ref).path.rstrip("/")
        ref = path.rsplit("/", 1)[-1]
    param = "id" if ref.isdigit() else "slug"
    data = _get_json(GAMMA, "/markets", {param: ref})
    if isinstance(data, list) and data:
        return normalize_market(data[0], now=now)
    return None


def price_history(token_id: str, interval: str = "1w",
                  fidelity: int = 60) -> list[tuple[int, float]]:
    data = _get_json(CLOB, "/prices-history", {
        "market": token_id, "interval": interval, "fidelity": int(fidelity),
    }, ttl=60.0)
    rows = data.get("history") if isinstance(data, dict) else None
    out: list[tuple[int, float]] = []
    for row in rows or []:
        if isinstance(row, dict):
            t, p = _num(row.get("t")), _num(row.get("p"))
            if t is not None and p is not None:
                out.append((int(t), p))
    return out
