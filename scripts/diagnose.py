import argparse
import asyncio
import json
from collections import Counter
from datetime import datetime
from time import perf_counter

from backend.providers import CHINA, MockDataProvider
from backend.server import dotenv
from backend.tdx import TdxAiDataProvider


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", choices=("mock", "tdx"), default="mock")
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--sectors", action="store_true")
    parser.add_argument("--subscribe", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.rounds <= 30:
        parser.error("--rounds must be between 1 and 30")
    if args.source == "mock":
        batch = next(MockDataProvider().frames())
        print(json.dumps({"source": "mock", "expected": batch.expected,
                          "received": len(batch.quotes), "sectors": len(batch.sectors)}))
        return
    dotenv()
    provider = TdxAiDataProvider()
    stocks = await asyncio.to_thread(provider.connect)
    day = datetime.now(CHINA).date()
    trading = await asyncio.to_thread(provider.calendar, day)
    print(json.dumps({"source": "tdx", "trading_day": trading,
                      "universe_by_market": Counter(code[-2:] for code in stocks)}), flush=True)
    sectors = await asyncio.to_thread(provider.sectors, stocks) if args.sectors else {}
    if args.sectors:
        print(json.dumps({"sectors_with_members": len(sectors)}), flush=True)
    meta = await asyncio.to_thread(provider.metadata, stocks)
    for _ in range(args.rounds):
        start = perf_counter()
        batch = await asyncio.to_thread(provider.scan, stocks, sectors, meta, day)
        now = datetime.now(CHINA).timestamp()
        lags = sorted(now - quote.ts for quote in batch.quotes)
        print(json.dumps({"scan_seconds": round(perf_counter() - start, 3),
                          "snapshot_count": len(lags), "expected": batch.expected,
                          "limit_fields_count": sum(q.limits_known for q in batch.quotes),
                          "fresh_under_45s_count": sum(0 <= lag <= 45 for lag in lags),
                          "lag_p50_seconds": round(lags[len(lags) // 2], 2) if lags else None,
                          "lag_p95_seconds": round(lags[int((len(lags) - 1) * .95)], 2) if lags else None}),
              flush=True)
        await asyncio.sleep(provider.interval)
    if args.subscribe:
        loop = asyncio.get_running_loop()
        arrived = asyncio.Event()
        samples = list(stocks)[:1]
        def receive(data):
            try:
                response = json.loads(data)
            except (ValueError, TypeError):
                return
                if isinstance(response, dict) and response.get("ResultSets") and not response.get("ErrorId"):
                loop.call_soon_threadsafe(arrived.set)
        provider.sdk.subscribe(stock_list=samples, callback=receive)
        try:
            await asyncio.wait_for(arrived.wait(), timeout=30)
        except TimeoutError:
            print("subscription: no valid callback within 30 seconds")
        else:
            print("subscription: valid callback received for one symbol")
        finally:
            provider.sdk.unsubscribe(samples)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as error:
        raise SystemExit(f"Diagnosis failed ({type(error).__name__}). Check SDK, Key, permissions and network.") from None
