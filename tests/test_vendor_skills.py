"""Third-party skills vendored into house and installed into every contest by `house adapt`."""
import tempfile
import unittest
from pathlib import Path

from houselib import HOUSE_ROOT, adapters, scaffold
from tests.helpers import FAKE_TOOLS, make_root

VENDORED = {"codebase-design": "codebase-design", "frontend-design": "frontend-design",
            "webapp-testing": "webapp-testing", "no-ai-slop": "no-ai-slop",
            "video-director": "video-director"}
USERS = {"coder": "frontend-design", "judge": "webapp-testing", "pitch": "no-ai-slop",
         "coordinator": "codebase-design"}


class VendoredSkillsTest(unittest.TestCase):
    def test_vendored_with_license_and_attribution(self):
        for name in VENDORED:
            d = HOUSE_ROOT / "vendor/skills" / name
            self.assertTrue((d / "SKILL.md").is_file(), name)
            self.assertTrue((d / "ATTRIBUTION.md").is_file(), name)
            self.assertTrue(any(p.name.upper().startswith("LICENSE") for p in d.iterdir()), name)

    def test_profiles_point_at_their_skills(self):
        for agent, skill in USERS.items():
            body = (HOUSE_ROOT / "core/agents" / f"{agent}.md").read_text()
            self.assertIn(f"vendor/skills/{skill}/SKILL.md", body, agent)

    def test_adapt_does_not_copy_vendored_skills(self):
        with tempfile.TemporaryDirectory() as d:
            root = make_root(Path(d))
            c = scaffold.new_contest("c1", None, root=root, detect=lambda: FAKE_TOOLS)
            adapters.adapt(c, root, detect=lambda: FAKE_TOOLS)
            for name in VENDORED:
                self.assertFalse((c / ".claude/skills" / name).exists(), name)

    def test_legacy_vendor_copies_cleaned(self):
        import shutil
        with tempfile.TemporaryDirectory() as d:
            root = make_root(Path(d))
            c = scaffold.new_contest("c1", None, root=root, detect=lambda: FAKE_TOOLS)
            shutil.copytree(root / "vendor/skills/frontend-design", c / ".claude/skills/frontend-design")
            shutil.copytree(root / "vendor/skills/webapp-testing", c / ".claude/skills/webapp-testing")
            with (c / ".claude/skills/webapp-testing/SKILL.md").open("a") as f:
                f.write("\nlocal tweak\n")
            res = adapters.adapt(c, root, detect=lambda: FAKE_TOOLS)
            self.assertFalse((c / ".claude/skills/frontend-design").exists())
            self.assertTrue((c / ".claude/skills/webapp-testing/SKILL.md").is_file())
            self.assertTrue(any("webapp-testing" in w for w in res.warnings), res.warnings)

if __name__ == "__main__":
    unittest.main()
