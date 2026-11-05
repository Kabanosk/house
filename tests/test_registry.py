import tempfile
import unittest
from pathlib import Path

from houselib.registry import add, load


class RegistryTest(unittest.TestCase):
    def test_registry_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "contests.yaml"
            add(p, "grok-wro", "git@github.com:Kabanosk/grok-wro.git")
            add(p, "a-first", "")
            self.assertEqual(
                load(p), {"a-first": "", "grok-wro": "git@github.com:Kabanosk/grok-wro.git"}
            )
            self.assertTrue(p.read_text().splitlines()[0].startswith("a-first"))

    def test_load_missing_file_is_empty(self):
        self.assertEqual(load(Path("/nonexistent/contests.yaml")), {})

    def test_load_skips_comments(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "contests.yaml"
            p.write_text("# contests\nx: https://example.com/x.git\n")
            self.assertEqual(load(p), {"x": "https://example.com/x.git"})


if __name__ == "__main__":
    unittest.main()
