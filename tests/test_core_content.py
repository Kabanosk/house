import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

from houselib import HOUSE_ROOT, adapters, scaffold
from tests.helpers import FAKE_TOOLS, make_root

CORE = HOUSE_ROOT / "core"
TEMPLATE_FILES = [
    "spec.yaml", "DECISIONS.md", "QUESTIONS.md", "PROGRESS.md", "ideas.md",
    "submissions.csv", "experiments.csv", "state.json", "reports/TEMPLATE.md", "tasks/TEMPLATE.md",
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

    def test_decisions_protocol_names_the_supersede_field(self):
        self.assertIn("Zastępuje", (CORE / "protocols/decisions.md").read_text())

    def test_briefing_tracks_last_briefing(self):
        self.assertIn("last_briefing", (CORE / "protocols/briefing.md").read_text())

    def test_optimize_mentions_spec_knobs(self):
        text = (CORE / "playbooks/optimize.md").read_text()
        for knob in ["explore_share", "stagnation_n", "endgame_share"]:
            self.assertIn(knob, text)


class ExperimentsContentTest(unittest.TestCase):
    def test_experiments_template_header(self):
        from houselib import exp
        header = (CORE / "templates/house/experiments.csv").read_text().splitlines()[0]
        self.assertEqual(header, ",".join(exp.COLUMNS))

    def test_spec_template_has_exp_knobs(self):
        from houselib import spec as specmod
        s = specmod.load_spec(CORE / "templates/house")
        text = (CORE / "templates/house/spec.yaml").read_text()
        self.assertRegex(text, r"(?m)^scoring:.*(?:\n  .*)*?\n  direction: null")
        self.assertEqual(specmod.get(s, "exp.budget"), "5m")
        self.assertEqual(specmod.get(s, "exp.scorer_minutes"), 10)
        self.assertEqual(specmod.get(s, "exp.shared"), [])

    def test_playbook_and_protocols_use_house_exp(self):
        text = (CORE / "playbooks/optimize.md").read_text()
        for cmd in ("house exp new", "house exp run", "house exp status", "--from E-"):
            self.assertIn(cmd, text)
        self.assertIn("house exp status", (CORE / "protocols/stagnation.md").read_text())
        self.assertIn("E-###", (CORE / "protocols/submissions.md").read_text())


if __name__ == "__main__":
    unittest.main()


class CheckFlowContentTest(unittest.TestCase):
    """P1: acceptance run by code, coder stops at review, house merge is the gate."""

    def read(self, rel: str) -> str:
        return (HOUSE_ROOT / rel).read_text()

    def test_report_template_has_the_eight_handoff_sections(self):
        from houselib import handoff
        text = self.read("core/templates/house/reports/TEMPLATE.md")
        heads = [h for h, _ in handoff.SECTIONS] + ["Result vs acceptance"]
        idx = [text.index(f"\n## {h}\n") for h in heads]
        self.assertEqual(idx, sorted(idx))
        cc = text.index("\n## Critical context\n")
        nxt = text.index("\n## Constraints and human decisions\n")
        self.assertTrue(cc < text.index("### Observed") < text.index("### Unverified") < nxt)
        self.assertIn("Next action:", text)
        self.assertIn("Done when:", text)
        self.assertIn("house evidence <contest> T### --attempt N", text)
        self.assertNotIn("## Evidence", text)
        self.assertNotIn("## What was done", text)
        for line in ("**Agent / model:**", "**Branch / commits:**", "**Date:**"):
            self.assertIn(line, text)
        self.assertEqual(sorted(handoff.report_sections(text)), sorted(heads))

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

    def test_retro_and_judge_use_evidence(self):
        retro = self.read("adapters/claude-code/skills/house-retro/SKILL.md")
        self.assertIn("house evidence", retro)
        self.assertIn("--session", retro)
        self.assertIn("attempts.jsonl", retro)
        self.assertIn("house evidence", self.read("core/agents/judge.md"))

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


class SourcesInSkillsTest(unittest.TestCase):
    """K3: /house-start puts contest documents in `.house/sources/` and points to them by line range."""

    def setUp(self):
        self.text = (HOUSE_ROOT / "adapters/claude-code/skills/house-start/SKILL.md").read_text()

    def test_start_asks_for_files_and_uses_house_sources_add(self):
        self.assertIn("house sources add", self.text)
        for needle in ("rules", "task statement", "API docs"):
            self.assertIn(needle, self.text)

    def test_pasted_text_only_short_and_via_stdin(self):
        self.assertIn("~5 000 characters", self.text)
        self.assertIn("house sources add <contest> - --name", self.text)

    def test_pasted_text_uses_a_quoted_heredoc(self):
        self.assertIn("house sources add <contest> - --name rules.md <<'HOUSE_SRC_<8 random hex>'", self.text)
        self.assertRegex(self.text, r"\n\s+HOUSE_SRC_<8 random hex>\n")

    def test_heredoc_marker_is_random_per_paste_checked_and_the_final_newline_is_mentioned(self):
        flat = " ".join(self.text.split())
        self.assertNotIn("<<'EOF'", self.text)
        self.assertIn("fresh for every paste", flat)
        self.assertRegex(flat, r"does not contain it")
        self.assertIn("adds one final newline", flat)

    def test_contest_name_explained_like_other_skills(self):
        self.assertIn("(contest name = the AGENTS.md title", self.text)

    def test_verbatim_and_never_retype_are_separate_bullets(self):
        bullets = [" ".join(b.split()) for b in re.split(r"\n   - ", self.text)[1:]]
        verbatim = [b for b in bullets if "verbatim" in b and "spec.yaml" in b]
        retype = [b for b in bullets if "never retype" in b]
        self.assertEqual(len(verbatim), 1, bullets)
        self.assertEqual(len(retype), 1, bullets)
        self.assertNotEqual(verbatim[0], retype[0])

    def test_spec_and_tasks_reference_path_and_line_range(self):
        self.assertIn(".house/sources/rules.md:L120-L180", self.text)
        self.assertIn("never retype", self.text)


class DecisionsInSkillsTest(unittest.TestCase):
    """A5: intake and ideation consult the human decisions of earlier contests, as constraints to confirm."""

    def test_start_and_ideate_consult_earlier_human_decisions(self):
        for skill in ("house-start", "house-ideate"):
            text = (HOUSE_ROOT / f"adapters/claude-code/skills/{skill}/SKILL.md").read_text()
            self.assertIn("$HOUSE_ROOT/bin/house decisions --by human", text, skill)
            self.assertRegex(text, r"(?i)confirm or drop", skill)
            self.assertRegex(text, r"(?i)never adopt", skill)

    def test_start_and_ideate_exclude_the_current_contest_and_do_not_wait_in_autopilot(self):
        for skill in ("house-start", "house-ideate"):
            text = (HOUSE_ROOT / f"adapters/claude-code/skills/{skill}/SKILL.md").read_text()
            self.assertIn("house decisions --by human --exclude <this contest>", text, skill)
            self.assertRegex(text, r"(?is)autopilot.{0,40}do not wait.{0,60}briefing", skill)


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


READING_PROFILES = ["coordinator", "coder", "researcher", "judge"]
READING_START = "Long inputs (contest docs, logs, data):"


def _reading_block(text):
    start = text.index(READING_START)
    return "\n".join(text[start:].splitlines()[:6])


class ReadingRulesContentTest(unittest.TestCase):
    def test_reading_rules_identical_in_profiles(self):
        blocks = {p: _reading_block((CORE / "agents" / f"{p}.md").read_text()) for p in READING_PROFILES}
        self.assertEqual(len(set(blocks.values())), 1, blocks)
        block = next(iter(blocks.values()))
        for needle in ("40 KB", "200 lines", "16 000", "allow_large", "offset", "sed -n", "line range"):
            self.assertIn(needle, block)

    def test_reading_rules_point_to_house_sources(self):
        block = _reading_block((CORE / "agents" / "coder.md").read_text())
        self.assertIn("first a table of contents (`house sources <contest>` for files in `.house/sources/`, "
                      "else `grep -n '^#' <file>`) or `grep -n` on the question's terms, then read a range "
                      "around the hits", block)
        for profile in READING_PROFILES:
            self.assertIn("house sources", (CORE / "agents" / f"{profile}.md").read_text(), profile)

    def test_reading_rules_in_generated_agents_md(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_root(Path(tmp))
            contest = scaffold.new_contest("c1", None, root=root, detect=lambda: FAKE_TOOLS)
            text = adapters.codex(contest, root)["AGENTS.md"]
        self.assertIn(READING_START, text)
        self.assertIn("allow_large", text)


class DeadEndsInstructionsTest(unittest.TestCase):
    """Workers report in the hand-off sections and write dead ends when they abandon an approach."""

    NEEDLES = ("Dead ends", "Observed", "Unverified", "Next action", "path:line")
    MOMENT = ("at the moment", "when you abandon")

    def _check(self, text):
        for needle in self.NEEDLES:
            self.assertIn(needle, text)
        self.assertTrue(any(m in text for m in self.MOMENT), text)
        self.assertIn("do not repeat", text.lower())

    def test_coder_profiles(self):
        for name in ("coder", "coder-codex"):
            with self.subTest(name=name):
                self._check((CORE / "agents" / f"{name}.md").read_text())

    def test_codex_task_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            house = Path(tmp) / ".house"
            (house / "tasks").mkdir(parents=True)
            task = house / "tasks" / "T001-x.md"
            task.write_text("---\nid: T001\n---\nDo it.\n")
            out = subprocess.run([str(HOUSE_ROOT / "bin/codex-task"), "--dry-run", str(task), tmp],
                                 capture_output=True, text=True, check=True).stdout
        self._check(out)
        self.assertLess(len(out), 2500)
