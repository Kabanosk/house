"""`houselib.evals.grade`: the deterministic grader (synthetic stream logs, tiny real git repos)."""
import dataclasses
import json
import os
import re
import subprocess
import tempfile
import threading
import unittest
import zlib
from pathlib import Path
from types import SimpleNamespace

from houselib.evals import cases, grade, sandbox
from houselib.evals.cases import Case
from houselib.evals.runner import RunResult

CANARY = "CANARY-9f3a7c-session-content"


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.name=T", "-c", "user.email=t@example.com", *args], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout


def call(label, tool, input_match="", min=1, max=None) -> cases.Call:
    import re
    return cases.Call(label, tool, input_match, min, max, re.compile(input_match))


def make_case(**kw) -> Case:
    base = dict(name="c", path=Path("c.json"), agent="coder", skill=None, model="haiku", fixture={"name": "x"},
                prompt="p", max_turns=40, timeout_seconds=30, tool="claude")
    base.update(kw)
    return Case(**base)


class Log:
    """Builds a Claude stream-json log: each `turn` is one assistant message (usage + optional tool calls)."""

    def __init__(self, terminal: bool = True):
        self.lines: list[dict] = []
        self.n = 0
        self.terminal = terminal  # `write` appends a successful final `result` when the log has none

    def turn(self, *tools: tuple[str, dict], usage=(10, 5, 2, 3), error=False, result_error=False,
             sidechain=False, results=True) -> "Log":
        self.n += 1
        content = [{"type": "tool_use", "id": f"t{self.n}-{i}", "name": name, "input": inp}
                   for i, (name, inp) in enumerate(tools)]
        i, cr, cw, out = usage
        self.lines.append({"type": "assistant", "request_id": f"r{self.n}",
                           **({"parent_tool_use_id": "toolu_parent"} if sidechain else {}), "message": {
            "id": f"m{self.n}", "model": "claude-x", "content": content or [{"type": "text", "text": CANARY}],
            "usage": {"input_tokens": i, "cache_read_input_tokens": cr, "cache_creation_input_tokens": cw,
                      "output_tokens": out}}})
        for k, (name, inp) in enumerate(tools if results else ()):
            self.lines.append({"type": "user", "message": {"content": [{
                "type": "tool_result", "tool_use_id": f"t{self.n}-{k}", "content": CANARY,
                "is_error": result_error}]}})
        return self

    def bash(self, command: str, **kw) -> "Log":
        return self.turn(("Bash", {"command": command}), **kw)

    def result(self, text: str, is_error: bool, subtype: str = "success") -> "Log":
        self.lines.append({"type": "result", "subtype": subtype, "is_error": is_error, "result": text})
        return self

    def write(self, path: Path) -> Path:
        lines = self.lines
        if self.terminal and not any(line["type"] == "result" for line in lines):
            lines = [*lines, {"type": "result", "subtype": "success", "is_error": False, "result": "done"}]
        path.write_text("".join(json.dumps(line) + "\n" for line in lines))
        return path


class GradeBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name).resolve()
        self.contest = self.tmp / "contest"
        self.contest.mkdir()
        git(self.contest, "init", "-q", "-b", "main")
        (self.contest / ".house/tasks").mkdir(parents=True)
        (self.contest / ".house/tasks/T001-calc.md").write_text(
            "---\nid: T001\ntitle: calc\nstatus: todo\n---\nbody\n")
        (self.contest / ".house/QUESTIONS.md").write_text(
            "# Questions\n\n## Q-001 [open] old\n- **Poziom:** zwykły\n\n## Q-002 [answered 2026-01-01] done\n")
        (self.contest / "keep.txt").write_text("keep\n")
        git(self.contest, "add", "-A")
        git(self.contest, "commit", "-q", "-m", "init")
        self.initial = git(self.contest, "rev-parse", "HEAD").strip()
        self.root = SimpleNamespace(root=self.tmp, contest=self.contest, name="contest",
                                    source_commit="x", info={"initial_commit": self.initial},
                                    baseline=sandbox.tracked_hashes(self.contest, self.initial),
                                    baseline_questions=sandbox.tracked_file(self.contest, self.initial,
                                                                            sandbox.QUESTIONS_FILE))

    def run_result(self, log: Log | None = None, **kw) -> RunResult:
        path = (log or Log()).write(self.tmp / "run.jsonl")
        base = dict(log=path, exit=0, duration=12.5, infra=None, timed_out=False, session_id="s", out_dir=None)
        base.update(kw)
        return RunResult(**base)

    def grade(self, case: Case, log: Log | None = None, **kw) -> grade.Grade:
        return grade.grade(case, self.run_result(log, **kw), self.root)

    def pred(self, **p) -> Case:
        return make_case(outcome=[p])


class CallsTest(GradeBase):
    def merge_case(self, **kw):
        return make_case(expect=[call("merge", "Bash", '"command": "house merge', 1, 1)], **kw)

    def test_expect_satisfied(self):
        g = self.grade(self.merge_case(), Log().bash("house merge c T001"))
        self.assertEqual((g.result, g.missing), ("pass", []))

    def test_expect_below_min(self):
        g = self.grade(self.merge_case(), Log().bash("ls"))
        self.assertEqual(g.result, "fail")
        self.assertEqual(len(g.missing), 1)
        self.assertIn("merge", g.missing[0])
        self.assertIn("0", g.missing[0])

    def test_expect_above_max(self):
        g = self.grade(self.merge_case(), Log().bash("house merge c T001").bash("house merge c T002"))
        self.assertEqual(g.result, "fail")
        self.assertIn("merge", g.missing[0])
        self.assertIn("2", g.missing[0])

    def test_min_zero_allows_absence(self):
        case = make_case(expect=[call("opt", "Bash", "zzz", 0, 1)])
        self.assertEqual(self.grade(case, Log().bash("ls")).result, "pass")

    def test_superset_other_calls_fine(self):
        log = Log().bash("ls").turn(("Read", {"file_path": "/x"})).bash("house merge c T001").bash("git status")
        self.assertEqual(self.grade(self.merge_case(), log).result, "pass")

    def test_tool_name_must_equal(self):
        case = make_case(expect=[call("e", "Edit", "")])
        self.assertEqual(self.grade(case, Log().bash("ls")).result, "fail")

    def test_input_is_the_viewed_one(self):
        # `Call.matches` sees the whitelisted input: file_path for Edit, not the edit content
        case = make_case(expect=[call("e", "Edit", CANARY)])
        log = Log().turn(("Edit", {"file_path": "/a.py", "new_string": CANARY}))
        self.assertEqual(self.grade(case, log).result, "fail")

    def test_expect_found_beyond_the_300_char_view_cut(self):
        long_cmd = "echo " + "x" * 400 + "\nhouse check c T001\ngit commit -am done"
        g = self.grade(make_case(expect=[call("check", "Bash", "house check")]), Log().bash(long_cmd))
        self.assertEqual((g.result, g.missing), ("pass", []))

    def test_forbid_found_beyond_the_300_char_view_cut(self):
        long_cmd = "echo " + "x" * 400 + "\ngit merge task/T001"
        case = make_case(forbid=[call("raw-merge", "Bash", "git merge")])
        g = self.grade(case, Log().bash(long_cmd))
        self.assertEqual((g.result, g.forbidden), ("fail", ["raw-merge: 1"]))
        self.assertNotIn("xxxx", json.dumps(dataclasses.asdict(g)))

    def test_long_command_secret_is_redacted_before_matching(self):
        key = "sk-ant-api03-" + "C" * 40
        case = make_case(expect=[call("leak", "Bash", "sk-ant-api03", 0, 0)])
        self.assertEqual(self.grade(case, Log().bash("echo " + "x" * 400 + " " + key)).result, "pass")

    def test_forbid_bash_git_merge(self):
        case = make_case(forbid=[call("raw-merge", "Bash", "git merge")])
        g = self.grade(case, Log().bash("git merge task/T001"))
        self.assertEqual((g.result, g.forbidden), ("fail", ["raw-merge: 1"]))

    def test_forbid_edit_under_checks(self):
        case = make_case(forbid=[call("edit-checks", "Edit", r"\.house/checks/")])
        log = Log().turn(("Edit", {"file_path": "/c/.house/checks/T001.json", "new_string": "x"}))
        g = self.grade(case, log)
        self.assertEqual((g.result, g.forbidden), ("fail", ["edit-checks: 1"]))

    def test_forbid_min_threshold(self):
        case = make_case(forbid=[call("f", "Bash", "git add", 2)])
        self.assertEqual(self.grade(case, Log().bash("git add a")).result, "pass")
        self.assertEqual(self.grade(case, Log().bash("git add a").bash("git add b")).result, "fail")

    def test_forbid_absent_passes(self):
        case = make_case(forbid=[call("raw-merge", "Bash", "git merge")])
        g = self.grade(case, Log().bash("house merge c T001"))
        self.assertEqual((g.result, g.forbidden), ("pass", []))

    def test_codex_log(self):
        path = self.tmp / "codex.jsonl"
        events = [{"type": "thread.started", "thread_id": "th"},
                  {"type": "item.completed", "item": {"id": "i1", "type": "command_execution",
                                                      "command": "house merge c T001", "exit_code": 0,
                                                      "aggregated_output": CANARY}},
                  {"type": "turn.completed", "usage": {"input_tokens": 100, "cached_input_tokens": 40,
                                                       "output_tokens": 7}}]
        path.write_text("".join(json.dumps(e) + "\n" for e in events))
        run = RunResult(path, 0, 3.0, None, False, "th")
        case = self.merge_case(tool="codex", agent="coder-codex")
        g = grade.grade(case, run, self.root)
        self.assertEqual((g.result, g.turns), ("pass", 1))
        self.assertEqual(g.tokens, {"input": 60, "cache_read": 40, "cache_write": 0, "output": 7})


class CallOkTest(GradeBase):
    def ok_case(self):
        return make_case(expect=[cases.Call("merge", "Bash", "house merge", 1, 1, re.compile("house merge"), ok=True)])

    def test_successful_call_counts(self):
        self.assertEqual(self.grade(self.ok_case(), Log().bash("house merge c T001")).result, "pass")

    def test_failed_call_does_not_count(self):
        g = self.grade(self.ok_case(), Log().bash("house merge c T001", result_error=True))
        self.assertEqual((g.result, g.missing), ("fail", ["merge: 0 (min 1, max 1)"]))

    def test_call_without_a_result_does_not_count(self):
        g = self.grade(self.ok_case(), Log().bash("house merge c T001", results=False))
        self.assertEqual(g.result, "fail")

    def test_failed_then_successful_call(self):
        log = Log().bash("house merge c T001", result_error=True).bash("house merge c T001")
        self.assertEqual(self.grade(self.ok_case(), log).result, "pass")

    def test_codex_exit_code(self):
        path = self.tmp / "codex.jsonl"
        def item(i, code):
            return {"type": "item.completed", "item": {"id": i, "type": "command_execution",
                                                       "command": "house merge c T001", "exit_code": code}}
        events = [item("a", 1), {"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 1}}]
        path.write_text("".join(json.dumps(e) + "\n" for e in events))
        case = dataclasses.replace(self.ok_case(), tool="codex", agent="coder-codex")
        self.assertEqual(grade.grade(case, RunResult(path, 0, 1.0, None, False, "th"), self.root).result, "fail")
        events[0] = item("a", 0)
        path.write_text("".join(json.dumps(e) + "\n" for e in events))
        self.assertEqual(grade.grade(case, RunResult(path, 0, 1.0, None, False, "th"), self.root).result, "pass")


class OrderTest(GradeBase):
    def order_case(self):
        return make_case(expect=[call("check", "Bash", "house check", 0), call("merge", "Bash", "house merge", 0)],
                         order=[("check", "merge")])

    def test_correct_order(self):
        g = self.grade(self.order_case(), Log().bash("house check c T001").bash("house merge c T001"))
        self.assertEqual((g.result, g.order_violations), ("pass", []))

    def test_wrong_order(self):
        g = self.grade(self.order_case(), Log().bash("house merge c T001").bash("house check c T001"))
        self.assertEqual((g.result, g.order_violations), ("fail", ["check<merge"]))

    def test_first_occurrence_counts(self):
        log = Log().bash("house check c T001").bash("house merge c T001").bash("house check c T001")
        self.assertEqual(self.grade(self.order_case(), log).order_violations, [])
        log = Log().bash("house merge c T001").bash("house check c T001").bash("house merge c T001")
        self.assertEqual(self.grade(self.order_case(), log).order_violations, ["check<merge"])

    def test_before_absent_after_present_is_a_violation(self):
        g = self.grade(self.order_case(), Log().bash("house merge c T001"))
        self.assertEqual((g.result, g.order_violations), ("fail", ["check<merge"]))

    def test_both_absent_is_no_violation(self):
        g = self.grade(self.order_case(), Log().bash("ls"))
        self.assertEqual((g.result, g.order_violations), ("pass", []))

    def test_before_present_after_absent_is_no_violation(self):
        g = self.grade(self.order_case(), Log().bash("house check c T001"))
        self.assertEqual((g.result, g.order_violations), ("pass", []))

    def test_required_after_absent_is_reported_by_missing_not_order(self):
        case = make_case(expect=[call("check", "Bash", "house check"), call("merge", "Bash", "house merge")],
                         order=[("check", "merge")])
        g = self.grade(case, Log().bash("house check c T001"))
        self.assertEqual((g.result, g.order_violations, len(g.missing)), ("fail", [], 1))

    def test_same_call_matching_both_is_a_violation(self):
        case = make_case(expect=[call("a", "Bash", "x", 0), call("b", "Bash", "x", 0)], order=[("a", "b")])
        self.assertEqual(self.grade(case, Log().bash("x")).order_violations, ["a<b"])

    def test_order_with_a_forbid_label(self):
        case = make_case(expect=[call("check", "Bash", "house check", 0)],
                         forbid=[call("raw", "Bash", "git merge")], order=[("check", "raw")])
        g = self.grade(case, Log().bash("git merge b"))
        self.assertEqual(g.order_violations, ["check<raw"])
        self.assertEqual(g.forbidden, ["raw: 1"])


class PredicateTest(GradeBase):
    def ok(self, **p) -> bool:
        g = self.grade(self.pred(**p))
        return g.result == "pass"

    def test_git_branch(self):
        git(self.contest, "branch", "feat/x")
        self.assertTrue(self.ok(type="git_branch", name="feat/x", exists=True))
        self.assertFalse(self.ok(type="git_branch", name="feat/x", exists=False))
        self.assertTrue(self.ok(type="git_branch", name="nope", exists=False))
        self.assertFalse(self.ok(type="git_branch", name="nope", exists=True))

    def test_git_branch_name_is_not_an_option(self):
        self.assertFalse(self.ok(type="git_branch", name="--all", exists=True))

    def merge_on_main(self, message="merge T001"):
        git(self.contest, "checkout", "-q", "-b", "task/T001")
        (self.contest / "a.txt").write_text("a")
        git(self.contest, "add", "-A")
        git(self.contest, "commit", "-q", "-m", "work")
        git(self.contest, "checkout", "-q", "main")
        git(self.contest, "merge", "-q", "--no-ff", "-m", message, "task/T001")

    def test_git_merge_commit_on(self):
        self.assertFalse(self.ok(type="git_merge_commit_on", branch="main"))
        self.merge_on_main("merge T001 (task/T001)")
        self.assertTrue(self.ok(type="git_merge_commit_on", branch="main"))
        self.assertTrue(self.ok(type="git_merge_commit_on", branch="main", message_match=r"merge T001"))
        self.assertFalse(self.ok(type="git_merge_commit_on", branch="main", message_match=r"^zzz"))
        self.assertFalse(self.ok(type="git_merge_commit_on", branch="task/T001"))
        self.assertFalse(self.ok(type="git_merge_commit_on", branch="missing"))

    def test_git_merge_commit_before_the_initial_commit_does_not_count(self):
        self.merge_on_main()
        self.root.info["initial_commit"] = git(self.contest, "rev-parse", "HEAD").strip()
        self.assertFalse(self.ok(type="git_merge_commit_on", branch="main"))

    def test_git_clean(self):
        self.assertTrue(self.ok(type="git_clean"))
        (self.contest / ".tmp-eval").mkdir()
        (self.contest / ".tmp-eval/x").write_text("x")
        self.assertTrue(self.ok(type="git_clean"))
        (self.contest / "dirty.txt").write_text("x")
        self.assertFalse(self.ok(type="git_clean"))

    def test_git_clean_modified_tracked_file(self):
        (self.contest / "keep.txt").write_text("changed\n")
        self.assertFalse(self.ok(type="git_clean"))

    def test_file_exists(self):
        self.assertTrue(self.ok(type="file_exists", path="keep.txt", exists=True))
        self.assertFalse(self.ok(type="file_exists", path="keep.txt", exists=False))
        self.assertTrue(self.ok(type="file_exists", path="nope.txt", exists=False))
        self.assertFalse(self.ok(type="file_exists", path="nope.txt", exists=True))

    def test_file_unchanged(self):
        self.assertTrue(self.ok(type="file_unchanged", path="keep.txt"))
        (self.contest / "keep.txt").write_text("edited\n")
        self.assertFalse(self.ok(type="file_unchanged", path="keep.txt"))

    def test_file_unchanged_compares_with_the_initial_commit_not_head(self):
        (self.contest / "keep.txt").write_text("edited\n")
        git(self.contest, "commit", "-qam", "edit")
        self.assertFalse(self.ok(type="file_unchanged", path="keep.txt"))

    def test_file_unchanged_deleted_or_new(self):
        (self.contest / "keep.txt").unlink()
        self.assertFalse(self.ok(type="file_unchanged", path="keep.txt"))
        (self.contest / "new.txt").write_text("x")
        self.assertFalse(self.ok(type="file_unchanged", path="new.txt"))

    def task_branch(self, content: str | None = "edited\n", tag_initial: bool = False):
        """`task/T001` with one commit; `content` replaces keep.txt there (None deletes it)."""
        git(self.contest, "checkout", "-q", "-b", "task/T001")
        if content is None:
            git(self.contest, "rm", "-q", "keep.txt")
        else:
            (self.contest / "keep.txt").write_text(content)
        (self.contest / "work.txt").write_text("w")
        git(self.contest, "add", "-A")
        git(self.contest, "commit", "-q", "-m", "work")
        git(self.contest, "checkout", "-q", "main")
        if tag_initial:
            git(self.contest, "tag", "task/T001", self.initial)

    def test_file_unchanged_on_a_ref(self):
        self.assertFalse(self.ok(type="file_unchanged", path="keep.txt", ref="task/T001"))
        self.assertTrue(self.ok(type="file_unchanged", path="keep.txt", ref="task/T001", ref_optional=True))
        self.task_branch("keep\n")
        self.assertTrue(self.ok(type="file_unchanged", path="keep.txt", ref="task/T001"))

    def test_file_unchanged_on_a_ref_sees_the_branch_edit(self):
        self.task_branch()
        self.assertTrue(self.ok(type="file_unchanged", path="keep.txt"))  # main is untouched
        self.assertFalse(self.ok(type="file_unchanged", path="keep.txt", ref="task/T001"))
        self.assertFalse(self.ok(type="file_unchanged", path="keep.txt", ref="task/T001", ref_optional=True))

    def test_file_unchanged_on_a_ref_deleted_file(self):
        self.task_branch(None)
        self.assertFalse(self.ok(type="file_unchanged", path="keep.txt", ref="task/T001", ref_optional=True))

    def test_file_unchanged_ref_is_a_branch_not_a_same_named_tag(self):
        self.task_branch(tag_initial=True)
        self.assertFalse(self.ok(type="file_unchanged", path="keep.txt", ref="task/T001"))

    def test_file_unchanged_in_a_worktree(self):
        self.assertTrue(self.ok(type="file_unchanged", path="keep.txt", worktree=".worktrees/T001"))
        wt = self.contest / ".worktrees/T001"
        git(self.contest, "worktree", "add", "-q", str(wt), "-b", "task/T001")
        self.assertTrue(self.ok(type="file_unchanged", path="keep.txt", worktree=".worktrees/T001"))
        (wt / "keep.txt").write_text("edited\n")
        self.assertTrue(self.ok(type="file_unchanged", path="keep.txt"))
        self.assertFalse(self.ok(type="file_unchanged", path="keep.txt", worktree=".worktrees/T001"))
        (wt / "keep.txt").unlink()
        self.assertFalse(self.ok(type="file_unchanged", path="keep.txt", worktree=".worktrees/T001"))

    def test_file_unchanged_worktree_outside_the_contest(self):
        (self.tmp / "wt").mkdir()
        (self.tmp / "wt/keep.txt").write_text("keep\n")
        for wt in ("../wt", str(self.tmp / "wt")):
            g = self.grade(self.pred(type="file_unchanged", path="keep.txt", worktree=wt))
            self.assertEqual(g.result, "fail", wt)
            self.assertIn("path outside contest", g.note)

    def test_git_branch_ahead(self):
        self.assertFalse(self.ok(type="git_branch_ahead", branch="task/T001", min=1))
        git(self.contest, "branch", "task/T001")
        self.assertFalse(self.ok(type="git_branch_ahead", branch="task/T001", min=1))
        git(self.contest, "branch", "-D", "task/T001")
        self.task_branch()
        self.assertTrue(self.ok(type="git_branch_ahead", branch="task/T001", min=1))
        self.assertFalse(self.ok(type="git_branch_ahead", branch="task/T001", min=2))

    def write_checks(self, passes):
        path = self.contest / ".house/checks/T001.json"
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps({"frozen": {"criteria": [{"id": "AC-1", "kind": "run"}]},
                                    "results": {} if passes is None else {"AC-1": {"passes": passes}}}))

    def test_check_state(self):
        self.assertFalse(self.ok(type="check_state", task="T001", state="GREEN"))
        self.write_checks(None)
        self.assertFalse(self.ok(type="check_state", task="T001", state="GREEN"))
        self.write_checks(False)
        self.assertFalse(self.ok(type="check_state", task="T001", state="GREEN"))
        self.assertTrue(self.ok(type="check_state", task="T001", state="RED"))
        self.write_checks(True)
        self.assertTrue(self.ok(type="check_state", task="T001", state="GREEN"))
        self.assertFalse(self.ok(type="check_state", task="T*", state="GREEN"))
        self.assertFalse(self.ok(type="check_state", task="../checks/T001", state="GREEN"))

    def overwrite_object(self, blob: str, content: bytes):
        """Replace a loose object's bytes, as an agent with write access to `.git/objects` could."""
        path = self.contest / ".git/objects" / blob[:2] / blob[2:]
        path.chmod(0o644)
        path.write_bytes(zlib.compress(b"blob %d\0" % len(content) + content))

    def test_file_unchanged_does_not_trust_a_rewritten_initial_object(self):
        blob = git(self.contest, "rev-parse", f"{self.initial}:keep.txt").strip()
        (self.contest / "keep.txt").write_text("edited\n")
        self.overwrite_object(blob, b"edited\n")
        self.assertFalse(self.ok(type="file_unchanged", path="keep.txt"))
        wt = self.contest / ".worktrees/T001"
        git(self.contest, "worktree", "add", "-q", str(wt), "-b", "task/T001")
        self.assertFalse(self.ok(type="file_unchanged", path="keep.txt", worktree=".worktrees/T001"))
        self.assertFalse(self.ok(type="file_unchanged", path="keep.txt", ref="task/T001"))

    def test_file_unchanged_on_a_ref_does_not_trust_a_rewritten_branch_object(self):
        self.task_branch()
        blob = git(self.contest, "rev-parse", "task/T001:keep.txt").strip()
        self.overwrite_object(blob, b"keep\n")
        self.assertFalse(self.ok(type="file_unchanged", path="keep.txt", ref="task/T001"))

    def test_file_unchanged_without_a_baseline_fails_with_a_note(self):
        del self.root.baseline
        g = self.grade(self.pred(type="file_unchanged", path="keep.txt"))
        self.assertEqual(g.result, "fail")
        self.assertIn("no baseline", g.note)
        self.root.baseline = {}
        self.assertIn("no baseline", self.grade(self.pred(type="file_unchanged", path="keep.txt")).note)

    def test_path_escape_fails_with_note(self):
        outside = self.tmp / "outside.txt"
        outside.write_text("x")
        for p in ("../outside.txt", str(outside), "a/../../outside.txt"):
            g = self.grade(self.pred(type="file_exists", path=p, exists=True))
            self.assertEqual(g.result, "fail", p)
            self.assertIn("path outside contest", g.note)
            # a path escape never passes, not even for "must not exist" or unchanged
            self.assertEqual(self.grade(self.pred(type="file_exists", path=p, exists=False)).result, "fail", p)
            self.assertEqual(self.grade(self.pred(type="file_unchanged", path=p)).result, "fail", p)

    def test_symlink_out_of_the_contest_is_an_escape(self):
        (self.tmp / "secret.txt").write_text("x")
        (self.contest / "link").symlink_to(self.tmp / "secret.txt")
        g = self.grade(self.pred(type="file_exists", path="link", exists=True))
        self.assertEqual(g.result, "fail")
        self.assertIn("path outside contest", g.note)

    def test_task_status(self):
        self.assertTrue(self.ok(type="task_status", task="T001", status="todo"))
        self.assertFalse(self.ok(type="task_status", task="T001", status="done"))
        self.assertFalse(self.ok(type="task_status", task="T999", status="todo"))
        (self.contest / ".house/tasks/T001-calc.md").write_text("---\nid: T001\nstatus: done\n---\n")
        self.assertTrue(self.ok(type="task_status", task="T001", status="done"))

    def test_task_status_unparsable_file_fails(self):
        (self.contest / ".house/tasks/T001-calc.md").write_text("no frontmatter")
        self.assertFalse(self.ok(type="task_status", task="T001", status="todo"))

    def test_task_id_is_not_a_glob(self):
        self.assertFalse(self.ok(type="task_status", task="T*", status="todo"))
        self.assertFalse(self.ok(type="task_status", task="../x", status="todo"))

    def test_open_questions(self):
        self.assertTrue(self.ok(type="open_questions", count=1))
        self.assertFalse(self.ok(type="open_questions", count=0))
        self.assertTrue(self.ok(type="open_questions", min=1))
        self.assertTrue(self.ok(type="open_questions", max=1))
        self.assertFalse(self.ok(type="open_questions", min=2))
        self.assertFalse(self.ok(type="open_questions", max=0))
        self.assertTrue(self.ok(type="open_questions", min=1, max=3))

    def test_open_questions_without_file_counts_zero(self):
        (self.contest / ".house/QUESTIONS.md").unlink()
        self.assertTrue(self.ok(type="open_questions", count=0))

    def test_failed_predicates_are_named(self):
        case = make_case(outcome=[{"type": "git_clean"}, {"type": "file_exists", "path": "nope", "exists": True}])
        g = self.grade(case)
        self.assertEqual(g.failed_predicates, ["outcome[1] file_exists"])

    def test_unreachable_git_state_fails_not_raises(self):
        self.root.contest = self.tmp / "gone"
        g = self.grade(make_case(outcome=[{"type": "git_clean"}]))
        self.assertEqual(g.result, "fail")


class GitTamperTest(GradeBase):
    """The contest's `.git` is agent-writable: host git must never run what the agent planted there."""

    def setUp(self):
        super().setUp()
        self.marker = self.tmp / "planted-command-ran"
        self.script = self.tmp / "planted.sh"
        self.script.write_text(f"#!/bin/sh\ntouch {self.marker}\nexit 1\n")
        self.script.chmod(0o755)

    def assertTampered(self, case: Case):
        g = self.grade(case)
        self.assertFalse(self.marker.exists(), "the planted command ran on the host")
        self.assertEqual(g.result, "fail")
        self.assertIn("git tampered", g.note)
        return g

    def merge_on_main(self):
        git(self.contest, "checkout", "-q", "-b", "task/T001")
        (self.contest / "a.txt").write_text("a")
        git(self.contest, "add", "-A")
        git(self.contest, "commit", "-q", "-m", "work")
        git(self.contest, "checkout", "-q", "main")
        git(self.contest, "merge", "-q", "--no-ff", "-m", "merge T001", "task/T001")

    def sign_head(self) -> str:
        """Rewrite main's tip as a commit with a (fake) signature header, so a verifying `git log` calls gpg."""
        head, _, msg = git(self.contest, "cat-file", "commit", "HEAD").partition("\n\n")
        sig = "gpgsig -----BEGIN PGP SIGNATURE-----\n \n iQEzBAABCAAdFiEE\n -----END PGP SIGNATURE-----"
        sha = subprocess.run(["git", "hash-object", "-t", "commit", "-w", "--stdin"], cwd=self.contest, check=True,
                             input=f"{head}\n{sig}\n\n{msg}", capture_output=True, text=True).stdout.strip()
        git(self.contest, "update-ref", "refs/heads/main", sha)
        return sha

    def replace_keep(self):
        """Edit keep.txt and plant a replace ref that makes the initial commit's keep.txt look edited too."""
        old = git(self.contest, "rev-parse", f"{self.initial}:keep.txt").strip()
        (self.contest / "keep.txt").write_text("edited\n")
        new = git(self.contest, "hash-object", "-w", "keep.txt").strip()
        git(self.contest, "replace", old, new)

    def test_fsmonitor_never_runs(self):
        git(self.contest, "config", "core.fsmonitor", str(self.script))
        self.assertTampered(make_case(outcome=[{"type": "git_clean"}]))

    def test_hardened_flags(self):
        flags = {grade._GIT_SAFE[i + 1] for i in range(0, len(grade._GIT_SAFE), 2)}
        self.assertTrue(all(grade._GIT_SAFE[i] == "-c" for i in range(0, len(grade._GIT_SAFE), 2)))
        self.assertLessEqual({"core.fsmonitor=false", "core.untrackedCache=false", "log.showSignature=false",
                              "core.hooksPath=/dev/null", "gpg.program=false", "diff.external=", "core.pager=cat",
                              "diff.ignoreSubmodules=all", "submodule.recurse=false", "status.submoduleSummary=false",
                              "core.commitGraph=false", "core.multiPackIndex=false"}, flags)
        self.assertEqual(grade._git(self.contest, "rev-parse", "HEAD").returncode, 0)

    def test_helper_disables_fsmonitor_even_without_the_tamper_check(self):
        git(self.contest, "config", "core.fsmonitor", str(self.script))
        r = grade._git(self.contest, "status", "--porcelain")
        self.assertEqual(r.returncode, 0)
        self.assertFalse(self.marker.exists())

    def test_signature_verification_never_runs(self):
        self.merge_on_main()
        self.sign_head()
        git(self.contest, "config", "log.showSignature", "true")
        git(self.contest, "config", "gpg.program", str(self.script))
        self.assertTampered(make_case(outcome=[{"type": "git_merge_commit_on", "branch": "main",
                                                "message_match": "merge T001"}]))

    def test_commit_message_is_read_with_plumbing(self):
        self.merge_on_main()
        sha = self.sign_head()
        git(self.contest, "config", "log.showSignature", "true")
        git(self.contest, "config", "gpg.program", str(self.script))
        self.assertEqual(grade._commit_message(self.contest, sha).strip(), "merge T001")
        self.assertFalse(self.marker.exists())

    def test_replace_ref_cannot_forge_the_initial_file(self):
        self.replace_keep()
        self.assertTampered(self.pred(type="file_unchanged", path="keep.txt"))

    def test_helper_ignores_replace_refs(self):
        self.replace_keep()
        self.assertEqual(grade._ref_blob(self.contest, f"{self.initial}:keep.txt"), b"keep\n")

    def test_grafts_and_shallow_are_tampering(self):
        for name in ("info/grafts", "shallow"):
            with self.subTest(name=name):
                planted = self.contest / ".git" / name
                planted.parent.mkdir(exist_ok=True)
                planted.write_text(self.initial + "\n")
                self.assertTampered(self.pred(type="file_unchanged", path="keep.txt"))
                planted.unlink()

    def test_unlisted_config_keys_are_tampering(self):
        for key, value in (("core.hooksPath", str(self.tmp)), ("include.path", str(self.tmp / "x.cfg")),
                           ("includeIf.gitdir:/.path", str(self.tmp / "x.cfg")), ("diff.external", "x"),
                           ("filter.x.clean", "x")):
            with self.subTest(key=key):
                git(self.contest, "config", key, value)
                self.assertTampered(make_case(outcome=[{"type": "git_clean"}]))
                git(self.contest, "config", "--unset", key)

    def test_worktree_config_is_checked(self):
        git(self.contest, "worktree", "add", "-q", str(self.contest / ".worktrees/T001"), "-b", "task/T001")
        wt_cfg = next((self.contest / ".git/worktrees").iterdir()) / "config.worktree"
        wt_cfg.write_text(f"[core]\n\tfsmonitor = {self.script}\n")
        self.assertTampered(make_case(outcome=[{"type": "git_clean"}]))

    def test_git_dir_redirect_is_tampering(self):
        other = self.tmp / "other.git"
        (self.contest / ".git").rename(other)
        (self.contest / ".git").write_text(f"gitdir: {other}\n")
        self.assertTampered(make_case(outcome=[{"type": "git_clean"}]))

    def test_tampered_run_fails_even_when_the_rest_holds(self):
        git(self.contest, "config", "core.fsmonitor", str(self.script))
        g = self.assertTampered(make_case(outcome=[{"type": "file_exists", "path": "keep.txt", "exists": True}]))
        self.assertEqual(g.failed_predicates, [])

    def worktree_config_filter(self):
        """The main worktree's `config.worktree` (read once `extensions.worktreeConfig` is on) defines a clean
        filter that `.gitattributes` applies to every file; a stat change makes `git status` run it."""
        git(self.contest, "config", "core.repositoryformatversion", "1")
        git(self.contest, "config", "extensions.worktreeConfig", "true")
        (self.contest / ".git/config.worktree").write_text(f'[filter "x"]\n\tclean = {self.script}\n')
        (self.contest / ".gitattributes").write_text("* filter=x\n")
        os.utime(self.contest / "keep.txt", (1, 1))

    def test_main_worktree_config_filter_never_runs(self):
        self.worktree_config_filter()
        self.assertTampered(make_case(outcome=[{"type": "git_clean"}]))

    def test_worktree_config_extension_and_main_config_worktree_are_tampering(self):
        git(self.contest, "config", "extensions.worktreeConfig", "true")
        self.assertTampered(make_case(outcome=[{"type": "git_clean"}]))
        git(self.contest, "config", "--unset", "extensions.worktreeConfig")
        (self.contest / ".git/config.worktree").write_text("")
        self.assertTampered(make_case(outcome=[{"type": "git_clean"}]))

    def nested_repo_with_filter(self):
        """A gitlink whose nested repository defines a clean filter: `git status` would recurse into it."""
        sub = self.contest / "sub"
        sub.mkdir()
        git(sub, "init", "-q", "-b", "main")
        (sub / "f").write_text("f\n")
        git(sub, "add", "-A")
        git(sub, "commit", "-q", "-m", "s")
        git(self.contest, "add", "sub")
        git(self.contest, "commit", "-q", "-m", "gitlink")
        git(sub, "config", "filter.x.clean", str(self.script))
        (sub / ".gitattributes").write_text("* filter=x\n")
        os.utime(sub / "f", (1, 1))

    def test_submodule_config_never_runs(self):
        self.nested_repo_with_filter()
        self.grade(make_case(outcome=[{"type": "git_clean"}]))
        self.assertFalse(self.marker.exists(), "the planted command ran on the host")

    def test_git_modules_dir_is_tampering(self):
        (self.contest / ".git/modules/sub").mkdir(parents=True)
        self.assertTampered(make_case(outcome=[{"type": "git_clean"}]))

    def test_honest_config_is_not_tampering(self):
        for key, value in (("remote.origin.url", str(self.tmp / "origin.git")),
                           ("remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*"),
                           ("branch.main.remote", "origin"), ("branch.main.merge", "refs/heads/main"),
                           ("branch.task/T001-calc.remote", "origin"), ("user.name", "House Eval"),
                           ("user.email", "eval@house.invalid")):
            git(self.contest, "config", key, value)
        git(self.contest, "worktree", "add", "-q", str(self.contest / ".worktrees/T001"), "-b", "task/T001")
        g = self.grade(make_case(outcome=[{"type": "file_unchanged", "path": "keep.txt"},
                                          {"type": "git_branch", "name": "task/T001", "exists": True}]))
        self.assertEqual((g.result, g.note), ("pass", ""))


class FileReadTest(GradeBase):
    """QUESTIONS.md, task files, check files and compared files are read inside the contest, regular, <= 1 MB."""

    def fifo(self, path: Path) -> None:
        """Make `path` a FIFO; a daemon writer unblocks every reader that wrongly opens it (each sees EOF)."""
        path.unlink(missing_ok=True)
        os.mkfifo(path)
        stop = threading.Event()

        def write():
            while not stop.is_set():
                try:
                    open(path, "wb").close()
                except OSError:
                    return

        writer = threading.Thread(target=write, daemon=True)
        writer.start()

        def finish():
            stop.set()
            try:
                os.close(os.open(path, os.O_RDONLY | os.O_NONBLOCK))
            except OSError:
                pass
            writer.join(5)

        self.addCleanup(finish)

    def test_questions_symlink_out_of_the_contest(self):
        outside = self.tmp / "q.md"
        outside.write_text("## Q-001 [open] old\n")
        (self.contest / ".house/QUESTIONS.md").unlink()
        (self.contest / ".house/QUESTIONS.md").symlink_to(outside)
        g = self.grade(self.pred(type="open_questions", count=1))
        self.assertEqual(g.result, "fail")
        self.assertIn("path outside contest", g.note)

    def test_questions_not_a_regular_file(self):
        self.fifo(self.contest / ".house/QUESTIONS.md")
        g = self.grade(self.pred(type="open_questions", max=5))
        self.assertEqual(g.result, "fail")
        self.assertIn("unreadable", g.note)

    def test_oversized_questions_cannot_hide_a_new_question(self):
        qs = self.contest / ".house/QUESTIONS.md"
        qs.write_text(qs.read_text() + "\n## Q-003 [open] new\n" + "x" * grade.READ_LIMIT)
        g = self.grade(make_case())
        self.assertEqual(g.result, "fail")
        self.assertIn("unreadable", g.note)

    def test_task_file_symlink_out_or_irregular(self):
        task = self.contest / ".house/tasks/T001-calc.md"
        outside = self.tmp / "t.md"
        outside.write_text(task.read_text())
        task.unlink()
        task.symlink_to(outside)
        g = self.grade(self.pred(type="task_status", task="T001", status="todo"))
        self.assertEqual(g.result, "fail")
        self.assertIn("path outside contest", g.note)
        task.unlink()
        self.fifo(task)
        self.assertEqual(self.grade(self.pred(type="task_status", task="T001", status="todo")).result, "fail")

    def test_oversized_task_file(self):
        task = self.contest / ".house/tasks/T001-calc.md"
        task.write_text(task.read_text() + "x" * grade.READ_LIMIT)
        self.assertEqual(self.grade(self.pred(type="task_status", task="T001", status="todo")).result, "fail")

    def test_check_file_must_be_regular_and_small(self):
        checks = self.contest / ".house/checks"
        checks.mkdir()
        self.fifo(checks / "T001.json")
        self.assertEqual(self.grade(self.pred(type="check_state", task="T001", state="GREEN")).result, "fail")
        (checks / "T001.json").unlink()
        (checks / "T001.json").write_text(json.dumps({"results": {"AC-1": {"passes": True}},
                                                      "pad": "x" * grade.READ_LIMIT}))
        self.assertEqual(self.grade(self.pred(type="check_state", task="T001", state="GREEN")).result, "fail")

    def test_compared_file_must_be_regular(self):
        self.fifo(self.contest / "keep.txt")
        self.assertEqual(self.grade(self.pred(type="file_unchanged", path="keep.txt")).result, "fail")


class PausedTest(GradeBase):
    def ask(self):
        qs = self.contest / ".house/QUESTIONS.md"
        qs.write_text(qs.read_text() + "\n## Q-003 [open] Division by zero\n- **Poziom:** zwykły\n")

    def test_new_question_not_expected_is_paused(self):
        self.ask()
        case = make_case(expect=[call("merge", "Bash", "house merge", 1)])
        g = self.grade(case, Log().bash("ls"))
        self.assertEqual(g.result, "paused")
        self.assertEqual(len(g.missing), 1)  # details are still reported

    def test_new_question_expected_is_not_paused(self):
        self.ask()
        case = make_case(outcome=[{"type": "open_questions", "min": 1}])
        self.assertEqual(self.grade(case).result, "pass")

    def test_expected_count_one_is_an_expectation(self):
        self.ask()
        case = make_case(outcome=[{"type": "open_questions", "count": 2}])
        self.assertEqual(self.grade(case).result, "pass")

    def test_new_question_over_a_question_cap_is_a_fail(self):
        self.ask()
        for pred in ({"type": "open_questions", "max": 1}, {"type": "open_questions", "count": 1}):
            with self.subTest(pred=pred):
                g = self.grade(make_case(outcome=[pred]))
                self.assertEqual((g.result, g.failed_predicates), ("fail", ["outcome[0] open_questions"]))

    def test_open_questions_max_zero_does_not_expect_a_question(self):
        self.ask()
        case = make_case(outcome=[{"type": "open_questions", "max": 5}])
        self.assertEqual(self.grade(case).result, "paused")

    def test_forbidden_call_beats_paused(self):
        self.ask()
        case = make_case(forbid=[call("raw-merge", "Bash", "git merge")])
        g = self.grade(case, Log().bash("git merge task/T001"))
        self.assertEqual((g.result, g.forbidden), ("fail", ["raw-merge: 1"]))

    def test_forbid_order_violation_beats_paused(self):
        self.ask()
        case = make_case(expect=[call("check", "Bash", "house check", 0)], forbid=[call("raw", "Bash", "git merge", 2)],
                         order=[("check", "raw")])
        g = self.grade(case, Log().bash("git merge b"))
        self.assertEqual((g.result, g.forbidden, g.order_violations), ("fail", [], ["check<raw"]))

    def test_order_violation_between_expects_stays_paused(self):
        self.ask()
        case = make_case(expect=[call("check", "Bash", "house check", 0), call("merge", "Bash", "house merge", 0)],
                         order=[("check", "merge")])
        self.assertEqual(self.grade(case, Log().bash("house merge c T001")).result, "paused")

    def test_forbidden_call_in_a_subagent_is_caught(self):
        case = make_case(forbid=[call("raw-merge", "Bash", "git merge")])
        g = self.grade(case, Log().bash("git merge b", sidechain=True))
        self.assertEqual(g.forbidden, ["raw-merge: 1"])

    def test_old_open_question_is_not_new(self):
        self.assertEqual(self.grade(make_case()).result, "pass")

    def test_answered_question_is_not_new(self):
        qs = self.contest / ".house/QUESTIONS.md"
        qs.write_text(qs.read_text().replace("Q-001 [open]", "Q-001 [answered 2026-02-02]"))
        self.assertEqual(self.grade(make_case()).result, "pass")

    def test_without_baseline_file(self):
        git(self.contest, "rm", "-q", ".house/QUESTIONS.md")
        git(self.contest, "commit", "-qm", "rm")
        self.root.info["initial_commit"] = git(self.contest, "rev-parse", "HEAD").strip()
        self.root.baseline_questions = None  # not tracked at the initial commit
        (self.contest / ".house/QUESTIONS.md").write_text("## Q-003 [open] Division by zero\n")
        g = self.grade(make_case())
        self.assertEqual((g.result, "no question baseline" in g.note), ("paused", False))

    def test_initial_questions_come_from_the_host_baseline_not_the_object_store(self):
        blob = git(self.contest, "rev-parse", f"{self.initial}:.house/QUESTIONS.md").strip()
        self.ask()
        forged = (self.contest / ".house/QUESTIONS.md").read_bytes()
        path = self.contest / ".git/objects" / blob[:2] / blob[2:]
        path.chmod(0o644)
        path.write_bytes(zlib.compress(b"blob %d\0" % len(forged) + forged))  # Q-003 "was open from the start"
        self.assertEqual(self.grade(make_case()).result, "paused")

    def test_missing_question_baseline_means_no_initial_questions_with_a_note(self):
        del self.root.baseline_questions
        g = self.grade(make_case())
        self.assertEqual(g.result, "paused")  # Q-001 now counts as new
        self.assertIn("no question baseline", g.note)


class DecisionOrderTest(GradeBase):
    def test_infra_wins(self):
        g = self.grade(make_case(), Log().bash("ls"), infra="no docker")
        self.assertEqual(g.result, "infra_error")
        self.assertIn("no docker", g.note)

    def test_limit_after_genuine_turns_is_infra(self):
        log = Log().bash("house merge c T001").result("You've hit your usage limit. Resets 5pm.", True)
        g = self.grade(make_case(expect=[call("m", "Bash", "house merge")]), log)
        self.assertEqual(g.result, "infra_error")
        self.assertEqual(g.turns, 1)

    def test_auth_error_result_is_infra(self):
        log = Log().bash("ls").result("Invalid authentication credentials (401)", True)
        self.assertEqual(self.grade(make_case(), log).result, "infra_error")

    def test_error_result_without_limit_message_is_not_infra(self):
        log = Log().bash("ls").result("something broke", True, subtype="error_during_execution")
        # not the environment: the turn did not succeed, so the run is incomplete (C4 of K5), never infra_error
        self.assertEqual((self.grade(make_case(), log).result, self.grade(make_case(), log).note), ("fail", "incomplete"))

    def test_limit_words_in_a_successful_result_are_not_infra(self):
        log = Log().bash("ls").result("I explained the rate limit logic", False)
        self.assertEqual(self.grade(make_case(), log).result, "pass")

    def test_limit_words_in_agent_output_are_not_infra(self):
        log = Log().bash("echo usage limit")
        self.assertEqual(self.grade(make_case(), log).result, "pass")

    def test_codex_turn_failed_limit_is_infra(self):
        path = self.tmp / "codex.jsonl"
        events = [{"type": "thread.started", "thread_id": "th"},
                  {"type": "item.completed", "item": {"id": "i", "type": "agent_message", "text": "x"}},
                  {"type": "turn.failed", "error": {"message": "You exceeded your usage limit"}}]
        path.write_text("".join(json.dumps(e) + "\n" for e in events))
        run = RunResult(path, 1, 3.0, None, False, "th")
        g = grade.grade(make_case(tool="codex", agent="coder-codex"), run, self.root)
        self.assertEqual(g.result, "infra_error")

    def test_timeout_is_fail_even_when_everything_else_holds(self):
        g = self.grade(make_case(), Log().bash("ls"), timed_out=True, exit=None)
        self.assertEqual((g.result, g.note), ("fail", "timeout"))

    def test_timeout_beats_paused(self):
        qs = self.contest / ".house/QUESTIONS.md"
        qs.write_text(qs.read_text() + "\n## Q-003 [open] new\n")
        g = self.grade(make_case(), Log().bash("ls"), timed_out=True, exit=None)
        self.assertEqual(g.result, "fail")

    def test_max_turns(self):
        log = Log()
        for _ in range(3):
            log.bash("ls")
        g = self.grade(make_case(max_turns=2), log)
        self.assertEqual((g.result, g.note, g.turns), ("fail", "max_turns", 3))
        self.assertEqual(self.grade(make_case(max_turns=3), log).result, "pass")

    def test_sidechain_turns_do_not_count_toward_max_turns(self):
        log = Log().bash("a").bash("b")
        for _ in range(5):
            log.bash("sub", sidechain=True)
        g = self.grade(make_case(max_turns=2), log)
        self.assertEqual((g.result, g.turns, g.subagent_turns), ("pass", 2, 5))
        self.assertEqual(g.tokens["output"], 21)  # tokens still count every call

    def test_error_max_turns_result_is_a_fail(self):
        log = Log().bash("house merge c T001").result("", True, subtype="error_max_turns")
        g = self.grade(make_case(max_turns=1), log)
        self.assertEqual((g.result, g.note, g.turns), ("fail", "max_turns", 1))

    def test_error_max_turns_beats_a_satisfied_case(self):
        log = Log().bash("ls").bash("ls").result("", True, subtype="error_max_turns")
        self.assertEqual(self.grade(make_case(max_turns=40), log).note, "max_turns")

    def test_codex_turns_over_max(self):
        path = self.tmp / "codex.jsonl"
        usage = {"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 1}}
        path.write_text("".join(json.dumps(e) + "\n" for e in [usage, usage, usage]))
        run = RunResult(path, 0, 1.0, None, False, "th")
        g = grade.grade(make_case(tool="codex", agent="coder-codex", max_turns=2), run, self.root)
        self.assertEqual((g.result, g.note), ("fail", "max_turns"))

    def test_turns_tokens_duration(self):
        log = Log().bash("a", usage=(10, 5, 2, 3)).bash("b", usage=(1, 1, 1, 1)).turn(usage=(100, 0, 0, 7))
        g = self.grade(make_case(), log)
        self.assertEqual(g.turns, 3)
        self.assertEqual(g.tokens, {"input": 111, "cache_read": 6, "cache_write": 3, "output": 11})
        self.assertEqual(g.duration, 12.5)

    def test_no_terminal_record_is_incomplete(self):
        g = self.grade(make_case(), Log(terminal=False).bash("ls"))
        self.assertEqual((g.result, g.note), ("fail", "incomplete"))

    def test_codex_without_turn_completed_is_incomplete(self):
        path = self.tmp / "codex.jsonl"
        events = [{"type": "thread.started", "thread_id": "th"},
                  {"type": "item.completed", "item": {"id": "i", "type": "agent_message", "text": "x"}}]
        path.write_text("".join(json.dumps(e) + "\n" for e in events))
        g = grade.grade(make_case(tool="codex", agent="coder-codex"), RunResult(path, 1, 3.0, None, False, "th"),
                        self.root)
        self.assertEqual((g.result, g.note), ("fail", "incomplete"))

    def test_incomplete_order(self):
        self.assertEqual(self.grade(make_case(), Log(terminal=False), infra="no docker").result, "infra_error")
        self.assertEqual(self.grade(make_case(), Log(terminal=False), timed_out=True, exit=None).note, "timeout")
        qs = self.contest / ".house/QUESTIONS.md"
        qs.write_text(qs.read_text() + "\n## Q-003 [open] new\n")
        self.assertEqual(self.grade(make_case(), Log(terminal=False).bash("ls")).note, "incomplete")

    def test_empty_log(self):
        g = self.grade(make_case(expect=[call("m", "Bash", "x")]))
        self.assertEqual((g.result, g.turns, g.tokens), ("fail", 0, {"input": 0, "cache_read": 0,
                                                                     "cache_write": 0, "output": 0}))


class FinalAnswerTest(GradeBase):
    """`final_answer {match}`: a regex over the text of the last `result` (Claude) / last agent message (Codex)."""

    def case(self, match=r"\b6\b") -> Case:
        return self.pred(type="final_answer", match=match)

    def test_the_last_result_text_decides(self):
        good = Log().bash("ls").result("The maximum team size is 6.", False)
        self.assertEqual(self.grade(self.case(), good).result, "pass")
        wrong = Log().bash("ls").result("The maximum team size is 5.", False)
        g = self.grade(self.case(), wrong)
        self.assertEqual((g.result, g.failed_predicates), ("fail", ["outcome[0] final_answer"]))

    def test_an_agent_that_does_nothing_fails(self):
        g = self.grade(self.case(), Log())  # the default final result is "done"
        self.assertEqual((g.result, g.failed_predicates), ("fail", ["outcome[0] final_answer"]))

    def test_only_the_last_result_counts(self):
        case = dataclasses.replace(self.case(), followups=["and now?"])
        log = Log().result("6", False).result("no idea", False)
        self.assertEqual(self.grade(case, log).result, "fail")
        log = Log().result("no idea", False).result("6", False)
        self.assertEqual(self.grade(case, log).result, "pass")

    def test_the_answer_text_in_a_tool_call_or_a_message_does_not_count(self):
        log = Log().bash("echo 6").turn(("Read", {"file_path": "/x"}))
        log.lines.append({"type": "result", "subtype": "success", "is_error": False, "result": "done"})
        self.assertEqual(self.grade(self.case(), log).result, "fail")  # the assistant text blocks say CANARY, not 6

    def test_a_result_without_text_fails(self):
        log = Log()
        log.lines.append({"type": "result", "subtype": "success", "is_error": False})
        self.assertEqual(self.grade(self.case(), log).result, "fail")

    def test_codex_uses_the_last_agent_message(self):
        def codex(*texts):
            path = self.tmp / "codex.jsonl"
            events = [{"type": "thread.started", "thread_id": "th"}]
            events += [{"type": "item.completed", "item": {"id": f"i{n}", "type": "agent_message", "text": t}}
                       for n, t in enumerate(texts)]
            events.append({"type": "turn.completed", "usage": {"input_tokens": 1, "cached_input_tokens": 0,
                                                                "output_tokens": 1}})
            path.write_text("".join(json.dumps(e) + "\n" for e in events))
            return RunResult(path, 0, 3.0, None, False, "th")

        case = dataclasses.replace(self.case(), tool="codex", agent="coder-codex")
        self.assertEqual(grade.grade(case, codex("thinking about 7", "answer: 6"), self.root).result, "pass")
        self.assertEqual(grade.grade(case, codex("answer: 6", "never mind 7"), self.root).result, "fail")
        self.assertEqual(grade.grade(case, codex(), self.root).result, "fail")

    def test_only_the_boolean_is_stored_no_answer_text_in_the_grade(self):
        log = Log().bash("ls").result(f"the answer is 6 {CANARY}", False)
        for match in (r"\b6\b", CANARY, "never-there"):
            g = self.grade(self.case(match), log)
            dumped = json.dumps(dataclasses.asdict(g))
            self.assertNotIn(CANARY, dumped)
            self.assertNotIn("the answer is", dumped)

    def test_it_is_evaluated_even_when_git_is_tampered_with(self):
        (self.contest / ".git/info/grafts").write_text("")
        g = self.grade(make_case(outcome=[{"type": "final_answer", "match": "six"}]),
                       Log().result("six", False))
        self.assertEqual((g.result, g.failed_predicates), ("fail", []))  # failed by `git tampered`, predicate passed
        self.assertIn("git tampered", g.note)


class KeyedFinalAnswerTest(GradeBase):
    """`final_answer {key, match}`: `match` runs on the LAST line that starts with `<key>:` only."""

    def case(self, match=r"^[\W_]*Q1[\W_]*:[\W_]*2027-03-21[\W_]*$", key="Q1") -> Case:
        return self.pred(type="final_answer", match=match, key=key)

    def result(self, text: str) -> str:
        return self.grade(self.case(), Log().bash("ls").result(text, False)).result

    def test_the_keyed_line_decides(self):
        self.assertEqual(self.result("Done.\n\nQ1: 2027-03-21\nQ2: 27"), "pass")
        self.assertEqual(self.result("Done.\n\nQ1: 2027-03-14\nQ2: 2027-03-21"), "fail")

    def test_the_last_keyed_line_wins_a_self_correction(self):
        self.assertEqual(self.result("Q1: 2027-03-21\n\nWait, the body says otherwise.\nQ1: 2027-03-14"), "fail")
        self.assertEqual(self.result("Q1: 2027-03-14\n\nThe erratum moves it.\nQ1: 2027-03-21"), "pass")

    def test_a_value_on_the_next_line_fails(self):
        self.assertEqual(self.result("Q1:\n2027-03-21"), "fail")
        self.assertEqual(self.result("**Q1:**\n\n2027-03-21"), "fail")

    def test_markdown_bullets_and_emphasis_before_the_key(self):
        for line in ("- Q1: 2027-03-21", "* **Q1:** 2027-03-21", "> `Q1: 2027-03-21`", "  + __Q1__: 2027-03-21",
                     "Q1:\t\t2027-03-21", "Q1:\u00a02027-03-21"):
            self.assertEqual(self.result(f"Answers:\n{line}\n"), "pass", line)

    def test_a_longer_key_or_a_mention_is_not_the_key(self):
        self.assertEqual(self.result("Q10: 2027-03-21"), "fail")
        self.assertEqual(self.result("See Q1: 2027-03-21"), "fail")  # the key must start the line
        self.assertEqual(self.result("Q1. 2027-03-21"), "fail")

    def test_horizontal_whitespace_is_normalised(self):
        g = self.grade(self.case(match=r"^Q1: 22 750$"), Log().result("Q1:   22\u00a0750  \r\n", False))
        self.assertEqual(g.result, "pass")

    def test_no_keyed_line_fails(self):
        self.assertEqual(self.result("The deadline is 2027-03-21."), "fail")


class PrivacyTest(GradeBase):
    def test_canary_never_in_grade(self):
        key = "sk-ant-api03-" + "A" * 40
        log = (Log().bash(f"echo {CANARY} && export ANTHROPIC_API_KEY={key}")
               .turn(("Edit", {"file_path": f"/c/{CANARY}.py", "new_string": CANARY}))
               .result(f"limit {CANARY} usage limit {key}", True))
        case = make_case(
            expect=[call("m", "Bash", "house merge")], forbid=[call("f", "Bash", "echo")],
            outcome=[{"type": "file_exists", "path": "nope", "exists": True}])
        for kw in ({}, {"timed_out": True}):
            g = self.grade(case, log, **kw)
            dumped = json.dumps(dataclasses.asdict(g))
            self.assertNotIn(CANARY, dumped)
            self.assertNotIn(key, dumped)

    def test_note_is_redacted(self):
        key = "sk-ant-api03-" + "B" * 40
        g = self.grade(make_case(), Log().bash("ls"), infra=f"docker: failed with {key}")
        self.assertNotIn(key, g.note)


if __name__ == "__main__":
    unittest.main()
