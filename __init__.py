"""Prediction Market Scout: read-only Polymarket tools, a /pm command, a watchlist
and two one-click scheduled tasks."""

from __future__ import annotations

import logging
from pathlib import Path

from . import schemas, tools

logger = logging.getLogger(__name__)

TOOLSET = "prediction_market_scout"

_TIME_SLOT = {
    "name": "time", "type": "time", "label": "What time?", "default": "08:00",
    "help": "24h local time, e.g. 08:00",
}
_DELIVER_SLOT = {
    "name": "deliver", "type": "enum", "label": "Where to deliver?", "default": "origin",
    "options": ["origin", "local", "telegram", "discord", "slack"],
    "optional": False, "strict": False,
    "help": "origin = the chat you set this up from; or any connected platform name",
}


def _register_blueprints(ctx) -> None:
    """Optional one-click schedules. Skipped quietly on a Hermes without blueprints."""
    register = getattr(ctx, "register_automation_blueprint", None)
    if register is None:
        return
    register(
        "daily-brief",
        title="Prediction market daily brief",
        description="Every day: your Polymarket watchlist alerts, the biggest 24h movers, "
                    "markets worth a closer look, and what closes soon.",
        category="daily",
        schedule_template="{minute} {hour} * * *",
        prompt_template=(
            "Call the polymarket_brief tool once. Send the markdown it returns as your "
            "whole message, unchanged. If the tool returns an error, say in one line that "
            "Polymarket could not be reached and stop."
        ),
        slots=[_TIME_SLOT, _DELIVER_SLOT],
        tags=["prediction-markets", "polymarket", "briefing"],
    )
    register(
        "watchlist-alerts",
        title="Prediction market watchlist alerts",
        description="Checks your Polymarket watchlist on a schedule and messages you only "
                    "when a price line or move trigger newly fires.",
        category="monitoring",
        schedule_template="*/{interval_min} * * * *",
        prompt_template=(
            "Call polymarket_watch with action=check. If new_alerts is empty, respond with "
            "[SILENT]. Otherwise write a short message listing each new alert: the market "
            "question, the rule that fired, the current Yes price and the link. If the tool "
            "returns an error, respond with [SILENT]."
        ),
        slots=[
            {"name": "interval_min", "type": "enum", "label": "How often?", "default": "30",
             "options": ["15", "30", "60"], "help": "minutes between checks"},
            _DELIVER_SLOT,
        ],
        tags=["prediction-markets", "polymarket", "alerts"],
    )


def register(ctx):
    scout = tools.Scout(ctx)
    handlers = tools.build_handlers(scout)

    for schema in schemas.ALL:
        ctx.register_tool(
            name=schema["name"],
            toolset=TOOLSET,
            schema=schema,
            handler=handlers[schema["name"]],
        )

    ctx.register_command(
        "pm",
        scout.command,
        description="Polymarket briefing, movers, signals and watchlist",
        args_hint="[brief|movers|search <words>|watch ...]",
    )

    skill_md = Path(__file__).parent / "skills" / "prediction-market-scout" / "SKILL.md"
    if skill_md.exists():
        ctx.register_skill(
            "prediction-market-scout", skill_md,
            description="Read Polymarket odds, find movers, set price alerts and schedule a daily brief.",
        )

    try:
        _register_blueprints(ctx)
    except Exception as exc:  # a schedule template problem must never disable the plugin
        logger.warning("prediction-market-scout: scheduled-task templates skipped (%s)", exc)
