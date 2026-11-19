from collections import Counter, deque
from dataclasses import dataclass, field
from datetime import datetime, time
from math import isfinite
from statistics import mean

from backend.providers import CHINA, Quote

PRIORITY = {
    "stock": 90, "sector": 80, "sector_flow": 70, "sector_accumulation": 60,
    "sector_rotation": 50, "market": 40, "reseal": 40, "rapid_rise": 30,
    "open": 20, "open_down": 10,
}


def ranked_members(quotes):
    leaders = sorted(quotes, key=lambda q: (-q.price / q.previous_close, q.code))[:5]
    if not leaders:
        return ""
    parts = []
    for q in leaders:
        periods = []
        for days, pct in (("三", q.pct_3d), ("五", q.pct_5d), ("十", q.pct_10d)):
            if pct is None or not isfinite(pct):
                periods.append(f"{days}日暂无")
            else:
                direction = "涨" if pct >= 0 else "跌"
                periods.append(f"{days}日{direction}百分之{abs(pct):.2f}")
        parts.append("，".join([q.name, *periods]))
    return f"。今日涨幅前{len(leaders)}名。" + "。".join(parts)


def session(ts):
    dt = datetime.fromtimestamp(ts, CHINA)
    if dt.weekday() >= 5:
        return None
    clock = dt.time()
    if time(9, 15) <= clock < time(9, 25, 1):
        return dt.date(), -1
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
    standalone: bool = False


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
        self.sector_front = None
        self.flow_sample = None
        self.previous_flows = {}
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
        auction = segment[1] == -1
        if self.segment is not None and self.segment[1] == -1 and not auction:
            self.stocks.clear()
            self.trend.clear()
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
            self.sector_front = None
            self.flow_sample = None
            self.previous_flows.clear()
            for state in self.stocks.values():
                state.history.clear()
        self.clock, self.segment, self.reset_pending = ts, segment, False
        events, volume, moves = [], [], {}
        for q in sorted(rows, key=lambda quote: quote.ts):
            if session(q.ts) != segment or ts - q.ts > cfg["late_seconds"]:
                continue
            state = self.stocks.get(q.code)
            current = "" if auction else limit(q)
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
                    boost = 2 if q.code in cfg["watchlist"] else 0
                    if not auction and state.quote.limits_known and q.limits_known and previous != current:
                        if previous == "up":
                            self.totals["opened"] += 1
                            self.opened_stocks.add(q.code)
                            events.append(Event(ts, "open", q.code, f"{q.name}打开涨停",
                                                PRIORITY["open"] + boost))
                        if previous == "down":
                            events.append(Event(ts, "open_down", q.code, f"{q.name}打开跌停",
                                                PRIORITY["open_down"] + boost))
                        if current:
                            first = current not in state.touched
                            self.transitions.append((ts, current, first, q.code))
                            if current == "up":
                                if not first:
                                    self.totals["resealed"] += 1
                                    self.resealed_stocks.add(q.code)
                                    events.append(Event(ts, "reseal", q.code,
                                                        f"{q.name}重新封住涨停", PRIORITY["reseal"] + boost))
                    if not auction:
                        self.detect_stock(q, state, events, volume, moves)
                else:
                    state.history.clear()
            if current:
                state.touched.add(current)
            if auction:
                change = (q.price / q.previous_close - 1) * 100
                if change >= cfg["rise_3m"] or change <= -cfg["fall_3m"]:
                    direction = "高于" if change > 0 else "低于"
                    events.append(Event(q.ts, "stock", q.code,
                                        f"教练教练请注意，开盘集合竞价，{q.name}虚拟撮合价"
                                        f"{direction}昨收百分之{abs(change):.2f}", PRIORITY["stock"]))
            state.quote = q
            if not q.suspended:
                state.history.append(q)
                cutoff = q.ts - 180 * (cfg["baseline_windows"] + 1) - cfg["gap_seconds"]
                while state.history[0].ts < cutoff:
                    state.history.popleft()

        while self.transitions and self.transitions[0][0] <= ts - 600:
            self.transitions.popleft()
        counts = Counter()
        fresh = {}
        for state in self.stocks.values():
            q = state.quote
            if ts - q.ts <= cfg["late_seconds"]:
                counts["covered"] += 1
                counts["limit_covered"] += not auction and (q.limits_known or q.suspended)
                if q.suspended:
                    counts["suspended"] += 1
                    continue
                fresh[q.code] = q
                if not auction:
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
                                f"{five['up']['new']}只涨停股票，新增{five['down']['new']}只跌停股票",
                                PRIORITY["market"]))

        recent_up = {code for stamp, side, _, code in self.transitions
                     if side == "up" and stamp > ts - 180}
        sector_rows = self.detect_sectors(batch.sectors, moves, recent_up, ts, events, fresh)
        self.detect_sector_rotation(sector_rows, batch.sectors, ts, events)
        if batch.flow_sample is not None and batch.flow_sample != self.flow_sample:
            sample_quotes = {q.code: q for q in rows if q.code in fresh and q.ts == fresh[q.code].ts}
            self.detect_sector_flows(batch.sectors, sample_quotes, batch.flow_sample, ts, events)
        self.trend.append({"ts": ts, "up": counts["rising"], "down": counts["falling"]})
        snapshot = {"ts": ts, "auction": auction, "up": counts["up"], "down": counts["down"],
                    "rising": counts["rising"], "falling": counts["falling"],
                    "covered": counts["covered"], "expected": batch.expected, "coverage": coverage,
                    "limit_coverage": limit_coverage,
                    "reliable": limit_coverage >= cfg["coverage"], "suspended": counts["suspended"],
                    "opened": self.totals["opened"], "resealed": self.totals["resealed"],
                    "opened_stocks": len(self.opened_stocks), "resealed_stocks": len(self.resealed_stocks),
                    "windows": windows, "trend": list(self.trend), "sectors": sector_rows[:15],
                    "volume": sorted(volume, key=lambda row: -row["ratio"])[:15]}
        unusual = {event.key for event in events if event.kind == "stock"}
        events = [event for event in events if event.kind != "rapid_rise" or event.key not in unusual]
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
                kind = "stock" if spike or falling else "rapid_rise"
                priority = PRIORITY[kind] + (2 if q.code in cfg["watchlist"] else 0)
                priority += int(spike and rising)
                prefix = "教练教练请注意，" if kind == "stock" else "快速涨幅，"
                events.append(Event(q.ts, kind, q.code,
                                    f"{prefix}{q.name}，{minutes}分钟" + "，".join(details), priority))

    def detect_sectors(self, sectors, moves, recent_up, ts, events, fresh=None):
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
            ranking = ranked_members(fresh[code] for code in (sectors[name] & fresh.keys())) if fresh else ""
            events.append(Event(ts, "sector", name, f"板块热点，{name}，{details}{ranking}",
                                PRIORITY["sector"], standalone=bool(ranking)))
        return sorted(rows, key=lambda row: (-abs(row["pct"]), row["name"]))

    def detect_sector_rotation(self, rows, sectors, ts, events):
        # Use a sustained change in the strongest three-minute advancing sector.
        rising = [row for row in rows if row["pct"] >= self.settings["sector_pct"]]
        if not rising:
            return
        name = max(rising, key=lambda row: row["pct"])["name"]
        previous = self.sector_front
        if previous is None or ts - previous[2] > 180:
            self.sector_front = (name, None, ts)
        elif name == previous[0]:
            self.sector_front = (name, None, ts)
        elif name != previous[1]:
            self.sector_front = (previous[0], name, ts)
        elif ts - previous[2] >= 20:
            old, new = sectors.get(previous[0], set()), sectors[name]
            if old and len(old & new) / min(len(old), len(new)) < self.settings["sector_overlap"]:
                events.append(Event(ts, "sector_rotation", f"{previous[0]}->{name}",
                                    f"板块轮动，三分钟领涨板块由{previous[0]}切换至{name}",
                                    PRIORITY["sector_rotation"]))
            self.sector_front = (name, None, ts)

    def detect_sector_flows(self, sectors, fresh, sample, ts, events):
        if sample is None or (self.flow_sample is not None and sample <= self.flow_sample):
            return
        prior_sample = self.flow_sample
        self.flow_sample = sample
        old_flows = self.previous_flows
        self.previous_flows = {}
        flows, accumulating = [], []
        for name, members in sectors.items():
            current = {code: fresh[code].main_flow_wan for code in members & fresh.keys()
                       if fresh[code].main_flow_wan is not None and isfinite(fresh[code].main_flow_wan)}
            if not members or len(current) / len(members) < self.settings["sector_coverage"]:
                continue
            self.previous_flows[name] = current
            total = sum(current.values())
            if abs(total) >= self.settings.get("sector_flow_wan", 5000):
                flows.append((abs(total), name, total, len(current), len(members)))
            if prior_sample is None or not 30 <= sample - prior_sample <= 180:
                continue
            prior = old_flows.get(name, {})
            common = current.keys() & prior.keys()
            if len(common) / len(members) < self.settings["sector_coverage"]:
                continue
            changes = [current[code] - prior[code] for code in common]
            increase = sum(changes)
            gaining = sum(value > 0 for value in changes)
            if increase >= self.settings.get("sector_accumulation_wan", 2000) and gaining > len(changes) / 2:
                accumulating.append((increase, name, gaining, len(common)))
        if flows:
            _, name, total, count, size = max(flows)
            direction = "净流入" if total > 0 else "净流出"
            events.append(Event(ts, "sector_flow", name,
                                f"板块资金，{name}已覆盖{count}只，共{size}只，"
                                f"主力{direction}{abs(total):.0f}万元", PRIORITY["sector_flow"]))
        if accumulating:
            amount, name, gaining, count = max(accumulating)
            events.append(Event(ts, "sector_accumulation", name,
                                f"板块增仓信号，{name}近两次采样主力净流入增加{amount:.0f}万元，"
                                f"{count}只中有{gaining}只净流入增加", PRIORITY["sector_accumulation"]))

