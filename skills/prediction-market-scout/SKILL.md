---
name: prediction-market-scout
description: "Read Polymarket odds, find the biggest movers, flag unusual markets, set price alerts and schedule a daily brief."
version: 1.0.0
author: HATWHY4041
license: MIT
metadata:
  hermes:
    tags: [prediction-markets, polymarket, research, alerts]
---

# Prediction Market Scout

Use the `polymarket_*` tools to answer questions about Polymarket prediction markets. Everything is read-only public data: no key, no wallet, no trading.

## When to Use

- "What's hot on Polymarket?", "what moved today?", "what resolves this week?"
- A person, team, token or event is named and the user wants the odds on it
- The user pastes a polymarket.com link and wants a read on it
- The user wants a price alert, a watchlist, or a recurring briefing

## When NOT to Use

- Placing bets or moving money. This plugin cannot and must not.
- Questions about a market that is not on Polymarket (Kalshi, sportsbooks, etc.)
- Predicting who will win. Report the market's odds and what moved them; do not claim an edge.

## Tools

| Tool | Use it for |
|---|---|
| `polymarket_scan` | `trending`, `movers` or `closing_soon` lists |
| `polymarket_search` | Find markets by keyword. Run this first when the user names a topic. |
| `polymarket_market` | One market in depth: all outcomes, spread, liquidity, 7-day price history, flags |
| `polymarket_signals` | Markets worth a closer look (big moves, volume spikes, wide spreads, odd pricing, undecided and closing) |
| `polymarket_watch` | `add` / `remove` / `list` / `check` alert rules on a market |
| `polymarket_brief` | One call that returns a finished markdown briefing |

## Workflow

1. Topic named, no link: `polymarket_search`, then `polymarket_market` on the best match.
2. "What's going on?": `polymarket_brief`, and send its `markdown` as written.
3. Alerts: `polymarket_watch` with `action=add`. Price lines are percentages (`above: 70` means 70%). `move` is points in 24h. `check` only returns alerts that are new since the last check, so it is safe to run on a schedule.
4. Scheduling: point the user to the two ready-made scheduled tasks "Prediction market daily brief" and "Prediction market watchlist alerts", or create a cron job whose prompt calls `polymarket_brief` or `polymarket_watch` with `action=check`.

## Reading the numbers

- A Yes price of 0.62 means the market currently implies about a 62% chance. It is a price, not a fact.
- "Points" are percentage points of that price. A move from 40% to 48% is +8.0 pts.
- Flags are prompts to look closer, not predictions. Thin markets (low liquidity, wide spread) can show prices that nobody would actually trade at.
- Always include the market link so the user can check the live price themselves.

## Rules

- Never present flags or odds as financial advice or a recommendation to trade.
- If a tool returns an `error`, say plainly that Polymarket could not be reached or the market was not found. Do not invent numbers.
- Report numbers exactly as the tool returned them.
