"""Tool schemas: what the model reads to decide when to call each tool."""

_LIMIT = {"type": "integer", "description": "How many markets to return (1-15).", "minimum": 1, "maximum": 15}

SCAN = {
    "name": "polymarket_scan",
    "description": (
        "List live Polymarket prediction markets. mode=trending (highest 24h volume), "
        "mode=movers (biggest 24h price change), or mode=closing_soon (resolving within "
        "the next few days). Use for 'what is hot on Polymarket', 'what moved today', "
        "'what resolves this week'. Read-only public data."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "mode": {"type": "string", "enum": ["trending", "movers", "closing_soon"],
                     "description": "Which ranking to use. Default trending."},
            "limit": _LIMIT,
            "hours": {"type": "number", "description": "For closing_soon only: window in hours (default 72)."},
        },
    },
}

SEARCH = {
    "name": "polymarket_search",
    "description": (
        "Search Polymarket by keyword (a person, team, token, event, topic) and return "
        "matching open markets with current odds. Use before polymarket_market when the "
        "user names a topic instead of giving a market link."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Keywords, e.g. 'fed rate cut' or 'bitcoin 150k'."},
            "limit": _LIMIT,
        },
        "required": ["query"],
    },
}

MARKET = {
    "name": "polymarket_market",
    "description": (
        "Deep dive on one Polymarket market: current odds for every outcome, bid/ask, "
        "liquidity, volume, time to resolution, a 7-day price history summary, and any "
        "attention flags. Accepts a market slug, numeric id, or a polymarket.com link."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "market": {"type": "string", "description": "Slug, numeric id, or polymarket.com URL."},
        },
        "required": ["market"],
    },
}

SIGNALS = {
    "name": "polymarket_signals",
    "description": (
        "Scan the most active Polymarket markets and flag the ones worth a closer look: "
        "big 24h moves, volume spikes, wide bid/ask spreads, outcome prices that do not "
        "add to 100%, and undecided markets about to close. These are heuristic prompts "
        "to investigate, not predictions or trade advice."
    ),
    "parameters": {"type": "object", "properties": {"limit": _LIMIT}},
}

WATCH = {
    "name": "polymarket_watch",
    "description": (
        "Manage a personal watchlist of Polymarket markets with alert rules. "
        "action=add (needs market, plus any of above/below price lines and a move trigger), "
        "action=remove, action=list, or action=check (evaluates every rule now and reports "
        "only NEW alerts, so it is safe to run on a schedule). Price lines are percentages "
        "(70 means 70%). move is a number of percentage points in 24h."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["add", "remove", "list", "check"]},
            "market": {"type": "string", "description": "Slug, id or URL (add), or slug/question text (remove)."},
            "above": {"type": "number", "description": "Alert when the Yes price rises to this percent or more."},
            "below": {"type": "number", "description": "Alert when the Yes price falls to this percent or less."},
            "move": {"type": "number", "description": "Alert on a move of this many percentage points in 24h."},
        },
        "required": ["action"],
    },
}

BRIEF = {
    "name": "polymarket_brief",
    "description": (
        "Build a complete prediction-market briefing in one call: watchlist alerts, the "
        "biggest 24h movers, markets worth a closer look, and what closes soon. Returns "
        "ready-to-send markdown. Ideal for a scheduled daily or hourly message to "
        "Telegram, Discord or Slack."
    ),
    "parameters": {"type": "object", "properties": {"limit": _LIMIT}},
}

ALL = [SCAN, SEARCH, MARKET, SIGNALS, WATCH, BRIEF]
