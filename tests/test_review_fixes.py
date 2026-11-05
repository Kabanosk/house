"""Regression tests for findings of the final review (2026-11-05)."""
import subprocess
import tempfile
import unittest
from pathlib import Path

from houselib import HOUSE_ROOT, adapters, cli, export, scaffold
from houselib.fm import parse_frontmatter
from tests.helpers import FAKE_TOOLS, git, make_root

NONE = {"claude": False, "codex": False, "cursor": False, "grok_bot": False,
        "ollama": {"installed": False, "models": []}, "gpu": None}
ALL = dict(NONE, claude=True, codex=True, ollama={"installed": True, "models": ["qwen3:8b"]})


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.root = make_root(self.tmp)
        self.c = scaffold.new_contest("c1", None, root=self.root, detect=lambda: FAKE_TOOLS)

    def tearDown(self):
        self._tmp.cleanup()

    def gen(self, rel):
        return (self.c / rel).read_text()


class CodexTaskFlagsTest(unittest.TestCase):  # C1, I1
    def run_dry(self, *extra):
        with tempfile.TemporaryDirectory() as d:
            task = Path(d) / ".house/tasks/T001-x.md"
            task.parent.mkdir(parents=True)
            task.write_text("---\nid: T001\n---\nDo it\n")
            out = subprocess.run([str(HOUSE_ROOT / "bin/codex-task"), "--dry-run", *extra, str(task), d],
                                 capture_output=True, text=True, check=True).stdout
            return out, str(Path(d).resolve() / ".house")

    def test_uses_supported_sandbox_flags_and_house_dir(self):
        out, hd = self.run_dry()
        self.assertTrue(out.startswith("codex exec --sandbox workspace-write"), out)
        self.assertNotIn("--full-auto", out)
        self.assertIn(f"--add-dir {hd}", out)
        self.assertIn("network_access=true", out)

    def test_prompt_separated_from_options(self):
        out, _ = self.run_dry()
        self.assertIn(" -- '---", out)  # task files start with frontmatter; without `--` codex parses it as a flag

    def test_read_only(self):
        out, _ = self.run_dry("--read-only")
        self.assertTrue(out.startswith("codex exec --sandbox read-only"), out)


class CodexDriverTest(Base):  # I1, I2, I3, I6
    def test_coder_codex_is_one_driver_that_commits(self):
        adapters.adapt(self.c, self.root, detect=lambda: ALL)
        text = self.gen(".claude/agents/coder-codex.md")
        self.assertEqual(text.count("thin driver"), 1)
        driver, _, codex_part = text.partition("--- Instructions for Codex ---")
        self.assertNotIn("codex-task", codex_part)
        self.assertIn("commit", driver)
        self.assertIn("600000", driver)

    def test_ideation_not_read_only(self):
        adapters.adapt(self.c, self.root, detect=lambda: ALL)
        self.assertNotIn("e.g. ideation", self.gen(".claude/agents/ideator.md"))

    def test_paths_substituted_in_generated_files(self):
        adapters.adapt(self.c, self.root, detect=lambda: ALL)
        hd = str((self.c / ".house").resolve())
        for f in list((self.c / ".claude").rglob("*.md")):
            text = f.read_text()
            self.assertNotIn("$HOUSE_DIR", text, f)
            self.assertNotIn("$HOUSE_ROOT", text, f)
        self.assertIn(hd, self.gen(".claude/agents/coder.md"))


class AdaptRobustnessTest(Base):  # I4, I5, M3, M7
    def test_gitignored_claude_md_does_not_crash(self):
        (self.c / ".gitignore").write_text("CLAUDE.md\n.claude/\n")
        adapters.adapt(self.c, self.root, detect=lambda: NONE)
        self.assertIn("AGENTS.md", git(self.c, "ls-files"))

    def test_existing_agents_md_backed_up(self):
        (self.c / "AGENTS.md").write_text("their own rules\n")
        adapters.adapt(self.c, self.root, detect=lambda: NONE)
        self.assertEqual(self.gen("AGENTS.md.orig"), "their own rules\n")
        adapters.adapt(self.c, self.root, detect=lambda: NONE)  # second run: generated file is not backed up again
        self.assertEqual(self.gen("AGENTS.md.orig"), "their own rules\n")

    def test_claude_md_sets_worktree_and_skill_preferences(self):
        adapters.adapt(self.c, self.root, detect=lambda: NONE)
        text = self.gen("CLAUDE.md")
        self.assertIn("git worktree add .worktrees/", text)
        self.assertIn("EnterWorktree", text)
        self.assertIn("writing-plans", text)

    def test_roster_marks_omitted_agents(self):
        adapters.adapt(self.c, self.root, detect=lambda: NONE)
        row = [ln for ln in self.gen("AGENTS.md").splitlines() if ln.startswith("| `coder-codex`")][0]
        self.assertIn("unavailable (use coder)", row)

    def test_descriptions_are_quoted(self):
        adapters.adapt(self.c, self.root, detect=lambda: NONE)
        line = [ln for ln in self.gen(".claude/agents/coder.md").splitlines() if ln.startswith("description:")][0]
        self.assertTrue(line.startswith('description: "'), line)


class TemplatesAndSkillsTest(unittest.TestCase):  # M1, M2
    def test_task_template_is_not_a_ready_task(self):
        meta, _ = parse_frontmatter((HOUSE_ROOT / "core/templates/house/tasks/TEMPLATE.md").read_text())
        self.assertEqual(meta["status"], "template")

    def test_skills_do_not_chain_slash_commands(self):
        for f in (HOUSE_ROOT / "adapters/claude-code/skills").rglob("SKILL.md"):
            body = parse_frontmatter(f.read_text())[1]
            for bad in ("run `/house-", "continue to `/house-", "Run `/house-"):
                self.assertNotIn(bad, body, f)


class NewContestGitignoreTest(Base):  # M4
    def test_contest_gitignore(self):
        lines = self.gen(".gitignore").splitlines()
        for pat in (".env*", "!.env.example", ".worktrees/"):
            self.assertIn(pat, lines)


class CliNameValidationTest(unittest.TestCase):  # M6
    def test_export_rejects_bad_name(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(cli.main(["export", "..", str(Path(d) / "out")]), 1)
            self.assertFalse((Path(d) / "out").exists())


class SecretPatternsTest(unittest.TestCase):  # I7
    def test_more_key_formats_detected(self):
        samples = ["sk_live_" + "a" * 24, "AIza" + "b" * 35, "hf_" + "c" * 34, "github_pat_" + "d" * 40,
                   "xoxb-" + "1" * 12 + "-abcdefabcdef", 'API_KEY = "' + "e" * 20 + '"',
                   "secret: '" + "f" * 18 + "'"]
        for s in samples:
            self.assertTrue(any(r.search(s) for r in export.SECRET_RES), s)

    def test_example_env_not_flagged(self):
        for s in ["XAI_API_KEY=", 'API_KEY = "<your key>"', "token: null"]:
            self.assertFalse(any(r.search(s) for r in export.SECRET_RES), s)


if __name__ == "__main__":
    unittest.main()
