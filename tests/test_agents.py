import unittest

from houselib import HOUSE_ROOT
from houselib.fm import parse_frontmatter

AGENTS = ["coordinator", "researcher", "ideator", "coder", "coder-codex", "judge", "pitch", "summarizer"]
KEYS = {"id", "model_tier", "fallback", "description", "tools", "inputs", "outputs", "acceptance", "max_turns"}
TIERS = {"opus", "sonnet", "haiku", "codex", "local"}
EXPECTED = {
    "coordinator": ("opus", None), "researcher": ("haiku", None), "ideator": ("codex", "opus"),
    "coder": ("sonnet", None), "coder-codex": ("codex", "coder"), "judge": ("opus", None),
    "pitch": ("sonnet", None), "summarizer": ("local", "haiku"),
}


def load(name):
    return parse_frontmatter((HOUSE_ROOT / "core/agents" / f"{name}.md").read_text())


class AgentProfilesTest(unittest.TestCase):
    def test_profiles_have_exact_keys_and_tiers(self):
        for name in AGENTS:
            meta, body = load(name)
            self.assertEqual(set(meta), KEYS, name)
            self.assertEqual(meta["id"], name)
            self.assertIn(meta["model_tier"], TIERS, name)
            self.assertEqual((meta["model_tier"], meta["fallback"]), EXPECTED[name], name)
            self.assertIn("PROGRESS.md", body, name)

    def test_ideator_differs_from_coder(self):
        self.assertNotEqual(load("ideator")[0]["model_tier"], load("coder")[0]["model_tier"])

    def test_ideator_reads_ideas_archive(self):
        self.assertIn("ideas.md", load("ideator")[1])

    def test_summarizer_tries_ollama(self):
        self.assertIn("ollama run", load("summarizer")[1])


if __name__ == "__main__":
    unittest.main()
