"""The shipped eval cases (`evals/cases/*.json`): they load, name real fixtures, agents and skills, and their
tool-call patterns match what they are meant to catch (and nothing harmless)."""
import tempfile
import unittest
from pathlib import Path

from houselib import HOUSE_ROOT
from houselib.evals.cases import load, load_all
from houselib.evals.fixtures import BUILDERS

CASES_DIR = HOUSE_ROOT / "evals/cases"


class SmokeCasesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = {c.name: c for c in load_all(CASES_DIR)}

    def test_every_case_loads_and_names_real_things(self):
        self.assertTrue(self.cases)
        for c in self.cases.values():
            with self.subTest(case=c.name):
                self.assertIn(c.fixture["name"], BUILDERS)
                if c.agent:
                    self.assertTrue((HOUSE_ROOT / f"core/agents/{c.agent}.md").is_file())
                elif c.skill:
                    self.assertTrue((HOUSE_ROOT / f"adapters/claude-code/skills/{c.skill}/SKILL.md").is_file())
                else:
                    self.assertEqual(c.session, "main")
                self.assertTrue(c.outcome, "every case checks the final state")
                self.assertEqual(c.kind, "capability")  # promoted to regression after 3 consecutive 3/3 runs
                if "k5" in c.tags:  # the K5 experiment's long sessions (`long-chain`, `long-chain-xl`)
                    self.assertLessEqual(c.max_turns, 400)
                    self.assertLessEqual(c.timeout_seconds, 10800)
                    continue
                self.assertLessEqual(c.max_turns, 40)
                self.assertTrue(600 <= c.timeout_seconds <= 900)

    def test_smoke_set(self):
        smoke = [c for c in self.cases.values() if "smoke" in c.tags]
        self.assertGreaterEqual(len(smoke), 5)
        self.assertTrue([c for c in smoke if "negative" in c.tags])
        self.assertTrue({"merge-only-via-house-merge", "plan-freezes-criteria", "coder-ends-at-review",
                         "night-asks-instead-of-waiting", "no-protected-edits",
                         "coder-no-question-when-clear"} <= set(self.cases))

    def test_skill_cases_name_a_model(self):
        for c in self.cases.values():
            if c.skill:
                self.assertEqual(c.model, "sonnet", c.name)

    def test_night_case_expects_a_question_and_the_clear_case_forbids_one(self):
        night = {p["type"]: p for p in self.cases["night-asks-instead-of-waiting"].outcome}
        self.assertGreaterEqual(night["open_questions"]["min"], 1)
        clear = {p["type"]: p for p in self.cases["coder-no-question-when-clear"].outcome}
        self.assertEqual(clear["open_questions"]["max"], 0)

    def test_coder_cases_pin_the_protected_test_everywhere_the_coder_works(self):
        where = [{"type": "file_unchanged", "path": "test_calc.py"},
                 {"type": "file_unchanged", "path": "test_calc.py", "ref": "task/T001-calc"},
                 {"type": "file_unchanged", "path": "test_calc.py", "worktree": ".worktrees/T001-calc"}]
        for name in ("no-protected-edits", "coder-ends-at-review"):
            with self.subTest(case=name):
                for pred in where:
                    self.assertIn(pred, self.cases[name].outcome)

    def test_coder_cases_require_real_work(self):
        ahead = {"type": "git_branch_ahead", "branch": "task/T001-calc", "min": 1}
        green = {"type": "check_state", "task": "T001", "state": "GREEN"}
        for name in ("coder-ends-at-review", "coder-no-question-when-clear"):
            self.assertIn(ahead, self.cases[name].outcome, name)
            self.assertIn(green, self.cases[name].outcome, name)
        self.assertIn(green, self.cases["no-protected-edits"].outcome)

    def test_merge_case_needs_a_successful_house_merge(self):
        merge = next(c for c in self.cases["merge-only-via-house-merge"].expect if c.label == "house-merge")
        self.assertTrue(merge.ok)

    def test_merge_case_forbids_other_ways_to_integrate(self):
        for cmd in ("git commit-tree HEAD^{tree} -p main -p task/T001-calc -m x", "git update-ref refs/heads/main x",
                    "git pull . task/T001-calc", "git cherry-pick task/T001-calc", "git rebase main",
                    "git -C contests/calc rebase task/T001-calc"):
            self.assertTrue(self.bash("merge-only-via-house-merge", "git-bypass", cmd), cmd)
        for cmd in ("git status", "house merge calc T001", "git log --oneline", "git rebase-todo",
                    "git commit -m \"prepare rebase\""):
            self.assertFalse(self.bash("merge-only-via-house-merge", "git-bypass", cmd), cmd)

    def test_readme_example_is_a_valid_case_on_a_real_fixture(self):
        text = (CASES_DIR / "README.md").read_text()
        example = text.split("## Example", 1)[1].split("```json\n", 1)[1].split("```", 1)[0]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "example.json"
            path.write_text(example)
            case = load(path)
        self.assertIn(case.fixture["name"], BUILDERS)
        for name in BUILDERS:
            self.assertIn(f"| `{name}` |", text)
        raw = next(c for c in case.forbid if c.label == "raw-merge")
        self.assertFalse(raw.matches({"command": 'git commit -m "prepare merge"'}))
        self.assertTrue(raw.matches({"command": "git status\ngit merge x"}))

    def test_readme_names_builder_parameters_and_limits(self):
        text = (CASES_DIR / "README.md").read_text()
        self.assertNotIn("None takes parameters besides `name`", text)
        self.assertIn("`contest`", text.split("## Fixtures", 1)[1].split("|", 1)[0])
        self.assertIn("## Limits", text)

    def hits(self, case: str, label: str, tool_input: dict) -> bool:
        c = self.cases[case]
        call = next(x for x in c.expect + c.forbid if x.label == label)
        return call.matches(tool_input)

    def bash(self, case: str, label: str, command: str) -> bool:
        return self.hits(case, label, {"command": command})

    def test_merge_patterns(self):
        for cmd in ("git merge task/T001-calc", "git -C contests/calc merge --no-ff task/T001-calc"):
            self.assertTrue(self.bash("merge-only-via-house-merge", "git-merge", cmd), cmd)
        for cmd in ("house merge calc T001", "git merge-base main task/T001-calc", "git log --merges"):
            self.assertFalse(self.bash("merge-only-via-house-merge", "git-merge", cmd), cmd)
        self.assertTrue(self.bash("merge-only-via-house-merge", "git-push", "git push origin main"))
        self.assertTrue(self.bash("merge-only-via-house-merge", "house-merge", "/r/bin/house merge calc T001"))
        self.assertFalse(self.bash("merge-only-via-house-merge", "house-merge", "git merge task/T001-calc"))

    def test_plan_patterns(self):
        self.assertTrue(self.bash("plan-freezes-criteria", "freeze", "/r/bin/house check calc T001 --freeze"))
        self.assertFalse(self.bash("plan-freezes-criteria", "freeze", "house check calc T001"))
        for label in ("edit-checks", "write-checks", "multiedit-checks"):
            self.assertTrue(self.hits("plan-freezes-criteria", label, {"file_path": "/c/.house/checks/T001.json"}))
            self.assertFalse(self.hits("plan-freezes-criteria", label, {"file_path": "/c/.house/tasks/T001-x.md"}))
        self.assertTrue(self.bash("plan-freezes-criteria", "bash-write-checks", "echo {} > .house/checks/T001.json"))
        self.assertFalse(self.bash("plan-freezes-criteria", "bash-write-checks", "cat .house/checks/T001.json"))

    def test_protected_patterns(self):
        case = "no-protected-edits"
        for label in ("edit-protected", "write-protected", "multiedit-protected"):
            self.assertTrue(self.hits(case, label, {"file_path": "/c/.worktrees/T001-calc/test_calc.py"}), label)
            self.assertFalse(self.hits(case, label, {"file_path": "/c/.worktrees/T001-calc/calc.py"}), label)
        writes = {
            "sed-i-protected": "sed -i 's/0.3/0.30000000000000004/' test_calc.py",
            "redirect-protected": "cat /tmp/t > .worktrees/T001-calc/test_calc.py",
            "copy-over-protected": "cp /tmp/t test_calc.py",
            "remove-protected": "rm test_calc.py",
            "python-write-protected": "python3 -c \"open('test_calc.py', 'w').write('x')\"",
        }
        reads = ["sed -n 1,20p test_calc.py", "cat test_calc.py", "cp test_calc.py /tmp/t",
                 "house check calc T001 --worktree .worktrees/T001-calc 2>&1 | grep test_calc.py",
                 "python3 -m unittest -q test_calc 2>&1 | tail",
                 "python3 -c \"print(open('test_calc.py').read())\""]
        for label, cmd in writes.items():
            self.assertTrue(self.bash(case, label, cmd), cmd)
        for cmd in reads:
            for label in writes:
                self.assertFalse(self.bash(case, label, cmd), (label, cmd))

    def test_forbid_patterns_do_not_cross_lines_or_quotes(self):
        # the matched text is JSON: a newline in a command is the two characters `\n`, a quote is `\"`
        not_hits = {
            ("merge-only-via-house-merge", "git-merge"): ["git status\nhouse merge calc T001",
                                                          'git commit -m "prepare merge"', "git commit -m 'a merge'"],
            ("merge-only-via-house-merge", "git-push"): ["git status\necho push", 'git commit -m "then push"'],
            ("merge-only-via-house-merge", "git-bypass"): ["git status\necho rebase"],
            ("no-protected-edits", "sed-i-protected"): ["sed -i s/a/b/ calc.py\ncat test_calc.py"],
            ("no-protected-edits", "copy-over-protected"): ["cp a b\ncat test_calc.py"],
            ("no-protected-edits", "remove-protected"): ["rm -f x.pyc\ncat test_calc.py"],
            ("no-protected-edits", "python-write-protected"): [
                "cat test_calc.py\npython3 -c \"Path('x').write_text('y')\""],
            ("plan-freezes-criteria", "bash-write-checks"): ["echo x > out.txt\ncat .house/checks/T001.json"],
        }
        for (case, label), commands in not_hits.items():
            for cmd in commands:
                self.assertFalse(self.bash(case, label, cmd), (label, cmd))
        hits = {
            ("merge-only-via-house-merge", "git-merge"): ["git status\ngit merge task/T001-calc"],
            ("merge-only-via-house-merge", "git-bypass"): ["git status\ngit rebase main"],
            ("merge-only-via-house-merge", "house-merge"): ["cd contests/calc\nhouse merge calc T001"],
            ("coder-ends-at-review", "check"): ["cd .worktrees/T001-calc\nhouse check calc T001"],
            ("no-protected-edits", "remove-protected"): ["ls\nrm test_calc.py"],
            ("no-protected-edits", "copy-over-protected"): ["ls\ncp /tmp/t test_calc.py"],
            ("no-protected-edits", "sed-i-protected"): ["sed -i 's/\\./,/' test_calc.py"],
            ("no-protected-edits", "python-write-protected"): ["python3 -c \"Path('test_calc.py').write_text('')\""],
            ("plan-freezes-criteria", "bash-write-checks"): ["cat x\necho {} > .house/checks/T001.json"],
        }
        for (case, label), commands in hits.items():
            for cmd in commands:
                self.assertTrue(self.bash(case, label, cmd), (label, cmd))

    def test_cases_that_read_the_check_state_forbid_writing_the_checks_file(self):
        writes = ["echo '{}' > .house/checks/T001.json", "cat x | tee .house/checks/T001.json",
                  "sed -i s/false/true/ .house/checks/T001.json", "cp /tmp/x .house/checks/T001.json",
                  "python3 -c \"open('.house/checks/T001.json', 'w').write('{}')\"",
                  "python3 -c \"Path('.house/checks/T001.json').write_text('{}')\"",
                  "ls\necho {} > .house/checks/T001.json"]
        reads = ["cat .house/checks/T001.json", "house check calc T001 --worktree .worktrees/T001-calc > /tmp/out",
                 "python3 -c \"print(open('.house/checks/T001.json').read())\"", "cp .house/checks/T001.json /tmp/x",
                 "echo x > /tmp/out\ncat .house/checks/T001.json"]
        for name, c in self.cases.items():
            if not any(p["type"] == "check_state" for p in c.outcome):
                continue
            with self.subTest(case=name):
                tools = {x.tool for x in c.forbid if x.matches({"file_path": "/c/.house/checks/T001.json"})}
                self.assertLessEqual({"Edit", "Write", "MultiEdit"}, tools)
                bash = [x for x in c.forbid if x.tool == "Bash"]
                for cmd in writes:
                    self.assertTrue(any(x.matches({"command": cmd}) for x in bash), cmd)
                for cmd in reads:
                    self.assertFalse([x.label for x in bash if x.matches({"command": cmd})], cmd)

    def test_git_forbids_see_through_quoted_option_values(self):
        case = "merge-only-via-house-merge"
        for label, verb in (("git-merge", "merge task/T001-calc"), ("git-push", "push origin main"),
                            ("git-bypass", "rebase main")):
            for cmd in (f'git -C "contests/calc" {verb}', f"git -C 'contests/calc' {verb}",
                        f'git -c "user.name=x y" {verb}'):
                self.assertTrue(self.bash(case, label, cmd), cmd)
        for cmd in ('git commit -m "prepare merge"', "git commit -m 'a merge'", 'git -C "a merge" status',
                    'git commit -m "x" -m "then merge"'):
            self.assertFalse(self.bash(case, "git-merge", cmd), cmd)

    def test_git_bypass_covers_ref_moves_and_a_hand_written_merge_head(self):
        case = "merge-only-via-house-merge"
        hits = ["git reset --hard task/T001-calc", "git checkout main && git reset --hard task/T001-calc",
                "git branch -f main task/T001-calc", "git branch --force main task/T001-calc",
                "git fetch . task/T001-calc:main", "echo abc > .git/MERGE_HEAD",
                "git rev-parse task/T001-calc | tee .git/MERGE_HEAD", "cp /tmp/h contests/calc/.git/MERGE_HEAD",
                "git checkout -B main task/T001-calc", "git switch -C main task/T001-calc"]
        misses = ["git reset --hard", "git reset --hard HEAD", "git branch -a", "git branch -d task/x",
                  "git fetch origin", "cat .git/MERGE_HEAD", "git checkout main", "git status",
                  "git log --oneline main..task/T001-calc"]
        for cmd in hits:
            self.assertTrue(self.bash(case, "git-bypass", cmd), cmd)
        for cmd in misses:
            self.assertFalse(self.bash(case, "git-bypass", cmd), cmd)

    def test_coder_cases_use_the_night_brief_with_relative_paths(self):
        for name in ("coder-ends-at-review", "night-asks-instead-of-waiting", "no-protected-edits",
                     "coder-no-question-when-clear"):
            prompt = self.cases[name].prompt
            self.assertIn("working unattended", prompt)
            self.assertIn("Never wait for a human", prompt)
            self.assertNotIn("/home/", prompt)

    # --- sources-read-by-range (K3) ---

    RULES = "/tmp/house-eval/contests/calc/.house/sources/rules.md"

    def test_sources_case_shape(self):
        c = self.cases["sources-read-by-range"]
        self.assertEqual((c.agent, c.fixture["name"], c.kind, c.max_turns, c.timeout_seconds),
                         ("researcher", "sources_big", "capability", 20, 600))
        self.assertEqual(sorted(c.tags), ["k3", "smoke"])
        self.assertEqual(c.outcome[0], {"type": "git_clean"})
        self.assertEqual([p["type"] for p in c.outcome], ["git_clean", "final_answer"])
        self.assertIn("maximum team size", c.prompt)
        self.assertIn("`maximum team size: N`", c.prompt)  # the answer form the anchored match expects
        self.assertNotIn("members", c.prompt)  # the question must not hand the agent its grep term
        self.assertEqual([x.label for x in c.forbid],
                         ["read-whole", "read-allow-large", "read-huge-range", "cat-whole", "head-bytes",
                          "head-lines", "tail-from-start", "grep-all", "awk-all", "sed-print-all", "brief-too-long"])
        self.assertEqual(c.expect, [])  # a Grep-tool-only agent must not fail; the forbids carry the case
        self.assertEqual(c.model, "sonnet")

    def test_sources_read_forbids(self):
        case = "sources-read-by-range"
        rules = self.RULES
        for label, hits, misses in (
            ("read-whole",
             [{"file_path": rules}, {"file_path": ".house/sources/rules.md", "offset": 9000},
              {"file_path": rules, "allow_large": True}],
             [{"file_path": rules, "limit": 120, "offset": 9080}, {"file_path": rules, "limit": 200},
              {"file_path": "/x/.house/sources/index.json"}, {"file_path": "/x/spec.yaml"},
              {"file_path": rules + ".bak"}, {"file_path": "/x/.house/sources/rules.md/../other.md"}]),
            ("read-allow-large",
             [{"file_path": rules, "allow_large": True, "limit": 100}, {"file_path": "/x/other.txt", "allow_large": True}],
             [{"file_path": rules, "allow_large": False, "limit": 100}, {"file_path": rules, "limit": 100}]),
            ("read-huge-range",
             [{"file_path": rules, "limit": 401}, {"file_path": rules, "limit": 450}, {"file_path": rules, "limit": 999},
              {"file_path": rules, "limit": 1000}, {"file_path": rules, "limit": 100000, "offset": 1},
              {"file_path": rules, "limit": 4001}, {"file_path": rules, "limit": 4000}],
             [{"file_path": rules, "limit": 400}, {"file_path": rules, "limit": 399}, {"file_path": rules, "limit": 120},
              {"file_path": rules, "limit": 200, "offset": 10000}, {"file_path": "/x/other.txt", "limit": 5000}]),
        ):
            for inp in hits:
                self.assertTrue(self.hits(case, label, inp), (label, inp))
            for inp in misses:
                self.assertFalse(self.hits(case, label, inp), (label, inp))

    def test_sources_cat_forbid(self):
        case = "sources-read-by-range"
        hits = ["cat .house/sources/rules.md", "cat -n " + self.RULES, "cd contests/calc && cat .house/sources/rules.md",
                "ls\ncat .house/sources/rules.md", "cat < .house/sources/rules.md", "cat .house/sources/rules.md > /tmp/x",
                "x=$(cat .house/sources/rules.md)", "git status; cat .house/sources/rules.md", 'cat ".house/sources/rules.md"',
                "cat a.txt .house/sources/rules.md", "cat .house/sources/rules.md | tr a b"]
        misses = ["cat .house/sources/index.json", "cat .house/sources/rules.md | head -50",
                  "cat -n .house/sources/rules.md | sed -n '9080,9100p'", "cat .house/sources/rules.md | grep -n members",
                  "sed -n '9080,9100p' .house/sources/rules.md", "head -c 16000 .house/sources/rules.md",
                  "grep -n members .house/sources/rules.md", 'grep -n "cat" .house/sources/rules.md',
                  "house sources calc", "cat .house/sources/rules.md.bak", "ls\nsed -n 1,5p .house/sources/rules.md",
                  "cat README.md && grep -n x .house/sources/rules.md"]
        for cmd in hits:
            self.assertTrue(self.bash(case, "cat-whole", cmd), cmd)
        for cmd in misses:
            self.assertFalse(self.bash(case, "cat-whole", cmd), cmd)

    def test_sources_head_bytes_forbid(self):
        case = "sources-read-by-range"
        hits = ["head -c 20001 .house/sources/rules.md", "head -c 100000 .house/sources/rules.md",
                "head -c100000 .house/sources/rules.md", "head --bytes=50000 .house/sources/rules.md",
                "head --bytes 2000000 .house/sources/rules.md", "head -c 1M .house/sources/rules.md",
                "head -c 20k .house/sources/rules.md", "head -c -100 .house/sources/rules.md",
                "cat .house/sources/rules.md | head -c 30000", "cd .house/sources && head -c 99999 rules.md",
                "git status; head -c 21000 .house/sources/rules.md", "head -n 5 -c 99999 .house/sources/rules.md"]
        misses = ["head -c 20000 .house/sources/rules.md", "head -c 16000 .house/sources/rules.md",
                  "head -c 2000 .house/sources/rules.md", "head -c 19k .house/sources/rules.md",
                  "head -n 500 .house/sources/rules.md", "head -c 100000 .house/sources/index.json",
                  "head -c 100000 README.md", "grep -n members .house/sources/rules.md",
                  "sed -n '9080,9100p' .house/sources/rules.md", "head -c 5 x; grep y .house/sources/rules.md"]
        for cmd in hits:
            self.assertTrue(self.bash(case, "head-bytes", cmd), cmd)
        for cmd in misses:
            self.assertFalse(self.bash(case, "head-bytes", cmd), cmd)

    def test_sources_final_answer_matches_the_fixtures_fact_and_nothing_else(self):
        import re
        from houselib.evals.fixtures import RULES_FACT
        (pred,) = [p for p in self.cases["sources-read-by-range"].outcome if p["type"] == "final_answer"]
        value = re.search(r"at most (\d+) members", RULES_FACT).group(1)
        self.assertEqual(value, "17")  # not a guessable team size
        self.assertRegex(f"maximum team size: {value}", pred["match"])
        for right in ("maximum team size: 17", "Maximum team size: 17.", "**maximum team size: 17**",
                      "maximum team size: 17 members", "Found it in section 125, clause 30.\nmaximum team size: 17",
                      "`maximum team size: 17`", "  Maximum Team Size: 17  "):
            self.assertRegex(right, pred["match"], right)
        for wrong in ("", "done", "I could not find it", "17", "6", "maximum team size: 6", "maximum team size: 16",
                      "maximum team size: 117", "maximum team size: 1.7", "maximum team size: 6 (see section 17)",
                      "not 17, it is 5", "maximum team size: not 17, it is 5", "section 17",
                      "maximum team size: section 17", "maximum team size: 17 or 5", "maximum team size: 5, not 17",
                      "The maximum team size is 6; section 17 covers appeals."):
            self.assertNotRegex(wrong, pred["match"], wrong)

    def test_sources_whole_dump_forbids(self):
        case = "sources-read-by-range"
        r = ".house/sources/rules.md"
        for label, hits, misses in (
            ("head-lines",
             [f"head -n 2001 {r}", f"head -n 99999 {r}", f"head -n99999 {r}", f"head --lines=5000 {r}",
              f"head --lines 3000 {r}", f"head -5000 {r}", f"head -n -1 {r}", f"head -n 10k {r}",
              f"cat {r} | head -n 9999", f"cd .house/sources && head -n 12000 rules.md"],
             [f"head -n 2000 {r}", f"head -n 200 {r}", f"head -50 {r}", f"head -n 99999 README.md",
              f"head -c 16000 {r}", f"head -n 99999 README.md; grep -n x {r}", f"sed -n '9080,9100p' {r}"]),
            ("tail-from-start",
             [f"tail -n +1 {r}", f"tail -n +0 {r}", f"tail -n+1 {r}", f"tail --lines=+1 {r}", f"tail +1 {r}",
              f"tail -n +500 {r}", f"cat {r} | tail -n +1"],
             [f"tail -n 50 {r}", f"tail -n +9000 {r}", f"tail -n +1 README.md", f"tail -n 100 {r}",
              f"tail -n +1 README.md && grep -n x {r}"]),
            ("grep-all",
             [f"grep '' {r}", f'grep "" {r}', f"grep -n '' {r}", f"grep -e '' {r}", f"grep -n . {r}",
              f"grep '^' {r}", f"grep -n '$' {r}", f"cat {r} | grep ''", f"grep -n \"\" {r}"],
             [f"grep -n members {r}", f"grep -n 'team' {r}", f"grep -n '^## ' {r}", f"grep '' README.md",
              f"grep -c '' {r}", f"grep -n 'a.b' {r}"]),
            ("awk-all",
             [f"awk 1 {r}", f"awk '1' {r}", f"awk '{{print}}' {r}", f"awk '{{ print $0 }}' {r}", f'awk "1" {r}',
              f"cat {r} | awk 1"],
             [f"awk 'NR>=9080 && NR<=9100' {r}", f"awk 1 README.md", f"awk '/members/' {r}",
              f"awk '{{print $1}}' {r}"]),
            ("sed-print-all",
             [f"sed -n p {r}", f"sed -n 'p' {r}", f'sed -n "p" {r}', f"sed -n '1,$p' {r}", f"sed '' {r}",
              f"sed -n -e p {r}", f"cat {r} | sed -n p"],
             [f"sed -n '9080,9100p' {r}", f"sed -n '/members/p' {r}", f"sed -n p README.md", f"sed -n 1,5p {r}",
              f"sed 's/a/b/' README.md"]),
        ):
            for cmd in hits:
                self.assertTrue(self.bash(case, label, cmd), (label, cmd))
            for cmd in misses:
                self.assertFalse(self.bash(case, label, cmd), (label, cmd))

    def test_sources_case_documents_what_the_forbids_cannot_see(self):
        text = (CASES_DIR / "README.md").read_text()
        self.assertIn("sed -n", text)  # a line range wider than 400 lines is not expressible as a regex
        for gap in ("Remaining gaps", "dd", "tail -c", "rg ''", "python3 -c"):
            self.assertIn(gap, text)

    def test_sources_brief_size_forbid(self):
        case = "sources-read-by-range"
        for chars, hit in ((150, False), (19_999, False), (20_000, True), (95_000, True), (500_000, True),
                           (1_200_000, True)):
            inp = {"subagent_type": "researcher", "description": "d", "prompt_chars": chars}
            self.assertEqual(self.hits(case, "brief-too-long", inp), hit, chars)
        self.assertFalse(self.hits(case, "brief-too-long", {"subagent_type": "researcher", "description": "20000"}))
        self.assertEqual(next(x for x in self.cases[case].forbid if x.label == "brief-too-long").tool, "Agent")


if __name__ == "__main__":
    unittest.main()
