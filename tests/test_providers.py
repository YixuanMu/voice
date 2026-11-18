import unittest

from backend.providers import MockDataProvider


class ProviderTests(unittest.TestCase):
    def test_frame_has_full_universe(self):
        frames = MockDataProvider().frames()
        first, second = next(frames), next(frames)
        self.assertEqual(5000, len({q.code for q in first.quotes}))
        self.assertTrue(all(a.amount < b.amount and b.ts > a.ts
                            for a, b in zip(first.quotes, second.quotes)))

