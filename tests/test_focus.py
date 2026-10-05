"""Focus list: choosing which stocks the desk watches, and the guards around trading them."""

from __future__ import annotations

import http.client
import json
import threading

from helpers import THURSDAY, DeskTestCase, FakeBroker, at, falling_bars, rising_bars

import console
import instruments
import journal
import run_study
from common import aoi_file, load_json, save_json, watchlist

RED_SPY = {"color": "red", "low": 589.5, "high": 590.5, "confluence": []}
RED_NVDA = {"color": "red", "low": 189.6, "high": 190.4, "confluence": ["CHoCH"]}
GREEN_NVDA = {"color": "green", "low": 209.6, "high": 210.4, "confluence": ["CHoCH"]}


def publish(symbol, zones, hh=9, mm=45):
    save_json(aoi_file(symbol), {"symbol": symbol, "tradable": True, "written_at": at(THURSDAY, hh, mm).isoformat(),
                                 "source": "Ops live read", "approximate": False, "zones": zones})


def bars_for(now):
    return {"SPY": falling_bars(THURSDAY, now), "NVDA": falling_bars(THURSDAY, now, start=200.0, end=190.0)}


class FocusTests(DeskTestCase):
    def setUp(self):
        super().setUp()
        self.now = at(THURSDAY, 10, 30)

    def paper(self, broker, bars=None):
        return run_study.cmd_paper(self.now, broker_factory=lambda: broker, bars=bars or bars_for(self.now))

    def test_add_and_remove_focus(self):
        run_study.set_focus("nvda", True)
        self.assertEqual(watchlist()["focus"], ["SPY", "NVDA"])
        run_study.move_focus("NVDA", -1)
        self.assertEqual(watchlist()["focus"], ["NVDA", "SPY"])
        run_study.set_focus("NVDA", False)
        self.assertEqual(watchlist()["focus"], ["SPY"])
        self.assertIn("NVDA", watchlist()["symbols"])  # remembered, just not watched
        with self.assertRaises(SystemExit):
            run_study.set_focus("not a ticker", True)

    def test_new_stock_is_watch_only(self):
        run_study.set_focus("NVDA", True)
        publish("NVDA", [RED_NVDA])
        broker = FakeBroker()
        self.paper(broker)
        self.assertEqual(broker.submitted, [])
        nvda = [e for e in journal.read_events() if e.get("symbol") == "NVDA"][-1]
        self.assertEqual(nvda["reasons"], ["instrument_off"])

    def test_trading_switched_on_buys_one_nvda_call(self):
        run_study.set_focus("NVDA", True)
        run_study.set_trading("NVDA", "options", True)
        publish("NVDA", [RED_NVDA])
        broker = FakeBroker()
        r = self.paper(broker)
        self.assertEqual(r["decision"], "enter", r.get("reasons"))
        self.assertEqual(len(broker.submitted), 1)
        occ = instruments.parse_occ(broker.submitted[0]["symbol"])
        self.assertEqual((occ["underlying"], occ["right"], occ["strike"]), ("NVDA", "call", 190.0))
        entry = [e for e in journal.read_events() if e["event"] == "order"][-1]
        self.assertEqual(entry["underlying"], "NVDA")

    def test_only_one_entry_when_two_stocks_qualify(self):
        run_study.set_focus("NVDA", True)
        run_study.set_trading("NVDA", "options", True)
        publish("SPY", [RED_SPY])
        publish("NVDA", [RED_NVDA])  # the CHoCH tag gives NVDA the higher score
        broker = FakeBroker()
        r = self.paper(broker)
        self.assertEqual(len(broker.submitted), 1)
        self.assertEqual(r["symbol"], "NVDA")
        spy = [e for e in journal.read_events() if e.get("symbol") == "SPY" and e["event"] == "skip"][-1]
        self.assertEqual(spy["reasons"], ["another_symbol_chosen"])

    def test_off_focus_stock_never_trades(self):
        run_study.set_trading("NVDA", "options", True)  # switched on but not on the focus list
        publish("NVDA", [RED_NVDA])
        broker = FakeBroker()
        self.paper(broker)
        self.assertEqual(broker.submitted, [])

    def test_aoi_must_match_the_stock(self):
        run_study.set_focus("NVDA", True)
        run_study.set_trading("NVDA", "options", True)
        save_json(aoi_file("NVDA"), {"symbol": "SPY", "tradable": True, "written_at": at(THURSDAY, 9, 45).isoformat(),
                                     "approximate": False, "zones": [RED_NVDA]})
        broker = FakeBroker()
        self.paper(broker)
        self.assertEqual(broker.submitted, [])
        nvda = [e for e in journal.read_events() if e.get("symbol") == "NVDA"][-1]
        self.assertIn("aoi_tradable", nvda["reasons"])

    def test_aoi_set_needs_a_watchlist_stock(self):
        with self.assertRaises(SystemExit):
            run_study.cmd_aoi_set(at(THURSDAY, 9, 41), ["red:1:2"], [], "Ops", symbol="TSLA")
        run_study.set_focus("TSLA", True)
        data = run_study.cmd_aoi_set(at(THURSDAY, 9, 41), ["red:300:301"], [], "Ops", symbol="TSLA")
        self.assertEqual(data["symbol"], "TSLA")
        self.assertEqual(load_json("aoi_override.TSLA.json")["zones"][0]["low"], 300.0)

    def test_sell_signal_on_nvda_buys_a_put(self):
        run_study.set_focus("NVDA", True)
        run_study.set_trading("NVDA", "options", True)
        publish("NVDA", [GREEN_NVDA])
        broker = FakeBroker()
        bars = {"SPY": falling_bars(THURSDAY, self.now),
                "NVDA": rising_bars(THURSDAY, self.now, start=200.0, end=210.0)}
        self.paper(broker, bars=bars)
        self.assertEqual(instruments.parse_occ(broker.submitted[0]["symbol"])["right"], "put")

    def test_manual_holding_with_switch_off_is_left_alone(self):
        broker = FakeBroker(positions=[{"symbol": "SNDK", "qty": 3.0, "avg_entry_price": 50.0}])
        sent = run_study.cmd_manage(at(THURSDAY, 15, 56), broker, bars={})
        self.assertEqual(sent, [])

    def test_desk_position_still_managed_after_unfocusing(self):
        run_study.set_focus("NVDA", True)
        sym = "NVDA261001C00190000"
        journal.log("order", now=at(THURSDAY, 10, 5), role="entry", order_id="e1", symbol=sym, underlying_price=190.0)
        run_study.set_focus("NVDA", False)
        broker = FakeBroker(positions=[{"symbol": sym, "qty": 1.0, "avg_entry_price": 3.0}])
        sent = run_study.cmd_manage(at(THURSDAY, 15, 56), broker, bars={})
        self.assertEqual(len(sent), 1)

    def test_nvda_stop_uses_nvda_price(self):
        run_study.set_focus("NVDA", True)
        run_study.set_trading("NVDA", "options", True)
        sym = "NVDA261001C00190000"
        journal.log("order", now=at(THURSDAY, 10, 5), role="entry", order_id="e1", symbol=sym, underlying_price=200.0)
        broker = FakeBroker(positions=[{"symbol": sym, "qty": 1.0, "avg_entry_price": 3.0}])
        now = at(THURSDAY, 11, 0)
        held = run_study.cmd_manage(now, broker, bars={"NVDA": falling_bars(THURSDAY, now, start=201, end=199.5)})
        self.assertEqual(held, [])  # 0.25% against: hold
        stopped = run_study.cmd_manage(now, broker, bars={"NVDA": falling_bars(THURSDAY, now, start=201, end=199.2)})
        self.assertEqual(len(stopped), 1)  # 0.40% against: out


class FocusConsoleTests(DeskTestCase):
    def setUp(self):
        super().setUp()
        self.server = console.make_server(port=0)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        res, _ = self.req("POST", "/unlock", {"pin": "4793"})
        self.cookie = res.getheader("Set-Cookie").split(";")[0]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        super().tearDown()

    def req(self, method, path, body=None, cookie=None, ctype="application/json"):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {"Host": f"127.0.0.1:{self.port}", "Content-Type": ctype}
        if cookie:
            headers["Cookie"] = cookie
        conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
        res = conn.getresponse()
        data = res.read()
        conn.close()
        return res, data

    def test_add_focus_from_console(self):
        res, data = self.req("POST", "/api/focus", {"action": "add", "symbol": "nvda"}, cookie=self.cookie)
        self.assertEqual(res.status, 200, data)
        self.assertEqual(json.loads(data)["focus"], ["SPY", "NVDA"])
        res, data = self.req("GET", "/api/state", cookie=self.cookie)
        nvda = [f for f in json.loads(data)["focus"] if f["symbol"] == "NVDA"][0]
        self.assertEqual((nvda["focus"], nvda["trading"]), (True, False))

    def test_focus_api_needs_the_pin_and_json(self):
        res, _ = self.req("POST", "/api/focus", {"action": "add", "symbol": "NVDA"})
        self.assertEqual(res.status, 401)
        res, _ = self.req("POST", "/api/focus", {"action": "add", "symbol": "NVDA"}, cookie=self.cookie,
                          ctype="text/plain")
        self.assertEqual(res.status, 415)

    def test_console_cannot_switch_trading_on(self):
        res, _ = self.req("POST", "/api/focus", {"action": "trade", "symbol": "NVDA", "on": True}, cookie=self.cookie)
        self.assertEqual(res.status, 400)
        res, _ = self.req("POST", "/api/focus", {"action": "add", "symbol": "DROP TABLE"}, cookie=self.cookie)
        self.assertEqual(res.status, 400)
