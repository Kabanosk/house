"""Regression tests for problems found in the end-to-end run (2026-11-05, e2e-wordcount)."""
import unittest

from houselib import HOUSE_ROOT, adapters

SKILLS = HOUSE_ROOT / "adapters/claude-code/skills"


class E2EFixesTest(unittest.TestCase):
    def test_shell_rules_avoid_cd_chains(self):  # workers were denied `cd <wt> && git ...`
        tmpl = (HOUSE_ROOT / "adapters/codex/AGENTS.md.tmpl").read_text()
        self.assertIn("git -C", tmpl)
        self.assertIn("cd", tmpl.split("## Shell")[1].split("##")[0])

    def test_run_cleans_up_worktrees_after_merge(self):
        text = (SKILLS / "house-run/SKILL.md").read_text()
        self.assertIn("git worktree remove", text)
        self.assertIn("git branch -d", text)

    def test_codex_driver_raises_bash_timeout(self):  # ideator's codex run was killed at the 2 min default
        self.assertIn("600000", adapters.CODEX_DRIVER)

    def test_progress_newest_at_bottom(self):  # agents append; "newest on top" produced mixed order
        self.assertIn("najnowsze na dole", (HOUSE_ROOT / "core/templates/house/PROGRESS.md").read_text())

    def test_ship_exports_to_durable_default(self):
        self.assertIn("$HOUSE_ROOT/exports/", (SKILLS / "house-ship/SKILL.md").read_text())
        self.assertIn("exports/", (HOUSE_ROOT / ".gitignore").read_text().splitlines())


if __name__ == "__main__":
    unittest.main()
