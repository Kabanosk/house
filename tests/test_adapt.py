import hashlib
import subprocess
import tempfile
import unittest
from pathlib import Path

from houselib import HOUSE_ROOT, adapters, manifest, scaffold
from houselib.fm import parse_frontmatter
from tests.helpers import FAKE_TOOLS, make_root

NONE = {"claude": False, "codex": False, "cursor": False, "grok_bot": False,
        "ollama": {"installed": False, "models": []}, "gpu": None}
ALL = dict(NONE, claude=True, codex=True, ollama={"installed": True, "models": ["qwen3:8b"]})
SKILLS = ["house-start", "house-status", "house-ideate", "house-plan",
          "house-run", "house-judge", "house-ship", "house-submit", "house-retro"]


def house_hash(contest: Path) -> str:
    h = hashlib.sha256()
    for p in sorted((contest / ".house").rglob("*")):
        if p.is_file() and p.name not in ("tools.json", "generated.json"):  # adapt's own files
            h.update(str(p.relative_to(contest)).encode() + p.read_bytes())
    return h.hexdigest()


class ResolveTierTest(unittest.TestCase):
    def test_codex_falls_back_recursively(self):
        profiles = adapters.load_profiles(HOUSE_ROOT)
        self.assertEqual(adapters.resolve_tier(profiles["coder-codex"], NONE, profiles), "sonnet")
        self.assertEqual(adapters.resolve_tier(profiles["ideator"], NONE, profiles), "opus")
        self.assertEqual(adapters.resolve_tier(profiles["summarizer"], NONE, profiles), "haiku")
        self.assertEqual(adapters.resolve_tier(profiles["summarizer"], ALL, profiles), "local")
        self.assertEqual(adapters.resolve_tier(profiles["ideator"], ALL, profiles), "codex")


class AdaptTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_root(Path(self._tmp.name))
        self.contest = scaffold.new_contest("c1", None, root=self.root, detect=lambda: FAKE_TOOLS)

    def tearDown(self):
        self._tmp.cleanup()

    def agent(self, name):
        return parse_frontmatter((self.contest / ".claude/agents" / f"{name}.md").read_text())

    def test_no_tools_uses_fallbacks(self):
        adapters.adapt(self.contest, self.root, detect=lambda: NONE)
        self.assertFalse((self.contest / ".claude/agents/coder-codex.md").exists())
        self.assertFalse((self.contest / ".claude/agents/coordinator.md").exists())
        self.assertEqual(self.agent("ideator")[0]["model"], "opus")
        self.assertEqual(self.agent("summarizer")[0]["model"], "haiku")

    def test_all_tools_generates_codex_driver(self):
        adapters.adapt(self.contest, self.root, detect=lambda: ALL)
        meta, body = self.agent("coder-codex")
        self.assertEqual(meta["model"], "haiku")
        self.assertIn("codex-task", body)
        self.assertIn("codex-task", self.agent("ideator")[1])

    def test_every_agent_has_claude_model(self):
        adapters.adapt(self.contest, self.root, detect=lambda: ALL)
        for f in (self.contest / ".claude/agents").glob("*.md"):
            meta, _ = parse_frontmatter(f.read_text())
            self.assertIn(meta["model"], {"opus", "sonnet", "haiku"}, f.name)
            self.assertEqual(set(meta), {"name", "description", "model"}, f.name)

    def test_skills_installed(self):
        adapters.adapt(self.contest, self.root, detect=lambda: NONE)
        for s in SKILLS:
            meta, _ = parse_frontmatter((self.contest / ".claude/skills" / s / "SKILL.md").read_text())
            self.assertEqual(meta["name"], s)
            self.assertEqual(meta.get("disable-model-invocation") is True, s != "house-status", s)

    def test_claude_md_and_agents_md(self):
        adapters.adapt(self.contest, self.root, detect=lambda: NONE)
        self.assertEqual((self.contest / "CLAUDE.md").read_text().splitlines()[0], "@AGENTS.md")
        agents_md = (self.contest / "AGENTS.md").read_text()
        self.assertIn(str((self.contest / ".house").resolve()), agents_md)
        self.assertIn(str(self.root.resolve()), agents_md)
        for placeholder in ("{contest_name}", "{house_dir}", "{house_root}", "{roster}", "{coordinator}"):
            self.assertNotIn(placeholder, agents_md)

    def test_adapt_commits_generated_files(self):
        from tests.helpers import git
        (self.contest / "wip.py").write_text("user work\n")
        adapters.adapt(self.contest, self.root, detect=lambda: ALL)
        self.assertEqual(git(self.contest, "status", "--porcelain").strip(), "?? wip.py")
        self.assertIn("AGENTS.md", git(self.contest, "ls-files"))

    def test_rerun_leaves_house_untouched(self):
        adapters.adapt(self.contest, self.root, detect=lambda: NONE)
        before = house_hash(self.contest)
        adapters.adapt(self.contest, self.root, detect=lambda: ALL)
        self.assertEqual(house_hash(self.contest), before)


class ManifestAdaptTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_root(Path(self._tmp.name))
        self.c = scaffold.new_contest("c1", None, root=self.root, detect=lambda: FAKE_TOOLS)

    def tearDown(self):
        self._tmp.cleanup()

    def adapt(self, tools=NONE):
        return adapters.adapt(self.c, self.root, detect=lambda: tools)

    def test_teammate_agent_survives_and_is_not_committed(self):
        from tests.helpers import git
        mate = self.c / ".claude/agents/teammate.md"
        mate.parent.mkdir(parents=True)
        mate.write_text("---\nname: teammate\n---\nmine\n")
        self.adapt()
        self.adapt(ALL)
        self.assertEqual(mate.read_text(), "---\nname: teammate\n---\nmine\n")
        self.assertIn("?? .claude/agents/teammate.md", git(self.c, "status", "--porcelain"))

    def test_manifest_written_and_committed(self):
        from tests.helpers import git
        self.adapt()
        files = manifest.load(self.c)
        for rel in ("AGENTS.md", "CLAUDE.md", ".claude/agents/coder.md", ".claude/skills/house-run/SKILL.md"):
            self.assertIn(rel, files)
        self.assertIn(".house/generated.json", git(self.c, "ls-files"))
        self.assertEqual(git(self.c, "status", "--porcelain").strip(), "")

    def test_hand_edit_kept_as_orig(self):
        self.adapt()
        coder = self.c / ".claude/agents/coder.md"
        coder.write_text(coder.read_text() + "\nmy local rule\n")
        res = self.adapt()
        self.assertIn("my local rule", (self.c / ".claude/agents/coder.md.orig").read_text())
        self.assertNotIn("my local rule", coder.read_text())
        self.assertTrue(res.warnings)

    def test_stale_agent_removed_and_deletion_committed(self):
        from tests.helpers import git
        self.adapt(ALL)
        self.assertTrue((self.c / ".claude/agents/coder-codex.md").exists())
        self.adapt(NONE)
        self.assertFalse((self.c / ".claude/agents/coder-codex.md").exists())
        self.assertNotIn(".claude/agents/coder-codex.md", git(self.c, "ls-files"))

    def test_legacy_contest_no_spurious_orig(self):
        (self.c / "AGENTS.md").write_text(adapters.MARKER + "\nold generated\n")
        agent = self.c / ".claude/agents/coder.md"
        agent.parent.mkdir(parents=True)
        agent.write_text("old generated agent\n")
        self.adapt()
        self.assertEqual(list(self.c.rglob("*.orig")), [])


class CodexTaskTest(unittest.TestCase):
    def test_dry_run_prints_command(self):
        with tempfile.TemporaryDirectory() as d:
            task = Path(d) / ".house/tasks/T001-x.md"
            task.parent.mkdir(parents=True)
            task.write_text("---\nid: T001\n---\nDo it\n")
            out = subprocess.run([str(HOUSE_ROOT / "bin/codex-task"), "--dry-run", str(task), d],
                                 capture_output=True, text=True, check=True).stdout
            self.assertTrue(out.startswith("codex exec --sandbox workspace-write"), out)
            self.assertIn("reports/T001.md", out)


if __name__ == "__main__":
    unittest.main()
