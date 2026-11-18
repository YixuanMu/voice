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
        _, error = await asyncio.wait_for(process.communicate(payload.encode("ascii") + b"\n"), 45)
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
        self.pending = dict(sorted(self.pending.items(), key=lambda item: -item[1][0].priority)[:32])
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
            ranked = sorted(self.pending.items(), key=lambda item: -item[1][0].priority)
            self.clear()
            chosen = [(key, event) for key, (event, received) in ranked if now - received < 20][:3]
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

