import tempfile
import unittest
from pathlib import Path

from houselib import doctor, registry, scaffold, setup
from tests.helpers import FAKE_TOOLS, git, make_root

NONE = {"claude": False, "codex": False, "cursor": False, "grok_bot": False, "uv": False, "ruff": False,
        "ollama": {"installed": False, "models": []}, "gpu": None}


class SetupTest(unittest.TestCase):
    def test_installs_user_skills_and_backs_up_changed_ones(self):
        with tempfile.TemporaryDirectory() as d:
            root, home = make_root(Path(d)), Path(d) / "home"
            (root / "user-skills/grill-me").mkdir(parents=True)
            (root / "user-skills/grill-me/SKILL.md").write_text("new\n")
            (home / ".claude/skills/grill-me").mkdir(parents=True)
            (home / ".claude/skills/grill-me/SKILL.md").write_text("old local edit\n")
            installed = setup.install_user_skills(root, home)
            self.assertEqual(installed, ["grill-me"])
            self.assertEqual((home / ".claude/skills/grill-me/SKILL.md").read_text(), "new\n")
            backups = list((home / ".claude/skills").glob("grill-me.bak-*"))
            self.assertEqual(len(backups), 1)
            setup.install_user_skills(root, home)  # identical now: no second backup
            self.assertEqual(len(list((home / ".claude/skills").glob("grill-me.bak-*"))), 1)

    def test_hints_for_missing_tools(self):
        hints = "\n".join(setup.hints(NONE))
        for needle in ("uv", "codex login", "superpowers", "claude"):
            self.assertIn(needle, hints)

    def test_no_hints_when_everything_present(self):
        tools = dict(NONE, claude=True, codex=True, uv=True, ruff=True,
                     ollama={"installed": True, "models": ["qwen3:8b"]})
        self.assertEqual([h for h in setup.hints(tools) if "superpowers" not in h], [])

    def test_repo_ships_user_skills(self):
        from houselib import HOUSE_ROOT
        for name in ("grill-me", "retro", "writing-for-agents", "prototype"):
            self.assertTrue((HOUSE_ROOT / "user-skills" / name / "SKILL.md").is_file(), name)


class DoctorUvRuffTest(unittest.TestCase):
    def test_detects_uv_and_ruff(self):
        tools = doctor.detect(which=lambda n: "/x" if n in ("uv", "ruff") else None, run=lambda _: None)
        self.assertTrue(tools["uv"] and tools["ruff"])


class AdoptTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.root = make_root(self.tmp)
        self.repo = self.tmp / "existing"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.name", "Team Name")
        git(self.repo, "config", "user.email", "team@example.com")
        (self.repo / "model.py").write_text("x = 1\n")
        git(self.repo, "add", ".")
        git(self.repo, "commit", "-qm", "work")
        git(self.repo, "remote", "add", "origin", "git@github.com:someone/contest.git")

    def tearDown(self):
        self._tmp.cleanup()

    def test_adopt_links_in_place_without_committing(self):
        c = scaffold.adopt("techarena-france-2026", self.repo, root=self.root, detect=lambda: FAKE_TOOLS)
        self.assertTrue(c.is_symlink())
        self.assertEqual(c.resolve(), self.repo.resolve())
        self.assertTrue((self.repo / ".house/spec.yaml").is_file())
        self.assertEqual(len(git(self.repo, "log", "--oneline").splitlines()), 1)  # nothing committed
        self.assertEqual(registry.load(self.root / "contests.yaml"),
                         {"techarena-france-2026": "git@github.com:someone/contest.git"})
        self.assertEqual(git(self.repo, "config", "user.email").strip(), "team@example.com")  # untouched

    def test_adopt_refuses_non_repo_and_existing_name(self):
        with self.assertRaises(ValueError):
            scaffold.adopt("x1", self.tmp, root=self.root, detect=lambda: FAKE_TOOLS)  # not a repo root
        scaffold.adopt("x2", self.repo, root=self.root, detect=lambda: FAKE_TOOLS)
        with self.assertRaises(FileExistsError):
            scaffold.adopt("x2", self.repo, root=self.root, detect=lambda: FAKE_TOOLS)


if __name__ == "__main__":
    unittest.main()
