"""Pure analysis over normalised markets. No network, no Hermes imports."""

from __future__ import annotations

from typing import Any, Optional

DEFAULTS: dict[str, float] = {
    "min_volume_24h": 5000.0,   # ignore markets quieter than this (USD, last 24h)
    "move_pts": 8.0,            # a "big move" is at least this many points in 24h
    "wide_spread": 0.05,        # bid/ask gap (0.05 = 5 points) counted as wide
    "spike_ratio": 3.0,         # 24h volume vs the 7-day daily average
    "closing_hours": 72.0,      # "closing soon" window
    "band_low": 0.25,           # "still undecided" price band
    "band_high": 0.75,
}


def settings(overrides: Optional[dict] = None) -> dict[str, float]:
    cfg = dict(DEFAULTS)
    for key, value in (overrides or {}).items():
        if key in cfg:
            try:
                cfg[key] = float(value)
            except (TypeError, ValueError):
                pass
    return cfg


# --------------------------------------------------------------------------- #
# Formatting helpers
# --------------------------------------------------------------------------- #

def money(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    if value >= 1_000_000:
        return f"${value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"${value / 1_000:.1f}K"
    return f"${value:.0f}"


def pct(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    p = value * 100
    return f"{p:.1f}%" if p < 10 or p > 90 else f"{p:.0f}%"


def pts(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    return f"{value * 100:+.1f} pts"


def ends_in(hours: Optional[float]) -> str:
    if hours is None:
        return "no end date"
    if hours <= 0:
        return "past end date"
    if hours < 48:
        return f"{hours:.0f}h"
    days = hours / 24
    if days < 21:
        return f"{days:.0f}d"
    return f"{days / 7:.0f}w"


def summary(m: dict) -> dict:
    """The compact shape returned to the model and the chat."""
    return {
        "question": m["question"],
        "slug": m["slug"],
        "yes_price": None if m["yes"] is None else round(m["yes"], 4),
        "yes": pct(m["yes"]),
        "change_24h_pts": None if m["change_1d"] is None else round(m["change_1d"] * 100, 1),
        "volume_24h": money(m["volume_24h"]),
        "liquidity": money(m["liquidity"]),
        "ends_in": ends_in(m["hours_to_end"]),
        "url": m["url"],
    }


# --------------------------------------------------------------------------- #
# Rankings
# --------------------------------------------------------------------------- #

def movers(markets: list[dict], cfg: dict, limit: int) -> list[dict]:
    pool = [m for m in markets
            if m["change_1d"] is not None and m["volume_24h"] >= cfg["min_volume_24h"]]
    pool.sort(key=lambda m: abs(m["change_1d"]), reverse=True)
    return pool[:limit]


def closing_soon(markets: list[dict], cfg: dict, limit: int,
                 hours: Optional[float] = None) -> list[dict]:
    window = hours if hours is not None else cfg["closing_hours"]
    pool = [m for m in markets
            if m["hours_to_end"] is not None and 0 < m["hours_to_end"] <= window
            and m["volume_24h"] >= cfg["min_volume_24h"]]
    pool.sort(key=lambda m: (m["hours_to_end"], -m["volume_24h"]))
    return pool[:limit]


def compute_signals(m: dict, cfg: dict) -> list[dict]:
    """Heuristic attention flags. These are prompts to look closer, not predictions."""
    out: list[dict] = []
    vol24 = m["volume_24h"]
    active = vol24 >= cfg["min_volume_24h"]

    d1 = m["change_1d"]
    if d1 is not None and active and abs(d1) * 100 >= cfg["move_pts"]:
        out.append({
            "type": "big_move",
            "weight": 2.0 * min(abs(d1) * 100 / cfg["move_pts"], 3.0),
            "detail": f"moved {pts(d1)} in 24h on {money(vol24)} of volume",
        })

    v1w = m["volume_1w"]
    if v1w and v1w > 0 and active:
        ratio = vol24 / (v1w / 7.0)
        if ratio >= cfg["spike_ratio"]:
            out.append({
                "type": "volume_spike",
                "weight": 1.5 * min(ratio / cfg["spike_ratio"], 3.0),
                "detail": f"24h volume is {ratio:.1f}x its recent daily average",
            })

    spread = m["spread"]
    if spread is not None and spread >= cfg["wide_spread"] and m["liquidity"] >= 1000:
        out.append({
            "type": "wide_spread",
            "weight": 1.0,
            "detail": f"bid/ask gap is {spread * 100:.1f} pts, so the shown price may be unreliable",
        })

    if m["is_binary"] and len(m["prices"]) == 2 and all(p is not None for p in m["prices"]):
        total = sum(m["prices"])
        if abs(total - 1.0) >= 0.03:
            out.append({
                "type": "price_sum_gap",
                "weight": 1.0,
                "detail": f"outcome prices add up to {total * 100:.0f}% instead of 100%",
            })

    h = m["hours_to_end"]
    if (h is not None and 0 < h <= cfg["closing_hours"] and m["yes"] is not None
            and cfg["band_low"] <= m["yes"] <= cfg["band_high"] and active):
        out.append({
            "type": "closing_undecided",
            "weight": 2.0,
            "detail": f"resolves in {ends_in(h)} and is still priced at {pct(m['yes'])}",
        })
    return out


def rank_signals(markets: list[dict], cfg: dict, limit: int) -> list[dict]:
    ranked = []
    for m in markets:
        signals = compute_signals(m, cfg)
        if signals:
            ranked.append({
                "score": round(sum(s["weight"] for s in signals), 2),
                "market": m,
                "signals": signals,
            })
    ranked.sort(key=lambda r: r["score"], reverse=True)
    return ranked[:limit]


# --------------------------------------------------------------------------- #
# Price history
# --------------------------------------------------------------------------- #

def history_stats(history: list[tuple[int, float]]) -> Optional[dict]:
    """Summarise a (timestamp, price) series: range, 24h change, overall change."""
    if len(history) < 2:
        return None
    history = sorted(history)
    last_t, last_p = history[-1]
    first_t, first_p = history[0]
    prices = [p for _, p in history]

    day_ago = last_t - 86400
    base = first_p
    for t, p in history:
        if t <= day_ago:
            base = p
        else:
            break

    change_total = last_p - first_p
    if abs(change_total) < 0.02:
        trend = "flat"
    else:
        trend = "up" if change_total > 0 else "down"
    return {
        "points": len(history),
        "start": round(first_p, 4),
        "latest": round(last_p, 4),
        "high": round(max(prices), 4),
        "low": round(min(prices), 4),
        "change_24h_pts": round((last_p - base) * 100, 1),
        "change_period_pts": round(change_total * 100, 1),
        "trend": trend,
    }


# --------------------------------------------------------------------------- #
# Watchlist rules
# --------------------------------------------------------------------------- #

def evaluate_watch(item: dict, m: dict) -> dict[str, str]:
    """Return {rule_key: human text} for every rule on `item` that is true right now."""
    hit: dict[str, str] = {}
    yes = m["yes"]
    above, below, move = item.get("above"), item.get("below"), item.get("move")
    if above is not None and yes is not None and yes >= above:
        hit["above"] = f"price {pct(yes)} is at or above your {pct(above)} line"
    if below is not None and yes is not None and yes <= below:
        hit["below"] = f"price {pct(yes)} is at or below your {pct(below)} line"
    d1 = m["change_1d"]
    if move is not None and d1 is not None and abs(d1) * 100 >= move:
        hit["move"] = f"moved {pts(d1)} in 24h (your trigger is {move:g} pts)"
    return hit
