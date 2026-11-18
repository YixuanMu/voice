import argparse
import asyncio
import json
import os
import sqlite3
import webbrowser
from dataclasses import asdict
from pathlib import Path
from time import time

from tornado.web import Application, HTTPError, RequestHandler, StaticFileHandler
from tornado.websocket import WebSocketHandler

from backend.engine import MarketEngine
from backend.providers import MockDataProvider, ReplayDataProvider
from backend.speech import Announcer

ROOT = Path(__file__).resolve().parents[1]


class Store:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.execute("""CREATE TABLE IF NOT EXISTS log(
            id INTEGER PRIMARY KEY, type TEXT, source TEXT, received REAL, payload TEXT)""")

    def add(self, kind, source, payload):
        with self.db:
            self.db.execute("INSERT INTO log(type, source, received, payload) VALUES(?, ?, ?, ?)",
                            (kind, source, time(), json.dumps(payload, ensure_ascii=False)))
            self.db.execute("DELETE FROM log WHERE id <= (SELECT MAX(id) - 5000 FROM log)")

    def recent(self, source):
        rows = self.db.execute("""
            SELECT type, received, payload FROM log WHERE source = ? ORDER BY id DESC LIMIT 100
        """, (source,))
        return [{"type": kind, "received": stamp, **json.loads(payload)}
                for kind, stamp, payload in rows]


def validate(settings, changes):
    updated = settings.copy()
    for key, value in changes.items():
        if key not in settings:
            raise ValueError(f"Unknown setting: {key}")
        sample = settings[key]
        if key == "watchlist":
            if not isinstance(value, list) or len(value) > 200 or any(
                    not isinstance(code, str) or not 1 <= len(code) <= 24 for code in value):
                raise ValueError(key)
        elif type(sample) is bool:
            if type(value) is not bool:
                raise ValueError(key)
        else:
            low, high = {
                "rate": (-10, 10), "volume": (0, 100), "coverage": (0.01, 1),
                "sector_coverage": (0.01, 1), "sector_overlap": (0.01, 1),
                "baseline_windows": (1, 12), "merge_seconds": (0, 5),
                "gap_seconds": (2, 120), "late_seconds": (2, 120), "cooldown": (1, 3600),
            }.get(key, (0.01, 1_000_000_000))
            if (type(value) not in (int, float) or not low <= value <= high
                    or (type(sample) is int and int(value) != value)):
                raise ValueError(key)
            value = type(sample)(value)
        updated[key] = value
    return updated


class Monitor:
    def __init__(self, provider, settings, store, override_path):
        self.provider = provider
        self.settings = settings
        self.store = store
        self.override_path = override_path
        self.engine = MarketEngine(settings)
        self.announcer = Announcer(settings, self.record)
        self.status = "connecting"
        self.snapshot = None
        self.delay = None
        self.event_last = {}
        self.active = set()

    def record(self, kind, payload):
        self.store.add(kind, self.provider.label, payload)

    def state(self):
        return {"source": self.provider.label, "status": self.status, "settings": self.settings,
                "delay": self.delay if self.provider.label == "tdx" else None,
                "market": self.snapshot, "log": self.store.recent(self.provider.label)}

    def update(self, changes):
        updated = validate(self.settings, changes)
        temp = self.override_path.with_suffix(".tmp")
        temp.write_text(json.dumps(updated, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.override_path)
        self.settings.update(updated)
        if updated["paused"]:
            self.announcer.clear()

    async def run(self):
        backoff = 1
        while True:
            try:
                async for batch in self.provider.batches():
                    if batch is None:
                        self.status = "closed"
                        self.delay = None
                        self.engine.disconnect()
                        self.announcer.clear()
                        self.active.clear()
                        continue
                    real = self.provider.label == "tdx"
                    now = time() if real else None
                    if not batch.quotes:
                        raise RuntimeError("行情响应没有可用记录")
                    self.delay = max(0, now - max(q.ts for q in batch.quotes)) if real else None
                    if real and self.delay > self.settings["late_seconds"]:
                        self.status = "stale"
                        self.engine.disconnect()
                        self.announcer.clear()
                        self.active.clear()
                        continue
                    result = self.engine.ingest(batch, now)
                    if result is None:
                        continue
                    backoff = 1
                    self.status = "live"
                    self.snapshot, events = result
                    self.publish_events(events)
                self.status = "complete"
                return
            except Exception as error:
                self.status = "disconnected"
                self.engine.disconnect()
                self.announcer.clear()
                self.active.clear()
                self.record("connection", {"text": f"行情连接中断（{type(error).__name__}），{backoff}秒后重试"})
                await asyncio.sleep(backoff)
                backoff = min(60, backoff * 2)

    def publish_events(self, events):
        now = time()
        cooldown = self.settings["cooldown"]
        self.event_last = {key: stamp for key, stamp in self.event_last.items()
                           if now - stamp < cooldown}
        active = set()
        fresh = []
        for event in events:
            key = event.kind, event.key
            repeating = event.kind in {"stock", "rapid_rise", "market", "sector"}
            if key in active:
                continue
            if repeating:
                active.add(key)
            if key in self.event_last or (repeating and key in self.active):
                continue
            self.event_last[key] = now
            fresh.append(event)
        self.active = active
        for event in fresh[:50]:
            self.record("event", asdict(event))
        prefix = {"mock": "模拟行情。", "replay": "回放行情。", "tdx": ""}[self.provider.label]
        if prefix and fresh:
            fresh[0].text = prefix + fresh[0].text
        self.announcer.offer(fresh)


class API(RequestHandler):
    def initialize(self, monitor):
        self.monitor = monitor

    def get(self, action):
        if action != "state":
            raise HTTPError(404)
        self.write(self.monitor.state())

    def post(self, action):
        if self.request.headers.get("Origin") != f"{self.request.protocol}://{self.request.host}":
            raise HTTPError(403)
        try:
            if action == "settings":
                data = json.loads(self.request.body)
                if not isinstance(data, dict):
                    raise ValueError("settings")
                self.monitor.update(data)
            elif action == "test":
                if self.monitor.settings["paused"]:
                    raise HTTPError(409, reason="Speech is paused")
                self.monitor.announcer.test()
            else:
                raise HTTPError(404)
        except ValueError as error:
            self.set_status(400)
            self.write({"error": str(error)})
            return
        self.write({"ok": True})


class Socket(WebSocketHandler):
    def initialize(self, monitor):
        self.monitor = monitor
        self.timer = None

    def open(self):
        self.timer = asyncio.create_task(self.stream())

    async def stream(self):
        while True:
            await self.write_message(self.monitor.state())
            await asyncio.sleep(0.75)

    def on_close(self):
        if self.timer:
            self.timer.cancel()


def make_app(monitor, frontend):
    return Application([
        (r"/api/(state|settings|test)", API, {"monitor": monitor}),
        (r"/ws", Socket, {"monitor": monitor}),
        (r"/(.*)", StaticFileHandler, {"path": frontend, "default_filename": "index.html"}),
    ], websocket_ping_interval=20)


def dotenv():
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            key, separator, value = line.strip().partition("=")
            if separator and key in {"VOICE_SOURCE", "VOICE_REPLAY", "VOICE_TDX_CHUNK",
                                    "VOICE_TDX_INTERVAL", "VOICE_TDX_SECTORS"}:
                os.environ.setdefault(key, value.strip().strip('"'))


async def main(args):
    dotenv()
    source = os.getenv("VOICE_SOURCE", "mock").lower()
    if source == "mock":
        provider = MockDataProvider()
    elif source == "replay":
        provider = ReplayDataProvider(os.getenv("VOICE_REPLAY", "var/replay.jsonl"))
    elif source == "tdx":
        from backend.tdx import TdxAiDataProvider
        provider = TdxAiDataProvider()
    else:
        raise SystemExit("VOICE_SOURCE must be mock, replay or tdx.")
    state_dir = ROOT / "var"
    state_dir.mkdir(exist_ok=True)
    overrides = state_dir / "settings.json"
    settings = json.loads((ROOT / "config/config.yaml").read_text(encoding="utf-8"))
    if overrides.exists():
        settings = validate(settings, json.loads(overrides.read_text(encoding="utf-8")))
    store = Store(state_dir / "events.sqlite3")
    monitor = Monitor(provider, settings, store, overrides)
    app = make_app(monitor, ROOT / "frontend/dist")
    server = app.listen(args.port, address="127.0.0.1")
    url = f"http://127.0.0.1:{args.port}"
    print(f"Dashboard: {url} ({source})", flush=True)
    if args.open:
        await asyncio.to_thread(webbrowser.open, url)
    tasks = [asyncio.create_task(monitor.run()), asyncio.create_task(monitor.announcer.run())]
    try:
        await asyncio.Event().wait()
    finally:
        server.stop()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        store.db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open", action="store_true")
    asyncio.run(main(parser.parse_args()))
