"""`house doctor --context`: estimate of what a contest session always loads."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from houselib import cli, context


def skill(d: Path, name: str, extra: str = "", description: str = "does a thing") -> Path:
    p = d / name / "SKILL.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"---\nname: {name}\ndescription: {description}\n{extra}---\nbody " * 1 + "x" * 500 + "\n")
    return p


def plugins(home: Path, installed: dict, enabled: dict | None) -> None:
    pdir = home / ".claude/plugins"
    pdir.mkdir(parents=True, exist_ok=True)
    data = {"version": 2, "plugins": {pid: [{"installPath": str(path)}] for pid, path in installed.items()}}
    (pdir / "installed_plugins.json").write_text(json.dumps(data))
    if enabled is not None:
        (home / ".claude/settings.json").write_text(json.dumps({"enabledPlugins": enabled}))


class ContextTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.home = self.tmp / "home"
        self.contest = self.tmp / "c1"
        (self.contest / ".house").mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def test_estimate_tokens(self):
        self.assertEqual(context.estimate_tokens("abcd"), 1)
        self.assertEqual(context.estimate_tokens("abcde"), 2)
        self.assertEqual(context.estimate_tokens(""), 0)

    def test_skill_entries_reads_frontmatter(self):
        d = self.home / ".claude/skills"
        skill(d, "a", description="x")
        skill(d, "h", extra="disable-model-invocation: true\n")
        entries = {e.name: e for e in context.skill_entries(d, "user")}
        self.assertEqual(set(entries), {"a", "h"})
        self.assertTrue(entries["a"].model_invocable)
        self.assertFalse(entries["h"].model_invocable)
        self.assertEqual(entries["a"].source, "user")
        self.assertEqual(entries["a"].tokens, context.estimate_tokens("a" + "x"))

    def test_skill_with_multiline_yaml_still_counted(self):
        d = self.home / ".claude/skills"
        p = d / "multi" / "SKILL.md"
        p.parent.mkdir(parents=True)
        p.write_text("---\nname: multi\ndescription: >\n  long folded\n  text here\n---\nbody\n")
        (e,) = context.skill_entries(d, "user")
        self.assertEqual(e.name, "multi")
        self.assertGreater(e.tokens, 0)
        self.assertTrue(e.model_invocable)

    def test_plugin_dirs_only_enabled(self):
        p1, p2 = self.tmp / "p1", self.tmp / "p2"
        plugins(self.home, {"p1@m": p1, "p2@m": p2}, {"p1@m": True, "p2@m": False})
        self.assertEqual(context.plugin_skill_dirs(self.home), [("plugin:p1", p1 / "skills")])

    def test_plugin_dirs_tolerate_missing_files(self):
        self.assertEqual(context.plugin_skill_dirs(self.home), [])
        (self.home / ".claude/plugins").mkdir(parents=True)
        (self.home / ".claude/plugins/installed_plugins.json").write_text("{not json")
        self.assertEqual(context.plugin_skill_dirs(self.home), [])

    def test_measure_finds_duplicates_and_totals(self):
        p1 = self.tmp / "p1"
        plugins(self.home, {"p1@m": p1}, {"p1@m": True})
        skill(p1 / "skills", "grill-me")
        skill(self.home / ".claude/skills", "grill-me")
        skill(self.contest / ".claude/skills", "house-run", extra="disable-model-invocation: true\n")
        (self.contest / "AGENTS.md").write_text("a" * 400)
        r = context.measure(self.contest, self.home)
        self.assertEqual(r.duplicates, {"grill-me": ["plugin:p1", "user"]})
        self.assertIn(("AGENTS.md", 100), r.files)
        invocable = sum(e.tokens for e in r.skills if e.model_invocable)
        self.assertEqual(r.total, 100 + invocable)
        hidden = [e for e in r.skills if not e.model_invocable]
        self.assertEqual([e.name for e in hidden], ["house-run"])

    def test_measure_counts_agent_descriptions(self):
        a = self.contest / ".claude/agents/coder.md"
        a.parent.mkdir(parents=True)
        a.write_text("---\nname: coder\ndescription: \"abcdefgh\"\nmodel: sonnet\n---\nlong body " + "y" * 900)
        r = context.measure(self.contest, self.home)
        self.assertEqual(r.agents, [("coder", 2)])

    def test_format_report_has_total(self):
        (self.contest / "AGENTS.md").write_text("a" * 40)
        text = context.format_report(context.measure(self.contest, self.home))
        self.assertTrue(text.splitlines()[-1].startswith("TOTAL ≈"), text)

    def test_cli_doctor_context_requires_name(self):
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(cli.main(["doctor", "--context"]), 1)
        self.assertIn("error:", err.getvalue())


if __name__ == "__main__":
    unittest.main()
