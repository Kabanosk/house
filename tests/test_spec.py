"""Reading the nested contest spec.yaml."""
import tempfile
import unittest
from pathlib import Path

from houselib import HOUSE_ROOT, spec


class SpecTest(unittest.TestCase):
    def test_template_loads(self):
        with tempfile.TemporaryDirectory() as d:
            house = Path(d)
            (house / "spec.yaml").write_text((HOUSE_ROOT / "core/templates/house/spec.yaml").read_text())
            s = spec.load_spec(house)
        self.assertEqual(spec.get(s, "limits.max_parallel_agents"), 3)
        self.assertIsNone(spec.get(s, "scoring.local_scorer"))
        self.assertEqual(spec.get(s, "mode"), "copilot")
        self.assertIs(spec.get(s, "deploy"), True)
        self.assertEqual(spec.get(s, "timeline.milestones"), [])
        self.assertEqual(spec.get(s, "nope.x", "d"), "d")

    def test_comments_and_quotes(self):
        with tempfile.TemporaryDirectory() as d:
            house = Path(d)
            (house / "spec.yaml").write_text(
                '# head\nkind: optimize   # human\nscoring:\n  local_scorer: "python score.py # not a comment"\n'
                "  metric: f1  # max\nguard:\n  push_allow: [origin, team]\n")
            s = spec.load_spec(house)
        self.assertEqual(spec.get(s, "kind"), "optimize")
        self.assertEqual(spec.get(s, "scoring.local_scorer"), "python score.py # not a comment")
        self.assertEqual(spec.get(s, "scoring.metric"), "f1")
        self.assertEqual(spec.get(s, "guard.push_allow"), ["origin", "team"])

    def test_deep_nesting_is_tolerated(self):
        with tempfile.TemporaryDirectory() as d:
            house = Path(d)
            (house / "spec.yaml").write_text(
                "scoring:\n  rubric:\n    - name: impact\n      levels:\n        1: weak\n        5: strong\n"
                "  local_scorer: python score.py\nmode: autopilot\n")
            s = spec.load_spec(house)
        self.assertEqual(spec.get(s, "scoring.local_scorer"), "python score.py")
        self.assertIsNone(spec.get(s, "scoring.rubric"))
        self.assertEqual(spec.get(s, "mode"), "autopilot")

    def test_missing_file(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(spec.load_spec(Path(d)), {})


if __name__ == "__main__":
    unittest.main()
