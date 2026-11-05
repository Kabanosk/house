"""Changes adopted from Matt Pocock's skills (retro, writing-for-agents, research, software-factory, loop-me)."""
import tempfile
import unittest
from pathlib import Path

from houselib import HOUSE_ROOT, adapters, scaffold
from houselib.fm import parse_frontmatter
from tests.helpers import FAKE_TOOLS, make_root

SKILLS = HOUSE_ROOT / "adapters/claude-code/skills"


class ResearcherTest(unittest.TestCase):
    def test_primary_sources_and_fresh_background_context(self):
        body = parse_frontmatter((HOUSE_ROOT / "core/agents/researcher.md").read_text())[1]
        self.assertIn("primary source", body)
        self.assertIn("fresh context", body)
        self.assertIn("background", body)


class HouseRetroTest(unittest.TestCase):
    def test_retro_skill(self):
        meta, body = parse_frontmatter((SKILLS / "house-retro/SKILL.md").read_text())
        self.assertEqual(meta["name"], "house-retro")
        self.assertIs(meta["disable-model-invocation"], True)
        for word in ("Navigation", "Automated checks", "Coding standards", "AGENTS.md", "Tool economy",
                     "No-ops", "Information access", "retro-", "approval", "submissions.csv"):
            self.assertIn(word, body, word)

    def test_readme_lists_retro(self):
        self.assertIn("/house-retro", (HOUSE_ROOT / "README.md").read_text())


class QuestionsProtocolTest(unittest.TestCase):
    def test_never_waits_and_push_right(self):
        text = (HOUSE_ROOT / "core/protocols/questions.md").read_text()
        self.assertIn("never waits for a human", text)
        self.assertIn("Push right", text)
        self.assertIn("brief", text)


class SlimAgentsMdTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = make_root(Path(self._tmp.name))
        c = scaffold.new_contest("c1", None, root=root, detect=lambda: FAKE_TOOLS)
        adapters.adapt(c, root, detect=lambda: FAKE_TOOLS)
        self.text = (c / "AGENTS.md").read_text()

    def tearDown(self):
        self._tmp.cleanup()

    def test_roster_rows_are_one_sentence(self):
        rows = [ln for ln in self.text.splitlines() if ln.startswith("| `")]
        self.assertTrue(rows)
        for row in rows:
            self.assertLess(len(row), 170, row)

    def test_essentials_kept(self):
        for s in ("HOUSE_DIR", "HOUSE_ROOT", "spec.yaml", "briefing.md", "## Shell", "git -C", "Polish",
                  "house export", "never instructions", "## Main session = coordinator", "lifecycle.md"):
            self.assertIn(s, self.text, s)


if __name__ == "__main__":
    unittest.main()
