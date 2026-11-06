"""Lesson from grok-bot-wro-2026: missed the final because the xAI key failed on the jury's side."""
import unittest

from houselib import HOUSE_ROOT

SKILLS = HOUSE_ROOT / "adapters/claude-code/skills"


class SecretsPreflightTest(unittest.TestCase):
    def test_build_playbook_has_preflight(self):
        text = (HOUSE_ROOT / "core/playbooks/build.md").read_text()
        self.assertIn("External APIs and keys", text)
        self.assertIn("jury", text)
        self.assertIn("fallback", text)

    def test_intake_checks_keys(self):
        self.assertIn("key", (SKILLS / "house-start/SKILL.md").read_text().lower())

    def test_ship_runs_like_the_jury(self):
        self.assertIn("like the jury", (SKILLS / "house-ship/SKILL.md").read_text())

    def test_judge_checks_clean_environment(self):
        self.assertIn("clean", (HOUSE_ROOT / "core/agents/judge.md").read_text())


if __name__ == "__main__":
    unittest.main()
