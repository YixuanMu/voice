import asyncio
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

CHINA = timezone(timedelta(hours=8))


@dataclass(frozen=True, slots=True)
class Quote:
    code: str
    name: str
    ts: float
    price: float
    previous_close: float
    amount: float
    upper: float | None = None
    lower: float | None = None
    suspended: bool = False
    limits_known: bool = True
    pct_3d: float | None = None
    pct_5d: float | None = None
    pct_10d: float | None = None
    main_flow_wan: float | None = None


@dataclass(slots=True)
class Batch:
    quotes: list[Quote]
    expected: int
    sectors: dict[str, set[str]]
    flow_sample: float | None = None


class MockDataProvider:
    label = "mock"

    def __init__(self, count=5000, interval=0.5):
        self.count = count
        self.interval = interval

    def frames(self, day_offset=0):
        start = (datetime(2026, 1, 5, 9, 30, tzinfo=CHINA)
                 + timedelta(days=day_offset)).timestamp()
        codes = [f"MOCK{i:05}" for i in range(self.count)]
        sectors = {"模拟·商业航天": set(codes[:30]),
                   "模拟·机器人": set(codes[30:60]),
                   "模拟·卫星通信": set(codes[:22] + codes[60:68])}
        amounts = [0] * self.count
        for tick in range(240):
            quotes = []
            for index, code in enumerate(codes):
                phase = tick % 36
                prices = ((index % 9) - 4) * 0.01
                step_amount = 100000 + index % 100 * 100
                if tick >= 20 and index < 12:
                    prices += min(phase, 8) * 0.06
                    if 2 <= phase <= 8:
                        step_amount *= 8
                if tick >= 20 and 12 <= index < 18 and 5 <= phase <= 12 and phase != 8:
                    prices = 1
                if tick >= 20 and 30 <= index < 35 and 10 <= phase <= 16:
                    prices = -1
                if 91 <= tick <= 112 and 30 <= index < 52:
                    prices += min(tick - 90, 8) * 0.07
                if index == self.count - 1:
                    prices = 1
                amounts[index] += step_amount
                quotes.append(Quote(code, f"模拟股票{index + 1}", start + tick * 10,
                                    round(10 + prices, 2), 10, amounts[index], 11, 9,
                                    pct_3d=float(index % 12 - 3), pct_5d=float(index % 15),
                                    pct_10d=float(index % 20 - 5),
                                    main_flow_wan=max(0, tick // 6 - 2) * 120 if index < 30 else 0))
            yield Batch(quotes, self.count, sectors, start + tick // 6 * 60)

    async def batches(self):
        day_offset = 0
        while True:
            for frame in self.frames(day_offset):
                yield frame
                await asyncio.sleep(self.interval)
            day_offset += 3 if day_offset % 7 == 4 else 1


class ReplayDataProvider:
    label = "replay"

    def __init__(self, path, interval=0.5):
        self.path = Path(path)
        self.interval = interval

    async def batches(self):
        with self.path.open(encoding="utf-8") as source:
            for line in source:
                data = json.loads(line)
                yield Batch([Quote(**row) for row in data["quotes"]], data["expected"],
                            {name: set(codes) for name, codes in data["sectors"].items()},
                            data.get("flow_sample"))
                await asyncio.sleep(self.interval)


def encode_batch(batch):
    return json.dumps({"quotes": [asdict(quote) for quote in batch.quotes],
                       "expected": batch.expected,
                       "flow_sample": batch.flow_sample,
                       "sectors": {name: sorted(codes) for name, codes in batch.sectors.items()}},
                      ensure_ascii=False)

