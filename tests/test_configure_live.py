import tempfile
import unittest
from pathlib import Path

from scripts.configure_live import configure


class ConfigureLiveTests(unittest.TestCase):
    def test_rejected_key_restores_previous_configuration(self):
        with tempfile.TemporaryDirectory() as folder:
            ini = Path(folder) / "TdxAiData.ini"
            env = Path(folder) / ".env"
            previous = b"[Token]\r\ntoken=TDX-original-123\r\n"
            ini.write_bytes(previous)
            env.write_bytes(b"VOICE_SOURCE=mock\n")

            def reject():
                self.assertIn(b"token=TDX-candidate-123\r\n", ini.read_bytes())
                raise ValueError("Rejected")

            with self.assertRaisesRegex(ValueError, "Rejected"):
                configure(ini, env, "\ufeff \"TDX-candidate-123\"\n", verify=reject)
            self.assertEqual(previous, ini.read_bytes())
            self.assertEqual(b"VOICE_SOURCE=mock\n", env.read_bytes())

            configure(ini, env, "TDX-candidate-123", verify=lambda: None)
            self.assertIn(b"token=TDX-candidate-123\r\n", ini.read_bytes())
            self.assertEqual(b"VOICE_SOURCE=tdx\n", env.read_bytes())
