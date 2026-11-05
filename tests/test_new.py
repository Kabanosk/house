import tempfile
import unittest
from pathlib import Path

from houselib import registry, scaffold
from tests.helpers import EMAIL, FAKE_TOOLS, NAME, git, make_root


class NewContestTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_root(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def new(self, name):
        return scaffold.new_contest(name, None, root=self.root, detect=lambda: FAKE_TOOLS)

    def test_scaffolds_house_dir_and_registers(self):
        c = self.new("grok-bot-wro-2026")
        self.assertEqual(c, self.root / "contests" / "grok-bot-wro-2026")
        for rel in (".house/spec.yaml", ".house/playbooks/build.md", ".house/protocols/briefing.md",
                    ".house/tools.json", ".house/tasks/TEMPLATE.md"):
            self.assertTrue((c / rel).is_file(), rel)
        self.assertEqual(registry.load(self.root / "contests.yaml"), {"grok-bot-wro-2026": ""})

    def test_one_commit_with_owner_identity(self):
        c = self.new("x1")
        log = git(c, "log", "--format=%an <%ae>|%s").splitlines()
        self.assertEqual(log, [f"{NAME} <{EMAIL}>|chore: scaffold house"])
        self.assertEqual(git(c, "config", "user.email").strip(), EMAIL)

    def test_existing_contest_refused_and_untouched(self):
        c = self.new("x1")
        (c / ".house/spec.yaml").write_text("mine\n")
        with self.assertRaises(FileExistsError):
            self.new("x1")
        self.assertEqual((c / ".house/spec.yaml").read_text(), "mine\n")

    def test_clone_of_repo_that_already_has_house(self):
        first = self.new("orig")
        bare = Path(self._tmp.name) / "orig.git"
        git(first.parent, "clone", "-q", "--bare", str(first), str(bare))
        c = scaffold.new_contest("again", str(bare), root=self.root, detect=lambda: FAKE_TOOLS)
        self.assertTrue((c / ".house/spec.yaml").is_file())
        self.assertEqual(registry.load(self.root / "contests.yaml")["again"], str(bare))

    def test_bad_names_rejected_before_any_change(self):
        for bad in ["Grok Bot", "../x", "", "-x", "a--b", "x-", "Grok-Bot", "a_b"]:
            with self.assertRaises(ValueError, msg=bad):
                self.new(bad)
        self.assertFalse((self.root / "contests").exists())
        self.assertFalse((self.root / "contests.yaml").exists())


if __name__ == "__main__":
    unittest.main()
