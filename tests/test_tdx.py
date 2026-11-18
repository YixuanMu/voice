import unittest
from datetime import date, datetime

from backend.providers import CHINA
from backend.tdx import TdxAiDataProvider, parse_snapshot


class FakeSDK:
    def get_stock_list(self, market, list_type):
        return [{"Code": "600000.SH", "Name": "浦发银行"}, "920001.BJ", "000001.SZ", "TEST.HK"]

    def get_trading_dates(self, market, start, end):
        return [end]

    def get_sector_list(self, list_type):
        return [{"Code": "880001.SH", "Name": "测试行业"}]

    def get_stock_list_in_sector(self, code, block_type, list_type):
        return ["600000.SH", "TEST.HK"]

    def get_more_info_batch(self, stock_list, field_list, return_df):
        return {"600000.SH": {"HqDate": "20260918", "ZTPrice": "9.97", "DTPrice": "8.15"}}

    def get_market_snapshot_batch(self, stock_list, field_list, return_df):
        return {"600000.SH": {"Now": "9.07", "LastClose": "9.06", "Amount": "46996.94",
                              "RefreshTime": "095025"},
                "000001.SZ": {"Now": "15.01"}}


class TdxTests(unittest.TestCase):
    def test_documented_batch_schema_and_partial_response(self):
        provider = TdxAiDataProvider(FakeSDK())
        day = date(2026, 9, 18)
        stocks = provider.connect()
        self.assertTrue(provider.calendar(day))
        self.assertEqual({"600000.SH", "920001.BJ", "000001.SZ"}, set(stocks))
        sectors = provider.sectors(stocks)
        self.assertEqual({"测试行业": {"600000.SH"}}, sectors)
        frame = provider.scan(stocks, sectors, provider.metadata(stocks), day)
        self.assertEqual(3, frame.expected)
        quote, = frame.quotes
        self.assertEqual(9.97, quote.upper)
        self.assertEqual(8.15, quote.lower)
        self.assertAlmostEqual(469969400, quote.amount)
        self.assertEqual(datetime(2026, 9, 18, 9, 50, 25, tzinfo=CHINA).timestamp(), quote.ts)

    def test_stale_calendar_date_and_no_price_limits(self):
        sample = {"Now": "9.07", "LastClose": "9.06", "Amount": "1", "RefreshTime": "100000"}
        meta = {"HqDate": "20260918", "ZTPrice": "0", "DTPrice": "0"}
        self.assertIsNone(parse_snapshot("600000.SH", "样本", sample, meta, date(2026, 9, 21)))
        quote = parse_snapshot("600000.SH", "样本", sample, meta, date(2026, 9, 18))
        self.assertIsNone(quote.upper)
        self.assertIsNone(quote.lower)
        self.assertTrue(quote.limits_known)
