"""Watchlist rules stored in the plugin's own state (ctx.state). No Hermes imports."""

from __future__ import annotations

from typing import Any, Callable, Optional

from . import analysis

KEY = "watchlist"
MAX_ITEMS = 40


def load(state: Any) -> list[dict]:
    try:
        items = state.get(KEY, default=[])
    except Exception:
        return []
    if not isinstance(items, list):
        return []
    return [i for i in items if isinstance(i, dict) and i.get("slug")]


def save(state: Any, items: list[dict]) -> None:
    state.set(KEY, items)


def _prob(value: Any) -> Optional[float]:
    """Accept 0.7 or 70 (meaning 70%); return a 0-1 probability or None."""
    if value is None or value == "":
        return None
    v = float(value)
    if v > 1:
        v = v / 100.0
    if not 0 < v < 1:
        raise ValueError("price lines must be between 0 and 100 percent")
    return v


def add_rule(items: list[dict], market: dict, above: Any = None, below: Any = None,
             move: Any = None) -> tuple[list[dict], str]:
    above_p, below_p = _prob(above), _prob(below)
    move_v = None
    if move not in (None, ""):
        move_v = float(move)
        if move_v <= 0:
            raise ValueError("move trigger must be a positive number of points")
    if above_p is None and below_p is None and move_v is None:
        # Sensible default so "watch this" with no numbers still does something.
        move_v = 5.0

    items = [i for i in items if i["slug"] != market["slug"]]
    if len(items) >= MAX_ITEMS:
        raise ValueError(f"watchlist is full ({MAX_ITEMS} markets); remove one first")
    items.append({
        "slug": market["slug"],
        "question": market["question"],
        "above": above_p,
        "below": below_p,
        "move": move_v,
        "triggered": [],
    })
    return items, f"Watching: {market['question']}"


def remove_rule(items: list[dict], ref: str) -> tuple[list[dict], bool]:
    ref = (ref or "").strip().lower()
    kept = [i for i in items
            if i["slug"].lower() != ref and ref not in i.get("question", "").lower()]
    return kept, len(kept) != len(items)


def describe(item: dict) -> str:
    rules = []
    if item.get("above") is not None:
        rules.append(f"alert at or above {analysis.pct(item['above'])}")
    if item.get("below") is not None:
        rules.append(f"alert at or below {analysis.pct(item['below'])}")
    if item.get("move") is not None:
        rules.append(f"alert on a {item['move']:g}+ pt move in 24h")
    return ", ".join(rules) or "no rules"


def run_check(items: list[dict],
              fetch: Callable[[str], Optional[dict]]) -> tuple[list[dict], list[dict], list[str], list[dict]]:
    """Evaluate every rule.

    Returns (new_alerts, active_conditions, errors, updated_items). A rule alerts
    once when it becomes true and re-arms when it stops being true, so a cron job
    running every few minutes does not repeat itself.
    """
    new_alerts: list[dict] = []
    active: list[dict] = []
    errors: list[str] = []
    updated: list[dict] = []
    for item in items:
        item = dict(item)
        try:
            market = fetch(item["slug"])
        except Exception as exc:  # network trouble for one market must not hide the rest
            errors.append(f"{item.get('question', item['slug'])}: {exc}")
            updated.append(item)
            continue
        if market is None:
            errors.append(f"{item.get('question', item['slug'])}: market not found (it may have resolved)")
            updated.append(item)
            continue
        hit = analysis.evaluate_watch(item, market)
        previously = set(item.get("triggered") or [])
        for key, text in hit.items():
            row = {"market": analysis.summary(market), "rule": key, "message": text}
            active.append(row)
            if key not in previously:
                new_alerts.append(row)
        item["triggered"] = sorted(hit)
        updated.append(item)
    return new_alerts, active, errors, updated
