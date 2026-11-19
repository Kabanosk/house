"""K5 `long-input`: the `spec_huge` rulebook (size, headings, the ten planted facts and their traps), the fixture
(document only as material, the `house sources add` layout, hidden tests red on the stub and green on the
reference), the case file and its two arms applied to a built fixture. No agent and no Docker run here: the hidden
tests run on the host against repo-authored code only (the stub and the reference solution)."""
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from houselib import HOUSE_ROOT, registry, sources
from houselib.evals import cases, fixtures, grade, runner, specfx
from tests.helpers import git, make_root
from tests.test_eval_sandbox import ScratchBase, fast_env

CASE = HOUSE_ROOT / "evals/cases/long-input.json"
TEST_ENV = {"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1", "LANG": "C.UTF-8"}
READING_START = "Long inputs (contest docs, logs, data):"
READING_FILES = ("AGENTS.md", ".claude/agents/coder.md", ".claude/agents/researcher.md", ".claude/agents/judge.md")


def fact(label: str) -> dict:
    return next(f for f in specfx.FACTS if f["label"] == label)


def predicate(f: dict) -> dict:
    """The case's outcome predicate of a fact."""
    return {"label": f["label"], "type": "final_answer", "key": f"Q{f['q']}", "match": specfx.answer_pattern(f)}


def line_of(doc: str, needle: str) -> int:
    """0-based index of the only line that contains `needle`."""
    hits = [i for i, line in enumerate(doc.splitlines()) if needle in line]
    if len(hits) != 1:
        raise AssertionError(f"{needle!r} is on {len(hits)} lines")
    return hits[0]


def section_of(doc: str, line_no: int) -> str:
    """The `###` (or `##`) heading the line belongs to."""
    lines = doc.splitlines()
    for i in range(line_no, -1, -1):
        if lines[i].startswith("##"):
            return lines[i]
    return ""


class SpecDocumentTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = specfx.document()

    def test_size_lines_and_determinism(self):
        self.assertTrue(480_000 <= len(self.doc) <= 520_000, len(self.doc))
        self.assertLess(max(len(line) for line in self.doc.splitlines()), sources.LONG_LINE)
        specfx.document.cache_clear()
        self.assertEqual(specfx.document(), self.doc)

    def test_realistic_headings(self):
        headings = re.findall(r"(?m)^#{1,3} .*$", self.doc)
        self.assertGreater(len(headings), 120)
        for heading in ("# Part I — Official Rules", "# Part II — Submission API Reference", "## 4. Submissions",
                        "### 4.3 Submission file format (final phase)", "### 18.2 GET /v2/submissions",
                        "## Appendix C — Errata"):
            self.assertIn(heading, headings)
        self.assertTrue(self.doc.startswith(f"# {specfx.CHALLENGE} — Official Rules and Submission API Reference\n"))

    def test_ten_facts_planted_once_computed_ones_nowhere(self):
        self.assertEqual([f["label"] for f in specfx.FACTS], [f"fact-{i:02d}" for i in range(1, 11)])
        self.assertEqual([f["q"] for f in specfx.FACTS], list(range(1, 11)))
        for f in specfx.FACTS:
            for pattern in f["planted"]:
                self.assertEqual(specfx.occurrences(pattern, self.doc), 1, (f["label"], pattern))
            for pattern in f["absent"]:
                self.assertEqual(specfx.occurrences(pattern, self.doc), 0, (f["label"], pattern))
        kinds = [f["kind"] for f in specfx.FACTS]
        self.assertEqual(kinds.count("erratum"), 1)
        self.assertEqual(kinds.count("near-duplicate"), 1)
        self.assertGreaterEqual(kinds.count("combined"), 2)

    def test_the_erratum_near_the_end_overrides_the_early_rule(self):
        lines = self.doc.splitlines()
        original = line_of(self.doc, "Final submission deadline: 2027-03-14 23:59 UTC")
        erratum = line_of(self.doc, "2027-03-21")
        self.assertEqual(section_of(self.doc, original), "### 2.3 Final phase")
        self.assertLess(original / len(lines), 0.1)
        self.assertGreater(erratum / len(lines), 0.95)
        self.assertEqual(section_of(self.doc, erratum), "## Appendix C — Errata")
        self.assertTrue(lines[erratum].startswith("| E-03 | 2.3 | The final submission deadline is 2027-03-21"))
        self.assertEqual(specfx.occurrences("2027-03-14", self.doc), 1)
        self.assertIn("Where an erratum and the body of the rules differ, the erratum\napplies.", self.doc)

    def test_the_validator_rules_are_amended_by_an_erratum_too(self):
        rule = line_of(self.doc, "4.3.7 SUB-04 (forecast)")
        self.assertIn("one to three digits", "\n".join(self.doc.splitlines()[rule:rule + 3]))
        amended = line_of(self.doc, "| E-02 | 4.3.7 |")
        self.assertIn("one or two digits. A value with three decimal places breaks SUB-04.",
                      self.doc.splitlines()[amended])
        self.assertIn("2027-04-05T00:00:00Z to 2027-04-11T23:45:00Z", self.doc)
        self.assertEqual(section_of(self.doc, line_of(self.doc, "2027-04-11T23:45:00Z")), "### 5.4 Forecast windows")

    def test_near_duplicate_quota_lines_in_different_sections(self):
        warmup = line_of(self.doc, "at most 72 submission files per calendar week")
        final = line_of(self.doc, "at most 27 submission files per calendar week")
        self.assertEqual(section_of(self.doc, warmup), "### 4.6 Submission quotas in the warm-up phase")
        self.assertEqual(section_of(self.doc, final), "### 9.2 Upload quotas in the final phase")
        lines = self.doc.splitlines()
        strip = lambda text: re.sub(r"^\d+\.\d+\.\d+ ", "", text)
        self.assertEqual(strip(lines[warmup]).replace("warm-up", "X").replace("72", "N"),
                         strip(lines[final]).replace("final", "X").replace("27", "N"))

    def test_combined_facts_need_two_sections(self):
        doc = self.doc
        self.assertEqual(section_of(doc, line_of(doc, "| Solar | €18 500 |")), "### 7.1 Track prizes")
        self.assertEqual(section_of(doc, line_of(doc, "Open Methods Award of €4 250")), "### 7.4 Special awards")
        self.assertEqual(section_of(doc, line_of(doc, "1 240 sites")), "### 5.1 Sites")
        self.assertEqual(section_of(doc, line_of(doc, "computed on 35% of the test sites")),
                         "### 8.2 Public and private leaderboards")
        code = doc.splitlines()[line_of(doc, "| E-2207 |")]
        self.assertIn("breaks rule SUB-05", code)
        self.assertNotIn("duplicate", code.lower())
        self.assertEqual(section_of(doc, line_of(doc, "| E-2207 |")), "### 16.3 Upload error codes")
        self.assertIn("4.3.8 SUB-05 (duplicates). No two data rows have the same `site_id` and the same `timestamp`",
                      doc)
        self.assertIn("(a repeated upload)", doc.splitlines()[line_of(doc, "| E-2311 |")])  # the "duplicate" decoy

    def test_lookup_facts_sit_in_their_sections(self):
        doc = self.doc
        for needle, heading in (("at most 340 GPU-hours", "### 6.2 Compute budgets"),
                                ("an integer from 1 to 175", "### 18.2 GET /v2/submissions"),
                                (f"`{specfx.TOKEN_HEADER}` header", "### 14.1 API tokens"),
                                ("at most 37 MiB", "### 18.1 POST /v2/submissions"),
                                ("appeals-q7k@halcyon-grid.example", "### 12.3 How to appeal")):
            self.assertEqual(section_of(doc, line_of(doc, needle)), heading, needle)

    def test_answer_patterns(self):
        good = {
            "fact-01": ["Q1: 2027-03-21", "**Q1:** 2027-03-21", "- Q1: `2027-03-21`", "Q1: 2027-03-21 23:59 UTC"],
            "fact-02": ["Q2: 27", "**Q2**: 27", "Q2: 27 submission files per calendar week"],
            "fact-03": ["Q3: 22750", "Q3: 22 750", "Q3: €22,750", "Q3: 22750 EUR", "Q3: 22,750.00 euros",
                        "Q3: 22\u00a0750", "Q3: 22\u202f750 €"],
            "fact-04": ["Q4: E-2207", "Q4: `E-2207`"],
            "fact-05": ["Q5: 340", "Q5: 340 GPU-hours"],
            "fact-06": ["Q6: 175"],
            "fact-07": [f"Q7: {specfx.TOKEN_HEADER}", f"Q7: `{specfx.TOKEN_HEADER}`",
                        f"q7: {specfx.TOKEN_HEADER.lower()}"],
            "fact-08": ["Q8: 434", "Q8: 434 sites"],
            "fact-09": ["Q9: 37", "Q9: 37 MiB"],
            "fact-10": ["Q10: appeals-q7k@halcyon-grid.example", "Q10: <appeals-q7k@halcyon-grid.example>"],
        }
        for label, answers in good.items():
            pred = predicate(fact(label))
            for answer in answers:
                self.assertTrue(grade.final_answer_holds(pred, f"Done.\n\n{answer}\nQ99: x\n"), (label, answer))
        bad = {
            "fact-01": ["Q1: 2027-03-14", "Q1: 2027-03-21 or 2027-03-14", "Q10: 2027-03-21"],
            "fact-02": ["Q2: 72", "Q2: 27 or 72", "Q2: 270"],
            "fact-03": ["Q3: 18500", "Q3: 18 500", "Q3: 4250", "Q3: €22.750", "Q3: 22.750", "Q3: 22.750,00"],
            "fact-04": ["Q4: E-2311", "Q4: SUB-05", "Q4: E-22070"],
            "fact-05": ["Q5: 48", "Q5: 3400"],
            "fact-06": ["Q6: 500", "Q6: 1750", "Q6: 175 (default 50) or 200"],
            "fact-07": ["Q7: Authorization", "Q7: X-HG-Request-Id", "Q7: X-HG-Team-Token",
                        f"Q7: {specfx.TOKEN_HEADER}s"],
            "fact-08": ["Q8: 1240", "Q8: 434.5", "Q8: 806"],
            "fact-09": ["Q9: 8", "Q9: 12", "Q9: 37 GiB", "Q9: 374"],
            "fact-10": ["Q10: rules-desk@halcyon-grid.example", "Q10: appeals@halcyon-grid.example"],
        }
        for label, answers in bad.items():
            pred = predicate(fact(label))
            for answer in answers:
                self.assertFalse(grade.final_answer_holds(pred, f"Done.\n\n{answer}\n"), (label, answer))
        q2 = predicate(fact("fact-02"))
        self.assertFalse(grade.final_answer_holds(q2, "The answer to Q2 is 27."))
        self.assertFalse(grade.final_answer_holds(q2, "Q2: 27\n\nOn second thought:\nQ2: 72"))  # last line wins
        self.assertTrue(grade.final_answer_holds(q2, "Q2: 72\n\nNo, 72 is the warm-up quota:\nQ2: 27"))
        self.assertFalse(grade.final_answer_holds(q2, "Q2:\n27"))  # the value must be on the key's line

    def test_header_is_not_guessable_from_the_names(self):
        header = specfx.TOKEN_HEADER
        self.assertTrue(header.startswith("X-"))
        for word in ("HG", "Halcyon", "Grid", "Team", "Token", "Auth", "Api", "Key"):
            self.assertNotIn(word.lower(), header.lower(), word)

    def test_q4_asks_for_duplicates_as_the_only_violation(self):
        self.assertIn("the only rule its file breaks", fact("fact-04")["question"])

    def test_reference_follows_the_rules_and_the_errata(self):
        namespace: dict = {}
        exec(compile(specfx.REFERENCE_VALIDATE, "validate.py", "exec"), namespace)  # repo-authored code
        validate = namespace["validate"]
        self.assertEqual(validate(specfx.SAMPLE), [])
        row = "site_id,timestamp,forecast_kw\nHG-0001,2027-04-05T00:00:00Z,{}\n"
        self.assertEqual(validate(row.format("1.25")), [])
        self.assertEqual(validate(row.format("1.125")), ["SUB-04"])


def build(tmp: Path, reference: bool = False) -> tuple[Path, dict]:
    """Build `spec_huge` in a throwaway house root below `tmp`; returns the contest and the info dict."""
    root = make_root(tmp)
    shutil.copytree(HOUSE_ROOT / "houselib", root / "houselib", ignore=shutil.ignore_patterns("__pycache__"))
    contest = root / "contests/halcyon"
    info = fixtures.spec_huge(contest, env=fast_env(), reference=reference)
    return contest, info


def run_hidden(contest: Path) -> subprocess.CompletedProcess:
    """The hidden tests, run the way the case's predicate runs them (contest as cwd, unittest discover)."""
    hidden = contest.parents[2] / fixtures.HIDDEN_DIR / specfx.HIDDEN
    return subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", str(hidden), "-t", str(hidden),
                           "-p", specfx.HIDDEN_FILE], cwd=contest, capture_output=True, text=True, env=TEST_ENV,
                          timeout=300, check=False)


class SpecFixtureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        base = Path(cls._tmp.name)
        (base / "a").mkdir()
        (base / "b").mkdir()
        cls.contest, cls.info = build(base / "a")
        cls.ref_contest, _ = build(base / "b", reference=True)
        cls.material = cls.contest.parents[2] / fixtures.MATERIAL_DIR

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_info_and_clean_registered_contest(self):
        self.assertEqual((self.info["fixture"], self.info["task_ids"], self.info["expect"], self.info["hidden"],
                          self.info["material"]), ("spec_huge", [], "", "spec", "rules.md"))
        self.assertEqual(git(self.contest, "status", "--porcelain").strip(), "")
        self.assertEqual(git(self.contest, "branch", "--show-current").strip(), "main")
        self.assertEqual(registry.load(self.contest.parents[1] / "contests.yaml"), {"halcyon": ""})
        tracked = set(git(self.contest, "ls-files").split())
        self.assertTrue(set(specfx.contest_files()) <= tracked)

    def test_the_document_is_material_only(self):
        self.assertEqual((self.material / specfx.MATERIAL).read_text(encoding="utf-8"), specfx.document())
        self.assertFalse((self.contest / ".house/sources").exists())
        revs = git(self.contest, "rev-list", "--all").split()
        r = subprocess.run(["git", "grep", "-l", "-F", specfx.TOKEN_HEADER, *revs], cwd=self.contest,
                           capture_output=True, text=True, check=False)
        self.assertEqual((r.returncode, r.stdout), (1, ""))  # in no commit of the contest
        for rel in specfx.contest_files():
            text = (self.contest / rel).read_text()
            for f in specfx.FACTS:
                for pattern in f["planted"]:
                    self.assertEqual(specfx.occurrences(pattern, text), 0, (rel, f["label"]))

    def test_the_sources_layout_is_the_one_house_sources_add_writes(self):
        index = json.loads((self.material / specfx.SOURCES_INDEX_MATERIAL).read_text())
        [entry] = index["files"]
        data = specfx.document().encode("utf-8")
        self.assertEqual((entry["name"], entry["origin"], entry["bytes"]), ("rules.md", "rules.md", len(data)))
        self.assertEqual(entry["sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual((self.material / specfx.SOURCES_GITIGNORE_MATERIAL).read_text(), ".add-*\n")

    def test_hidden_tests_fail_on_the_stub_and_pass_on_the_reference(self):
        red = run_hidden(self.contest)
        self.assertNotEqual(red.returncode, 0, red.stderr[-2000:])
        self.assertEqual(runner.parse_test_summary(red.stderr)[0], 0)
        green = run_hidden(self.ref_contest)
        self.assertEqual(green.returncode, 0, green.stderr[-3000:])
        self.assertEqual(runner.parse_test_summary(green.stderr), (specfx.HIDDEN_TEST_COUNT, 0))

    def test_the_smoke_test_passes_on_the_reference_only(self):
        argv = [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "."]
        for contest, code in ((self.contest, 1), (self.ref_contest, 0)):
            r = subprocess.run(argv, cwd=contest, capture_output=True, text=True, env=TEST_ENV, timeout=60,
                               check=False)
            self.assertEqual(r.returncode, code, r.stderr[-2000:])

    def test_hidden_tests_are_outside_the_contest(self):
        self.assertTrue((self.contest.parents[2] / fixtures.HIDDEN_DIR / "spec" / specfx.HIDDEN_FILE).is_file())
        self.assertEqual([p.relative_to(self.contest).as_posix() for p in self.contest.rglob(specfx.HIDDEN_FILE)],
                         ["tests/test_validate.py"])  # only the visible smoke test
        self.assertNotIn("SUB-04", (self.contest / "tests/test_validate.py").read_text())

    def test_reference_is_refused_from_a_case_file(self):
        data = json.loads(CASE.read_text())
        data["fixture"]["reference"] = True
        path = Path(self._tmp.name) / "long-input.json"
        path.write_text(json.dumps(data))
        with self.assertRaises(ValueError) as ctx:
            cases.load(path)
        self.assertIn("fixture.reference", str(ctx.exception))

    def test_unknown_parameter_is_refused(self):
        with self.assertRaises(ValueError):
            fixtures.spec_huge(Path(self._tmp.name) / "zz/contests/x", env=fast_env(), bogus=1)


class LongInputCaseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.case = cases.load(CASE)

    def test_shape(self):
        c = self.case
        self.assertEqual((c.session, c.agent, c.skill, c.model), ("main", None, None, "sonnet"))
        self.assertEqual((c.fixture, c.kind, c.max_turns, c.timeout_seconds, c.tags),
                         ({"name": "spec_huge", "contest": "halcyon"}, "capability", 120, 3600, ["k5"]))
        self.assertEqual((c.followups, c.expect, c.forbid), ([], [], []))
        self.assertEqual(set(c.variants), {"pasted", "sources"})

    def test_predicates(self):
        outcome = self.case.outcome
        self.assertEqual([p["label"] for p in outcome], ["validator-tests"] + [f["label"] for f in specfx.FACTS])
        hidden = outcome[0]
        self.assertEqual(hidden, {"label": "validator-tests", "type": "hidden_tests", "dir": "spec",
                                  "command": ["python3", "-m", "unittest", "discover", "-s", "{hidden}", "-t",
                                              "{hidden}", "-p", "test_validate.py"],
                                  "min_passed": specfx.HIDDEN_TEST_COUNT})
        self.assertEqual(specfx.HIDDEN_TEST_COUNT, 14)
        for pred, f in zip(outcome[1:], specfx.FACTS):
            self.assertEqual(pred, predicate(f))

    def test_both_arms_ask_the_same_apart_from_where_the_document_is(self):
        pasted, src = self.case.variants["pasted"].prompt, self.case.variants["sources"].prompt
        self.assertEqual(pasted, specfx.prompt("pasted"))
        self.assertEqual(src, specfx.prompt("sources"))
        self.assertEqual(self.case.prompt, src)
        body = specfx.prompt_body()
        self.assertEqual(pasted, specfx.PASTED_LEAD + "\n\n" + body)
        self.assertEqual(src, specfx.SOURCES_LEAD + "\n\n" + body)
        self.assertEqual(pasted.count("{material:rules.md}"), 1)
        self.assertNotIn("{material:", src)
        self.assertIn("`.house/sources/rules.md`", src)
        self.assertNotIn(".house/sources", pasted)

    def test_the_task_states_its_contract_and_gives_nothing_away(self):
        body = specfx.prompt_body()
        for f in specfx.FACTS:
            self.assertIn(f"Q{f['q']}. {f['question']}", body)
            self.assertNotIn(f["answer"].lower(), body.lower(), f["label"])
        for word in ("errat", "appendix", "hidden", "experiment", "SUB-0", "2027", "decimal", "window", "compact"):
            self.assertNotIn(word.lower(), body.lower(), word)
        for needle in ("`validate(text: str) -> list[str]`", "sorted list of the distinct codes", "`[]` for a valid",
                       "Standard library only", "exactly in the form `Q1: <answer>`", "nothing else on those lines",
                       "no tasks, branches or sub-agents"):
            self.assertIn(needle, body)

    def test_pasted_edits_name_every_installed_reading_block(self):
        edits = self.case.variants["pasted"].edit
        self.assertEqual([e.path for e in edits], list(READING_FILES))
        self.assertEqual({(e.pattern, e.repl) for e in edits},
                         {(r"\nLong inputs \(contest docs, logs, data\):\n(?:- [^\n]*\n){5}(?=\n|\Z)", "")})
        self.assertEqual([(c.material, c.to) for c in self.case.variants["sources"].copy],
                         [("rules.md", ".house/sources/rules.md"), ("sources-index.json", ".house/sources/index.json"),
                          ("sources-gitignore", ".house/sources/.gitignore")])


class LongInputVariantsTest(ScratchBase):
    def setUp(self):
        super().setUp()
        self.case = cases.load(CASE)

    def test_pasted_puts_the_document_in_the_prompt_and_drops_the_reading_rules(self):
        variant = self.case.variants["pasted"]
        with self.temp_root(self.case.fixture) as root:
            before = {rel: (root.contest / rel).read_text() for rel in READING_FILES}
            head = git(root.contest, "rev-parse", "HEAD").strip()
            runner.apply_variant(root, variant)
            for rel in READING_FILES:
                after = (root.contest / rel).read_text()
                self.assertIn(READING_START, before[rel], rel)
                self.assertNotIn("Long inputs", after, rel)
                self.assertNotIn("allow_large", after, rel)
                self.assertEqual(after, re.sub(variant.edit[0].pattern, "", before[rel]), rel)
                self.assertLess(len(before[rel]) - len(after), 1000, rel)
            r = subprocess.run(["grep", "-rlF", READING_START, "."], cwd=root.contest, capture_output=True, text=True,
                               check=False)
            self.assertEqual(r.stdout, "")  # nowhere else in the installed contest
            self.assertFalse((root.contest / ".house/sources").exists())
            self.assertEqual(git(root.contest, "status", "--porcelain").strip(), "")
            self.assertNotEqual(git(root.contest, "rev-parse", "HEAD").strip(), head)
            self.assertEqual(git(root.contest, "log", "-1", "--format=%s").strip(), "chore: setup")
            text = runner.variant_prompt(root, variant, self.case.prompt)
            doc = specfx.document()
            self.assertEqual(text, specfx.PASTED_LEAD.replace("{material:rules.md}", doc) + "\n\n"
                             + specfx.prompt_body())
            self.assertLess(len(text), runner.MAX_PROMPT_CHARS)

    def test_a_changed_reading_block_makes_the_pasted_edit_fail(self):
        variant = self.case.variants["pasted"]
        with self.temp_root(self.case.fixture) as root:
            path = root.contest / "AGENTS.md"
            text = path.read_text()
            start = text.index(READING_START)
            end = start
            for _ in range(6):
                end = text.index("\n", end) + 1
            path.write_text(text[:end] + "- A sixth rule.\n" + text[end:])
            with self.assertRaises(Exception) as ctx:
                runner.apply_variant(root, variant)
            self.assertIn("matched nothing", str(ctx.exception))

    def test_sources_copies_the_document_with_its_index(self):
        variant = self.case.variants["sources"]
        with self.temp_root(self.case.fixture) as root:
            self.assertFalse((root.contest / ".house/sources").exists())
            agents_md = (root.contest / "AGENTS.md").read_text()
            runner.apply_variant(root, variant)
            stored = root.contest / ".house/sources"
            self.assertEqual((stored / "rules.md").read_text(encoding="utf-8"), specfx.document())
            listing = sources.listing(root.contest)
            self.assertIn("rules.md", listing)
            for word in ("warning: content differs", "not in index", "missing"):
                self.assertNotIn(word, listing)
            self.assertIn("## 4. Submissions", listing)
            tracked = git(root.contest, "ls-files", ".house/sources").split()
            self.assertEqual(sorted(tracked), [".house/sources/.gitignore", ".house/sources/index.json",
                                               ".house/sources/rules.md"])
            self.assertEqual(git(root.contest, "status", "--porcelain").strip(), "")
            self.assertEqual((root.contest / "AGENTS.md").read_text(), agents_md)  # the reading rules stay
            self.assertIn(READING_START, agents_md)
            self.assertEqual(runner.variant_prompt(root, variant, self.case.prompt), self.case.prompt)

    def test_the_sandbox_refuses_a_reference_build(self):
        with self.assertRaises(ValueError) as ctx:
            with self.temp_root({"name": "spec_huge", "contest": "halcyon", "reference": True}):
                pass
        self.assertIn("test-only", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
