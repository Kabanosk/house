import json
import re
import unittest

from houselib import HOUSE_ROOT

CORE = HOUSE_ROOT / "core"
TEMPLATE_FILES = [
    "spec.yaml", "DECISIONS.md", "QUESTIONS.md", "PROGRESS.md", "ideas.md",
    "submissions.csv", "state.json", "reports/TEMPLATE.md", "tasks/TEMPLATE.md",
]
PROTOCOLS = ["questions", "briefing", "decisions", "submissions", "stagnation", "export"]
PLAYBOOKS = ["lifecycle", "build", "optimize"]
SPEC_KEYS = ["contest", "kind", "goal", "scoring", "timeline", "rules", "mode",
             "limits", "submissions", "deploy", "languages", "tools", "team"]


class CoreContentTest(unittest.TestCase):
    def test_all_files_exist(self):
        paths = [CORE / "templates/house" / f for f in TEMPLATE_FILES]
        paths += [CORE / "protocols" / f"{p}.md" for p in PROTOCOLS]
        paths += [CORE / "playbooks" / f"{p}.md" for p in PLAYBOOKS]
        missing = [str(p) for p in paths if not p.is_file()]
        self.assertEqual(missing, [])

    def test_spec_has_all_top_level_keys(self):
        text = (CORE / "templates/house/spec.yaml").read_text()
        for key in SPEC_KEYS:
            self.assertRegex(text, re.compile(rf"^{key}:", re.M), key)

    def test_spec_defaults(self):
        text = (CORE / "templates/house/spec.yaml").read_text()
        for line in ["mode: copilot", "max_parallel_agents: 3", "ideas_per_round: 5", "stagnation_n: 3",
                     "auto_submit: true", "explore_share: 0.25", "endgame_share: 0.20", "deploy: true",
                     "tools: see tools.json"]:
            self.assertIn(line, text)

    def test_state_and_submissions(self):
        self.assertEqual(json.loads((CORE / "templates/house/state.json").read_text()), {"last_briefing": None})
        header = (CORE / "templates/house/submissions.csv").read_text().splitlines()[0]
        self.assertEqual(header, "time,submission_id,commit,kind,local_score,leaderboard_score,note")

    def test_templates_have_no_entry_lookalikes(self):
        for name in ("QUESTIONS.md", "DECISIONS.md"):
            text = (CORE / "templates/house" / name).read_text()
            self.assertIsNone(re.search(r"^## [QD]-", text, re.M), name)

    def test_house_tmp_is_gitignored(self):
        self.assertIn("tmp/", (CORE / "templates/house/.gitignore").read_text().splitlines())

    def test_briefing_tracks_last_briefing(self):
        self.assertIn("last_briefing", (CORE / "protocols/briefing.md").read_text())

    def test_optimize_mentions_spec_knobs(self):
        text = (CORE / "playbooks/optimize.md").read_text()
        for knob in ["explore_share", "stagnation_n", "endgame_share"]:
            self.assertIn(knob, text)


if __name__ == "__main__":
    unittest.main()


class CheckFlowContentTest(unittest.TestCase):
    """P1: acceptance run by code, coder stops at review, house merge is the gate."""

    def read(self, rel: str) -> str:
        return (HOUSE_ROOT / rel).read_text()

    def test_task_template_has_structured_acceptance(self):
        from houselib.fm import parse_frontmatter
        meta, _ = parse_frontmatter(self.read("core/templates/house/tasks/TEMPLATE.md"))
        self.assertIn("protected", meta)
        self.assertTrue(any(isinstance(a, dict) and "run" in a for a in meta["acceptance"]))

    def test_run_skill_uses_merge_gate(self):
        text = self.read("adapters/claude-code/skills/house-run/SKILL.md")
        self.assertIn("house merge", text)
        self.assertIn("merged", text)

    def test_plan_skill_freezes(self):
        self.assertIn("--freeze", self.read("adapters/claude-code/skills/house-plan/SKILL.md"))

    def test_coders_stop_at_review(self):
        for rel in ("core/agents/coder.md", "core/agents/coder-codex.md"):
            text = self.read(rel)
            self.assertIn("status: review", text, rel)
            self.assertIn("house check", text, rel)
            self.assertNotIn("status: done", text, rel)

    def test_codex_driver_runs_house_check(self):
        from houselib import adapters
        self.assertIn("house check", adapters.CODEX_DRIVER)
        self.assertNotIn("Set the task status", adapters.CODEX_DRIVER)

    def test_judge_records_manual(self):
        self.assertIn("--manual", self.read("core/agents/judge.md"))

    def test_lifecycle_statuses(self):
        text = self.read("core/playbooks/lifecycle.md")
        for word in ("review", "merged", "house merge", "--freeze"):
            self.assertIn(word, text)


class SignalContentTest(unittest.TestCase):
    """P3: notify section in spec, decision levels in QUESTIONS and the protocol."""

    def read(self, rel: str) -> str:
        return (HOUSE_ROOT / rel).read_text()

    def test_spec_has_notify_and_guard(self):
        import tempfile
        from pathlib import Path

        from houselib import spec
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "spec.yaml").write_text(self.read("core/templates/house/spec.yaml"))
            s = spec.load_spec(Path(d))
        self.assertIn("alias", s["notify"])
        self.assertIn("signal_group", s["notify"])
        self.assertEqual(spec.get(s, "guard.push_allow"), ["origin"])

    def test_questions_template_has_levels(self):
        text = self.read("core/templates/house/QUESTIONS.md")
        for word in ("**Poziom:**", "**Termin:**", "**Domyślnie:**"):
            self.assertIn(word, text)

    def test_protocol_levels_and_notify(self):
        text = self.read("core/protocols/questions.md")
        for word in ("krytyczny", "zawsze człowiek", "Termin", "house notify", "house ask"):
            self.assertIn(word, text)

    def test_start_skill_asks_for_alias_and_group(self):
        text = self.read("adapters/claude-code/skills/house-start/SKILL.md")
        self.assertIn("notify.alias", text)
        self.assertIn("signal_group", text)


class ReviewContentTest(unittest.TestCase):
    """P5: cross-model review in the flow."""

    def read(self, rel: str) -> str:
        return (HOUSE_ROOT / rel).read_text()

    def test_plan_marks_review_always(self):
        self.assertIn("review: always", self.read("adapters/claude-code/skills/house-plan/SKILL.md"))

    def test_run_reviews_milestones_and_always_tasks(self):
        text = self.read("adapters/claude-code/skills/house-run/SKILL.md")
        self.assertIn("house review", text)
        self.assertIn("--since last", text)

    def test_spec_has_review_budget(self):
        import tempfile
        from pathlib import Path

        from houselib import spec
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "spec.yaml").write_text(self.read("core/templates/house/spec.yaml"))
            s = spec.load_spec(Path(d))
        self.assertEqual(spec.get(s, "limits.codex_reviews_per_day"), 4)
        self.assertEqual(spec.get(s, "review.minutes"), 20)

    def test_second_opinion_vendored(self):
        d = HOUSE_ROOT / "vendor/skills/second-opinion"
        self.assertTrue((d / "SKILL.md").is_file())
        self.assertIn("MIT", (d / "LICENSE").read_text())
        self.assertIn("d73fea9", (d / "ATTRIBUTION.md").read_text())
