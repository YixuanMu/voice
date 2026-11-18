import asyncio
import json
from contextlib import suppress
from time import monotonic

from backend.engine import Event


async def speak_windows(text, rate, volume):
    process = await asyncio.create_subprocess_exec(
        "powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
        "-File", "scripts/speak.ps1",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    payload = json.dumps({"text": text, "rate": rate, "volume": volume})
    try:
        _, error = await asyncio.wait_for(process.communicate(payload.encode("ascii") + b"\n"), 90)
        if process.returncode:
            raise RuntimeError(error.decode(errors="replace").strip() or "Windows TTS failed")
    finally:
        if process.returncode is None:
            with suppress(ProcessLookupError):
                process.kill()
            await process.wait()


class Announcer:
    def __init__(self, settings, record, speak=speak_windows):
        self.settings = settings
        self.record = record
        self.speak = speak
        self.pending = {}
        self.last = {}
        self.ready = asyncio.Event()

    def offer(self, events):
        if self.settings["paused"]:
            return
        now = monotonic()
        cooldown = self.settings["cooldown"]
        self.last = {key: stamp for key, stamp in self.last.items()
                     if now - stamp < cooldown}
        for event in events:
            key = event.kind, event.key
            if key in self.last:
                continue
            previous = self.pending.get(key)
            if previous is None or event.priority >= previous[0].priority:
                self.pending[key] = event, now
        ranked = sorted(self.pending.items(), key=lambda item: -item[1][0].priority)
        kind_counts = {}
        self.pending = {}
        for key, item in ranked:
            kind_counts[key[0]] = kind_counts.get(key[0], 0) + 1
            if kind_counts[key[0]] <= 8 and len(self.pending) < 32:
                self.pending[key] = item
        if self.pending:
            self.ready.set()

    def clear(self):
        self.pending.clear()
        self.ready.clear()

    async def run(self):
        while True:
            await self.ready.wait()
            await asyncio.sleep(self.settings["merge_seconds"])
            if self.settings["paused"]:
                self.clear()
                continue
            now = monotonic()
            self.pending = {key: item for key, item in self.pending.items() if now - item[1] < 60}
            ranked = sorted(self.pending.items(), key=lambda item: -item[1][0].priority)
            chosen = []
            for key, (event, _) in ranked[:3]:
                # A five-stock ranking forms its own speech segment.
                if event.standalone and chosen:
                    break
                chosen.append((key, event))
                if event.standalone:
                    break
            for key, _ in chosen:
                self.pending.pop(key)
            if not self.pending:
                self.ready.clear()
            if not chosen:
                continue
            for key, _ in chosen:
                self.last[key] = now
            text = "。".join(event.text for _, event in chosen)
            try:
                await self.speak(text, self.settings["rate"], self.settings["volume"])
            except Exception as error:
                self.record("speech_error", {"text": text, "error": str(error)[:300]})
            else:
                self.record("speech", {"text": text})

    def test(self):
        self.offer([Event(0, "test", "test", "中文语音播报测试。行情监控系统已经启动。", 1000)])

