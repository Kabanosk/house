"""Ideation must ground novelty/impact in real prior art before an idea is chosen (lesson from grok-bot-wro-2026)."""
import unittest

from houselib import HOUSE_ROOT

CORE = HOUSE_ROOT / "core"
SKILLS = HOUSE_ROOT / "adapters/claude-code/skills"


def read(p):
    return p.read_text()


class PriorArtTest(unittest.TestCase):
    def test_protocol_lists_sources_and_card(self):
        text = read(CORE / "protocols/prior-art.md")
        for needle in ("house yc", "Requests for Startups", "Devpost", "Product Hunt", "risk, not a veto", "prior-art-"):
            self.assertIn(needle, text)

    def test_no_choice_without_card(self):
        self.assertIn("protocols/prior-art.md", read(CORE / "playbooks/lifecycle.md"))
        ideate = read(SKILLS / "house-ideate/SKILL.md")
        self.assertIn("prior-art", ideate)
        self.assertLess(ideate.index("prior-art"), ideate.index("judge"))

    def test_judge_scores_from_evidence_and_records_live_checks(self):
        judge = read(CORE / "agents/judge.md")
        self.assertIn("prior-art card", judge)
        self.assertIn("live", judge.lower())

    def test_ideator_can_start_from_real_problems(self):
        self.assertIn("Requests for Startups", read(CORE / "agents/ideator.md"))

    def test_ideas_archive_has_prior_art_column(self):
        self.assertIn("Najbliższe istniejące", read(CORE / "templates/house/ideas.md"))

    def test_retro_compares_with_official_verdict(self):
        self.assertIn("official", read(SKILLS / "house-retro/SKILL.md").lower())


if __name__ == "__main__":
    unittest.main()
