"""Offline tests. The plugin is loaded the way Hermes loads it (as a package) and the
network is faked, so these run anywhere with just the Python standard library:

    python -m unittest discover -s tests -v
"""

import importlib.util
import io
import json
import re
import string
import sys
import unittest
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent


def _load_package():
    spec = importlib.util.spec_from_file_location(
        "pms", ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
    module = importlib.util.module_from_spec(spec)
    sys.modules["pms"] = module
    spec.loader.exec_module(module)
    return module


pms = _load_package()
from pms import analysis, polymarket, render, schemas, tools, watchlist  # noqa: E402

NOW = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)


def raw_market(**over):
    base = {
        "id": "501", "question": "Will X happen?", "slug": "will-x-happen",
        "outcomes": '["Yes", "No"]', "outcomePrices": '["0.62", "0.38"]',
        "clobTokenIds": '["tokYes", "tokNo"]',
        "volume24hr": 120000.5, "volume1wk": 200000, "volumeNum": 900000, "liquidityNum": 50000,
        "oneDayPriceChange": 0.09, "oneWeekPriceChange": 0.12,
        "bestBid": 0.61, "bestAsk": 0.63, "spread": 0.02,
        "endDate": "2026-11-09T12:00:00Z", "closed": False, "active": True,
        "events": [{"slug": "x-event"}],
    }
    base.update(over)
    return base


def market(**over):
    return polymarket.normalize_market(raw_market(**over), now=NOW)


class FakeState:
    def __init__(self):
        self.data = {}

    def get(self, key, default=None):
        return json.loads(json.dumps(self.data.get(key, default)))

    def set(self, key, value):
        self.data[key] = json.loads(json.dumps(value))


class FakeCtx:
    def __init__(self, config=None):
        self.state = FakeState()
        self.config = config or {}
        self.tools, self.commands, self.skills, self.blueprints = {}, {}, {}, {}

    def get_config(self, key, default=None):
        return self.config.get(key, default)

    def register_tool(self, name, toolset, schema, handler, **kw):
        self.tools[name] = (toolset, schema, handler)

    def register_command(self, name, handler, description="", args_hint="", argument_mode=None):
        self.commands[name] = handler

    def register_skill(self, name, path, description="", frontmatter=None):
        assert Path(path).exists()
        self.skills[name] = path

    def register_automation_blueprint(self, key, **kw):
        self.blueprints[key] = kw


class FakeResp:
    def __init__(self, payload):
        self._b = json.dumps(payload).encode()

    def read(self, n=-1):
        return self._b if n < 0 else self._b[:n]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


# --------------------------------------------------------------------------- #

class NormalizeTests(unittest.TestCase):
    def test_json_string_fields_are_parsed(self):
        m = market()
        self.assertEqual(m["outcomes"], ["Yes", "No"])
        self.assertAlmostEqual(m["yes"], 0.62)
        self.assertEqual(m["token_ids"], ["tokYes", "tokNo"])
        self.assertEqual(m["url"], "https://polymarket.com/event/x-event")
        self.assertAlmostEqual(m["hours_to_end"], 48.0)

    def test_list_fields_also_work(self):
        m = market(outcomes=["Yes", "No"], outcomePrices=["0.3", "0.7"])
        self.assertAlmostEqual(m["yes"], 0.3)

    def test_yes_is_found_when_listed_second(self):
        m = market(outcomes='["No", "Yes"]', outcomePrices='["0.7", "0.3"]')
        self.assertEqual(m["yes_index"], 1)
        self.assertAlmostEqual(m["yes"], 0.3)

    def test_falls_back_to_market_url_without_event(self):
        m = market(events=None)
        self.assertEqual(m["url"], "https://polymarket.com/market/will-x-happen")

    def test_bad_rows_are_dropped(self):
        self.assertIsNone(polymarket.normalize_market({"slug": "no-question"}))
        self.assertIsNone(polymarket.normalize_market("nope"))
        m = market(oneDayPriceChange="not a number", bestBid=None, endDate=None)
        self.assertIsNone(m["change_1d"])
        self.assertIsNone(m["hours_to_end"])

    def test_closed_and_inactive_are_filtered_from_lists(self):
        rows = [raw_market(), raw_market(slug="b", closed=True), raw_market(slug="c", active=False)]
        self.assertEqual(len(polymarket._normalize_all(rows, NOW)), 1)


class AnalysisTests(unittest.TestCase):
    cfg = analysis.settings()

    def test_all_flags_fire_on_a_hot_market(self):
        kinds = {s["type"] for s in analysis.compute_signals(market(), self.cfg)}
        self.assertEqual(kinds, {"big_move", "volume_spike", "closing_undecided"})

    def test_quiet_market_is_not_flagged(self):
        quiet = market(volume24hr=100, oneDayPriceChange=0.2)
        self.assertEqual(analysis.compute_signals(quiet, self.cfg), [])

    def test_wide_spread_and_price_gap(self):
        m = market(spread=0.09, outcomePrices='["0.62", "0.30"]', endDate="2027-01-01T00:00:00Z",
                   oneDayPriceChange=0.0, volume1wk=None)
        kinds = {s["type"] for s in analysis.compute_signals(m, self.cfg)}
        self.assertEqual(kinds, {"wide_spread", "price_sum_gap"})

    def test_ranking_is_by_score(self):
        hot = market()
        mild = market(slug="mild", oneDayPriceChange=0.0, volume1wk=None, endDate="2027-01-01T00:00:00Z",
                      spread=0.08)
        ranked = analysis.rank_signals([mild, hot], self.cfg, 5)
        self.assertEqual([r["market"]["slug"] for r in ranked], ["will-x-happen", "mild"])
        self.assertGreater(ranked[0]["score"], ranked[1]["score"])

    def test_movers_and_closing(self):
        a = market(slug="a", oneDayPriceChange=-0.2)
        b = market(slug="b", oneDayPriceChange=0.05)
        c = market(slug="c", endDate="2027-06-01T00:00:00Z")
        self.assertEqual([m["slug"] for m in analysis.movers([b, a], self.cfg, 5)], ["a", "b"])
        self.assertEqual([m["slug"] for m in analysis.closing_soon([c, a], self.cfg, 5)], ["a"])

    def test_history_stats(self):
        hist = [(1000 + 3600 * i, 0.50 + 0.0025 * i) for i in range(49)]
        s = analysis.history_stats(hist)
        self.assertEqual(s["trend"], "up")
        self.assertEqual(s["change_24h_pts"], 6.0)
        self.assertEqual(s["change_period_pts"], 12.0)
        self.assertIsNone(analysis.history_stats([(1, 0.5)]))

    def test_formatting(self):
        self.assertEqual(analysis.money(1_250_000), "$1.2M")
        self.assertEqual(analysis.money(12_300), "$12.3K")
        self.assertEqual(analysis.pct(0.625), "62%")
        self.assertEqual(analysis.pct(0.034), "3.4%")
        self.assertEqual(analysis.pts(0.081), "+8.1 pts")
        self.assertEqual(analysis.ends_in(5), "5h")
        self.assertEqual(analysis.ends_in(24 * 5), "5d")

    def test_settings_ignores_junk(self):
        cfg = analysis.settings({"move_pts": "12", "bogus": 1, "wide_spread": "abc"})
        self.assertEqual(cfg["move_pts"], 12.0)
        self.assertEqual(cfg["wide_spread"], analysis.DEFAULTS["wide_spread"])
        self.assertNotIn("bogus", cfg)


class WatchlistTests(unittest.TestCase):
    def test_percent_or_fraction_both_accepted(self):
        items, _ = watchlist.add_rule([], market(), above=70, below=0.2)
        self.assertEqual(items[0]["above"], 0.7)
        self.assertEqual(items[0]["below"], 0.2)

    def test_default_rule_and_validation(self):
        items, _ = watchlist.add_rule([], market())
        self.assertEqual(items[0]["move"], 5.0)
        with self.assertRaises(ValueError):
            watchlist.add_rule([], market(), above=250)
        with self.assertRaises(ValueError):
            watchlist.add_rule([], market(), move=-3)

    def test_adding_twice_replaces(self):
        items, _ = watchlist.add_rule([], market(), above=70)
        items, _ = watchlist.add_rule(items, market(), below=10)
        self.assertEqual(len(items), 1)
        self.assertIsNone(items[0]["above"])

    def test_remove_by_slug_or_text(self):
        items, _ = watchlist.add_rule([], market(), above=70)
        self.assertFalse(watchlist.remove_rule(items, "nothing")[1])
        self.assertTrue(watchlist.remove_rule(items, "will X happen")[1])
        self.assertTrue(watchlist.remove_rule(items, "will-x-happen")[1])

    def test_alert_fires_once_then_rearms(self):
        items, _ = watchlist.add_rule([], market(), above=60)
        state = {"yes": "0.62"}
        fetch = lambda slug: market(outcomePrices=json.dumps([state["yes"], "0.3"]))

        new, active, errors, items = watchlist.run_check(items, fetch)
        self.assertEqual(len(new), 1)
        self.assertEqual(new[0]["rule"], "above")
        new, active, _, items = watchlist.run_check(items, fetch)
        self.assertEqual((len(new), len(active)), (0, 1))
        state["yes"] = "0.50"
        new, active, _, items = watchlist.run_check(items, fetch)
        self.assertEqual((len(new), len(active)), (0, 0))
        state["yes"] = "0.65"
        new, _, _, _ = watchlist.run_check(items, fetch)
        self.assertEqual(len(new), 1)

    def test_one_failure_does_not_hide_the_rest(self):
        items, _ = watchlist.add_rule([], market(), above=60)
        items, _ = watchlist.add_rule(items, market(slug="b", question="B?"), above=60)

        def fetch(slug):
            if slug == "b":
                raise polymarket.PolymarketError("boom")
            return market()

        new, _, errors, updated = watchlist.run_check(items, fetch)
        self.assertEqual(len(new), 1)
        self.assertEqual(len(errors), 1)
        self.assertEqual(len(updated), 2)


class HttpTests(unittest.TestCase):
    def setUp(self):
        polymarket.clear_cache()
        patcher = mock.patch.object(polymarket.time, "sleep", lambda s: None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_query_string_and_defaults(self):
        seen = []

        def fake(req, timeout=None):
            seen.append(req.full_url)
            return FakeResp([raw_market()])

        with mock.patch.object(polymarket.urllib.request, "urlopen", fake):
            out = polymarket.list_markets(limit=500, now=NOW)
        self.assertEqual(len(out), 1)
        url = seen[0]
        self.assertTrue(url.startswith("https://gamma-api.polymarket.com/markets?"))
        for part in ("closed=false", "order=volume24hr", "ascending=false", "limit=100"):
            self.assertIn(part, url)
        self.assertNotIn("None", url)

    def test_responses_are_cached(self):
        calls = []

        def fake(req, timeout=None):
            calls.append(1)
            return FakeResp([raw_market()])

        with mock.patch.object(polymarket.urllib.request, "urlopen", fake):
            polymarket.list_markets(now=NOW)
            polymarket.list_markets(now=NOW)
        self.assertEqual(len(calls), 1)

    def test_server_errors_retry_once_then_fail_cleanly(self):
        calls = []

        def fake(req, timeout=None):
            calls.append(1)
            raise urllib.error.HTTPError(req.full_url, 503, "x", {}, io.BytesIO(b""))

        with mock.patch.object(polymarket.urllib.request, "urlopen", fake):
            with self.assertRaises(polymarket.PolymarketError) as cm:
                polymarket.list_markets(now=NOW)
        self.assertEqual(len(calls), 2)
        self.assertIn("503", str(cm.exception))

    def test_unreachable_and_bad_json(self):
        with mock.patch.object(polymarket.urllib.request, "urlopen",
                               side_effect=urllib.error.URLError("no route")):
            with self.assertRaises(polymarket.PolymarketError):
                polymarket.list_markets(now=NOW)
        polymarket.clear_cache()

        class Junk(FakeResp):
            def __init__(self):
                self._b = b"<html>nope</html>"

        with mock.patch.object(polymarket.urllib.request, "urlopen", lambda r, timeout=None: Junk()):
            with self.assertRaises(polymarket.PolymarketError):
                polymarket.list_markets(now=NOW)

    def test_only_allowlisted_hosts(self):
        with self.assertRaises(polymarket.PolymarketError):
            polymarket._get_json("https://evil.example.com", "/steal")

    def test_get_market_accepts_link_slug_or_id(self):
        seen = []

        def fake(req, timeout=None):
            seen.append(req.full_url)
            return FakeResp([raw_market()])

        with mock.patch.object(polymarket.urllib.request, "urlopen", fake):
            self.assertIsNotNone(polymarket.get_market(
                "https://polymarket.com/event/x-event/will-x-happen", now=NOW))
            polymarket.clear_cache()
            polymarket.get_market("501", now=NOW)
        self.assertIn("slug=will-x-happen", seen[0])
        self.assertIn("id=501", seen[1])

    def test_get_market_missing(self):
        with mock.patch.object(polymarket.urllib.request, "urlopen", lambda r, timeout=None: FakeResp([])):
            self.assertIsNone(polymarket.get_market("nope", now=NOW))
        self.assertIsNone(polymarket.get_market("  "))

    def test_search_and_history_parsing(self):
        payload = {"events": [{"slug": "e1", "markets": [
            raw_market(), raw_market(slug="gone", closed=True), raw_market(slug="b", volume24hr=5)]}]}
        with mock.patch.object(polymarket.urllib.request, "urlopen", lambda r, timeout=None: FakeResp(payload)):
            found = polymarket.search_markets("x", 10, now=NOW)
        self.assertEqual([m["slug"] for m in found], ["will-x-happen", "b"])

        polymarket.clear_cache()
        hist = {"history": [{"t": 1, "p": 0.5}, {"t": 2, "p": "0.6"}, {"t": "bad"}, "junk"]}
        with mock.patch.object(polymarket.urllib.request, "urlopen", lambda r, timeout=None: FakeResp(hist)):
            self.assertEqual(polymarket.price_history("tok"), [(1, 0.5), (2, 0.6)])


class ToolTests(unittest.TestCase):
    def setUp(self):
        self.ctx = FakeCtx()
        self.scout = tools.Scout(self.ctx)
        self.handlers = tools.build_handlers(self.scout)

    def call(self, name, **args):
        return json.loads(self.handlers[name](args))

    def test_scan_modes(self):
        pool = [market(slug="a", oneDayPriceChange=-0.2), market(slug="b", oneDayPriceChange=0.05)]
        with mock.patch.object(polymarket, "list_markets", return_value=pool) as lm:
            movers = self.call("polymarket_scan", mode="movers", limit=1)
            trending = self.call("polymarket_scan")
            closing = self.call("polymarket_scan", mode="closing_soon", hours=24 * 30)
        self.assertEqual(movers["markets"][0]["question"], "Will X happen?")
        self.assertEqual(movers["count"], 1)
        self.assertEqual(trending["mode"], "trending")
        self.assertEqual(closing["mode"], "closing_soon")
        kwargs = lm.call_args_list[-1].kwargs
        iso = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertTrue(iso.match(kwargs["end_date_min"]) and iso.match(kwargs["end_date_max"]))

    def test_bad_mode_is_a_clean_error(self):
        out = self.call("polymarket_scan", mode="bogus")
        self.assertIn("error", out)

    def test_network_failure_becomes_json_error(self):
        with mock.patch.object(polymarket, "list_markets",
                               side_effect=polymarket.PolymarketError("could not reach Polymarket")):
            out = self.call("polymarket_signals")
        self.assertEqual(out["error"], "could not reach Polymarket")

    def test_unexpected_exception_never_escapes(self):
        with mock.patch.object(polymarket, "list_markets", side_effect=RuntimeError("secret detail")):
            raw = self.handlers["polymarket_signals"]({})
        self.assertNotIn("secret detail", raw)
        self.assertIn("error", json.loads(raw))

    def test_search_requires_query(self):
        self.assertIn("error", self.call("polymarket_search"))
        with mock.patch.object(polymarket, "search_markets", return_value=[market()]):
            out = self.call("polymarket_search", query="x")
        self.assertEqual(out["count"], 1)

    def test_market_detail_with_and_without_history(self):
        hist = [(1000 + 3600 * i, 0.50 + 0.0025 * i) for i in range(49)]
        with mock.patch.object(polymarket, "get_market", return_value=market()), \
                mock.patch.object(polymarket, "price_history", return_value=hist) as ph:
            out = self.call("polymarket_market", market="will-x-happen")
        ph.assert_called_once_with("tokYes", "1w", 60)
        self.assertEqual(out["price_history_7d"]["trend"], "up")
        self.assertEqual(out["outcomes"][1], {"outcome": "No", "price": 0.38})
        self.assertTrue(out["flags"])

        with mock.patch.object(polymarket, "get_market", return_value=market()), \
                mock.patch.object(polymarket, "price_history", side_effect=polymarket.PolymarketError("x")):
            out = self.call("polymarket_market", market="will-x-happen")
        self.assertIsNone(out["price_history_7d"])
        self.assertEqual(out["question"], "Will X happen?")

        with mock.patch.object(polymarket, "get_market", return_value=None):
            self.assertIn("error", self.call("polymarket_market", market="nope"))

    def test_watch_full_flow(self):
        with mock.patch.object(polymarket, "get_market", return_value=market()):
            added = self.call("polymarket_watch", action="add", market="will-x-happen", above=60)
            self.assertTrue(added["ok"])
            listed = self.call("polymarket_watch", action="list")
            self.assertEqual(listed["count"], 1)
            first = self.call("polymarket_watch", action="check")
            second = self.call("polymarket_watch", action="check")
            removed = self.call("polymarket_watch", action="remove", market="will-x-happen")
        self.assertEqual(len(first["new_alerts"]), 1)
        self.assertEqual(len(second["new_alerts"]), 0)
        self.assertEqual(second["still_active"], 1)
        self.assertTrue(removed["ok"])
        self.assertEqual(self.call("polymarket_watch", action="list")["count"], 0)
        self.assertIn("error", self.call("polymarket_watch", action="explode"))
        self.assertIn("error", self.call("polymarket_watch", action="add"))

    def test_brief_has_every_section(self):
        pool = [market(), market(slug="b", question="Second?", oneDayPriceChange=-0.15)]
        with mock.patch.object(polymarket, "list_markets", return_value=pool):
            out = self.call("polymarket_brief")
        md = out["markdown"]
        for heading in ("# Prediction Market Brief", "Watchlist alerts", "Biggest 24h movers",
                        "Worth a closer look", "Closing soon", "not financial advice"):
            self.assertIn(heading, md)
        self.assertEqual(out["counts"]["movers"], 2)

    def test_settings_come_from_config(self):
        ctx = FakeCtx({"min_volume_24h": 10_000_000, "default_limit": 2})
        scout = tools.Scout(ctx)
        self.assertEqual(scout.cfg()["min_volume_24h"], 10_000_000.0)
        self.assertEqual(scout.default_limit(), 2)
        with mock.patch.object(polymarket, "list_markets", return_value=[market()]):
            self.assertEqual(scout.scan("movers", 5), [])  # filtered out by the high floor

    def test_slash_command(self):
        pool = [market()]
        with mock.patch.object(polymarket, "list_markets", return_value=pool), \
                mock.patch.object(polymarket, "get_market", return_value=market()), \
                mock.patch.object(polymarket, "search_markets", return_value=pool), \
                mock.patch.object(polymarket, "price_history", return_value=[]):
            self.assertIn("Prediction Market Brief", self.scout.command(""))
            self.assertIn("Biggest 24h movers", self.scout.command("movers"))
            self.assertIn("Trending", self.scout.command("trending"))
            self.assertIn("Results for 'bitcoin'", self.scout.command("search bitcoin"))
            self.assertIn("Will X happen?", self.scout.command("market will-x-happen"))
            self.assertIn("watchlist is empty", self.scout.command("watch list").lower())
            self.assertIn("Watching", self.scout.command("watch add will-x-happen above 70 move 10"))
            self.assertIn("alert at or above 70%", self.scout.command("watch list"))
            self.assertIn("Watchlist alerts", self.scout.command("watch check"))
            self.assertIn("Removed", self.scout.command("watch remove will-x-happen"))
            self.assertIn("/pm", self.scout.command("help"))
            self.assertIn("Could not do that", self.scout.command("search"))
            self.assertIn("Could not do that", self.scout.command("bogus"))


class RegistrationTests(unittest.TestCase):
    def setUp(self):
        self.ctx = FakeCtx()
        pms.register(self.ctx)

    def test_registers_everything(self):
        self.assertEqual(set(self.ctx.tools), {s["name"] for s in schemas.ALL})
        self.assertIn("pm", self.ctx.commands)
        self.assertIn("prediction-market-scout", self.ctx.skills)
        self.assertEqual(set(self.ctx.blueprints), {"daily-brief", "watchlist-alerts"})

    def test_manifest_matches_registered_tools(self):
        text = (ROOT / "plugin.yaml").read_text()
        declared = set(re.findall(r"^  - (polymarket_\w+)\s*$", text, re.M))
        self.assertEqual(declared, set(self.ctx.tools))
        self.assertIn('requires_hermes: ">=0.21.5"', text)
        self.assertNotIn("provides_hooks", text)  # none registered, so none declared

    def test_schemas_are_wellformed(self):
        for name, (toolset, schema, handler) in self.ctx.tools.items():
            self.assertEqual(schema["name"], name)
            self.assertTrue(schema["description"])
            self.assertEqual(schema["parameters"]["type"], "object")
            for req in schema["parameters"].get("required", []):
                self.assertIn(req, schema["parameters"]["properties"])
            self.assertEqual(toolset, "prediction_market_scout")
            self.assertTrue(callable(handler))
            self.assertIsInstance(json.loads(handler({"action": "list"})), dict)

    def test_blueprint_templates_only_use_known_slots(self):
        derived = {"minute", "hour", "dow"}
        for key, bp in self.ctx.blueprints.items():
            slots = {s["name"] for s in bp["slots"]}
            self.assertEqual(len(slots), len(bp["slots"]))
            for tpl in (bp["prompt_template"], bp["schedule_template"]):
                for _, field, _, _ in string.Formatter().parse(tpl):
                    if field:
                        self.assertTrue(field in slots or field in derived, f"{key}: {field}")
            for slot in bp["slots"]:
                self.assertIn(slot["type"], {"time", "enum", "text", "weekdays"})
                self.assertTrue(slot["label"])

    def test_old_hermes_without_blueprints_still_registers(self):
        ctx = FakeCtx()
        del FakeCtx.register_automation_blueprint
        try:
            pms.register(ctx)
        finally:
            FakeCtx.register_automation_blueprint = lambda self, key, **kw: self.blueprints.__setitem__(key, kw)
        self.assertEqual(len(ctx.tools), 6)

    def test_no_forbidden_patterns_in_source(self):
        """Catalog rules: no core patching, no credential-file reads, no self-update, no binaries."""
        banned = [r"\bsetattr\(", r"sys\.modules\[", r"auth\.json", r"\.env\b", r"os\.environ",
                  r"subprocess", r"\beval\(", r"\bexec\(", r"__import__", r"os\.system"]
        for path in ROOT.glob("*.py"):
            text = path.read_text()
            for pattern in banned:
                self.assertIsNone(re.search(pattern, text), f"{path.name} matches {pattern}")
        for path in ROOT.rglob("*"):
            if path.is_file() and ".git" not in path.parts and "__pycache__" not in path.parts:
                self.assertNotIn(path.suffix, {".pyc", ".pyo", ".so", ".dll", ".exe", ".wasm"})
                self.assertNotEqual(path.read_bytes()[:4], b"\x7fELF")


if __name__ == "__main__":
    unittest.main()
