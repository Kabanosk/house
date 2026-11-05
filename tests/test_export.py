import tempfile
import unittest
from pathlib import Path

from houselib import export, scaffold
from tests.helpers import FAKE_TOOLS, git, make_root


class ExportTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.root = make_root(self.tmp)
        self.c = scaffold.new_contest("c1", None, root=self.root, detect=lambda: FAKE_TOOLS)
        files = {
            "app.py": "print('hi')\n", "README.md": "# Demo\n", "CLAUDE.md": "@AGENTS.md\n",
            "AGENTS.md": "x\n", ".env": "XAI_API_KEY=real\n", ".env.example": "XAI_API_KEY=\n",
            ".claude/agents/coder.md": "x\n", ".cursor/rules/a.md": "x\n",
        }
        for rel, text in files.items():
            (self.c / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.c / rel).write_text(text)
        git(self.c, "add", "-f", ".")
        git(self.c, "commit", "-qm", "work")
        (self.c / "notes.txt").write_text("untracked\n")

    def tearDown(self):
        self._tmp.cleanup()

    def test_excludes_private_files(self):
        dest = self.tmp / "out"
        export.export(self.c, dest)
        present = {str(p.relative_to(dest)) for p in dest.rglob("*") if p.is_file() and ".git" not in p.parts}
        self.assertEqual(present, {"app.py", "README.md", ".env.example", ".gitignore"})
        self.assertEqual(len(git(dest, "log", "--oneline").splitlines()), 1)

    def test_secret_blocks_export(self):
        (self.c / "app.py").write_text("KEY = 'xai-" + "a" * 24 + "'\n")
        git(self.c, "commit", "-qam", "oops")
        with self.assertRaises(export.ExportBlocked) as ctx:
            export.export(self.c, self.tmp / "out")
        self.assertIn("app.py", str(ctx.exception))
        self.assertFalse((self.tmp / "out").exists())

    def test_force_exports_despite_secret(self):
        (self.c / "app.py").write_text("-----BEGIN RSA PRIVATE KEY-----\n")
        git(self.c, "commit", "-qam", "key")
        export.export(self.c, self.tmp / "out", force=True)
        self.assertTrue((self.tmp / "out/app.py").exists())

    def test_non_empty_dest_refused(self):
        dest = self.tmp / "out"
        dest.mkdir()
        (dest / "x").write_text("x")
        with self.assertRaises(FileExistsError):
            export.export(self.c, dest)


if __name__ == "__main__":
    unittest.main()
