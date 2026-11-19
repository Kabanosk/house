"""`houselib.evidence`: facts about one attempt computed from the contest state."""
import dataclasses
import json
import tempfile
import unittest
from pathlib import Path

from houselib import evidence
from houselib.attempts import Row
from tests.helpers import EMAIL, NAME, git

QUESTION = "## {id} [open] A synthetic question\n- **Poziom:** krytyczny\n- **Blokuje:** T001\n\n"


def _questions(*ids: str) -> str:
    return "# Questions\n\n" + "".join(QUESTION.format(id=i) for i in ids)


def _checks_file(results: dict, criteria: list | None = None) -> str:
    frozen = {"criteria": criteria if criteria is not None else
              [{"id": cid, "kind": "run"} for cid in results]}
    return json.dumps({"frozen": frozen, "results": results})


class StateFactsTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.contest = Path(tmp.name).resolve()
        git(self.contest, "init", "-q", "-b", "main")
        git(self.contest, "config", "user.name", NAME)
        git(self.contest, "config", "user.email", EMAIL)
        self.house = self.contest / ".house"
        (self.house / "checks").mkdir(parents=True)
        (self.house / "QUESTIONS.md").write_text(_questions("Q-001"))
        (self.contest / "app.py").write_text("print('one')\n")
        self.base = self.commit("base")

    def commit(self, message: str) -> str:
        git(self.contest, "add", "-A")
        git(self.contest, "commit", "-q", "-m", message)
        return git(self.contest, "rev-parse", "HEAD").strip()

    def row(self, head: str, **kw) -> Row:
        return Row(task="T001", n=1, source="night", agent="worker", tool="claude",
                   base=kw.pop("base", self.base), head=head, **kw)

    def test_state_facts_questions_come_from_the_row(self):
        # workers write questions into the main checkout through $HOUSE_DIR, never on the task branch
        (self.house / "QUESTIONS.md").write_text(_questions("Q-001", "Q-002"))
        head = self.commit("questions on main")
        self.assertEqual(evidence.state_facts(self.contest, self.row(head, questions=["Q-007"])).questions_added,
                         ["Q-007"])
        self.assertEqual(evidence.state_facts(self.contest, self.row(head, questions=[])).questions_added, [])
        old = evidence.state_facts(self.contest, self.row(head))  # a row from before the field
        self.assertIsNone(old.questions_added)
        self.assertIn("questions added: unknown", evidence.render_full(old, self.row(head)))
        self.assertIn("questions added: none",
                      evidence.render_full(evidence.Facts(questions_added=[]), self.row(head)))

    def test_state_facts_diffstat_and_last_check(self):
        (self.contest / "app.py").write_text("print('two')\n")
        (self.house / "checks" / "T001.json").write_text(
            _checks_file({"AC-1": {"passes": True}, "AC-2": {"passes": False}}))
        head = self.commit("work")
        facts = evidence.state_facts(self.contest, self.row(head))
        self.assertIn("app.py", facts.diffstat)
        self.assertIn("2 files changed", facts.diffstat)
        self.assertEqual(facts.last_check, "RED (AC-2)")
        (self.house / "checks" / "T001.json").write_text(_checks_file({"AC-1": {"passes": True}}))
        self.assertEqual(evidence.state_facts(self.contest, self.row(head)).last_check, "GREEN")

    def test_state_facts_long_diffstat_keeps_summary_line_only(self):
        for i in range(12):
            (self.contest / f"f{i}.txt").write_text("x\n")
        head = self.commit("many files")
        facts = evidence.state_facts(self.contest, self.row(head))
        self.assertEqual(len(facts.diffstat.splitlines()), 1)
        self.assertIn("12 files changed", facts.diffstat)

    def test_state_facts_missing_checks_file_is_unknown(self):
        (self.contest / "app.py").write_text("print('two')\n")
        head = self.commit("work")
        self.assertEqual(evidence.state_facts(self.contest, self.row(head)).last_check, "unknown")

    def test_state_facts_unreadable_checks_file_is_no_check_result(self):
        # the shared gate reader words a present-but-unusable file as "no check result"; only a missing file is unknown
        (self.house / "checks" / "T001.json").write_text("{not json")
        head = self.commit("broken checks")
        self.assertEqual(evidence.state_facts(self.contest, self.row(head)).last_check, "no check result")

    def test_state_facts_missing_commits_do_not_raise(self):
        facts = evidence.state_facts(self.contest, self.row("f" * 40, base="e" * 40))
        self.assertIsNone(facts.questions_added)
        self.assertEqual(facts.diffstat, "")
        self.assertEqual(facts.violations, [])
        facts = evidence.state_facts(self.contest, self.row("", base=""))
        self.assertEqual(facts.diffstat, "")

    def test_state_facts_not_a_repo_does_not_raise(self):
        with tempfile.TemporaryDirectory() as other:
            facts = evidence.state_facts(Path(other), self.row("a" * 40))
        self.assertEqual((facts.questions_added, facts.diffstat, facts.last_check, facts.violations),
                         (None, "", "unknown", []))

    def test_state_facts_violations_from_task_worktree(self):
        (self.house / "tasks").mkdir()
        (self.house / "tasks" / "T001-thing.md").write_text("---\nid: T001\ntitle: thing\n---\n")
        head = self.commit("task")
        wt = self.contest / ".worktrees" / "T001-thing"
        git(self.contest, "worktree", "add", "-q", str(wt), "-b", "task/T001-thing")
        (wt / ".house" / "notes.txt").write_text("worker wrote into .house\n")
        facts = evidence.state_facts(self.contest, self.row(head))
        self.assertTrue(any(".house" in v for v in facts.violations), facts.violations)

    def test_state_facts_malformed_task_file_does_not_raise(self):
        (self.house / "tasks").mkdir()
        wt = self.contest / ".worktrees" / "T001-x"
        wt.mkdir(parents=True)
        for front in ("protected: 5", "acceptance: 7"):
            with self.subTest(front=front):
                (self.house / "tasks" / "T001-x.md").write_text(f"---\nid: T001\n{front}\n---\n")
                facts = evidence.state_facts(self.contest, self.row(self.base))
                self.assertIsInstance(facts.violations, list)

    def test_state_facts_branch_outside_worktrees_is_ignored(self):
        (self.house / "tasks").mkdir()
        (self.house / "tasks" / "T001-x.md").write_text("---\nid: T001\nbranch: task/../../elsewhere\n---\n")
        (self.contest.parent / "elsewhere").mkdir(exist_ok=True)
        self.addCleanup(lambda: (self.contest.parent / "elsewhere").rmdir())
        (self.contest / ".worktrees").mkdir()
        self.assertIsNone(evidence._worktree(self.contest, "T001"))

    def test_state_facts_no_worktree_means_no_violations(self):
        self.assertEqual(evidence.state_facts(self.contest, self.row(self.base)).violations, [])

    def test_log_facts_default_to_unknown_or_empty(self):
        facts = evidence.Facts()
        self.assertEqual((facts.check_after_last_edit, facts.model, facts.duration), ("unknown",) * 3)
        self.assertIsNone(facts.unbacked_claim)
        self.assertIsNone(facts.guard_refusals)
        self.assertIsNone(facts.n_calls)
        self.assertEqual((facts.test_edits, facts.repeats, facts.dead_end_repeats, facts.calls, facts.error_tails),
                         ([], [], [], [], []))

    def test_strings_are_redacted(self):
        secret = "sk-fakeFAKE0123456789abcd"
        (self.contest / f"{secret}.txt").write_text("x\n")
        head = self.commit("work")
        facts = evidence.state_facts(self.contest, self.row(head))
        self.assertNotIn(secret, facts.diffstat)
        self.assertIn("[REDACTED]", facts.diffstat)



# ---- log facts: synthetic Claude stream and Codex logs -------------------------------------------------------------
SESSION = "11111111-2222-3333-4444-555555555555"
CHECK = "house check c T001 --worktree wt"  # a night attempt's run of the gate on its own worktree


def _usage() -> dict:
    return {"input_tokens": 1, "cache_read_input_tokens": 2, "output_tokens": 3}


def _tu(tid: str, name: str, inp: dict, model="claude-haiku-4-5", ts: str | None = None) -> dict:
    rec = {"type": "assistant", "session_id": SESSION, "request_id": f"r{tid}", "parent_tool_use_id": None,
           "message": {"id": f"m{tid}", "model": model, "role": "assistant", "stop_reason": None, "usage": _usage(),
                       "content": [{"type": "tool_use", "id": tid, "name": name, "input": inp}]}}
    if ts:
        rec["timestamp"] = ts
    return rec


def _text(text: str, mid="mt") -> dict:
    return {"type": "assistant", "session_id": SESSION, "request_id": f"r{mid}", "parent_tool_use_id": None,
            "message": {"id": mid, "model": "claude-haiku-4-5", "role": "assistant", "stop_reason": None,
                        "usage": _usage(), "content": [{"type": "text", "text": text}]}}


def _tr(tid: str, content="out", is_error: bool | None = None) -> dict:
    block = {"type": "tool_result", "tool_use_id": tid, "content": content}
    if is_error is not None:
        block["is_error"] = is_error
    return {"type": "user", "session_id": SESSION, "parent_tool_use_id": None,
            "message": {"role": "user", "content": [block]}}


def _edit(tid: str, path: str, ok: bool | None = False, name="Edit") -> list[dict]:
    return [_tu(tid, name, {"file_path": path, "content": "CANARY-EDIT"}), _tr(tid, "done", ok)]


def _bash(tid: str, command: str, err: bool | None = False, out="out") -> list[dict]:
    return [_tu(tid, "Bash", {"command": command}), _tr(tid, out, err)]


def _codex_cmd(iid: str, command: str, code: int, out="") -> dict:
    return {"type": "item.completed", "item": {"id": iid, "type": "command_execution", "command": command,
                                                "aggregated_output": out, "exit_code": code,
                                                "status": "completed" if code == 0 else "failed"}}


def _codex_change(iid: str, paths: list[str], status="completed") -> dict:
    return {"type": "item.completed", "item": {"id": iid, "type": "file_change", "status": status,
                                                "changes": [{"path": p, "kind": "update"} for p in paths]}}


class LogFactsTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.contest = Path(tmp.name).resolve()
        self.house = self.contest / ".house"
        (self.house / "logs").mkdir(parents=True)
        (self.house / "reports").mkdir()
        self.wt = f"{self.contest}/.worktrees/T001-x"

    def write_log(self, name: str, *records) -> str:
        path = self.house / "logs" / name
        path.write_text("\n".join(r if isinstance(r, str) else json.dumps(r) for r in records) + "\n")
        return path.relative_to(self.contest).as_posix()

    def row(self, log: str = "", n=1, tool="claude", **kw) -> Row:
        kw.setdefault("start", "2000-01-01T00:00:00")  # every report written by a test is newer
        kw.setdefault("source", "night")
        return Row(task="T001", n=n, agent="worker", tool=tool, log=log, **kw)

    def facts(self, *records, tool="claude", n=1, **kw) -> evidence.Facts:
        row = self.row(self.write_log(f"a{n}.jsonl", *records), n=n, tool=tool, **kw)
        return evidence.log_facts(self.contest, row, evidence.Facts())

    def report(self, text: str) -> None:
        (self.house / "reports" / "T001.md").write_text(text)

    # check after the last edit
    def test_no_log_leaves_defaults(self):
        for row in (self.row(""), self.row(".house/logs/absent.jsonl")):
            self.assertEqual(evidence.log_facts(self.contest, row, evidence.Facts()), evidence.Facts())
        empty = self.row(self.write_log("empty.jsonl"))
        (self.contest / empty.log).write_text("")
        self.assertEqual(evidence.log_facts(self.contest, empty, evidence.Facts()), evidence.Facts())

    def test_log_facts_keeps_state_facts(self):
        base = evidence.Facts(diffstat="1 file changed", last_check="GREEN")
        row = self.row(self.write_log("a.jsonl", *_edit("e1", f"{self.wt}/a.py")))
        out = evidence.log_facts(self.contest, row, base)
        self.assertEqual((out.diffstat, out.last_check), ("1 file changed", "GREEN"))
        self.assertEqual(base.n_calls, None)  # the input is not mutated

    def test_no_check_after_last_edit(self):
        f = self.facts(*_bash("c1", CHECK), *_edit("e1", f"{self.wt}/a.py"))
        self.assertEqual(f.check_after_last_edit, "no")

    def test_check_after_edit_green_and_red(self):
        green = self.facts(*_edit("e1", f"{self.wt}/a.py"), *_bash("c1", CHECK, err=False))
        self.assertEqual(green.check_after_last_edit, "yes, GREEN")
        red = self.facts(*_edit("e1", f"{self.wt}/a.py"), *_bash("c1", CHECK, err=True, out="AC-1 RED"))
        self.assertEqual(red.check_after_last_edit, "yes, RED")
        unknown = self.facts(*_edit("e1", f"{self.wt}/a.py"), *_bash("c1", CHECK, err=None))
        self.assertEqual(unknown.check_after_last_edit, "yes, unknown")
        last = self.facts(*_edit("e1", f"{self.wt}/a.py"), *_bash("c1", CHECK, err=True),
                          *_bash("c2", CHECK, err=False))
        self.assertEqual(last.check_after_last_edit, "yes, GREEN")

    def test_house_check_via_wrapper_detected(self):
        for cmd in ("python3 /home/x/house/bin/house check c T001 --worktree wt",
                    "python3 -u -B /home/x/house/bin/house check c T001 --worktree wt",
                    "HOUSE_X=1 house check c T001 --worktree wt", "cd wt && house check c T001 --worktree .",
                    "house check c --work wt T001", "cd wt; bin/house check c T001 --worktree=wt 2>&1",
                    "/bin/bash -lc 'house check c T001 --worktree wt'",
                    "sh -c 'cd /w && house check c T001 --worktree wt'",
                    "house check T001 --worktree wt > /tmp/out", "house check c T001 --worktree wt;"):
            with self.subTest(cmd=cmd):
                f = self.facts(*_edit("e1", f"{self.wt}/a.py"), *_bash("c1", cmd))
                self.assertEqual(f.check_after_last_edit, "yes, GREEN")
                red = self.facts(*_edit("e1", f"{self.wt}/a.py"), *_bash("c1", cmd, err=True))
                self.assertEqual(red.check_after_last_edit, "yes, RED")

    def test_house_check_without_own_exit_code_is_unknown(self):
        for cmd in ("house check c T001 | tail -5", "house check c T001 || true", "house check c T001; echo done",
                    "! house check c T001", "house check c T001 &", "house check c T001 && echo ok",
                    "false || house check c T001", "(house check c T001)", "bash -lc 'house check c T001 | tail'",
                    "echo hi | house check c T001", "house check c T001 |& tee out",
                    "house check c T001 |& tail -5", "house check c T001|&tail"):
            with self.subTest(cmd=cmd):
                f = self.facts(*_edit("e1", f"{self.wt}/a.py"), *_bash("c1", cmd, err=False))
                self.assertEqual(f.check_after_last_edit, "yes, unknown")

    def test_not_a_check_run(self):
        for cmd in ("house merge T001", "echo house check", "echo house check ok", "grep -rn house check",
                    "cat ~/house check", "pytest tests/check", "HOUSE_ROOT=/x/house make check",
                    "house check --help", "house check -h", "house check c T001 --manual AC-1 --evidence x",
                    "house check c T001 --freeze", "house check c T001 --refreeze --reason r",
                    "house check c --all", "house check c T002", "time house check c T001",
                    "house check c T001 --man AC-1 --evidence x", "house check c T001 --free",
                    "house check c T001 --manual=AC-1 --evidence x", "house check c --al",
                    "house check c --work wt T002"):
            with self.subTest(cmd=cmd):
                f = self.facts(*_edit("e1", f"{self.wt}/a.py"), *_bash("c1", cmd))
                self.assertEqual(f.check_after_last_edit, "no")

    def test_edit_after_check_is_no_and_no_edits_is_unknown(self):
        f = self.facts(*_edit("e1", f"{self.wt}/a.py"), *_bash("c1", CHECK),
                       *_edit("e2", f"{self.wt}/b.py", name="Write"))
        self.assertEqual(f.check_after_last_edit, "no")
        self.assertEqual(self.facts(*_bash("c1", "ls")).check_after_last_edit, "unknown")

    def test_is_error_none_not_success(self):
        f = self.facts(*_edit("e1", f"{self.wt}/tests/test_a.py", ok=None), *_bash("c1", CHECK))
        self.assertEqual(f.test_edits, [])
        self.assertEqual(f.check_after_last_edit, "unknown")  # no successful edit to compare with
        failed = self.facts(*_edit("e1", f"{self.wt}/tests/test_a.py", ok=True))
        self.assertEqual(failed.test_edits, [])

    def test_codex_check_after_change(self):
        f = self.facts({"type": "thread.started", "thread_id": "th"}, _codex_change("i1", [f"{self.wt}/a.py"]),
                       _codex_cmd("i2", "/bin/bash -lc 'house check c T001 --worktree wt'", 0), tool="codex")
        self.assertEqual(f.check_after_last_edit, "yes, GREEN")
        f = self.facts(_codex_cmd("i2", "/bin/bash -lc 'house check c T001 --worktree wt'", 1),
                       _codex_change("i1", [f"{self.wt}/a.py"]), tool="codex")
        self.assertEqual(f.check_after_last_edit, "no")

    # claim
    def test_unbacked_claim_line(self):
        self.report("# Report\n\n## Result vs acceptance\nAll criteria passed.\n\n## Next\nok go\n")
        f = self.facts(*_edit("e1", f"{self.wt}/a.py"))
        self.assertTrue(f.unbacked_claim)
        self.report("Result vs acceptance: pass\n")
        self.assertTrue(self.facts(*_edit("e1", f"{self.wt}/a.py"), *_bash("c1", CHECK, err=True)).unbacked_claim)
        self.assertFalse(self.facts(*_edit("e1", f"{self.wt}/a.py"), *_bash("c1", CHECK)).unbacked_claim)

    def test_claim_uses_the_shared_section_parser(self):
        edit = _edit("e1", f"{self.wt}/a.py")
        self.report("## Result vs acceptance\nEach acceptance check from the task: pass / fail.\n")
        self.assertFalse(self.facts(*edit).unbacked_claim)  # the unfilled template line is no claim
        self.report("## Result vs acceptance\nAC-1: pass\n")
        self.assertTrue(self.facts(*edit).unbacked_claim)
        self.report("## Result vs acceptance\n```\n# AC-1\n```\nAC-1: pass\n\n## Next\nok\n")
        self.assertTrue(self.facts(*edit).unbacked_claim)

    def test_unbacked_claim_needs_a_claim(self):
        edit = _edit("e1", f"{self.wt}/a.py")
        self.assertFalse(self.facts(*edit).unbacked_claim)  # no report
        self.report("## Summary\nall green\n")
        self.assertFalse(self.facts(*edit).unbacked_claim)  # claim outside the section
        self.report("## Result vs acceptance\nAC-1 failing, AC-2 failing\n## Notes\npassed\n")
        self.assertFalse(self.facts(*edit).unbacked_claim)
        self.report("## RESULT VS ACCEPTANCE\nGreen\n")
        self.assertTrue(self.facts(*edit).unbacked_claim)

    def test_unbacked_claim_not_backed_by_piped_check(self):
        self.report("## Result vs acceptance\nAll pass\n")
        f = self.facts(*_edit("e1", f"{self.wt}/a.py"), *_bash("c1", "house check c T001 | tail -5", out="RED"))
        self.assertEqual(f.check_after_last_edit, "yes, unknown")
        self.assertTrue(f.unbacked_claim)

    def test_report_is_the_attempts_own_copy(self):
        self.report("## Result vs acceptance\nAC-1 failing\n")  # live report of a newer attempt
        (self.house / "reports" / "T001-attempt-1.md").write_text("## Result vs acceptance\nAll pass\n")
        edit = _edit("e1", f"{self.wt}/a.py")
        copy = ".house/reports/T001-attempt-1.md"  # relative to the contest, like `log`
        rows = [self.row("", n=1, report=copy), self.row("", n=2)]
        self.house.joinpath("attempts.jsonl").write_text(
            "".join(json.dumps(dataclasses.asdict(r)) + "\n" for r in rows))
        first = self.facts(*edit, n=1, report=copy)
        self.assertTrue(first.unbacked_claim)
        latest = self.facts(*edit, n=2)  # no copy yet: the live report of the latest attempt
        self.assertFalse(latest.unbacked_claim)

    def test_unbacked_claim_none_for_old_attempt_without_copy(self):
        self.report("## Result vs acceptance\nAll pass\n")
        rows = [self.row("", n=1), self.row("", n=2)]
        self.house.joinpath("attempts.jsonl").write_text(
            "".join(json.dumps(dataclasses.asdict(r)) + "\n" for r in rows))
        f = self.facts(*_edit("e1", f"{self.wt}/a.py"), n=1)
        self.assertIsNone(f.unbacked_claim)
        escape = self.facts(*_edit("e1", f"{self.wt}/a.py"), n=1, report="../outside.md")
        self.assertIsNone(escape.unbacked_claim)

    def test_shell_edit_counts_as_a_code_change(self):
        self.report("## Result vs acceptance\nAll pass\n")
        sed = _bash("s1", "sed -i s/a/b/ src/a.py")
        green = self.facts(*sed, *_bash("c1", CHECK))
        self.assertEqual(green.check_after_last_edit, "yes, GREEN")
        self.assertFalse(green.unbacked_claim)
        red = self.facts(*sed, *_bash("c1", CHECK, err=True))
        self.assertEqual(red.check_after_last_edit, "yes, RED")
        self.assertTrue(red.unbacked_claim)
        none = self.facts(*sed)
        self.assertEqual(none.check_after_last_edit, "no")
        self.assertTrue(none.unbacked_claim)

    def test_unbacked_claim_without_any_code_change_uses_last_check_of_log(self):
        self.report("## Result vs acceptance\nAll pass\n")
        run = _bash("s1", "python3 gen.py")  # what it writes cannot be told from the command
        green = self.facts(*run, *_bash("c1", CHECK))
        self.assertEqual(green.check_after_last_edit, "unknown")
        self.assertFalse(green.unbacked_claim)
        self.assertTrue(self.facts(*run, *_bash("c1", CHECK, err=True)).unbacked_claim)
        self.assertTrue(self.facts(*run).unbacked_claim)

    # E1: bookkeeping after the check (coder.md: check until GREEN, then report, PROGRESS.md, status)
    def test_bookkeeping_after_green_check_is_not_a_code_change(self):
        self.report("## Result vs acceptance\nAll criteria pass\n")
        house = f"{self.contest}/.house"
        f = self.facts(*_edit("e1", f"{self.wt}/app.py"), *_bash("c1", CHECK),
                       *_edit("e2", f"{house}/reports/T001.md", name="Write"),
                       *_edit("e3", f"{house}/PROGRESS.md"), *_edit("e4", f"{house}/tasks/T001-x.md"))
        self.assertEqual(f.check_after_last_edit, "yes, GREEN")
        self.assertFalse(f.unbacked_claim)

    def test_codex_bookkeeping_after_green_check_is_not_a_code_change(self):
        self.report("## Result vs acceptance\nAll criteria pass\n")
        house = f"{self.contest}/.house"
        f = self.facts({"type": "thread.started", "thread_id": "th"}, _codex_change("i1", [f"{self.wt}/app.py"]),
                       _codex_cmd("i2", "/bin/bash -lc 'house check c T001 --worktree wt'", 0),
                       _codex_change("i3", [f"{house}/reports/T001.md", f"{house}/PROGRESS.md",
                                            f"{house}/tasks/T001-x.md"]), tool="codex")
        self.assertEqual(f.check_after_last_edit, "yes, GREEN")
        self.assertFalse(f.unbacked_claim)

    def test_worktree_house_copy_and_paths_outside_are_not_code(self):
        edit = _edit("e1", f"{self.wt}/app.py")
        f = self.facts(*edit, *_bash("c1", CHECK), *_edit("e2", f"{self.wt}/.house/notes.md"),
                       *_edit("e3", "/elsewhere/scratch.py"), *_edit("e4", "/home/u/.claude/notes.md"))
        self.assertEqual(f.check_after_last_edit, "yes, GREEN")
        outside = self.facts(*_edit("e1", "/elsewhere/a.py"), *_bash("c1", CHECK))
        self.assertEqual(outside.check_after_last_edit, "unknown")
        self.assertEqual(self.facts(*edit, *_bash("c1", CHECK), *_edit("e2", "src/b.py")).check_after_last_edit,
                         "no")  # a relative path is the worker's own checkout

    # E2: a shell write after the check makes the check stale
    def test_shell_write_after_green_check_is_a_change(self):
        for cmd in ("sed -i s/a/b/ app.py", "echo x > app.py", "cat a | tee app.py", "mv a.py b.py",
                    "cp a.py b.py", "rm app.py", "git checkout -- app.py", "git checkout HEAD -- app.py",
                    "git apply fix.patch", "git restore app.py", "patch -p1 < fix.patch",
                    "bash -lc 'sed -i s/a/b/ app.py'", f"echo x >> {self.wt}/app.py"):
            with self.subTest(cmd=cmd):
                f = self.facts(*_edit("e1", f"{self.wt}/app.py"), *_bash("c1", CHECK), *_bash("s1", cmd))
                self.assertEqual(f.check_after_last_edit, "no")

    def test_commands_without_a_known_code_write_keep_the_check(self):
        for cmd in ("pytest -q", "ls", "grep -rn x .", "cat app.py > /dev/null", "echo done > .house/tmp/x",
                    "python3 gen.py", "echo x > $OUT", "git status", "git apply --check fix.patch",
                    "patch --dry-run -p1 < fix.patch", "echo x > /elsewhere/out.txt",
                    "house check c T001 --worktree wt > check.log"):
            with self.subTest(cmd=cmd):
                f = self.facts(*_edit("e1", f"{self.wt}/app.py"), *_bash("c1", CHECK), *_bash("s1", cmd))
                self.assertEqual(f.check_after_last_edit, "yes, GREEN")

    # A1 ruling: a `cd` earlier in the same command moves the directory later relative targets resolve against
    def _after_green(self, cmd: str, codex: bool = False) -> evidence.Facts:
        self.report("## Result vs acceptance\nAll criteria pass\n")
        if codex:
            return self.facts(_codex_change("i1", [f"{self.wt}/app.py"]),
                              _codex_cmd("i2", "/bin/bash -lc 'house check c T001 --worktree wt'", 0),
                              _codex_cmd("i3", cmd, 0), tool="codex")
        return self.facts(*_edit("e1", f"{self.wt}/app.py"), *_bash("c1", CHECK), *_bash("s1", cmd))

    def test_bookkeeping_after_cd_is_not_a_code_change(self):
        house = f"{self.contest}/.house"
        for cmd in ('cd "$HOUSE_DIR" && echo "- linia" >> PROGRESS.md',
                    f"cd {house} && sed -i 's/a/b/' tasks/T001-x.md",
                    "cd ../../.house && echo x >> PROGRESS.md",
                    f"pushd {house} && echo x >> PROGRESS.md",
                    f"cd {house}; echo x >> PROGRESS.md",
                    f"cd {house}\necho x >> PROGRESS.md",
                    "cd $HOUSE_DIR && echo x > PROGRESS.md", "cd `pwd`/x && echo x > a.py",
                    "cd ~bob && echo x > a.py", "cd src/* && echo x > a.py", "cd && echo x > a.py",
                    "cd - && echo x > a.py", f"cd {house} && popd && echo x > a.py",
                    "cd $X && cd sub && echo x > a.py",
                    f"cd {house} && echo x > PROGRESS.md && cd $X && echo y > b.py",
                    "bash -lc 'cd \"$HOUSE_DIR\" && echo x >> PROGRESS.md'",
                    f"sh -c 'cd {house} && sed -i s/a/b/ PROGRESS.md'"):
            with self.subTest(cmd=cmd):
                f = self._after_green(cmd)
                self.assertEqual(f.check_after_last_edit, "yes, GREEN")
                self.assertFalse(f.unbacked_claim)

    def test_codex_bookkeeping_after_cd_is_not_a_code_change(self):
        for cmd in ("/bin/bash -lc 'cd \"$HOUSE_DIR\" && echo x >> PROGRESS.md'",
                    f"/bin/bash -lc 'cd {self.contest}/.house && sed -i s/a/b/ tasks/T001-x.md'",
                    "/bin/bash -lc 'cd ../../.house && echo x >> PROGRESS.md'"):
            with self.subTest(cmd=cmd):
                f = self._after_green(cmd, codex=True)
                self.assertEqual(f.check_after_last_edit, "yes, GREEN")
                self.assertFalse(f.unbacked_claim)

    def test_code_write_after_cd_is_still_a_change(self):
        for cmd in ("cd src && sed -i 's/a/b/' app.py", f"cd {self.wt} && echo x > app.py",
                    "cd src && cd .. && echo x > app.py", f"cd {self.contest}/.house && echo x > {self.wt}/app.py",
                    f"cd {self.contest}/.house && cd $X && cd {self.wt}/src && rm app.py",
                    f"cd $X && echo x > /nowhere.txt && cd {self.wt}/src && rm app.py",
                    "bash -lc 'cd src && sed -i s/a/b/ app.py'", f"cd {self.wt}; rm app.py",
                    f"cd {self.contest}/.house && cd {self.wt} && git checkout -- app.py",
                    f"cd {self.contest}/.house && echo x > ../.worktrees/T001/app.py"):
            with self.subTest(cmd=cmd):
                self.assertEqual(self._after_green(cmd).check_after_last_edit, "no")
        self.assertEqual(self._after_green("/bin/bash -lc 'cd src && sed -i s/a/b/ app.py'",
                                           codex=True).check_after_last_edit, "no")

    def test_cd_only_moves_the_base_where_the_shell_would(self):
        root = str(self.contest)
        for cmd in ("(cd .house && echo x >> PROGRESS.md); echo y >> app.py",
                    "(cd .house && echo x >> PROGRESS.md) && sed -i s/a/b/ app.py",
                    "(cd .house; echo x >> PROGRESS.md);echo y >> app.py", "( cd .house ) ; echo y >> app.py",
                    "cd .house & echo y >> app.py", "cd .house && echo x >> PROGRESS.md & echo y >> app.py",
                    "cd .house || true; echo y >> app.py", "cd .house || cd .. ; echo y >> app.py",
                    "true || cd .house; echo y >> app.py", "cd .house | tee P2; echo y >> app.py",
                    "cd .house |& tee P2; echo y >> app.py", "echo a | cd .house; echo y >> app.py",
                    "(cd /x; (cd /y); echo z > t) && echo y >> app.py",
                    f"cd {root}/.house || cd $X; echo y >> app.py",
                    "bash -lc '(cd .house && echo x >> PROGRESS.md); echo y >> app.py'",
                    "cd $X || cd .house; echo y >> app.py",
                    # a `cd` after another command of an `&&` list may not have run once the list is over
                    f"false && cd {root}/.house; echo changed > app.py",
                    "true && cd .house; echo y >> app.py", "true && cd .house & echo y >> app.py",
                    "cd src && false && cd ../.house; echo y >> app.py"):
            with self.subTest(cmd=cmd):
                self.assertEqual(self._after_green(cmd).check_after_last_edit, "no")
        # the shell carries a `cd` of an `&&` / `;` list into later commands, a group or a pipeline stage it does not
        for cmd in ("cd .house && echo x >> PROGRESS.md | tee P2; echo y >> app.py",  # writes .house/app.py
                    "cd .house && (echo x >> PROGRESS.md) ; echo y >> app.py",
                    "(cd .house && echo x >> PROGRESS.md); cd .house; echo y >> app.py",
                    "cd .house && { echo x >> PROGRESS.md; }; echo y > a.py",
                    "cd .house; echo x | tee PROGRESS.md; echo y >> app.py",
                    "true && cd .house && echo y >> app.py", "cd .house && true && cd tasks; echo y >> app.py"):
            with self.subTest(cmd=cmd):
                self.assertEqual(self._after_green(cmd).check_after_last_edit, "yes, GREEN")

    def test_git_dir_that_cannot_be_told_does_not_count(self):
        for cmd in ("git -C $X checkout -- app.py", "git -C ~/x pull", "git -C `pwd` reset --hard",
                    "git -C $X stash", "git -C ~bob/x restore app.py", "git -C \"$HOUSE_DIR\" apply fix.patch",
                    f"git -C {self.contest} checkout -- app.py", f"git -C {self.contest} stash",
                    f"git -C {self.contest} restore --source=HEAD~1 app.py", f"git -C {self.contest} rm app.py"):
            with self.subTest(cmd=cmd):
                self.assertEqual(self._after_green(cmd).check_after_last_edit, "yes, GREEN")
        for cmd in (f"git -C {self.wt} checkout -- app.py", f"git -C {self.wt} stash", "git checkout -- app.py"):
            with self.subTest(cmd=cmd):
                self.assertEqual(self._after_green(cmd).check_after_last_edit, "no")

    def test_git_tree_write_on_the_contest_root_is_not_the_attempts_change(self):
        root = str(self.contest)
        for cmd in (f"git -C {root} pull", f"git -C {root} checkout main", f"git -C {root}/ merge task/T001-x",
                    f"cd {root} && git pull", f"git -C {root}/.worktrees/.. reset --hard"):
            with self.subTest(cmd=cmd):
                self.assertEqual(self._after_green(cmd).check_after_last_edit, "yes, GREEN")
        for cmd in (f"git -C {self.wt} checkout other", f"git -C {self.wt} pull", "git pull", "git -C . merge x",
                    f"git -C $X pull && git -C {self.wt} reset --hard"):
            with self.subTest(cmd=cmd):
                self.assertEqual(self._after_green(cmd).check_after_last_edit, "no")

    # E3, E4, E5: runs whose result is not the attempt's
    def test_background_check_is_unknown(self):
        call = [_tu("c1", "Bash", {"command": CHECK, "run_in_background": True}), _tr("c1", "started", False)]
        f = self.facts(*_edit("e1", f"{self.wt}/app.py"), *call)
        self.assertEqual(f.check_after_last_edit, "yes, unknown")

    def test_python_options_that_are_not_a_script_run(self):
        for cmd in ("python3 -V house check c T001 --worktree wt", "python3 --version house check c T001",
                    "python3 -h house check c T001", "python3 -c house check c T001",
                    "python3 -m house check c T001", "python3 - house check c T001",
                    "python3 -X dev house check c T001 --worktree wt"):
            with self.subTest(cmd=cmd):
                f = self.facts(*_edit("e1", f"{self.wt}/app.py"), *_bash("c1", cmd))
                self.assertEqual(f.check_after_last_edit, "no")

    def test_night_check_without_worktree_is_unknown(self):
        for cmd in ("house check c T001", "cd wt && house check c T001", "bash -lc 'house check c T001'"):
            with self.subTest(cmd=cmd):
                f = self.facts(*_edit("e1", f"{self.wt}/app.py"), *_bash("c1", cmd))
                self.assertEqual(f.check_after_last_edit, "yes, unknown")
        merge = self.facts(*_edit("e1", f"{self.wt}/app.py"), *_bash("c1", "house check c T001"), source="merge")
        self.assertEqual(merge.check_after_last_edit, "yes, GREEN")

    # E6: honest failures are not claims
    def test_failure_wording_and_template_are_not_a_claim(self):
        edit = _edit("e1", f"{self.wt}/app.py")
        for body in ("AC-1: fail (does not pass yet)", "AC-2: not green", "AC-1 passes, AC-2 doesn't pass",
                     "Each acceptance check from the task: pass / fail.",
                     "- Each acceptance check from the task: pass / fail."):
            with self.subTest(body=body):
                self.report(f"## Result vs acceptance\n{body}\n\n## Next\nok\n")
                self.assertFalse(self.facts(*edit).unbacked_claim)
        self.report("## Result vs acceptance\nAll criteria pass\n")
        self.assertTrue(self.facts(*edit).unbacked_claim)

    # E8: which report covers the attempt
    def test_missing_named_copy_is_unknown(self):
        self.report("## Result vs acceptance\nAll pass\n")
        f = self.facts(*_edit("e1", f"{self.wt}/app.py"), report=".house/reports/T001-attempt-1.md")
        self.assertIsNone(f.unbacked_claim)

    def test_live_report_older_than_the_attempt_is_unknown(self):
        import os
        self.report("## Result vs acceptance\nAll pass\n")
        live = self.house / "reports" / "T001.md"
        os.utime(live, (1_000_000_000, 1_000_000_000))  # 2001
        edit = _edit("e1", f"{self.wt}/app.py")
        self.assertIsNone(self.facts(*edit, start="2026-11-17T10:00:00").unbacked_claim)
        self.assertIsNone(self.facts(*edit, start="garbage").unbacked_claim)
        self.assertTrue(self.facts(*edit, start="2000-01-01T00:00:00").unbacked_claim)

    def test_model_prefers_main_thread(self):
        side = _tu("c3", "Bash", {"command": "ls"}, model="claude-haiku-4-5")
        side["parent_tool_use_id"] = "toolu_agent"
        side2 = _tu("c4", "Bash", {"command": "ls"}, model="claude-haiku-4-5")
        side2["parent_tool_use_id"] = "toolu_agent"
        f = self.facts(_tu("c1", "Bash", {"command": "ls"}, model="claude-opus-9"), side, side2)
        self.assertEqual(f.model, "claude-opus-9")
        only_side = self.facts(side, side2)
        self.assertEqual(only_side.model, "claude-haiku-4-5")

    # test edits
    def test_test_edits_patterns_and_protected(self):
        (self.house / "tasks").mkdir()
        (self.house / "tasks" / "T001-x.md").write_text("---\nid: T001\nprotected:\n  - fixtures/golden.json\n---\n")
        wt = f"{self.contest}/.worktrees/T001-x"
        f = self.facts(
            *_edit("e1", f"{wt}/tests/test_a.py"), *_edit("e2", f"{wt}/src/a_test.go"),
            *_edit("e3", f"{wt}/src/test_b.py", name="Write"), *_edit("e4", f"{wt}/fixtures/golden.json"),
            *_edit("e5", f"{wt}/src/app.py"), *_edit("e6", f"{wt}/tests/test_a.py", name="MultiEdit"),
            *_edit("e7", "tests/relative.py"), *_edit("e8", f"{wt}/tests/test_z.py", ok=True))
        self.assertEqual(f.test_edits, ["tests/test_a.py", "src/a_test.go", "src/test_b.py", "fixtures/golden.json",
                                        "tests/relative.py"])

    def test_test_edits_outside_the_contest_are_dropped(self):
        f = self.facts(*_edit("e1", "/elsewhere/tests/test_a.py"), *_edit("e2", "/home/u/proj/test_b.py"),
                       *_edit("e3", f"{self.wt}/tests/test_c.py"))
        self.assertEqual(f.test_edits, ["tests/test_c.py"])

    def test_codex_file_change_test_edit(self):
        wt = self.contest / ".worktrees" / "T001-x"
        f = self.facts({"type": "thread.started", "thread_id": "th"},
                       _codex_change("i1", [str(wt / "tests" / "test_a.py"), "src/app.py"]),
                       _codex_change("i2", ["tests/test_b.py"], status="failed"), tool="codex")
        self.assertEqual(f.test_edits, ["tests/test_a.py"])
        self.assertEqual(f.n_calls, 3)

    # guard refusals
    def test_guard_refusal_text_prefixed(self):
        text = "PreToolUse:Bash hook error: [python3 hook]: house guard: refusing `rm -rf /` (outside worktree)"
        f = self.facts(*_bash("c1", "rm -rf /", err=True, out=text), *_bash("c2", "ls", err=True, out="no such"),
                       *_bash("c3", "echo house guard:", err=False, out="house guard: not an error"))
        self.assertEqual(f.guard_refusals, 1)

    def test_claude_without_refusals_is_zero(self):
        self.assertEqual(self.facts(*_bash("c1", "ls")).guard_refusals, 0)

    def test_codex_guard_not_applicable(self):
        f = self.facts(_codex_cmd("i1", "ls", 1, "house guard: x"), tool="codex")
        self.assertIsNone(f.guard_refusals)

    # repeats
    def test_repeated_failing_command(self):
        cmd = "pytest tests/test_x.py -q"
        f = self.facts(*_bash("c1", cmd, err=True), *_bash("c2", cmd, err=True), *_bash("c3", "ls", err=True),
                       *_bash("c4", cmd, err=True), *_bash("c5", "make", err=True), *_bash("c6", "make", err=True),
                       *_bash("c7", cmd, err=False))
        self.assertEqual(f.repeats, [(cmd, 3)])

    def test_repeat_uses_redacted_command(self):
        a = "curl -H 'Authorization: Bearer sk-abcdefghijklmnopqrstuvwxyz123456' x"
        b = "curl -H 'Authorization: Bearer sk-zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz' x"
        f = self.facts(*_bash("c1", a, err=True), *_bash("c2", b, err=True), *_bash("c3", a, err=True))
        self.assertEqual(len(f.repeats), 1)
        self.assertNotIn("sk-", json.dumps(f.repeats))

    def test_dead_end_repeat_across_attempts(self):
        cmd = "pytest tests/test_x.py -q"
        old = self.write_log("a1.jsonl", *_bash("c1", cmd, err=True), *_bash("c2", "ls", err=True),
                             *_bash("c3", "make", err=False))
        new = self.write_log("a2.jsonl", *_bash("c1", cmd, err=True), *_bash("c2", "make", err=True),
                             *_bash("c3", "ls", err=False))
        self.house.joinpath("attempts.jsonl").write_text("".join(
            json.dumps(dataclasses.asdict(r)) + "\n" for r in (self.row(old, n=1), self.row(new, n=2))))
        f = evidence.log_facts(self.contest, self.row(new, n=2), evidence.Facts())
        self.assertEqual(f.dead_end_repeats, [cmd])
        first = evidence.log_facts(self.contest, self.row(old, n=1), evidence.Facts())
        self.assertEqual(first.dead_end_repeats, [])

    def test_dead_end_ignores_other_tasks_and_missing_logs(self):
        cmd = "pytest -q"
        new = self.write_log("a2.jsonl", *_bash("c1", cmd, err=True))
        other = self.write_log("o.jsonl", *_bash("c1", cmd, err=True))
        rows = [Row(task="T002", n=1, source="night", agent="w", tool="claude", log=other),
                self.row("", n=1, source="merge"), self.row(new, n=2)]
        self.house.joinpath("attempts.jsonl").write_text(
            "".join(json.dumps(dataclasses.asdict(r)) + "\n" for r in rows))
        f = evidence.log_facts(self.contest, self.row(new, n=2), evidence.Facts())
        self.assertEqual(f.dead_end_repeats, [])  # other tasks and rows without a worker log do not count

    def test_dead_end_unknown_when_an_earlier_log_is_missing(self):
        new = self.write_log("a2.jsonl", *_bash("c1", "pytest -q", err=True))
        rows = [self.row(".house/logs/gone.jsonl", n=1), self.row(new, n=2)]
        self.house.joinpath("attempts.jsonl").write_text(
            "".join(json.dumps(dataclasses.asdict(r)) + "\n" for r in rows))
        f = evidence.log_facts(self.contest, self.row(new, n=2), evidence.Facts())
        self.assertIsNone(f.dead_end_repeats)
        self.assertIn("commands that already failed in an earlier attempt: unknown",
                      evidence.render_full(f, self.row(new, n=2)))

    # calls, model, duration, tails
    def test_calls_model_and_counts(self):
        recs = [_tu("c1", "Bash", {"command": "ls\n  -la"}), _tr("c1"),
                _tu("c2", "Edit", {"file_path": "/w/a.py"}, model="claude-opus-9"), _tr("c2"),
                _tu("c3", "Agent", {"subagent_type": "Explore", "prompt": "CANARY"}, model="claude-opus-9"),
                _tu("c4", "Grep", {"pattern": "x"}, model="claude-opus-9")]
        f = self.facts(*recs)
        self.assertEqual(f.calls, ["Bash: ls -la", "Edit: /w/a.py", "Agent: Explore", "Grep"])
        self.assertEqual((f.n_calls, f.model), (4, "claude-opus-9"))

    def test_calls_capped_at_twenty(self):
        f = self.facts(*[r for i in range(23) for r in _bash(f"c{i}", f"echo {i}")])
        self.assertEqual(len(f.calls), 21)
        self.assertEqual((f.calls[0], f.calls[19], f.calls[20]), ("Bash: echo 0", "Bash: echo 19", "and 3 more"))
        self.assertEqual(f.n_calls, 23)

    def test_duration_from_row_then_events(self):
        rec = _bash("c1", "ls")
        f = self.facts(*rec, start="2026-11-17T10:00:00Z", end="2026-11-17T10:12:03Z")
        self.assertEqual(f.duration, "12m 03s")
        f = self.facts(*rec, start="2026-11-17T10:00:00+00:00", end="2026-11-17T11:02:05+00:00")
        self.assertEqual(f.duration, "1h 02m 05s")
        timed = [_tu("c1", "Bash", {"command": "ls"}, ts="2026-11-17T10:00:00Z"), _tr("c1"),
                 _tu("c2", "Bash", {"command": "ls"}, ts="2026-11-17T10:00:45Z")]
        self.assertEqual(self.facts(*timed, start="garbage").duration, "45s")
        self.assertEqual(self.facts(*rec).duration, "unknown")

    def test_error_tails(self):
        out = "\n".join(f"line {i}" for i in range(10)) + "\n" + "x" * 500 + "\ntoken sk-abcdefghijklmnopqrstuvwxyz123456"
        f = self.facts(*_bash("c1", "make", err=True, out=out), *_bash("c2", "ls", err=False, out="fine"))
        self.assertEqual(len(f.error_tails), 1)
        lines = f.error_tails[0].splitlines()
        self.assertEqual(len(lines), 5)
        self.assertTrue(all(len(l) <= 200 for l in lines))
        self.assertIn("[REDACTED]", lines[-1])
        self.assertNotIn("sk-abc", json.dumps(f.error_tails))
        self.assertEqual(lines[0], "line 7")

    def test_log_outside_contest_is_ignored(self):
        outside = self.contest.parent / "outside.jsonl"
        outside.write_text(json.dumps(_bash("c1", "ls")[0]) + "\n")
        self.addCleanup(outside.unlink)
        row = self.row("../outside.jsonl")
        self.assertEqual(evidence.log_facts(self.contest, row, evidence.Facts()), evidence.Facts())

    # privacy
    def test_canaries(self):
        token = "sk-abcdefghijklmnopqrstuvwxyz123456"
        records = [
            _tu("t1", "Write", {"file_path": "/a/b.py", "content": "CANARY-WRITE"}),
            _tu("t2", "Agent", {"subagent_type": "Explore", "description": "look", "prompt": "CANARY-PROMPT"}),
            _tr("t1", "CANARY-RESULT"), _text("CANARY-TEXT"),
            _tu("t3", "Bash", {"command": f'curl -H "Authorization: Bearer {token}" x', "description": "CANARY-DESC"}),
            _tr("t3", "CANARY-RESULT-2", is_error=False)]
        f = self.facts(*records)
        text = json.dumps(dataclasses.asdict(f))
        for bad in ("CANARY", token, "sk-abc"):
            self.assertNotIn(bad, text)
        self.assertIn("[REDACTED]", text)
        self.assertIn("curl", text)
        self.assertEqual(f.n_calls, 3)


# ---- rendering ---------------------------------------------------------------------------------------------------
class RenderTest(unittest.TestCase):
    ROW = Row(task="T007", n=2, source="night", agent="worker", tool="claude", outcome="failed",
              message="CANARY-MESSAGE")

    def busy(self) -> evidence.Facts:
        return evidence.Facts(
            questions_added=["Q-002"], diffstat=" a.py | 2 +-\n 1 file changed", last_check="RED (AC-1)",
            violations=["edited protected file x"], check_after_last_edit="yes, RED", unbacked_claim=True,
            test_edits=["tests/test_a.py", "tests/test_b.py"], guard_refusals=2, repeats=[("pytest -x", 3)],
            dead_end_repeats=["make all"], calls=["Bash: pytest -x", "Edit: a.py"],
            error_tails=["line1\nline2"], model="claude-haiku-4-5", duration="2m 03s", n_calls=2)

    def test_full_header_and_facts(self):
        text = evidence.render_full(self.busy(), self.ROW)
        self.assertEqual(text.splitlines()[0], "T007 attempt 2 \u00b7 night \u00b7 claude \u00b7 failed")
        for want in ("check after last edit: yes, RED", "claim without backing in the log: yes",
                     "test files edited: tests/test_a.py, tests/test_b.py", "guard refusals: 2",
                     "pytest -x", "make all", "Q-002", "last check: RED (AC-1)", "1 file changed",
                     "edited protected file x", "Bash: pytest -x", "line2", "claude-haiku-4-5", "2m 03s",
                     "number of calls: 2"):
            self.assertIn(want, text)
        self.assertNotIn("CANARY", text)  # the register note is not printed

    def test_full_unknowns(self):
        text = evidence.render_full(evidence.Facts(), self.ROW)
        self.assertIn("check after last edit: unknown", text)
        self.assertIn("claim without backing in the log: unknown", text)
        self.assertIn("guard refusals: unknown", text)
        self.assertIn("last check: unknown", text)
        codex = dataclasses.replace(self.ROW, tool="codex")
        self.assertIn("guard refusals: not applicable", evidence.render_full(evidence.Facts(), codex))
        clean = evidence.Facts(unbacked_claim=False, guard_refusals=0)
        self.assertIn("claim without backing in the log: no", evidence.render_full(clean, self.ROW))
        self.assertIn("guard refusals: 0", evidence.render_full(clean, self.ROW))

    def test_full_has_no_judging_words(self):
        text = evidence.render_full(self.busy(), self.ROW).lower()
        for word in ("probably", "seems", "should", "likely", "suspicious"):
            self.assertNotIn(word, text)

    def test_short_form_at_most_five_lines(self):
        lines = evidence.render_short(self.busy(), self.ROW)
        self.assertLessEqual(len(lines), 5)
        self.assertEqual(lines, [
            "check after last edit: yes, RED", "claim without backing in the log", "guard refusals: 2",
            "repeated failing commands: 1", "test files edited: tests/test_a.py, tests/test_b.py"])

    def test_short_form_only_non_empty_lines_and_first_always(self):
        self.assertEqual(evidence.render_short(evidence.Facts(), self.ROW), ["check after last edit: unknown"])
        facts = evidence.Facts(check_after_last_edit="no", guard_refusals=0, unbacked_claim=False)
        self.assertEqual(evidence.render_short(facts, self.ROW), ["check after last edit: no"])

    def test_short_form_codex_not_applicable_and_long_paths_cut(self):
        codex = dataclasses.replace(self.ROW, tool="codex")
        facts = evidence.Facts(test_edits=[f"tests/test_{i:03d}_{'x' * 20}.py" for i in range(10)])
        lines = evidence.render_short(facts, codex)
        self.assertEqual(lines[1], "guard refusals: not applicable")
        self.assertLessEqual(len(lines[2]), len("test files edited: ") + 103)
        self.assertTrue(lines[2].endswith("..."))

    def test_session_form_has_no_attempt_or_state_lines(self):
        text = evidence.render_full(self.busy(), None)
        self.assertNotIn("attempt", text)
        self.assertNotIn("last check", text)
        self.assertNotIn("check after last edit", text)
        self.assertNotIn("diffstat", text)
        self.assertIn("guard refusals: 2", text)
        self.assertIn("pytest -x", text)
        self.assertIn("claude-haiku-4-5", text)
        self.assertIn("guard refusals: unknown", evidence.render_full(evidence.Facts(), None))

    def test_session_facts_from_events(self):
        records = [*(r for i in range(3) for r in _bash(f"b{i}", "pytest -x", err=True, out="boom")),
                   _tu("g1", "Edit", {"file_path": "/w/a.py"}), _tr("g1", "house guard: no", is_error=True)]
        path = self._write(records)
        facts = evidence.session_facts(transcript_events(path))
        self.assertEqual(facts.n_calls, 4)
        self.assertEqual(facts.guard_refusals, 1)
        self.assertEqual(facts.repeats, [("pytest -x", 3)])
        self.assertEqual(facts.model, "claude-haiku-4-5")
        self.assertEqual((facts.check_after_last_edit, facts.last_check, facts.unbacked_claim, facts.test_edits),
                         (evidence.UNKNOWN, evidence.UNKNOWN, None, []))

    def _write(self, records) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / f"{SESSION}.jsonl"
        path.write_text("\n".join(json.dumps(r) for r in records) + "\n")
        return path


def transcript_events(path: Path):
    from houselib import transcript
    return transcript.claude_stream(path).events


if __name__ == "__main__":
    unittest.main()
