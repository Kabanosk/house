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
