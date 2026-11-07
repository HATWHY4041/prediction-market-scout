# Prediction Market Scout

A [Hermes Agent](https://github.com/NousResearch/hermes-agent) plugin that turns Polymarket into something your agent can research, watch and brief you on. It is read-only and needs no API key.

- **Live odds**: search any topic, or paste a polymarket.com link, and get prices, spread, liquidity and a 7-day price history.
- **Movers and attention signals**: biggest 24h moves, volume spikes, wide spreads, odd pricing, and undecided markets that are about to close.
- **Price-alert watchlist**: "tell me if this goes above 70% or moves 10 points in a day". Alerts fire once and re-arm when the price comes back.
- **`/pm` command**: quick answers without a model round trip.
- **Scheduled tasks in one click**: a daily briefing and a quiet alert checker.

It does more than a prompt: six tools, persistent state, a slash command, a bundled skill and two automation blueprints.

## Install

```bash
hermes plugins install HATWHY4041/prediction-market-scout
hermes plugins enable prediction-market-scout
```

Hermes 0.21.5 or newer. No dependencies beyond the Python standard library.

## Try it

Ask in chat:

- "What's moving on Polymarket today?"
- "What are the odds on the Fed cutting rates, and how has it changed this week?"
- "Watch this market and alert me if it goes above 70%: https://polymarket.com/event/..."
- "Which markets close in the next 48 hours and are still undecided?"

Or use the command:

```
/pm                      daily brief
/pm movers               biggest 24h moves
/pm trending             highest volume right now
/pm closing              closing soon
/pm signals              markets worth a closer look
/pm search bitcoin       find markets
/pm market <slug|link>   one market in depth
/pm watch add <slug|link> above 70 below 20 move 10
/pm watch list | check | remove <slug>
```

## Tools

| Tool | What it does |
|---|---|
| `polymarket_scan` | `trending`, `movers` or `closing_soon` lists |
| `polymarket_search` | Find markets by keyword |
| `polymarket_market` | One market: all outcomes, spread, liquidity, 7-day history, flags |
| `polymarket_signals` | Ranked list of markets with attention signals |
| `polymarket_watch` | `add`, `remove`, `list`, `check` alert rules |
| `polymarket_brief` | A finished markdown briefing in one call |

## Scheduled tasks

Two automation blueprints appear in Hermes's scheduled-task picker:

- **Prediction market daily brief**: pick a time and where to deliver it.
- **Prediction market watchlist alerts**: checks your watchlist every N minutes and stays silent (`[SILENT]`) unless something new fired.

## Settings

Under `plugins.entries.prediction-market-scout.settings`:

| Key | Default | Meaning |
|---|---|---|
| `min_volume_24h` | 5000 | Ignore markets quieter than this (USD, 24h) |
| `move_pts` | 8 | 24h move, in percentage points, that counts as big |
| `closing_hours` | 72 | How far ahead "closing soon" looks |
| `default_limit` | 5 | Markets per list (1-15) |

## Disclosure

- **Network**: HTTPS GET requests only, to `gamma-api.polymarket.com` and `clob.polymarket.com` (Polymarket's public APIs). The client refuses any other host. Responses are cached in memory for 30 seconds.
- **Credentials**: none. No API key, wallet, account or environment variable is read or required.
- **Read-only**: the plugin cannot place orders, sign anything or move money.
- **Storage**: the watchlist is kept in the plugin's own Hermes profile state. Nothing is written elsewhere.
- **No background processes, no telemetry, no self-updating code, no binaries.** Scheduled tasks are ordinary Hermes cron jobs the user chooses to create.
- **Not financial advice.** Prices are market-implied probabilities and flags are prompts to look closer.

## Development

```bash
python -m unittest discover -s tests -v
```

The tests are offline; the network is mocked.

## License

MIT
