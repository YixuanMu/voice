import json
import subprocess
import sys
import unittest

from backend.providers import MockDataProvider


class ProviderTests(unittest.TestCase):
    def test_mock_diagnostic_cli(self):
        result = subprocess.run(
            [sys.executable, "-m", "scripts.diagnose", "--source", "mock"],
            capture_output=True, text=True, check=True, timeout=15,
        )
        self.assertEqual({"source": "mock", "expected": 5000, "received": 5000, "sectors": 3},
                         json.loads(result.stdout))

    def test_frame_has_full_universe(self):
        frames = MockDataProvider().frames()
        first, second = next(frames), next(frames)
        self.assertEqual(5000, len({q.code for q in first.quotes}))
        self.assertTrue(all(a.amount < b.amount and b.ts > a.ts
                            for a, b in zip(first.quotes, second.quotes)))

