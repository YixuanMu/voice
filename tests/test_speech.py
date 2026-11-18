import asyncio
import unittest

from backend.engine import Event
from backend.speech import Announcer


class SpeechTests(unittest.IsolatedAsyncioTestCase):
    async def test_priority_merge_pause_and_cooldown(self):
        spoken = []
        completed = asyncio.Event()

        async def speak(text, rate, volume):
            spoken.append(text)
            completed.set()

        cfg = {"paused": False, "cooldown": 30, "merge_seconds": 0, "rate": 0, "volume": 80}
        announcer = Announcer(cfg, lambda *_: None, speak)
        task = asyncio.create_task(announcer.run())
        try:
            low = Event(0, "stock", "A", "低")
            high = Event(0, "sector", "B", "高", 90)
            announcer.offer([low, low, high])
            await asyncio.wait_for(completed.wait(), 2)
            self.assertEqual(["高。低"], spoken)
            announcer.offer([low, high])
            self.assertFalse(announcer.pending)
            cfg["paused"] = True
            announcer.offer([Event(0, "test", "C", "暂停")])
            self.assertFalse(announcer.pending)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

