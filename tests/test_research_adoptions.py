"""Adopted from the deep-research note on hackathon teams (2026-11-06)."""
import re
import unittest

from houselib import HOUSE_ROOT

CORE = HOUSE_ROOT / "core"
SKILLS = HOUSE_ROOT / "adapters/claude-code/skills"


class ResearchAdoptionsTest(unittest.TestCase):
    def test_demo_checklist_protocol(self):
        text = (CORE / "protocols/demo-checklist.md").read_text()
        for needle in ("incognito", "timeout", "console", "deployed URL", "backup recording",
                       "another machine", "before the deadline", "video"):
            self.assertIn(needle, text)
        for user in (SKILLS / "house-ship/SKILL.md", CORE / "agents/judge.md"):
            self.assertIn("demo-checklist.md", user.read_text(), user)

    def test_spec_has_deliverables_block(self):
        spec = (CORE / "templates/house/spec.yaml").read_text()
        self.assertRegex(spec, re.compile(r"^deliverables:", re.M))
        for key in ("video_required", "video_max_seconds", "live_demo", "slides", "starter_code_allowed",
                    "ai_apis_allowed", "external_data_allowed", "cloud_deploy_allowed"):
            self.assertIn(key, spec)
        self.assertIn("deliverables", (SKILLS / "house-start/SKILL.md").read_text())

    def test_video_template_used_by_pitch(self):
        tmpl = (CORE / "protocols/demo-video.md").read_text()
        for mark in ("0:00", "0:20", "0:40", "1:50", "2:20", "2:40"):
            self.assertIn(mark, tmpl)
        self.assertIn("demo-video.md", (CORE / "agents/pitch.md").read_text())

    def test_demo_flow_artifact(self):
        self.assertIn("demo-flow.md", (CORE / "playbooks/build.md").read_text())
        self.assertIn("demo-flow.md", (CORE / "agents/pitch.md").read_text())

    def test_roadmap_records_later_ideas(self):
        text = (HOUSE_ROOT / "docs/ROADMAP.md").read_text()
        for needle in ("failure injection", "role compression", "MLflow", "API-first"):
            self.assertIn(needle, text)


if __name__ == "__main__":
    unittest.main()
