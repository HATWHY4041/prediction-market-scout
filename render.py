"""Plain-text/markdown rendering for the /pm command and the daily brief."""

from __future__ import annotations

from typing import Any

from . import analysis

DISCLAIMER = ("Informational only, not financial advice. Prices are Polymarket's "
              "public data and can be stale or thin.")


def market_line(index: int, m: dict, extra: str = "") -> str:
    change = analysis.pts(m["change_1d"]) if m["change_1d"] is not None else "n/a"
    head = (f"{index}. **{m['question']}** — {analysis.pct(m['yes'])} "
            f"({change}) · {analysis.money(m['volume_24h'])} 24h · ends {analysis.ends_in(m['hours_to_end'])}")
    lines = [head]
    if extra:
        lines.append(f"   {extra}")
    if m["url"]:
        lines.append(f"   {m['url']}")
    return "\n".join(lines)


def market_list(title: str, markets: list[dict]) -> str:
    if not markets:
        return f"**{title}**\nNothing matched right now."
    body = "\n".join(market_line(i, m) for i, m in enumerate(markets, 1))
    return f"**{title}**\n{body}"


def signal_list(title: str, ranked: list[dict]) -> str:
    if not ranked:
        return f"**{title}**\nNo markets are flagging right now."
    out = []
    for i, row in enumerate(ranked, 1):
        why = "; ".join(s["detail"] for s in row["signals"])
        out.append(market_line(i, row["market"], extra=f"Why: {why}"))
    return f"**{title}**\n" + "\n".join(out)


def alert_list(title: str, alerts: list[dict]) -> str:
    if not alerts:
        return f"**{title}**\nNo watchlist alerts."
    out = []
    for i, row in enumerate(alerts, 1):
        m = row["market"]
        out.append(f"{i}. **{m['question']}** — {row['message']}\n   {m['url']}".rstrip())
    return f"**{title}**\n" + "\n".join(out)


def brief(data: dict[str, Any]) -> str:
    parts = ["# Prediction Market Brief"]
    parts.append(alert_list("Watchlist alerts", data.get("alerts", [])))
    parts.append(market_list("Biggest 24h movers", data.get("movers", [])))
    parts.append(signal_list("Worth a closer look", data.get("signals", [])))
    parts.append(market_list("Closing soon", data.get("closing", [])))
    for err in data.get("errors", []):
        parts.append(f"_Note: {err}_")
    parts.append(f"_{DISCLAIMER}_")
    return "\n\n".join(parts)
