import asyncio
import json
import tempfile
from pathlib import Path

from tornado.httpclient import HTTPRequest
from tornado.testing import AsyncHTTPTestCase, gen_test
from tornado.websocket import websocket_connect

from backend.providers import MockDataProvider
from backend.server import Monitor, Store, make_app


class ServerTests(AsyncHTTPTestCase):
    def get_app(self):
        self.folder = tempfile.TemporaryDirectory()
        root = Path(self.folder.name)
        settings = json.loads(Path("config/config.yaml").read_text())
        self.store = Store(root / "events.sqlite3")
        self.monitor = Monitor(MockDataProvider(5000), settings, self.store, root / "settings.json")
        return make_app(self.monitor, root)

    def tearDown(self):
        super().tearDown()
        self.store.db.close()
        self.folder.cleanup()

    @gen_test
    async def test_websocket_snapshot_and_settings(self):
        job = asyncio.create_task(self.monitor.run())
        socket = await websocket_connect(self.get_url("/ws").replace("http:", "ws:"))
        try:
            state = json.loads(await socket.read_message())
            self.assertEqual("mock", state["source"])
            self.assertEqual(5000, state["market"]["covered"])
            origin = self.get_url("").rstrip("/")
            uri = self.get_url("/api/settings")
            for header, body, expected in [
                ({}, '{"volume":50}', 403),
                ({"Origin": origin}, '{"volume":999}', 400),
                ({"Origin": origin}, '{"volume":50}', 200),
            ]:
                response = await self.http_client.fetch(
                    HTTPRequest(uri, method="POST", headers=header, body=body), raise_error=False)
                self.assertEqual(expected, response.code)
            self.assertEqual(50, self.monitor.settings["volume"])
            self.assertEqual(50, json.loads(self.monitor.override_path.read_text())["volume"])
        finally:
            socket.close()
            job.cancel()
            await asyncio.gather(job, return_exceptions=True)
