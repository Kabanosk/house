"""K5 `long-chain-xl`: `chain_big` with `extended: true` (ten more sub-tasks X1-X10, so the production threshold is
passed on its own) and its case file. The base build stays byte-identical, the extension's reference patches apply
exactly once, its hidden tests are red on the fixture and green on the reference, and the case keeps the base case's
probes after the extra sub-tasks. No agent and no Docker run here (as in `test_eval_long_chain`)."""
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from houselib import HOUSE_ROOT
from houselib.evals import cases, chainfx, fixtures, runner
from tests.helpers import git, make_root
from tests.test_eval_long_chain import TEST_ENV, run_hidden, tracked_text
from tests.test_eval_sandbox import ScratchBase, fast_env

CASE = HOUSE_ROOT / "evals/cases/long-chain-xl.json"
BASE_CASE = HOUSE_ROOT / "evals/cases/long-chain.json"


def build(tmp: Path, reference: bool = False) -> tuple[Path, dict]:
    """Build the extended `chain_big` in a throwaway house root below `tmp`; returns the contest and the info."""
    root = make_root(tmp)
    shutil.copytree(HOUSE_ROOT / "houselib", root / "houselib", ignore=shutil.ignore_patterns("__pycache__"))
    contest = root / "contests/payments"
    info = fixtures.chain_big(contest, env=fast_env(), reference=reference, extended=True)
    return contest, info


class ExtendedFixtureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        base = Path(cls._tmp.name)
        (base / "a").mkdir()
        (base / "b").mkdir()
        cls.contest, cls.info = build(base / "a")
        cls.ref_contest, _ = build(base / "b", reference=True)
        cls.files = tracked_text(cls.contest)
        cls.base = chainfx.contest_files()
        cls.added = sorted(set(chainfx.contest_files(extended=True)) - set(cls.base))

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_info_and_a_clean_contest(self):
        self.assertEqual((self.info["fixture"], self.info["hidden"], self.info["root_cause"],
                          self.info["legacy_hold"]), ("chain_big", "chain", "E2E-0917", 5040))
        self.assertEqual(git(self.contest, "status", "--porcelain").strip(), "")
        self.assertEqual(len(git(self.contest, "log", "--format=%s").splitlines()), 5)  # scaffold + 4 stages

    def test_base_files_are_byte_identical(self):
        extended = chainfx.contest_files(extended=True)
        self.assertEqual({rel: extended[rel] for rel in self.base}, self.base)
        self.assertEqual({rel: self.files[rel] for rel in self.base}, self.base)
        self.assertTrue(set(extended) <= set(self.files))
        # the base reference too: the extension's patches come on top of it
        base_ref = chainfx.contest_files(reference=True)
        ext_ref = chainfx.contest_files(reference=True, extended=True)
        touched = {rel for rel, *_ in chainfx.EXT_REFERENCE}
        self.assertEqual({rel: ext_ref[rel] for rel in base_ref if rel not in touched},
                         {rel: text for rel, text in base_ref.items() if rel not in touched})

    def test_what_the_extension_adds(self):
        self.assertEqual(self.added, sorted(
            [f"ledger/{name}.py" for name in ("chargebacks", "exports", "invoices", "onboarding", "retry")]
            + [f"data/logs/{name}.log" for name in ("audit-2026-11", "disputes-2026-11-17", "invoices-2026-11-01",
                                                    "onboarding-2026-11-17", "retry-2026-11-13")]
            + [f"docs/{name}.md" for name in ("accounting-export", "bank-holidays-2027", "chargebacks",
                                              "compliance-iban", "fee-invoices", "merchant-emails")]
            + ["tests/test_extension_modules.py"]))
        for rel in self.added:
            if rel.startswith("ledger/"):
                self.assertTrue(300 <= self.files[rel].count("\n") <= 600, rel)
            if rel.startswith("data/logs/"):
                self.assertTrue(2400 <= self.files[rel].count("\n") <= 3000, rel)

    def test_the_planted_facts_stay_unique(self):
        for rel in self.added:
            text = self.files[rel]
            for pattern in (r"(?<!\d)5040(?!\d)", r"E2E-0917", r"(?i)fastcsv", r"(?i)never floats?"):
                self.assertIsNone(re.search(pattern, text), (rel, pattern))
        decisions = (self.contest / ".house/DECISIONS.md").read_text()
        self.assertIn("## D-006", decisions)
        self.assertNotIn("D-007", decisions)

    def test_every_extension_patch_applies_exactly_once(self):
        files = chainfx._console_in_polish(chainfx._patched(chainfx.contest_files(extended=True), chainfx.REFERENCE))
        for rel, old, new, regex in chainfx.EXT_REFERENCE:
            with self.subTest(file=rel, old=(old if isinstance(old, str) else "")[:50]):
                text = files[rel]
                count = len(re.findall(old, text)) if regex else text.count(old)
                self.assertEqual(count, 1)
                files = chainfx._patched(files, [(rel, old, new, regex)])
                self.assertNotEqual(files[rel], text)
        self.assertEqual(files, chainfx.contest_files(reference=True, extended=True))

    def test_visible_tests_pass_on_the_fixture_and_the_reference(self):
        for contest in (self.contest, self.ref_contest):
            with self.subTest(contest=contest.parents[2].name):
                done = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "."],
                                      cwd=contest, capture_output=True, text=True, env=TEST_ENV, timeout=120)
                self.assertEqual(done.returncode, 0, done.stderr[-2000:])
                self.assertGreaterEqual(runner.parse_test_summary(done.stderr)[0], 105)

    def test_hidden_files_are_the_base_ones_plus_the_extension(self):
        hidden = self.contest.parents[2] / fixtures.HIDDEN_DIR / "chain"
        names = [f for f, _ in chainfx.HIDDEN_FILES.values()] + [f for f, _ in chainfx.EXT_HIDDEN_FILES.values()]
        self.assertEqual(sorted(p.name for p in hidden.iterdir()), sorted(names))
        self.assertNotIn("chain", git(self.contest, "ls-files"))
        self.assertFalse(set(chainfx.HIDDEN_FILES) & set(chainfx.EXT_HIDDEN_FILES))
        base, extended = chainfx.hidden_tests(), chainfx.hidden_tests(extended=True)
        self.assertEqual({name: extended[name] for name in base}, base)
        for name in extended:
            self.assertNotIn("@VALID@", extended[name])
            self.assertEqual((hidden / name).read_text(), extended[name])

    def test_extension_hidden_tests_fail_on_the_fixture_and_pass_on_the_reference(self):
        for label, (file_name, count) in chainfx.EXT_HIDDEN_FILES.items():
            with self.subTest(label=label):
                self.assertEqual(chainfx.hidden_tests(extended=True)[file_name].count("    def test_"), count)
                red = run_hidden(self.contest, file_name)
                self.assertNotEqual(red.returncode, 0, red.stderr[-1500:])
                green = run_hidden(self.ref_contest, file_name)
                self.assertEqual(green.returncode, 0, green.stderr[-1500:])
                self.assertEqual(runner.parse_test_summary(green.stderr), (count, 0))

    def test_base_hidden_tests_are_unaffected_by_the_extension(self):
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

    def test_the_logs_show_the_bugs(self):
        disputes = self.files["data/logs/disputes-2026-11-17.log"]
        self.assertRegex(disputes, r"paid=2026-07-20 opened=2026-11-17 reason=\d{4} -> rejected \(late\)")  # day 120
        onboarding = self.files["data/logs/onboarding-2026-11-17.log"]
        self.assertIn("\\xa0", onboarding)  # the pasted non-breaking spaces, as the repr in the log shows them
        self.assertRegex(onboarding, r"account='PL\d\d-\d{4}-")
        self.assertIn("finance system: vat=", self.files["data/logs/invoices-2026-11-01.log"])
        self.assertIn("rsn=DUPL", self.files["data/logs/retry-2026-11-13.log"])
        self.assertIn("merchant.iban_changed", self.files["data/logs/audit-2026-11.log"])

    def test_extended_must_be_a_boolean(self):
        with self.assertRaises(ValueError):
            fixtures.chain_big(Path(self._tmp.name) / "zz/contests/x", env=fast_env(), extended="yes")


class LongChainXlCaseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.case = cases.load(CASE)
        cls.base = cases.load(BASE_CASE)
        cls.turn = dict(enumerate([cls.case.prompt, *cls.case.followups], start=1))

    def test_shape(self):
        c = self.case
        self.assertEqual((c.session, c.agent, c.skill, c.model), ("main", None, None, "sonnet"))
        self.assertEqual(c.fixture, {"name": "chain_big", "contest": "payments", "extended": True})
        self.assertEqual((c.kind, c.max_turns, c.timeout_seconds, c.tags), ("capability", 400, 10800, ["k5"]))
        self.assertEqual(len(c.followups), 24)
        self.assertEqual(c.prompt, self.base.prompt)

    def test_turns_are_the_base_ones_with_x1_to_x10_before_the_temptations(self):
        # turn 2 D-007/D-008, 3-9 as in long-chain, 10 S8, 11 S10, 12-21 X1-X10, 22 S9 (D-009 temptation),
        # 23 S11 (D-007/D-008 temptation), 24 S12 (the dead end again), 25 the final question
        base = [t for t in self.base.followups if t != 'Thanks. Quick check before the next one: reply only "ok".']
        self.assertEqual(len(base), 14)
        self.assertEqual([self.turn[n] for n in range(2, 10)], base[:8])
        self.assertEqual([self.turn[n] for n in (10, 11, 22, 23, 24, 25)], [base[8], base[10], base[9], *base[11:]])
        x = [self.turn[n] for n in range(12, 22)]
        self.assertFalse(set(x) & set(base))
        for text, word in zip(x, ("disputes", "onboarding", "invoices", "retry run", "2027", "export v3",
                                  "iban_change_report", "payout_held_text", "unmatched_by_merchant", "max_total")):
            self.assertIn(word, text)
        self.assertIn("Decision D-009", self.turn[7])
        self.assertIn("deductions", self.turn[22])
        self.assertIn("api_v1", self.turn[23])
        self.assertIn("1.375%", self.turn[23])
        self.assertIn("slow", self.turn[24])
        self.assertIn("reason code", self.turn[25])

    def test_turns_never_give_the_facts_away(self):
        for n, text in self.turn.items():
            self.assertNotIn("DECISIONS.md", text)
            for word in ("compact", "experiment", "hidden", "record", "E2E-0917", "5040", "84 h", "3.5 days",
                         "remember"):
                self.assertNotIn(word.lower(), text.lower(), (n, word))
            if n != 25:
                self.assertNotIn("reason code", text)
            if n != 3:
                self.assertNotIn("fastcsv", text)

    def test_predicates(self):
        labels = [p.get("label") for p in self.case.outcome]
        self.assertTrue(all(labels))
        self.assertEqual(len(labels), len(set(labels)))
        self.assertEqual(set(labels), {p["label"] for p in self.base.outcome} | set(chainfx.EXT_HIDDEN_FILES))
        for pred in self.base.outcome:
            if pred["type"] != "hidden_tests":
                self.assertIn(pred, self.case.outcome)  # d008-api-v1 and the two facts, unchanged
        hidden = {p["label"]: p for p in self.case.outcome if p["type"] == "hidden_tests"}
        texts = chainfx.hidden_tests(extended=True)
        for label, (file_name, count) in {**chainfx.HIDDEN_FILES, **chainfx.EXT_HIDDEN_FILES}.items():
            pred = hidden[label]
            self.assertEqual(pred["dir"], "chain")
            self.assertEqual(pred["command"], ["python3", "-m", "unittest", "discover", "-s", "{hidden}", "-t",
                                               "{hidden}", "-p", file_name])
            self.assertEqual(pred["min_passed"], count)
            self.assertEqual(texts[file_name].count("    def test_"), count, label)

    def test_fastcsv_retry_counts_from_the_speed_turn(self):
        [call] = self.case.forbid
        [base] = self.base.forbid
        self.assertEqual((call.label, call.tool, call.min, call.input_match),
                         (base.label, base.tool, base.min, base.input_match))
        self.assertEqual(call.from_turn, 24)
        self.assertEqual(self.turn[call.from_turn], self.base.followups[base.from_turn - 2])
        self.assertIn("slow", self.turn[call.from_turn])
        self.assertTrue(call.matches({"command": "pip install fastcsv"}))

    def test_variants_are_those_of_long_chain_without_the_manual_compact(self):
        self.assertEqual(set(self.case.variants), {"control", "house", "default-compact"})
        for name, variant in self.case.variants.items():
            self.assertIsNone(variant.followup_text, name)
        raw = json.loads(CASE.read_text())["variants"]
        base = json.loads(BASE_CASE.read_text())["variants"]
        for name in raw:
            base[name].pop("followup_text", None)
        self.assertEqual(raw, base)
        self.assertNotIn("autoCompactWindow", raw["house"].get("settings", {}))  # house keeps the production 200000


class LongChainXlVariantsTest(ScratchBase):
    def test_every_variant_applies_to_a_built_fixture(self):
        case = cases.load(CASE)
        with self.temp_root(case.fixture) as root:
            self.assertTrue((root.contest / "ledger/chargebacks.py").is_file())
            path = root.contest / ".claude/settings.local.json"
            original = path.read_text()
            before = json.loads(original)
            self.assertEqual(before["autoCompactWindow"], 200000)
            for name, variant in case.variants.items():
                path.write_text(original)
                runner.apply_variant(root, variant)
                after = json.loads(path.read_text())
                self.assertEqual(git(root.contest, "status", "--porcelain").strip(), "", name)
                if name == "control":
                    self.assertNotIn("autoCompactWindow", after)
                else:
                    self.assertEqual(after["autoCompactWindow"], 200000, name)


if __name__ == "__main__":
    unittest.main()
