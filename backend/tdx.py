"""Read-only integration using the official TdxAiData batch snapshot API."""
import asyncio
import json
import os
from datetime import datetime, timedelta
from math import isfinite
from pathlib import Path

from backend.engine import session
from backend.providers import CHINA, Batch, Quote

SNAPSHOT_FIELDS = ["Now", "LastClose", "RefreshTime", "Amount", "ZAFPre3"]
META_FIELDS = ["HqDate", "ZTPrice", "DTPrice", "StopJYDate_Recent", "ZAFPre5", "ZAFPre10", "Zjl_HB"]


class TdxConnectionError(RuntimeError):
    pass


def optional_number(value):
    try:
        number = float(value)
        return number if isfinite(number) else None
    except (ValueError, TypeError):
        return None


def positive_number(value):
    number = optional_number(value)
    return number if number is not None and number > 0 else None


def parse_snapshot(code, name, row, meta, day):
    day_key = day.strftime("%Y%m%d")
    # RefreshTime contains no date. The separate trading-data day is required.
    if str(meta.get("HqDate")) != day_key:
        return None
    try:
        clock = str(row["RefreshTime"]).zfill(6)
        stamp = datetime.strptime(day_key + clock, "%Y%m%d%H%M%S").replace(tzinfo=CHINA)
        price, close = float(row["Now"]), float(row["LastClose"])
        amount = float(row["Amount"]) * 10000
        if not all(isfinite(value) for value in (price, close, amount)):
            return None
        if min(price, close) <= 0 or amount < 0:
            return None
    except (KeyError, ValueError, TypeError):
        return None
    upper = positive_number(meta.get("ZTPrice"))
    lower = positive_number(meta.get("DTPrice"))
    has_limits = (upper is not None and lower is not None) or all(
        str(meta.get(key)).strip() in {"0", "0.0", "0.00", "0.000"}
        for key in ("ZTPrice", "DTPrice"))
    suspended = str(meta.get("StopJYDate_Recent")) == day_key and amount == 0
    return Quote(code, name, stamp.timestamp(), price, close, amount, upper, lower,
                 suspended, has_limits, optional_number(row.get("ZAFPre3")),
                 optional_number(meta.get("ZAFPre5")), optional_number(meta.get("ZAFPre10")),
                 optional_number(meta.get("Zjl_HB")))


def code_names(items):
    result = {}
    for row in items or []:
        if isinstance(row, dict) and row.get("Code"):
            result[row["Code"]] = row.get("Name") or row["Code"]
        elif isinstance(row, str):
            result[row] = row
    return result


class TdxAiDataProvider:
    label = "tdx"

    def __init__(self, sdk=None):
        self.sdk = sdk
        self.chunk = int(os.getenv("VOICE_TDX_CHUNK", "200"))
        self.interval = float(os.getenv("VOICE_TDX_INTERVAL", "8"))
        self.sector_file = os.getenv("VOICE_TDX_SECTORS")
        if not 1 <= self.chunk <= 500 or not 1 <= self.interval <= 120:
            raise ValueError("VOICE_TDX_CHUNK must be 1–500 and VOICE_TDX_INTERVAL 1–120.")

    def connect(self):
        if self.sdk is None:
            from tdxaidata import tqs
            self.sdk = tqs
        items = code_names(self.sdk.get_stock_list(market="5", list_type=1))
        stocks = {code: name for code, name in items.items() if code.endswith((".SH", ".SZ", ".BJ"))}
        if not stocks:
            raise TdxConnectionError("通达信未返回股票列表。请检查数据服务 Key、账户积分及网络。")
        return stocks

    def calendar(self, day):
        end = day.strftime("%Y%m%d")
        start = (day - timedelta(days=14)).strftime("%Y%m%d")
        days = self.sdk.get_trading_dates("SH", start, end)
        if not days:
            raise RuntimeError("TdxAiData returned no trading calendar")
        return end in {str(item) for item in days}

    def sectors(self, stocks):
        if self.sector_file:
            data = json.loads(Path(self.sector_file).read_text(encoding="utf-8"))
            return {name: set(codes).intersection(stocks) for name, codes in data.items()}
        result = {}
        for code, name in code_names(self.sdk.get_sector_list(list_type=1)).items():
            members = self.sdk.get_stock_list_in_sector(code, block_type=0, list_type=0)
            covered = set(code_names(members)).intersection(stocks)
            if covered:
                result[name if name not in result else f"{name} ({code})"] = covered
        return result

    def fetch(self, fn, stocks, fields):
        codes = list(stocks)
        result = {}
        for index in range(0, len(codes), self.chunk):
            chunk = codes[index:index + self.chunk]
            rows = fn(stock_list=chunk, field_list=fields, return_df=False)
            if isinstance(rows, dict):
                result.update({code: rows[code] for code in chunk
                               if isinstance(rows.get(code), dict) and rows[code]})
        return result

    def metadata(self, stocks):
        return self.fetch(self.sdk.get_more_info_batch, stocks, META_FIELDS)

    def scan(self, stocks, sectors, meta, day):
        rows = self.fetch(self.sdk.get_market_snapshot_batch, stocks, SNAPSHOT_FIELDS)
        quotes = []
        for code, row in rows.items():
            quote = parse_snapshot(code, stocks[code], row, meta.get(code, {}), day)
            if quote:
                quotes.append(quote)
        return Batch(quotes, len(stocks), sectors)

    def scan_auction(self, stocks, previous, day):
        day_key = day.strftime("%Y%m%d")

        def fetch_auction(**kwargs):
            return self.sdk.get_call_auction_batch(type=1, date=day_key, **kwargs)

        rows = self.fetch(fetch_auction, stocks, ["Time", "Price"])
        quotes = []
        for code, row in rows.items():
            close = positive_number(previous.get(code, {}).get("LastClose"))
            clocks, prices = row.get("Time"), row.get("Price")
            if close is None or not isinstance(clocks, list) or not isinstance(prices, list):
                continue
            points = []
            for clock, value in zip(clocks, prices):
                digits = str(clock).zfill(6)
                price = positive_number(value)
                if len(digits) == 6 and digits.isascii() and digits.isdigit() and price is not None:
                    if "091500" <= digits <= "092500" and int(digits[-2:]) < 60:
                        points.append((digits, price))
            if points:
                clock, price = max(points, key=lambda point: point[0])
                stamp = datetime.strptime(day_key + clock, "%Y%m%d%H%M%S").replace(tzinfo=CHINA)
                quotes.append(Quote(code, stocks[code], stamp.timestamp(), price, close, 0,
                                    limits_known=False))
        return Batch(quotes, len(stocks), {})

    async def batches(self):
        day = None
        stocks = None
        meta = {}
        sectors = None
        previous = None
        while True:
            now = datetime.now(CHINA)
            segment = session(now.timestamp())
            if segment is None:
                yield None
                await asyncio.sleep(self.interval)
                continue
            if now.date() != day:
                stocks = await asyncio.to_thread(self.connect)
                trading = await asyncio.to_thread(self.calendar, now.date())
                day, meta = now.date(), {}
                previous, sectors = None, None
                meta_checked = 0
            if not trading:
                yield None
            elif segment[1] == -1:
                if previous is None:
                    previous = await asyncio.to_thread(self.fetch, self.sdk.get_market_snapshot_batch,
                                                       stocks, ["LastClose"])
                yield await asyncio.to_thread(self.scan_auction, stocks, previous, day)
                await asyncio.sleep(max(30, self.interval))
                continue
            else:
                if sectors is None:
                    sectors = await asyncio.to_thread(self.sectors, stocks)
                if now.timestamp() - meta_checked >= 60:
                    meta = await asyncio.to_thread(self.metadata, stocks)
                    meta_checked = now.timestamp()
                batch = await asyncio.to_thread(self.scan, stocks, sectors, meta, day)
                batch.flow_sample = meta_checked
                yield batch
            await asyncio.sleep(self.interval)
