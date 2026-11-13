"""Acceptance criteria frozen at plan time and run by code (`.house/checks/T###.json`)."""
import json
import tempfile
import time
import unittest
from pathlib import Path

from houselib import checks


def task_text(acceptance: str, protected: str = "protected: []\n", status: str = "todo") -> str:
    return (f"---\nid: T001\ntitle: x\nstatus: {status}\nowner: null\nbranch: null\ndepends_on: []\n"
            f"acceptance:\n{acceptance}{protected}---\n## Objective\nx\n")


RUN_OK = '  - id: AC-1\n    run: "python3 -c \'print(1)\'"\n'


class ChecksTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.house = self.tmp / ".house"
        (self.house / "tasks").mkdir(parents=True)
        (self.house / "DECISIONS.md").write_text("# Dziennik decyzji\n")
        self.work = self.tmp / "work"
        self.work.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def write_task(self, text: str, task_id: str = "T001") -> Path:
        p = self.house / "tasks" / f"{task_id}-x.md"
        p.write_text(text.replace("id: T001", f"id: {task_id}"))
        return p

    def frozen(self, acceptance: str, **kw):
        self.write_task(task_text(acceptance, **kw))
        checks.freeze(self.house, "T001")

    def test_parse_legacy_strings_are_manual(self):
        cs = checks.parse_acceptance(["demo works", "README exists"])
        self.assertEqual([(c.id, c.kind, c.text) for c in cs],
                         [("AC-1", "manual", "demo works"), ("AC-2", "manual", "README exists")])

    def test_parse_mappings_and_default_ids(self):
        cs = checks.parse_acceptance([{"run": "pytest", "timeout": 5}, {"id": "AC-9", "manual": "ok"}])
        self.assertEqual((cs[0].id, cs[0].kind, cs[0].run, cs[0].timeout), ("AC-1", "run", "pytest", 5))
        self.assertEqual((cs[1].id, cs[1].kind), ("AC-9", "manual"))
        self.assertEqual(checks.parse_acceptance([{"run": "x"}])[0].timeout, checks.DEFAULT_TIMEOUT)
        with self.assertRaises(ValueError):
            checks.parse_acceptance([{"id": "AC-1"}])

    def test_load_task_missing(self):
        with self.assertRaises(FileNotFoundError):
            checks.load_task(self.house, "T404")

    def test_freeze_writes_once(self):
        self.write_task(task_text(RUN_OK))
        path = checks.freeze(self.house, "T001")
        data = json.loads(path.read_text())
        self.assertEqual(data["task"], "T001")
        self.assertEqual(data["frozen"]["criteria"][0]["id"], "AC-1")
        with self.assertRaises(FileExistsError):
            checks.freeze(self.house, "T001")

    def test_unfrozen_task_refused(self):
        self.write_task(task_text(RUN_OK))
        with self.assertRaises(ValueError):
            checks.run_checks(self.house, "T001", self.work)

    def test_run_pass_and_fail(self):
        self.frozen(RUN_OK + '  - id: AC-2\n    run: "exit 3"\n')
        r = checks.run_checks(self.house, "T001", self.work)
        self.assertEqual([(x.id, x.passes, x.exit) for x in r.results], [("AC-1", True, 0), ("AC-2", False, 3)])
        self.assertFalse(r.green)
        stored = json.loads((self.house / "checks/T001.json").read_text())["results"]
        self.assertTrue(stored["AC-1"]["passes"])

    def test_timeout_fails_fast(self):
        self.frozen('  - id: AC-1\n    run: "sleep 5"\n    timeout: 1\n')
        t = time.monotonic()
        r = checks.run_checks(self.house, "T001", self.work)
        self.assertLess(time.monotonic() - t, 4)
        self.assertEqual((r.results[0].passes, r.results[0].exit), (False, None))
        self.assertIn("timeout", r.results[0].output_tail)

    def test_expect_regex_and_comparison(self):
        self.assertTrue(checks.expect_ok(None, ""))
        self.assertTrue(checks.expect_ok(r"^OK \d+$", "noise\nOK 12\n"))
        self.assertFalse(checks.expect_ok(r"^OK$", "KO"))
        self.assertTrue(checks.expect_ok("score >= 0.71", "score: 0.70\nscore: 0.75\n"))
        self.assertFalse(checks.expect_ok("score >= 0.71", "score=0.5"))
        self.assertFalse(checks.expect_ok("score >= 0.71", "no numbers"))
        self.assertTrue(checks.expect_ok("val loss < 1", "Val Loss: 0.4"))

    def test_expect_applied_to_stdout(self):
        self.frozen('  - id: AC-1\n    run: "echo score: 0.5"\n    expect: "score >= 0.71"\n')
        self.assertFalse(checks.run_checks(self.house, "T001", self.work).green)

    def test_mismatch_after_task_edit(self):
        self.frozen('  - id: AC-1\n    run: "exit 1"\n')
        self.write_task(task_text('  - id: AC-1\n    run: "true"\n'))
        r = checks.run_checks(self.house, "T001", self.work)
        self.assertTrue(r.mismatch)
        self.assertFalse(r.green)

    def test_init_script_failure_is_red(self):
        self.frozen(RUN_OK)
        (self.house / "init.sh").write_text("exit 7\n")
        r = checks.run_checks(self.house, "T001", self.work)
        self.assertFalse(r.init_ok)
        self.assertFalse(r.green)

    def test_manual_only_task(self):
        self.frozen("  - id: AC-1\n    manual: golden path works\n")
        r = checks.run_checks(self.house, "T001", self.work)
        self.assertTrue(r.green)
        self.assertFalse(r.complete)
        checks.record_manual(self.house, "T001", "AC-1", "reports/judge-1.md")
        r = checks.run_checks(self.house, "T001", self.work)
        self.assertTrue(r.complete)
        with self.assertRaises(ValueError):
            checks.record_manual(self.house, "T001", "AC-9", "x")

    def test_record_manual_rejects_run_criterion(self):
        self.frozen(RUN_OK)
        with self.assertRaises(ValueError):
            checks.record_manual(self.house, "T001", "AC-1", "x")

    def test_refreeze_logs_decision_and_keeps_results(self):
        self.frozen(RUN_OK + '  - id: AC-2\n    run: "exit 1"\n')
        checks.run_checks(self.house, "T001", self.work)
        (self.house / "DECISIONS.md").write_text("# D\n\n## D-001 a\n\n## D-003 c\n")
        self.write_task(task_text(RUN_OK + '  - id: AC-2\n    run: "true"\n'))
        did = checks.refreeze(self.house, "T001", "AC-2 was impossible on CPU")
        self.assertEqual(did, "D-004")
        text = (self.house / "DECISIONS.md").read_text()
        self.assertIn("## D-004 Zmiana kryteriów T001", text)
        self.assertIn("AC-2 was impossible on CPU", text)
        stored = json.loads((self.house / "checks/T001.json").read_text())
        self.assertIn("AC-1", stored["results"])
        self.assertNotIn("AC-2", stored["results"])
        self.assertTrue(checks.run_checks(self.house, "T001", self.work).green)

    def test_freeze_hashes_existing_protected_files(self):
        (self.tmp / "tests").mkdir()
        (self.tmp / "tests/test_a.py").write_text("x")
        self.frozen(RUN_OK, protected="protected:\n  - tests/test_a.py\n  - tests/later.py\n")
        prot = json.loads((self.house / "checks/T001.json").read_text())["frozen"]["protected"]
        self.assertEqual(len(prot["tests/test_a.py"]), 64)
        self.assertIsNone(prot["tests/later.py"])
        (self.tmp / "tests/new.py").write_text("y")
        self.write_task(task_text(RUN_OK, protected="protected:\n  - tests/test_a.py\n  - tests/later.py\n  - tests/new.py\n"))
        checks.refreeze(self.house, "T001", "add new.py")
        prot = json.loads((self.house / "checks/T001.json").read_text())["frozen"]["protected"]
        self.assertEqual(len(prot["tests/new.py"]), 64)

    def test_seal_protected(self):
        self.frozen(RUN_OK, protected="protected:\n  - tests/test_a.py\n  - tests/missing.py\n")
        (self.work / "tests").mkdir()
        (self.work / "tests/test_a.py").write_text("x")
        missing = checks.seal_protected(self.house, "T001", self.work)
        self.assertEqual(missing, ["tests/missing.py"])
        prot = json.loads((self.house / "checks/T001.json").read_text())["frozen"]["protected"]
        self.assertEqual(len(prot["tests/test_a.py"]), 64)
        self.assertIsNone(prot["tests/missing.py"])


    def test_freeze_refuses_task_without_criteria(self):
        self.write_task(task_text("  []\n".replace("  []\n", ""), protected="protected: []\n").replace("acceptance:\n", "acceptance: []\n"))
        with self.assertRaises(ValueError):
            checks.freeze(self.house, "T001")

    def test_bad_expect_regex_is_a_failure(self):
        self.frozen('  - id: AC-1\n    run: "echo x"\n    expect: "([unclosed"\n')
        r = checks.run_checks(self.house, "T001", self.work)
        self.assertFalse(r.results[0].passes)
        self.assertIn("expect", r.results[0].output_tail)

    def test_init_with_background_process_does_not_hang(self):
        self.frozen(RUN_OK)
        (self.house / "init.sh").write_text("sleep 30 &\necho started\n")
        t = time.monotonic()
        r = checks.run_checks(self.house, "T001", self.work)
        self.assertLess(time.monotonic() - t, 10)
        self.assertTrue(r.green)

    def test_timeout_kills_child_processes(self):
        import subprocess as sp
        self.frozen('  - id: AC-1\n    run: "sleep 7731; echo x"\n    timeout: 1\n')
        checks.run_checks(self.house, "T001", self.work)
        time.sleep(0.3)
        left = sp.run(["pgrep", "-f", "^sleep 7731"], capture_output=True, text=True).stdout.strip()
        self.assertEqual(left, "")


if __name__ == "__main__":
    unittest.main()
