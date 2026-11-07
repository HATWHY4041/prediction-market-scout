"""Tool handlers and the /pm slash command. Everything here is read-only."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from . import analysis, polymarket, render, watchlist
from .polymarket import PolymarketError

_ISO = "%Y-%m-%dT%H:%M:%SZ"

HELP = """**/pm — Prediction Market Scout** (Polymarket, read-only)

`/pm` or `/pm brief` — full briefing
`/pm trending` · `/pm movers` · `/pm closing` · `/pm signals`
`/pm search <words>` — find markets
`/pm market <slug or link>` — deep dive
`/pm watch add <slug or link> [above 70] [below 20] [move 10]`
`/pm watch list` · `/pm watch check` · `/pm watch remove <slug>`"""


def _clamp(value: Any, lo: int, hi: int, default: int) -> int:
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return default


class Scout:
    """Holds the plugin context so handlers can read settings and state."""

    def __init__(self, ctx: Any):
        self.ctx = ctx

    # ---- settings ---------------------------------------------------------- #

    def cfg(self) -> dict:
        overrides = {}
        for key in analysis.DEFAULTS:
            try:
                value = self.ctx.get_config(key, default=None)
            except Exception:
                value = None
            if value is not None:
                overrides[key] = value
        return analysis.settings(overrides)

    def default_limit(self) -> int:
        try:
            return _clamp(self.ctx.get_config("default_limit", default=5), 1, 15, 5)
        except Exception:
            return 5

    # ---- data (returns normalised markets; wrappers shape output) ---------- #

    def pool(self) -> list[dict]:
        return polymarket.list_markets(limit=100)

    def scan(self, mode: str, limit: int, hours: Any = None) -> list[dict]:
        cfg = self.cfg()
        if mode == "movers":
            return analysis.movers(self.pool(), cfg, limit)
        if mode == "closing_soon":
            window = float(hours) if hours not in (None, "") else cfg["closing_hours"]
            window = max(1.0, min(window, 24 * 60))
            now = datetime.now(timezone.utc)
            markets = polymarket.list_markets(
                end_date_min=now.strftime(_ISO),
                end_date_max=(now + timedelta(hours=window)).strftime(_ISO),
                limit=100, now=now,
            )
            return analysis.closing_soon(markets, cfg, limit, hours=window)
        if mode == "trending":
            markets = polymarket.list_markets(limit=max(limit * 3, 30))
            return [m for m in markets if m["volume_24h"] >= cfg["min_volume_24h"]][:limit]
        raise ValueError("mode must be trending, movers or closing_soon")

    def check_watchlist(self) -> tuple[list[dict], list[dict], list[str]]:
        items = watchlist.load(self.ctx.state)
        if not items:
            return [], [], []
        new, active, errors, updated = watchlist.run_check(items, polymarket.get_market)
        watchlist.save(self.ctx.state, updated)
        return new, active, errors

    def build_brief(self, limit: int) -> dict:
        cfg = self.cfg()
        errors: list[str] = []
        try:
            alerts, _active, watch_errors = self.check_watchlist()
            errors.extend(watch_errors)
        except Exception as exc:
            alerts = []
            errors.append(f"watchlist check skipped ({exc})")
        pool = self.pool()
        return {
            "alerts": alerts,
            "movers": analysis.movers(pool, cfg, limit),
            "signals": analysis.rank_signals(pool, cfg, limit),
            "closing": analysis.closing_soon(pool, cfg, min(limit, 3)),
            "errors": errors,
        }

    # ---- tool handlers (take args dict, return dict) ----------------------- #

    def t_scan(self, args: dict) -> dict:
        mode = str(args.get("mode") or "trending")
        limit = _clamp(args.get("limit"), 1, 15, self.default_limit())
        markets = self.scan(mode, limit, args.get("hours"))
        return {"mode": mode, "count": len(markets),
                "markets": [analysis.summary(m) for m in markets]}

    def t_search(self, args: dict) -> dict:
        query = str(args.get("query") or "").strip()
        if not query:
            raise ValueError("query is required")
        limit = _clamp(args.get("limit"), 1, 15, self.default_limit())
        markets = polymarket.search_markets(query, limit)
        return {"query": query, "count": len(markets),
                "markets": [analysis.summary(m) for m in markets]}

    def market_detail(self, ref: str) -> dict:
        market = polymarket.get_market(ref)
        if market is None:
            raise ValueError(f"no Polymarket market found for {ref!r}; try polymarket_search")
        outcomes = [
            {"outcome": name, "price": None if i >= len(market["prices"]) or market["prices"][i] is None
             else round(market["prices"][i], 4)}
            for i, name in enumerate(market["outcomes"])
        ]
        history = None
        tokens = market["token_ids"]
        if tokens and market["yes_index"] < len(tokens):
            try:
                history = analysis.history_stats(
                    polymarket.price_history(tokens[market["yes_index"]], "1w", 60))
            except PolymarketError:
                history = None  # the rest of the answer is still useful
        detail = analysis.summary(market)
        detail.update({
            "outcomes": outcomes,
            "best_bid": market["best_bid"],
            "best_ask": market["best_ask"],
            "spread_pts": None if market["spread"] is None else round(market["spread"] * 100, 1),
            "volume_total": analysis.money(market["volume_total"]),
            "end_date": market["end_date"],
            "price_history_7d": history,
            "flags": [s["detail"] for s in analysis.compute_signals(market, self.cfg())],
        })
        return detail

    def t_market(self, args: dict) -> dict:
        ref = str(args.get("market") or "").strip()
        if not ref:
            raise ValueError("market is required")
        return self.market_detail(ref)

    def t_signals(self, args: dict) -> dict:
        limit = _clamp(args.get("limit"), 1, 15, self.default_limit())
        ranked = analysis.rank_signals(self.pool(), self.cfg(), limit)
        return {"count": len(ranked), "markets": [
            {**analysis.summary(r["market"]), "score": r["score"],
             "flags": [s["detail"] for s in r["signals"]]}
            for r in ranked]}

    def t_watch(self, args: dict) -> dict:
        action = str(args.get("action") or "list").lower()
        state = self.ctx.state
        items = watchlist.load(state)

        if action == "list":
            return {"count": len(items), "watching": [
                {"market": i["question"], "slug": i["slug"], "rules": watchlist.describe(i)}
                for i in items]}

        if action == "add":
            ref = str(args.get("market") or "").strip()
            if not ref:
                raise ValueError("market is required to add")
            market = polymarket.get_market(ref)
            if market is None:
                raise ValueError(f"no Polymarket market found for {ref!r}")
            items, message = watchlist.add_rule(
                items, market, args.get("above"), args.get("below"), args.get("move"))
            watchlist.save(state, items)
            rule = next(i for i in items if i["slug"] == market["slug"])
            return {"ok": True, "message": message, "rules": watchlist.describe(rule),
                    "current": analysis.summary(market)}

        if action == "remove":
            ref = str(args.get("market") or "").strip()
            items, removed = watchlist.remove_rule(items, ref)
            if removed:
                watchlist.save(state, items)
            return {"ok": removed,
                    "message": "Removed from watchlist." if removed else f"Nothing on the watchlist matches {ref!r}."}

        if action == "check":
            new, active, errors = self.check_watchlist()
            return {"watching": len(items), "new_alerts": new, "still_active": len(active) - len(new),
                    "errors": errors}

        raise ValueError("action must be add, remove, list or check")

    def t_brief(self, args: dict) -> dict:
        limit = _clamp(args.get("limit"), 1, 10, self.default_limit())
        data = self.build_brief(limit)
        return {"markdown": render.brief(data),
                "counts": {k: len(v) for k, v in data.items() if isinstance(v, list)}}

    # ---- slash command ----------------------------------------------------- #

    def command(self, raw_args: str) -> str:
        try:
            return self._command((raw_args or "").strip())
        except (PolymarketError, ValueError) as exc:
            return f"Could not do that: {exc}"
        except Exception as exc:
            return f"Something went wrong ({type(exc).__name__}). Please try again in a moment."

    def _command(self, text: str) -> str:
        parts = text.split()
        sub = parts[0].lower() if parts else "brief"
        rest = parts[1:]
        limit = self.default_limit()

        if sub in ("help", "?", "-h", "--help"):
            return HELP
        if sub == "brief":
            return render.brief(self.build_brief(limit))
        if sub == "trending":
            return render.market_list("Trending on Polymarket", self.scan("trending", limit))
        if sub in ("movers", "mover"):
            return render.market_list("Biggest 24h movers", self.scan("movers", limit))
        if sub in ("closing", "closing_soon", "soon"):
            return render.market_list("Closing soon", self.scan("closing_soon", limit))
        if sub in ("signals", "signal"):
            return render.signal_list("Worth a closer look", analysis.rank_signals(self.pool(), self.cfg(), limit))
        if sub == "search":
            if not rest:
                raise ValueError("usage: /pm search <words>")
            return render.market_list(f"Results for '{' '.join(rest)}'",
                                      polymarket.search_markets(" ".join(rest), limit))
        if sub == "market":
            if not rest:
                raise ValueError("usage: /pm market <slug or link>")
            return self._market_text(self.market_detail(rest[0]))
        if sub == "watch":
            return self._watch_command(rest)
        raise ValueError("unknown command. Try /pm help")

    def _market_text(self, d: dict) -> str:
        lines = [f"**{d['question']}**", d["url"], ""]
        lines.append("Odds: " + " · ".join(
            f"{o['outcome']} {analysis.pct(o['price'])}" for o in d["outcomes"]))
        lines.append(f"24h change: {d['change_24h_pts']} pts · volume 24h {d['volume_24h']} "
                     f"· total {d['volume_total']} · liquidity {d['liquidity']} · ends in {d['ends_in']}")
        hist = d.get("price_history_7d")
        if hist:
            lines.append(f"7 days: {hist['start']:.2f} → {hist['latest']:.2f} "
                         f"(high {hist['high']:.2f}, low {hist['low']:.2f}, trend {hist['trend']})")
        for flag in d["flags"]:
            lines.append(f"• {flag}")
        lines.append(f"_{render.DISCLAIMER}_")
        return "\n".join(lines)

    def _watch_command(self, rest: list[str]) -> str:
        action = rest[0].lower() if rest else "list"
        args: dict = {"action": action}
        if action in ("add", "remove") and len(rest) > 1:
            args["market"] = rest[1]
        if action == "add":
            pairs = rest[2:]
            for key, value in zip(pairs[::2], pairs[1::2]):
                if key.lower() in ("above", "below", "move"):
                    args[key.lower()] = value
        result = self.t_watch(args)
        if action == "list":
            if not result["watching"]:
                return "Your watchlist is empty. Add one with `/pm watch add <slug> above 70`."
            return "**Watchlist**\n" + "\n".join(
                f"{n}. {w['market']} — {w['rules']}" for n, w in enumerate(result["watching"], 1))
        if action == "check":
            return render.alert_list("Watchlist alerts", result["new_alerts"]) + (
                "\n" + "\n".join(f"_Note: {e}_" for e in result["errors"]) if result["errors"] else "")
        if action == "add":
            return f"{result['message']} — {result['rules']}."
        return result["message"]


# --------------------------------------------------------------------------- #

def _wrap(fn: Callable[[dict], dict]) -> Callable[..., str]:
    """Hermes tool handlers must return a JSON string and never raise."""
    def handler(args: dict, **kwargs: Any) -> str:
        try:
            return json.dumps(fn(args or {}), ensure_ascii=False)
        except (PolymarketError, ValueError) as exc:
            return json.dumps({"error": str(exc)})
        except Exception as exc:
            return json.dumps({"error": f"unexpected problem ({type(exc).__name__})"})
    handler.__name__ = getattr(fn, "__name__", "handler")
    return handler


def build_handlers(scout: Scout) -> dict[str, Callable[..., str]]:
    return {
        "polymarket_scan": _wrap(scout.t_scan),
        "polymarket_search": _wrap(scout.t_search),
        "polymarket_market": _wrap(scout.t_market),
        "polymarket_signals": _wrap(scout.t_signals),
        "polymarket_watch": _wrap(scout.t_watch),
        "polymarket_brief": _wrap(scout.t_brief),
    }
