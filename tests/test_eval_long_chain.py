"""K5 `long-chain`: the `chain_big` fixture (sizes, planted facts, hidden tests red on the fixture and green on the
reference solution), the case file and its variants applied to a built fixture. No agent and no Docker run here: the
hidden tests run on the host against repo-authored code only (the generated fixture and its reference solution)."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from houselib import HOUSE_ROOT, registry
from houselib.evals import cases, chainfx, fixtures, runner
from tests.helpers import git, make_root
from tests.test_eval_sandbox import ScratchBase, fast_env

CASE = HOUSE_ROOT / "evals/cases/long-chain.json"
TEST_ENV = {"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1", "LANG": "C.UTF-8"}


def build(tmp: Path, reference: bool = False) -> tuple[Path, dict]:
    """Build `chain_big` in a throwaway house root below `tmp`; returns the contest and the info dict."""
    root = make_root(tmp)
    shutil.copytree(HOUSE_ROOT / "houselib", root / "houselib", ignore=shutil.ignore_patterns("__pycache__"))
    contest = root / "contests/payments"
    info = fixtures.chain_big(contest, env=fast_env(), reference=reference)
    return contest, info


def run_hidden(contest: Path, file_name: str) -> subprocess.CompletedProcess:
    """One hidden test file, run the way the case's predicate runs it (contest as cwd, unittest discover)."""
    hidden = contest.parents[2] / fixtures.HIDDEN_DIR / "chain"
    return subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", str(hidden), "-t", str(hidden),
                           "-p", file_name], cwd=contest, capture_output=True, text=True, env=TEST_ENV,
                          timeout=300, check=False)


def tracked_text(contest: Path) -> dict[str, str]:
    return {rel: (contest / rel).read_bytes().decode("utf-8") for rel in git(contest, "ls-files").split()}


class ChainFixtureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        base = Path(cls._tmp.name)
        (base / "a").mkdir()
        (base / "b").mkdir()
        cls.contest, cls.info = build(base / "a")
        cls.ref_contest, _ = build(base / "b", reference=True)
        cls.files = tracked_text(cls.contest)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_info_and_clean_registered_contest(self):
        self.assertEqual((self.info["fixture"], self.info["task_ids"], self.info["expect"], self.info["hidden"]),
                         ("chain_big", [], "", "chain"))
        self.assertEqual((self.info["root_cause"], self.info["legacy_hold"]), ("E2E-0917", 5040))
        self.assertEqual(git(self.contest, "status", "--porcelain").strip(), "")
        self.assertEqual(git(self.contest, "branch", "--show-current").strip(), "main")
        self.assertEqual(registry.load(self.contest.parents[1] / "contests.yaml"), {"payments": ""})
        self.assertGreaterEqual(len(git(self.contest, "log", "--format=%s").splitlines()), 4)

    def test_modules_are_as_long_as_real_ones(self):
        modules = {rel: text for rel, text in self.files.items()
                   if rel.startswith("ledger/") and not rel.endswith("__init__.py")}
        self.assertGreaterEqual(len(modules), 24)
        for rel, text in modules.items():
            self.assertTrue(300 <= text.count("\n") <= 600, (rel, text.count("\n")))

    def test_logs_and_data_are_big(self):
        for rel in ("data/logs/settlement-2026-11-12.log", "data/logs/reconcile-2026-11-14.log",
                    "data/logs/limits-2026-11-16.log"):
            self.assertTrue(2900 <= self.files[rel].count("\n") <= 3100, rel)
        self.assertGreater(self.files["data/logs/cards-2026-11-15.log"].count("\n"), 3900)
        self.assertGreater(self.files["data/statements/2026-11-full.csv"].count("\n"), 20_000)

    def test_planted_facts_occur_exactly_once(self):
        def where(pattern):
            return [(rel, n) for rel, text in self.files.items()
                    for n, line in enumerate(text.splitlines(), 1) if re.search(pattern, line)]
        root_cause = where(r"E2E-0917")
        self.assertEqual([rel for rel, _ in root_cause], ["data/logs/settlement-2026-11-12.log"])
        line = self.files[root_cause[0][0]].splitlines()[root_cause[0][1] - 1]
        self.assertIn("RJCT", line)
        self.assertIn("EndToEndId not unique", line)
        # the hold production really uses is written nowhere: it is computed from fragments in three modules
        self.assertEqual(where(r"(?<!\d)5040(?!\d)"), [])
        self.assertEqual([rel for rel, _ in where(r'"legacy-elixir": \{"days": 3, "hours": 12\}')],
                         ["ledger/bankdays.py"])
        self.assertEqual([rel for rel, _ in where(r'"bank_interface": "elixir"')], ["ledger/profiles.py"])
        self.assertEqual(where(r"(?i)fastcsv"), [])  # the dead end lives only in the human's turn

    def test_root_cause_needs_correlation_not_one_grep(self):
        lines = self.files["data/logs/settlement-2026-11-12.log"].splitlines()
        true = next(line for line in lines if "E2E-0917" in line)
        for word in ("MP0042", "Bazarek", "B20261112", "SETL/"):
            self.assertNotIn(word, true)  # the bank's report names only its own file id
        codes = {c for line in lines for c in re.findall(r"rsn=(E2E-\d{4})", line)}
        self.assertGreaterEqual(len(codes - {"E2E-0917"}), 4)  # decoy reason codes elsewhere in the log
        file_id = re.search(r"for (PAYF-\S+):", true).group(1)
        upload = [line for line in lines if f"bank file id {file_id}" in line]
        self.assertEqual(len(upload), 1)
        batch = re.search(r"upload of (B\d{8}-\d{2})\.csv", upload[0]).group(1)
        self.assertTrue(any(f"merchant=MP0042-" in line and f"/{batch}/" in line for line in lines))

    def test_rejected_file_ids_appear_only_in_the_chain(self):
        lines = self.files["data/logs/settlement-2026-11-12.log"].splitlines()
        true = next(line for line in lines if "E2E-0917" in line)
        first = re.search(r"status report (SR-\d+)", true).group(1)
        rejected = {re.search(r"for (PAYF-\S+):", line).group(1) for line in lines
                    if "grpSts=RJCT" in line and ("E2E-0917" in line or f"see {first}" in line)}
        self.assertEqual(len(rejected), 4)
        for line in lines:
            if any(f in line for f in rejected):
                self.assertTrue("bank file id" in line or "status report" in line, line)
        summary = next(line for line in lines if "files rejected by the bank" in line)
        self.assertNotIn("PAYF", summary)

    def test_no_fixture_text_states_decision_d007(self):
        for rel, text in self.files.items():
            if not rel.startswith("data/"):
                self.assertIsNone(re.search(r"(?i)never floats?|all values are integers|integer number of grosze|"
                                            r"without floats|integer grosze", text), rel)

    def test_production_hold_is_built_from_profile_interface_and_calendar(self):
        code = ("from ledger import settings\n"
                "print([settings.load(e, env={})['reserve_hold_minutes'] for e in ('production', 'preprod', 'staging')])")
        done = subprocess.run([sys.executable, "-c", code], cwd=self.contest, capture_output=True, text=True,
                              env=TEST_ENV, timeout=60)
        self.assertEqual(done.stdout.strip(), "[5040, 3240, 1440]", done.stderr)

    def test_root_cause_is_found_in_the_middle_of_the_log_not_at_its_ends(self):
        lines = self.files["data/logs/settlement-2026-11-12.log"].splitlines()
        n = next(i for i, line in enumerate(lines) if "E2E-0917" in line)
        self.assertTrue(500 < n < len(lines) - 500, n)
        self.assertGreater(sum(line.endswith("/000") for line in lines), 9)  # the cut references, in plain sight

    def test_decisions_stop_at_d006(self):
        text = (self.contest / ".house/DECISIONS.md").read_text()
        self.assertIn("## D-006", text)
        self.assertNotIn("D-007", text)
        self.assertNotIn("D-008", text)

    def test_visible_tests_pass_on_the_fixture_and_the_reference(self):
        for contest in (self.contest, self.ref_contest):
            with self.subTest(contest=contest.parents[2].name):
                done = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "."],
                                      cwd=contest, capture_output=True, text=True, env=TEST_ENV, timeout=120)
                self.assertEqual(done.returncode, 0, done.stderr[-2000:])
                self.assertGreaterEqual(runner.parse_test_summary(done.stderr)[0], 100)
                self.assertIn("unmatched bank line", done.stderr)  # the jobs' warnings show up in a test run

    def test_every_module_imports(self):
        names = [rel[len("ledger/"):-3] for rel in self.files if rel.startswith("ledger/") and rel.endswith(".py")]
        code = "import importlib\nfor n in %r:\n    importlib.import_module('ledger.' + n)\n" % sorted(names)
        done = subprocess.run([sys.executable, "-c", code], cwd=self.contest, capture_output=True, text=True,
                              env=TEST_ENV, timeout=60)
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_hidden_tests_fail_on_the_fixture_and_pass_on_the_reference(self):
        hidden = self.contest.parents[2] / fixtures.HIDDEN_DIR / "chain"
        self.assertEqual(sorted(p.name for p in hidden.iterdir()), sorted(f for f, _ in chainfx.HIDDEN_FILES.values()))
        self.assertNotIn("chain", git(self.contest, "ls-files"))
        for label, (file_name, count) in chainfx.HIDDEN_FILES.items():
            with self.subTest(label=label):
                red = run_hidden(self.contest, file_name)
                self.assertNotEqual(red.returncode, 0, red.stderr[-1500:])
                green = run_hidden(self.ref_contest, file_name)
                self.assertEqual(green.returncode, 0, green.stderr[-1500:])
                self.assertEqual(runner.parse_test_summary(green.stderr), (count, 0))

    def test_reference_leaves_the_partner_api_alone(self):
        self.assertEqual((self.ref_contest / "ledger/api_v1.py").read_bytes(),
                         (self.contest / "ledger/api_v1.py").read_bytes())

    def test_generation_is_deterministic(self):
        first = chainfx.contest_files()
        chainfx._generate.cache_clear()
        self.assertEqual(chainfx.contest_files(), first)
        self.assertEqual({rel: first[rel] for rel in first}, {rel: self.files[rel] for rel in first})

    def test_reference_is_refused_from_a_case_file(self):
        data = json.loads(CASE.read_text())
        data["fixture"]["reference"] = True
        path = Path(self._tmp.name) / "long-chain.json"
        path.write_text(json.dumps(data))
        with self.assertRaises(ValueError) as ctx:
            cases.load(path)
        self.assertIn("fixture.reference", str(ctx.exception))

    def test_unknown_parameter_is_refused(self):
        with self.assertRaises(ValueError):
            fixtures.chain_big(Path(self._tmp.name) / "zz/contests/x", env=fast_env(), bogus=1)


class LongChainCaseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.case = cases.load(CASE)

    def test_shape(self):
        c = self.case
        self.assertEqual((c.session, c.agent, c.skill, c.model), ("main", None, None, "sonnet"))
        self.assertEqual((c.fixture["name"], c.kind, c.max_turns, c.timeout_seconds, c.tags),
                         ("chain_big", "capability", 400, 10800, ["k5"]))
        # turn 2 D-007/D-008, 3-6 sub-tasks 2-5, 7 D-009, 8-9 sub-tasks 6-7, 10 the placeholder slot (K5 compacts
        # there), 11-15 sub-tasks 8-12, 16 the final question
        self.assertEqual(len(c.followups), 15)
        self.assertEqual(c.followups[8], 'Thanks. Quick check before the next one: reply only "ok".')

    def test_every_outcome_predicate_is_labelled(self):
        labels = [p.get("label") for p in self.case.outcome]
        self.assertTrue(all(labels))
        self.assertTrue({"s1-tests", "s2-tests", "s3-tests", "s4-tests", "s5-tests", "s6-tests", "d007-integers",
                         "d008-api-v1", "fact-root-cause", "fact-legacy-hold"} <= set(labels))

    def test_hidden_predicates_name_their_file_and_count(self):
        hidden = {p["label"]: p for p in self.case.outcome if p["type"] == "hidden_tests"}
        self.assertEqual(set(hidden), set(chainfx.HIDDEN_FILES))
        for label, (file_name, count) in chainfx.HIDDEN_FILES.items():
            pred = hidden[label]
            self.assertEqual(pred["dir"], "chain")
            self.assertEqual(pred["command"], ["python3", "-m", "unittest", "discover", "-s", "{hidden}", "-t",
                                               "{hidden}", "-p", file_name])
            self.assertEqual(pred["min_passed"], count)
            self.assertEqual(chainfx.hidden_tests()[file_name].count("    def test_"), count, label)

    def test_api_v1_is_pinned(self):
        self.assertIn({"label": "d008-api-v1", "type": "file_unchanged", "path": "ledger/api_v1.py"},
                      self.case.outcome)

    def test_turns_read_like_a_human_and_never_give_the_facts_away(self):
        turns = [self.case.prompt, *self.case.followups]
        for text in turns:
            self.assertNotIn("DECISIONS.md", text)
            for word in ("compact", "experiment", "hidden", "record", "E2E-0917", "5040", "84 h",
                         "3.5 days", "remember"):
                self.assertNotIn(word.lower(), text.lower(), (word, text[:60]))
        self.assertIn("Bazarek", self.case.prompt)
        self.assertIn("MP0042", self.case.prompt)
        self.assertIn("I have acceptance tests I will run afterwards", self.case.prompt)
        decisions = self.case.followups[0]
        self.assertIn("Decision D-007: amounts are integers in grosze, never floats.", decisions)
        self.assertIn("Decision D-008: do not change any signature in ledger/api_v1.py.", decisions)
        self.assertIn("fastcsv", self.case.followups[1])
        self.assertTrue(all("fastcsv" not in t for i, t in enumerate(turns) if i != 2))
        turn = dict(enumerate(turns, start=1))
        self.assertIn("Decision D-009: refunds are never netted against fees; keep them separate in reports.",
                      turn[7])
        # the temptations come late, after the production threshold: D-009 in turn 12, D-007/D-008 in turn 14,
        # the dead end again in turn 15; the early facts only in turn 16
        self.assertIn("deductions", turn[12])
        self.assertIn("api_v1", turn[14])  # invites the signature change
        self.assertIn("1.375%", turn[14])  # and a float
        self.assertIn("slow", turn[15])
        self.assertIn("reason code", turn[16])
        for n in range(2, 16):
            self.assertNotIn("reason code", turn[n])

    def answer_ok(self, label: str, text: str) -> bool:
        pred = next(p for p in self.case.outcome if p.get("label") == label)
        return re.search(pred["match"], text) is not None

    def test_final_answer_patterns(self):
        good = ("The bank rejected the marketplace batches with reason E2E-0917 (EndToEndId not unique): references "
                "were cut to 35 characters. Production really held payouts for 5040 minutes (84 hours).")
        self.assertTrue(self.answer_ok("fact-root-cause", good))
        self.assertTrue(self.answer_ok("fact-legacy-hold", good))
        for hold in ("5 040 minutes", "5,040 min", "84h", "3.5 days", "84 hours", "3 days and 12 hours", "3d 12h"):
            self.assertTrue(self.answer_ok("fact-legacy-hold", hold), hold)
        self.assertTrue(self.answer_ok("fact-root-cause", "reason code e2e-0917"))
        vague = ("The bank rejected batches because of duplicate references; production used the legacy hold of "
                 "2880, preprod 3240 (2 days 6 hours).")
        self.assertFalse(self.answer_ok("fact-root-cause", vague))
        self.assertFalse(self.answer_ok("fact-legacy-hold", vague))
        self.assertFalse(self.answer_ok("fact-legacy-hold", "15040 rows, 0.5040 ratio"))

    def bash(self, command: str) -> bool:
        call = next(c for c in self.case.forbid if c.label == "fastcsv-retry")
        return call.matches({"command": command})

    def test_fastcsv_retry_counts_attempts_in_sub_task_12(self):
        call = next(c for c in self.case.forbid if c.label == "fastcsv-retry")
        # one attempt in the late speed turn (turn 15 = sub-task 12) is the repeated dead end (C10 of K5)
        self.assertEqual((call.tool, call.min, call.from_turn), ("Bash", 1, 15))
        self.assertIn("slow", self.case.followups[call.from_turn - 2])
        for command in ("pip install fastcsv", "pip3 install --user fastcsv", 'pip install "fastcsv"',
                        "pip install 'fastcsv==0.1.3'", 'python3 -m pip install --user "fastcsv>=0.1"', "python3 -m pip install "
                        "--break-system-packages fastcsv", 'python3 -c "import fastcsv"',
                        "python3 -c 'from fastcsv import Reader'", "cd /x && python3 -c \"import importlib.util; "
                        "print(importlib.util.find_spec('fastcsv'))\"", "python3 - <<'EOF'\ntry:\n    import fastcsv\n"
                        "except ImportError:\n    print('no')\nEOF", "uv pip install fastcsv"):
            self.assertTrue(self.bash(command), command)
        for command in ("grep -rn fastcsv ledger/", "pip list", "python3 -m unittest discover -s tests -t .",
                        "pip install -e .", "ls data/statements", "echo fastcsv is not available",
                        "pip install -r requirements.txt\necho fastcsv"):
            self.assertFalse(self.bash(command), command)


class LongChainVariantsTest(ScratchBase):
    def test_the_slot_turn_is_neutral_for_control_and_a_compact_for_the_others(self):
        case = cases.load(CASE)
        slot = case.followups[8]
        self.assertEqual(slot, 'Thanks. Quick check before the next one: reply only "ok".')
        self.assertEqual(case.followups[7].split()[0:2], ["Several", "marketplace"])  # slot is turn 10, after turn 9
        self.assertIn("Corporate customers", case.followups[9])  # the old turn 10 (S8) is now turn 11
        self.assertEqual(case.variants["control"].followup_text, cases.FollowupText(8, slot))
        for name in ("house", "default-compact"):
            self.assertEqual(case.variants[name].followup_text, cases.FollowupText(8, "/compact"), name)

    def test_every_variant_applies_to_a_built_fixture(self):
        case = cases.load(CASE)
        self.assertEqual(set(case.variants), {"control", "house", "default-compact"})
        with self.temp_root(case.fixture) as root:
            path = root.contest / ".claude/settings.local.json"
            original = path.read_text()
            before = json.loads(original)
            self.assertIn("autoCompactWindow", before)
            head = git(root.contest, "rev-parse", "HEAD").strip()
            seen = {}
            for name, variant in case.variants.items():
                path.write_text(original)
                runner.apply_variant(root, variant)
                seen[name] = json.loads(path.read_text())
                self.assertEqual(git(root.contest, "status", "--porcelain").strip(), "", name)
                self.assertEqual(git(root.contest, "rev-parse", "HEAD").strip(), head, name)
            self.assertEqual(seen["house"], before)
            control = seen["control"]
            self.assertNotIn("autoCompactWindow", control)
            self.assertNotIn("CLAUDE_CODE_IDLE_COMPACT_MIN_TOKENS", control.get("env", {}))
            self.assertEqual(control["hooks"], before["hooks"])
            default = seen["default-compact"]
            self.assertEqual(default["autoCompactWindow"], before["autoCompactWindow"])
            self.assertEqual(default["env"], before["env"])
            self.assertNotIn("PreCompact", default["hooks"])
            self.assertNotIn("PostCompact", default["hooks"])
            for event in ("PreToolUse", "PostToolUse"):
                self.assertEqual(default["hooks"][event], before["hooks"][event])
            [start] = default["hooks"]["SessionStart"]
            self.assertEqual(start["matcher"], "startup|resume|clear")
            self.assertEqual(start["hooks"], before["hooks"]["SessionStart"][0]["hooks"])
            self.assertTrue(start["hooks"][0]["command"].endswith("bin/house-hook session-start"))
            self.assertTrue((root.contest / "CLAUDE.md").is_file())  # what a main-session case needs installed

    def test_the_sandbox_refuses_a_reference_build(self):
        with self.assertRaises(ValueError) as ctx:
            with self.temp_root({"name": "chain_big", "contest": "payments", "reference": True}):
                pass
        self.assertIn("test-only", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
