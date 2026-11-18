from collections import Counter, defaultdict, deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, time
from math import isfinite
from statistics import mean

from backend.providers import CHINA, Quote


def session(ts):
    dt = datetime.fromtimestamp(ts, CHINA)
    if dt.weekday() >= 5:
        return None
    clock = dt.time()
    if time(9, 30) <= clock <= time(11, 30):
        return dt.date(), 0
    if time(13) <= clock <= time(15):
        return dt.date(), 1
    return None


def limit(q):
    if q.suspended or not q.limits_known:
        return ""
    if q.upper and q.price >= q.upper - 0.001:
        return "up"
    if q.lower and q.price <= q.lower + 0.001:
        return "down"
    return ""


@dataclass(slots=True)
class Event:
    ts: float
    kind: str
    key: str
    text: str
    priority: int = 30


@dataclass(slots=True)
class Stock:
    quote: Quote
    history: deque = field(default_factory=deque)
    touched: set = field(default_factory=set)


def window_metrics(history, q, width, baseline_windows, tolerance):
    anchors = []
    for old in reversed(history):
        target = q.ts - width * (len(anchors) + 1)
        if old.ts <= target:
            if old.ts < target - tolerance:
                break
            anchors.append(old)
            if len(anchors) > baseline_windows:
                break
    if not anchors:
        return None
    first = anchors[0]
    change = (q.price / first.price - 1) * 100
    amount = q.amount - first.amount
    baseline = None
    if len(anchors) > baseline_windows:
        baseline = (first.amount - anchors[-1].amount) / (first.ts - anchors[-1].ts)
    expected = baseline * (q.ts - first.ts) if baseline else 0
    return change, amount, expected


class MarketEngine:
    def __init__(self, settings):
        self.settings = settings
        self.stocks = {}
        self.transitions = deque()
        self.totals = Counter()
        self.opened_stocks = set()
        self.resealed_stocks = set()
        self.trend = deque(maxlen=120)
        self.sector_leaders = {}
        self.clock = None
        self.segment = None
        self.reset_pending = True

    def disconnect(self):
        self.reset_pending = True

    def ingest(self, batch, now=None):
        cfg = self.settings
        rows = [q for q in batch.quotes if all(isfinite(x) for x in
                (q.ts, q.price, q.previous_close, q.amount))
                and q.ts > 0 and q.price > 0 and q.previous_close > 0 and q.amount >= 0]
        if not rows:
            return None
        ts = max(q.ts for q in rows)
        segment = session(ts)
        if segment is None or (self.clock is not None and ts <= self.clock):
            return None
        if now is not None and (now - ts > cfg["late_seconds"] or ts - now > 5):
            self.disconnect()
            return None
        day_changed = self.segment is None or segment[0] != self.segment[0]
        if day_changed:
            self.stocks.clear()
            self.totals.clear()
            self.opened_stocks.clear()
            self.resealed_stocks.clear()
            self.trend.clear()
        reset = (self.reset_pending or segment != self.segment or self.clock is None
                 or ts - self.clock > cfg["gap_seconds"])
        if reset:
            self.transitions.clear()
            self.sector_leaders.clear()
            for state in self.stocks.values():
                state.history.clear()
        self.clock, self.segment, self.reset_pending = ts, segment, False
        events, volume, moves = [], [], {}
        for q in sorted(rows, key=lambda quote: quote.ts):
            if session(q.ts) != segment or ts - q.ts > cfg["late_seconds"]:
                continue
            state = self.stocks.get(q.code)
            current = limit(q)
            if state is None:
                state = self.stocks[q.code] = Stock(q)
            elif q.ts <= state.quote.ts:
                continue
            else:
                previous = limit(state.quote)
                continuous = (not reset and not q.suspended and not state.quote.suspended
                              and q.ts - state.quote.ts <= cfg["gap_seconds"]
                              and q.amount >= state.quote.amount)
                if continuous:
                    boost = 40 if q.code in cfg["watchlist"] else 0
                    if state.quote.limits_known and q.limits_known and previous != current:
                        if previous == "up":
                            self.totals["opened"] += 1
                            self.opened_stocks.add(q.code)
                            events.append(Event(ts, "open", q.code, f"{q.name}涨停开板", 65 + boost))
                        if current:
                            first = current not in state.touched
                            self.transitions.append((ts, current, first, q.code))
                            if current == "up":
                                if not first:
                                    self.totals["resealed"] += 1
                                    self.resealed_stocks.add(q.code)
                                    events.append(Event(ts, "reseal", q.code,
                                                        f"{q.name}重新封住涨停", 75 + boost))
                    self.detect_stock(q, state, events, volume, moves)
                else:
                    state.history.clear()
            if current:
                state.touched.add(current)
            state.quote = q
            if not q.suspended:
                state.history.append(q)
                cutoff = q.ts - 180 * (cfg["baseline_windows"] + 1) - cfg["gap_seconds"]
                while state.history[0].ts < cutoff:
                    state.history.popleft()

        while self.transitions and self.transitions[0][0] <= ts - 600:
            self.transitions.popleft()
        counts = Counter()
        for state in self.stocks.values():
            q = state.quote
            if ts - q.ts <= cfg["late_seconds"]:
                counts["covered"] += 1
                counts["limit_covered"] += q.limits_known or q.suspended
                if q.suspended:
                    counts["suspended"] += 1
                    continue
                counts[limit(q)] += 1
                counts["rising"] += q.price > q.previous_close
                counts["falling"] += q.price < q.previous_close
        coverage = min(1.0, counts["covered"] / max(1, batch.expected))
        limit_coverage = min(1.0, counts["limit_covered"] / max(1, batch.expected))
        windows = {}
        for minutes in (1, 3, 5, 10):
            transitions = [(side, first, code) for stamp, side, first, code in self.transitions
                           if stamp > ts - minutes * 60]
            windows[minutes] = {
                side: {"new": len({code for direction, first, code in transitions
                                   if direction == side and first}),
                       "entries": sum(direction == side for direction, _, _ in transitions),
                       "repeat": sum(direction == side and not first for direction, first, _ in transitions)}
                for side in ("up", "down")
            }
        five = windows[5]
        threshold = cfg["market_limit_alert"]
        if limit_coverage >= cfg["coverage"] and any(side["new"] >= threshold for side in five.values()):
            events.append(Event(ts, "market", "limits", f"最近五分钟，全市场新增"
                                f"{five['up']['new']}只涨停股票，新增{five['down']['new']}只跌停股票", 90))

        recent_up = {code for stamp, side, _, code in self.transitions
                     if side == "up" and stamp > ts - 180}
        sector_rows = self.detect_sectors(batch.sectors, moves, recent_up, ts, events)
        self.trend.append({"ts": ts, "up": counts["rising"], "down": counts["falling"]})
        snapshot = {"ts": ts, "up": counts["up"], "down": counts["down"],
                    "rising": counts["rising"], "falling": counts["falling"],
                    "covered": counts["covered"], "expected": batch.expected, "coverage": coverage,
                    "limit_coverage": limit_coverage,
                    "reliable": limit_coverage >= cfg["coverage"], "suspended": counts["suspended"],
                    "opened": self.totals["opened"], "resealed": self.totals["resealed"],
                    "opened_stocks": len(self.opened_stocks), "resealed_stocks": len(self.resealed_stocks),
                    "windows": windows, "trend": list(self.trend), "sectors": sector_rows[:15],
                    "volume": sorted(volume, key=lambda row: -row["ratio"])[:15]}
        return snapshot, sorted(events, key=lambda event: (-event.priority, event.key))

    def detect_stock(self, q, state, events, volume, moves):
        cfg = self.settings
        for minutes in (1, 3):
            metrics = window_metrics(state.history, q, minutes * 60, cfg["baseline_windows"],
                                     cfg["gap_seconds"])
            if metrics is None:
                continue
            change, amount, expected = metrics
            if minutes == 3:
                moves[q.code] = (change, amount, expected)
            ratio = amount / expected if expected else 0
            spike = ratio >= cfg[f"amount_{minutes}m"] and amount >= cfg["min_amount"]
            rising = change >= cfg[f"rise_{minutes}m"]
            falling = change <= -cfg[f"fall_{minutes}m"]
            if spike:
                volume.append({"code": q.code, "name": q.name, "minutes": minutes,
                               "ratio": round(ratio, 2), "pct": round(change, 2)})
            if spike or rising or falling:
                details = []
                if spike:
                    details.append(f"成交额达到基线的{ratio:.1f}倍")
                if rising or falling:
                    direction = "上涨" if rising else "下跌"
                    details.append(f"股价{direction}百分之{abs(change):.2f}")
                priority = 55 if spike and rising else 35
                priority += 40 if q.code in cfg["watchlist"] else 0
                events.append(Event(q.ts, "stock", q.code,
                                    f"{q.name}，{minutes}分钟" + "，".join(details), priority))

    def detect_sectors(self, sectors, moves, recent_up, ts, events):
        cfg = self.settings
        rows, candidates, next_leaders = [], [], {}
        for name, codes in sectors.items():
            covered = codes & moves.keys()
            entered = len(codes & recent_up)
            if not codes or len(covered) / len(codes) < cfg["sector_coverage"]:
                continue
            changes = {code: moves[code][0] for code in covered}
            if not changes:
                continue
            pct = mean(changes.values())
            gaining = {code for code, change in changes.items() if change >= cfg["sector_stock_pct"]}
            falling = {code for code, change in changes.items() if change <= -cfg["sector_stock_pct"]}
            prior_leader, since, previous_gainers = self.sector_leaders.get(name, ("", 0, set()))
            leader = prior_leader if prior_leader in gaining else ""
            followers = gaining - previous_gainers - {leader}
            follows = (bool(leader) and 0 < ts - since <= 180
                       and len(followers) >= cfg["sector_members"] - 1 and bool(followers))
            if gaining:
                selected = leader or min(gaining, key=lambda code: (-changes[code], code))
                next_leaders[name] = (selected, since if leader else ts, gaining)
            expected = sum(moves[code][2] for code in covered)
            amount = sum(moves[code][1] for code in covered if moves[code][2] > 0)
            ratio = amount / expected if expected else 0
            row = {"name": name, "pct": round(pct, 2), "fast": len(gaining), "falling": len(falling),
                   "limits": entered, "ratio": round(ratio, 2), "covered": len(covered)}
            if follows:
                row["leader"] = self.stocks[leader].quote.name
                row["followers"] = len(followers)
            rows.append(row)
            if (abs(pct) >= cfg["sector_pct"] or max(len(gaining), len(falling)) >= cfg["sector_members"]
                    or entered >= cfg["sector_limits"] or ratio >= cfg["sector_amount"] or follows):
                active = gaining | falling | (codes & recent_up)
                score = abs(pct) + len(active) + ratio
                candidates.append((score, name, active or covered, row))
        self.sector_leaders = next_leaders
        accepted = []
        for _, name, active, row in sorted(candidates, key=lambda item: (-item[0], item[1])):
            if any(len(active & other) / min(len(active), len(other)) >= cfg["sector_overlap"]
                   for other in accepted):
                continue
            accepted.append(active)
            direction = "上涨" if row["pct"] >= 0 else "下跌"
            details = (f"过去三分钟平均{direction}百分之{abs(row['pct']):.2f}，"
                       f"{row['fast']}只快速上涨，{row['falling']}只快速下跌")
            if row["limits"]:
                details += f"，过去三分钟{row['limits']}只触及涨停"
            if row["ratio"]:
                details += f"，成交额达到基线的{row['ratio']:.1f}倍"
            if "leader" in row:
                details += f"，{row['leader']}率先拉升后，另有{row['followers']}只成分股达到拉升阈值"
            events.append(Event(ts, "sector", name, f"注意，{name}板块异动，{details}", 80))
        return sorted(rows, key=lambda row: (-abs(row["pct"]), row["name"]))

