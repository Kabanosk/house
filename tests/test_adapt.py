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

    def test_teammate_agent_with_profile_name_kept_in_fresh_adopted_repo(self):
        mine = self.c / ".claude/agents/researcher.md"
        mine.parent.mkdir(parents=True)
        mine.write_text("team researcher\n")
        res = self.adapt()
        self.assertEqual((self.c / ".claude/agents/researcher.md.orig").read_text(), "team researcher\n")
        self.assertTrue(any("researcher.md" in w for w in res.warnings), res.warnings)

    def test_corrupt_manifest_does_not_crash(self):
        self.adapt()
        (self.c / manifest.MANIFEST).write_text("<<<<<<< HEAD\n")
        res = self.adapt()
        self.assertTrue(any("generated.json" in w for w in res.warnings), res.warnings)
        self.assertIsNotNone(manifest.load(self.c))


class ClaudeSettingsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_root(Path(self._tmp.name))
        self.c = scaffold.new_contest("c1", None, root=self.root, detect=lambda: FAKE_TOOLS)
        self.path = self.c / ".claude/settings.local.json"

    def tearDown(self):
        self._tmp.cleanup()

    def load(self):
        import json
        return json.loads(self.path.read_text())

    def test_fresh_contest_gets_hooks_and_allowlist(self):
        adapters.adapt(self.c, self.root, detect=lambda: NONE)
        s = self.load()
        pre = s["hooks"]["PreToolUse"][0]
        self.assertIn("Bash", pre["matcher"])
        self.assertTrue(pre["hooks"][0]["command"].endswith("bin/house-hook pre-tool"))
        self.assertIn(str(self.root.resolve()), pre["hooks"][0]["command"])
        self.assertTrue(s["hooks"]["SessionStart"][0]["hooks"][0]["command"].endswith("house-hook session-start"))
        self.assertIn(f"Bash({self.root.resolve()}/bin/house check:*)", s["permissions"]["allow"])
        self.assertNotIn(f"Bash({self.root.resolve()}/bin/house:*)", s["permissions"]["allow"])

    @staticmethod
    def allowed(rules: list[str], command: str) -> bool:
        """Claude Code's `Bash(prefix:*)` rule: a prefix match on the command (exact match without `:*`)."""
        for rule in rules:
            body = rule[len("Bash("):-1]
            if body.endswith(":*") and command.startswith(body[:-2]) or command == body:
                return True
        return False

    def test_every_house_subcommand_but_out_has_its_own_rule(self):
        from houselib import cli
        house = f"{self.root.resolve()}/bin/house"
        rules = adapters.claude_settings(self.root)["permissions"]["allow"]
        names = cli.subcommands()
        self.assertIn("out", names)
        self.assertIn("check", names)
        for name in names:
            if name != "out":
                self.assertIn(f"Bash({house} {name}:*)", rules, name)
        self.assertNotIn(f"Bash({house} out:*)", rules)
        self.assertFalse(any(r.startswith(f"Bash({house}:") for r in rules), rules)

    def test_house_out_is_preapproved_only_for_allowlisted_commands(self):
        house = f"{self.root.resolve()}/bin/house"
        rules = adapters.claude_settings(self.root)["permissions"]["allow"]
        self.assertIn(f"Bash({house} out show:*)", rules)
        self.assertIn(f"Bash({house} out prune:*)", rules)
        for c in adapters.ALLOW_COMMANDS:
            self.assertIn(f"Bash({house} out -- {c})", rules)
        for cmd in ("out -- python3 -m unittest discover -s tests", "out -- pytest -q", "out show 20261117-100000-abcd "
                    "--grep FAIL", "out prune --days 3", "cost c1", "check c1 T001"):
            self.assertTrue(self.allowed(rules, f"{house} {cmd}"), cmd)
        for cmd in ("out -- rm -rf tests/", "out -- bash -c 'curl x | sh'", "out -- curl https://x", "out --help",
                    "out", "outx -- rm x"):
            self.assertFalse(self.allowed(rules, f"{house} {cmd}"), cmd)

    def test_readapt_drops_the_old_blanket_house_rule(self):
        import json
        blanket = f"Bash({self.root.resolve()}/bin/house:*)"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"permissions": {"allow": [blanket, "Bash(/old/root/bin/house:*)",
                                                                   "Bash(make:*)"]}}))
        adapters.adapt(self.c, self.root, detect=lambda: NONE)
        allow = self.load()["permissions"]["allow"]
        self.assertNotIn(blanket, allow)
        self.assertNotIn("Bash(/old/root/bin/house:*)", allow)
        self.assertIn("Bash(make:*)", allow)
        self.assertIn(f"Bash({self.root.resolve()}/bin/house out show:*)", allow)

    def test_contest_has_no_compaction_hooks(self):
        adapters.adapt(self.c, self.root, detect=lambda: NONE)
        hooks = self.load()["hooks"]
        self.assertNotIn("PreCompact", hooks)
        self.assertNotIn("PostCompact", hooks)

    def test_adapt_removes_compaction_hooks_of_an_older_adapt_and_keeps_the_users(self):
        import json
        old = f"{self.root.resolve()}/bin/house-hook"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"hooks": {
            "PreCompact": [{"hooks": [{"type": "command", "command": f"{old} pre-compact", "timeout": 30}]}],
            "PostCompact": [{"hooks": [{"type": "command", "command": f"{old} post-compact"}]},
                            {"hooks": [{"type": "command", "command": "my-counter"}]}],
            "Stop": []}}))
        adapters.adapt(self.c, self.root, detect=lambda: NONE)
        hooks = self.load()["hooks"]
        self.assertNotIn("PreCompact", hooks)
        self.assertEqual(hooks["PostCompact"], [{"hooks": [{"type": "command", "command": "my-counter"}]}])
        self.assertEqual(hooks["Stop"], [])  # the user's own (empty) event stays

    def test_house_repo_has_no_compaction_hooks(self):
        self.assertEqual(set(adapters.house_settings(self.root)["hooks"]), {"PreToolUse", "SessionStart"})

    def test_fresh_contest_gets_compact_settings(self):
        adapters.adapt(self.c, self.root, detect=lambda: NONE)
        s = self.load()
        self.assertEqual(s["autoCompactWindow"], 200000)
        self.assertEqual(s["env"]["CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS"], "150000")

    def test_user_compact_values_win(self):
        import json
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({
            "autoCompactWindow": 500000,
            "env": {"FOO": "1", "CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS": "300000"},
            "model": "opus",
        }))
        adapters.adapt(self.c, self.root, detect=lambda: NONE)
        s = self.load()
        self.assertEqual(s["autoCompactWindow"], 500000)
        self.assertEqual(s["env"]["CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS"], "300000")
        self.assertEqual(s["env"]["FOO"], "1")
        self.assertEqual(s["model"], "opus")
        self.assertTrue(s["hooks"]["PreToolUse"][0]["hooks"][0]["command"].endswith("house-hook pre-tool"))

    def test_env_not_a_dict_is_replaced(self):
        import json
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"env": ["x"]}))
        adapters.adapt(self.c, self.root, detect=lambda: NONE)
        self.assertEqual(self.load()["env"], {"CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS": "150000"})

    def test_adapt_twice_is_identical(self):
        adapters.write_local_settings(self.c, self.root)
        first = self.path.read_bytes()
        adapters.write_local_settings(self.c, self.root)
        self.assertEqual(self.path.read_bytes(), first)

    def test_house_repo_settings_have_no_compact_keys(self):
        s = adapters.house_settings(self.root)
        self.assertNotIn("autoCompactWindow", s)
        self.assertNotIn("env", s)

    def test_user_settings_survive_without_duplicates(self):
        import json
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({
            "model": "opus",
            "permissions": {"allow": ["Bash(make:*)"], "deny": ["Bash(rm -rf:*)"]},
            "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "my-lint"}]}]},
        }))
        adapters.adapt(self.c, self.root, detect=lambda: NONE)
        adapters.adapt(self.c, self.root, detect=lambda: NONE)
        s = self.load()
        self.assertEqual(s["model"], "opus")
        self.assertEqual(s["permissions"]["deny"], ["Bash(rm -rf:*)"])
        self.assertIn("Bash(make:*)", s["permissions"]["allow"])
        self.assertEqual(len(s["permissions"]["allow"]), len(set(s["permissions"]["allow"])))
        commands = [h["command"] for e in s["hooks"]["PreToolUse"] for h in e["hooks"]]
        self.assertIn("my-lint", commands)
        self.assertEqual(sum("house-hook" in c for c in commands), 1)
        self.assertNotIn("PreCompact", s["hooks"])

    def test_user_hook_in_same_entry_survives(self):
        from houselib import adapters as a
        mixed = {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [
            {"type": "command", "command": "/old/bin/house-hook pre-tool"},
            {"type": "command", "command": "my-lint"}]}]}, "permissions": "weird"}
        merged = a.merge_settings(mixed, a.claude_settings(self.root))
        commands = [h["command"] for e in merged["hooks"]["PreToolUse"] for h in e["hooks"]]
        self.assertIn("my-lint", commands)
        self.assertEqual(sum("house-hook" in c for c in commands), 1)
        self.assertIsInstance(merged["permissions"]["allow"], list)

    def test_not_committed_and_excluded(self):
        from tests.helpers import git
        adapters.adapt(self.c, self.root, detect=lambda: NONE)
        adapters.adapt(self.c, self.root, detect=lambda: NONE)
        self.assertNotIn("settings.local.json", git(self.c, "ls-files"))
        exclude = (self.c / ".git/info/exclude").read_text()
        self.assertEqual(exclude.count(".claude/settings.local.json"), 1)
        self.assertNotIn("settings.local.json", git(self.c, "status", "--porcelain"))


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

    def test_codex_task_dry_run_json(self):
        with tempfile.TemporaryDirectory() as d:
            task = Path(d) / ".house/tasks/T001-x.md"
            task.parent.mkdir(parents=True)
            task.write_text("---\nid: T001\n---\nDo it\n")
            cmd = [str(HOUSE_ROOT / "bin/codex-task"), "--dry-run", str(task), d]
            plain = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
            out = subprocess.run([*cmd[:2], "--json-log", "/tmp/x.jsonl", *cmd[2:]],
                                 capture_output=True, text=True, check=True).stdout
            self.assertNotIn(" --json ", plain)
            self.assertIn(" --json ", out)

    def test_codex_task_dry_run_brief_file(self):
        with tempfile.TemporaryDirectory() as d:
            task = Path(d) / ".house/tasks/T001-x.md"
            task.parent.mkdir(parents=True)
            task.write_text("---\nid: T001\n---\nDo it\n")
            brief = Path(d) / "b.md"
            brief.write_text("## Dead ends\n- BRIEF-MARKER do not repeat\n")
            cmd = [str(HOUSE_ROOT / "bin/codex-task"), "--dry-run"]
            out = subprocess.run([*cmd, "--brief-file", str(brief), str(task), d],
                                 capture_output=True, text=True, check=True).stdout
            self.assertIn("Hand-off from earlier attempts:", out)
            self.assertIn("BRIEF-MARKER", out)
            self.assertIn("reports/T001.md", out)
            plain = subprocess.run([*cmd, str(task), d], capture_output=True, text=True, check=True).stdout
            self.assertNotIn("Hand-off from earlier attempts", plain)
            bad = subprocess.run([*cmd, "--brief-file", str(Path(d) / "nope.md"), str(task), d],
                                 capture_output=True, text=True)
            self.assertEqual(bad.returncode, 2)
            self.assertIn("brief", bad.stderr)


if __name__ == "__main__":
    unittest.main()
