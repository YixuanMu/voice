import json
import unittest
from dataclasses import replace
from pathlib import Path

from backend.engine import PRIORITY, MarketEngine, Stock
from backend.providers import Batch, MockDataProvider


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.engine = MarketEngine(json.loads(Path("config/config.yaml").read_text()))
        self.first = next(MockDataProvider(2).frames()).quotes[0]

    def tick(self, offset, price):
        q = replace(self.first, ts=self.first.ts + offset, price=price,
                    amount=self.first.amount + max(0, offset) * 10000)
        return self.engine.ingest(Batch([q], 1, {}))

    def test_warmup_and_reseal(self):
        first, events = self.tick(0, 11)
        self.assertEqual((1, []), (first["up"], events))
        opened, _ = self.tick(10, 10)
        self.assertEqual(1, opened["opened"])
        sealed, _ = self.tick(20, 11)
        self.assertEqual(1, sealed["resealed"])
        self.assertEqual({"new": 0, "entries": 1, "repeat": 1}, sealed["windows"][5]["up"])
        self.assertIsNone(self.tick(20, 9))

    def test_first_entry_and_reconnect(self):
        self.tick(0, 10)
        first, _ = self.tick(10, 11)
        self.assertEqual(1, first["windows"][5]["up"]["new"])
        self.engine.disconnect()
        snapshot, events = self.tick(20, 9)
        self.assertEqual((0, 1, []), (snapshot["opened"], snapshot["down"], events))
        self.assertEqual(0, snapshot["windows"][5]["down"]["new"])

    def test_lunch_warmup(self):
        self.tick(0, 10)
        afternoon, events = self.tick(12600, 11)
        self.assertEqual((1, []), (afternoon["up"], events))
        self.assertEqual(0, afternoon["windows"][5]["up"]["entries"])

    def test_auction_alert_does_not_create_limit_transition_at_open(self):
        auction, events = self.tick(-900, 11)
        self.assertTrue(auction["auction"])
        self.assertEqual(0, auction["up"])
        self.assertIn("开盘集合竞价", events[0].text)
        self.assertIsNone(self.tick(-200, 11))
        opened, events = self.tick(0, 11)
        self.assertFalse(opened["auction"])
        self.assertEqual((1, 0, []), (opened["up"], opened["windows"][5]["up"]["entries"], events))

    def test_partial_coverage_suppresses_market_summary(self):
        self.engine.settings["market_limit_alert"] = 1
        self.tick(0, 10)
        q = replace(self.first, ts=self.first.ts + 10, price=11)
        snapshot, events = self.engine.ingest(Batch([q], 2, {}))
        self.assertFalse(snapshot["reliable"])
        self.assertEqual(1, snapshot["windows"][5]["up"]["new"])
        self.assertFalse(any(event.kind == "market" for event in events))

    def test_overlapping_sectors_and_temporal_followers(self):
        sectors = {"行业": {"A", "B", "C"}, "概念": {"A", "B", "C"}}
        self.engine.settings["sector_coverage"] = 1.0
        first = {code: (change, 1, 1) for code, change in [("A", 2), ("B", 0), ("C", 0)]}
        self.engine.stocks["A"] = Stock(self.first)
        self.engine.detect_sectors(sectors, first, set(), 100, [])
        second = {code: (3, 1, 1) for code in first}
        events = []
        rows = self.engine.detect_sectors(sectors, second, set(), 110, events)
        self.assertEqual(1, len(events))
        self.assertTrue(all(row["followers"] == 2 for row in rows))

    def test_full_universe_detects_volume_and_sector(self):
        kinds = set()
        highest_new = {"up": 0, "down": 0}
        strongest = 0
        for index, batch in enumerate(MockDataProvider().frames()):
            snapshot, events = self.engine.ingest(batch)
            for event in events:
                kinds.add(event.kind)
                self.assertEqual(event.kind == "stock", event.text.startswith("教练教练请注意"))
                if event.kind == "sector":
                    self.assertIn("今日涨幅前5名", event.text)
                    for period in ("三日", "五日", "十日"):
                        self.assertEqual(5, event.text.count(period))
                    self.assertTrue(event.standalone)
            strongest = max(strongest, *(row["ratio"] for row in snapshot["volume"]), 0)
            for side in highest_new:
                highest_new[side] = max(highest_new[side], snapshot["windows"][5][side]["new"])
            if index == 68:
                break
        self.assertEqual(5000, snapshot["covered"])
        self.assertEqual({"up": 6, "down": 5}, highest_new)
        self.assertGreater(strongest, 3)
        self.assertTrue(set(PRIORITY) - {"sector_rotation"} <= kinds,
                        set(PRIORITY) - kinds)

    def test_sector_rotation_requires_sustained_distinct_leader(self):
        events = []
        sectors = {"甲": {"A"}, "乙": {"B"}}
        self.engine.detect_sector_rotation([{"name": "甲", "pct": 1}], sectors, 100, events)
        self.engine.detect_sector_rotation([{"name": "乙", "pct": 2}], sectors, 110, events)
        self.assertEqual([], events)
        self.engine.detect_sector_rotation([{"name": "乙", "pct": 2}], sectors, 131, events)
        event, = events
        self.assertEqual("sector_rotation", event.kind)
        self.assertIn("由甲切换至乙", event.text)

