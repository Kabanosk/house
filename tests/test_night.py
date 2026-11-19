"""`house night`: preflight, task picking, worker commands and the unattended loop (no real agents)."""
import io
import json
import subprocess
import tempfile
import time
import unittest
import uuid
from collections import namedtuple
from contextlib import redirect_stderr
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

from houselib import adapters, attempts, checks, guard, merge, night, scaffold
from houselib.fm import parse_frontmatter
from tests.helpers import FAKE_TOOLS, git, make_root
from tests.test_evidence import _bash, _edit, _text

Usage = namedtuple("Usage", "total used free")
GB = 1024 ** 3


def task_text(tid: str, deps: str = "[]", run: str = "python3 -c 'import hello'", status: str = "todo",
              extra: str = "") -> str:
    return (f"---\nid: {tid}\ntitle: x\nstatus: {status}\nowner: null\nbranch: null\ndepends_on: {deps}\n{extra}"
            f"acceptance:\n  - id: AC-1\n    run: \"{run}\"\nprotected: []\n---\nx\n")


def fake_run(logged_in=True, expires_in_h=72, codex_ok=True):
    def run(argv, **kw):
        if argv[:3] == ["claude", "auth", "status"]:
            exp = (datetime.now(UTC) + timedelta(hours=expires_in_h)).isoformat().replace("+00:00", "Z")
            return subprocess.CompletedProcess(argv, 0, json.dumps({"loggedIn": logged_in, "loginExpiresAt": exp}), "")
        if argv[:3] == ["codex", "login", "status"]:
            return subprocess.CompletedProcess(argv, 0 if codex_ok else 1, "Logged in" if codex_ok else "", "")
        return subprocess.run(argv, **kw)
    return run


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_root(Path(self._tmp.name))
        self.c = scaffold.new_contest("c1", None, root=self.root, detect=lambda: FAKE_TOOLS)
        self.house = self.c / ".house"
        adapters.adapt(self.c, self.root, detect=lambda: FAKE_TOOLS)

    def tearDown(self):
        self._tmp.cleanup()

    def add(self, tid, **kw):
        (self.house / "tasks" / f"{tid}-x.md").write_text(task_text(tid, **kw))
        git(self.c, "add", ".house")
        git(self.c, "commit", "-q", "-m", f"plan {tid}")
        checks.freeze(self.house, tid)

    def status(self, tid):
        return parse_frontmatter(next((self.house / "tasks").glob(f"{tid}-*.md")).read_text())[0]["status"]


class PreflightTest(Base):
    def pf(self, **kw):
        return night.preflight(self.c, hours=8, run=fake_run(**{k: v for k, v in kw.items() if k != "free"}),
                               disk_free=lambda _: Usage(100 * GB, 0, kw.get("free", 50) * GB))

    def test_clean_contest_passes(self):
        self.assertEqual(self.pf(), ([], []))

    def test_problems(self):
        (self.c / ".gitignore").write_text("changed\n")
        self.assertTrue(any("uncommitted" in p for p in self.pf()[0]))
        git(self.c, "checkout", "--", ".gitignore")
        self.assertTrue(any("disk" in p for p in self.pf(free=3)[0]))
        self.assertTrue(any("disk" in w for w in self.pf(free=7)[1]))
        self.assertTrue(any("logged in" in p for p in self.pf(logged_in=False)[0]))
        self.assertTrue(any("expires" in p for p in self.pf(expires_in_h=2)[0]))
        tools = json.loads((self.house / "tools.json").read_text())
        (self.house / "tools.json").write_text(json.dumps({**tools, "codex": True}))
        git(self.c, "commit", "-qam", "codex available")
        self.assertTrue(any("codex" in w.lower() for w in self.pf(codex_ok=False)[1]))
        (self.house / "STOP").write_text("")
        self.assertTrue(any("STOP" in p for p in self.pf()[0]))

    def test_red_task_and_missing_settings(self):
        self.add("T001", status="merged", run="exit 1")
        self.assertTrue(any("T001" in p for p in self.pf()[0]))
        (self.c / ".claude/settings.local.json").unlink()
        self.assertTrue(any("house adapt" in p for p in self.pf()[0]))


class PickTest(Base):
    def test_pick_ready_order_and_limit(self):
        self.add("T001")
        self.add("T002", deps="[T001]")
        self.add("T003")
        self.add("T004")
        self.assertEqual(night.pick_ready(self.house, running={"T003"}, limit=5), ["T001", "T004"])
        self.assertEqual(night.pick_ready(self.house, running=set(), limit=2), ["T001", "T003"])

    def test_prepare_worktree_once(self):
        self.add("T001")
        wt = night.prepare_worktree(self.c, "T001")
        self.assertTrue((wt / ".git").exists())
        meta = parse_frontmatter((self.house / "tasks/T001-x.md").read_text())[0]
        self.assertEqual((meta["status"], meta["branch"]), ("doing", "task/T001-x"))
        self.assertEqual(night.prepare_worktree(self.c, "T001"), wt)

    def test_commands_and_brief(self):
        self.add("T001")
        self.add("T002", extra="agent: coder-codex\n")
        wt = self.c / ".worktrees/T001-x"
        cmd = night.worker_command(self.c, checks.load_task(self.house, "T001"), wt, {"codex": True})
        self.assertEqual(cmd[:4], ["claude", "-p", "--agent", "coder"])
        self.assertIn("--permission-prompts", cmd)
        self.assertIn(str(self.c / ".claude/settings.local.json"), cmd)
        codex = night.worker_command(self.c, checks.load_task(self.house, "T002"), wt, {"codex": True})
        self.assertTrue(codex[0].endswith("bin/codex-task"))
        fallback = night.worker_command(self.c, checks.load_task(self.house, "T002"), wt, {"codex": False})
        self.assertEqual(fallback[0], "claude")
        sid, jl = "11111111-2222-3333-4444-555555555555", self.house / "tmp/night/x/T001-1.jsonl"
        stream = night.worker_command(self.c, checks.load_task(self.house, "T001"), wt, {"codex": True},
                                      session_id=sid)
        self.assertEqual(stream[stream.index("--session-id") + 1], sid)
        self.assertEqual(stream[stream.index("--output-format") + 1], "stream-json")
        self.assertIn("--verbose", stream)
        self.assertIn("Implement exactly one task", stream[-1])
        self.assertNotIn("--session-id", cmd)
        cx = night.worker_command(self.c, checks.load_task(self.house, "T002"), wt, {"codex": True},
                                  session_id=sid, json_log=jl)
        self.assertEqual(cx[1:3], ["--json-log", str(jl)])
        self.assertEqual(cx[3:], [str(self.house / "tasks/T002-x.md"), str(wt)])
        self.assertNotIn("--session-id", cx)
        bf = self.house / "tmp/night/x/T002-2.brief.md"
        cb = night.worker_command(self.c, checks.load_task(self.house, "T002"), wt, {"codex": True},
                                  json_log=jl, brief_file=bf)
        self.assertEqual(cb[cb.index("--brief-file") + 1], str(bf))
        self.assertEqual(cb[-2:], [str(self.house / "tasks/T002-x.md"), str(wt)])
        self.assertNotIn("--brief-file", cx)
        bf.parent.mkdir(parents=True)
        bf.write_text("BRIEF-BODY\n")
        cl = night.worker_command(self.c, checks.load_task(self.house, "T001"), wt, {"codex": True},
                                  previous="failed: X", brief_file=bf)
        self.assertIn("Hand-off from earlier attempts:\nBRIEF-BODY", cl[-1])
        self.assertNotIn("Previous attempt failed", cl[-1])
        b = night.brief(self.c, "T001", wt)
        self.assertIn("c1", b)
        self.assertIn("T001-x.md", b)
        self.assertIn("status: review", b)


class FakeProc:
    def __init__(self, action, cwd):
        self.action, self.cwd, self.pid, self.done, self.started = action, cwd, 4242, None, time.monotonic()

    def poll(self):
        if self.done is None and self.action != "hang":
            self.action(self.cwd) if callable(self.action) else None
            self.done = 0
        return self.done

    def wait(self, timeout=None):
        return self.poll()

    def kill(self):
        self.done = -9


def commit_file(name, text):
    def act(cwd):
        (cwd / name).write_text(f"{text}# {time.monotonic_ns()}\n")
        git(cwd, "add", "-A")
        git(cwd, "commit", "-q", "-m", f"work {name}")
    return act


class LoopTest(Base):
    def launcher(self, plan):
        def launch(cmd, cwd, log):
            tid = cwd.name.split("-", 1)[0]
            action = plan[tid].pop(0) if plan[tid] else None
            return FakeProc(action, cwd)
        return launch

    def run_night(self, plan, **kw):
        return night.run_night(self.c, hours=1, launcher=self.launcher(plan), sleep=lambda s: None, **kw)

    def test_good_task_merged_and_reported(self):
        self.add("T001")
        rep = self.run_night({"T001": [commit_file("hello.py", "X = 1\n")]})
        self.assertEqual(self.status("T001"), "done")
        self.assertEqual([a.outcome for a in rep.attempts], ["merged"])
        reports = list((self.house / "reports").glob("night-*.md"))
        self.assertEqual(len(reports), 1)
        self.assertIn("noc", (self.house / "PROGRESS.md").read_text())

    def test_night_logs_are_jsonl_with_session(self):
        self.add("T001")
        self.add("T002", extra="agent: coder-codex\n")
        (self.house / "tools.json").write_text(json.dumps({"codex": True}))
        calls = {}

        def launch(cmd, cwd, log):
            tid = cwd.name.split("-", 1)[0]
            calls.setdefault(tid, []).append((cmd, log))
            return FakeProc(commit_file("hello.py", "raise SystemExit(1)\n") if tid == "T001" else None, cwd)

        rep = night.run_night(self.c, hours=1, launcher=launch, sleep=lambda s: None)
        t1 = [a for a in rep.attempts if a.task == "T001"]
        t2 = [a for a in rep.attempts if a.task == "T002"]
        self.assertEqual(len(t1), 3)
        self.assertTrue(all(a.log.endswith(".jsonl") for a in rep.attempts), [a.log for a in rep.attempts])
        for a in t1:
            uuid.UUID(a.session)
        self.assertEqual(len({a.session for a in t1}), 3)
        for (cmd, log), a in zip(calls["T001"], t1, strict=True):
            self.assertEqual(cmd[cmd.index("--session-id") + 1], a.session)
            self.assertEqual(log, self.c / a.log)
        self.assertEqual({a.session for a in t2}, {""})
        for (cmd, log), a in zip(calls["T002"], t2, strict=True):
            self.assertEqual(cmd[cmd.index("--json-log") + 1], str(self.c / a.log))
            self.assertEqual(log, (self.c / a.log).with_suffix(".out"))

    def test_bad_task_blocked_after_three_attempts(self):
        self.add("T001")
        bad = commit_file("hello.py", "raise SystemExit(1)\n")
        rep = self.run_night({"T001": [bad, bad, bad]})
        self.assertEqual(len([a for a in rep.attempts if a.task == "T001"]), 3)
        self.assertEqual(self.status("T001"), "blocked")

    def test_no_commits_back_to_todo_then_blocked(self):
        self.add("T001")
        rep = self.run_night({"T001": [None, None, None]})
        self.assertEqual(rep.attempts[0].outcome, "no-commits")
        self.assertEqual(self.status("T001"), "blocked")

    def test_hang_killed(self):
        self.add("T001", extra="")
        (self.house / "spec.yaml").write_text("night:\n  task_minutes: 0\n")
        git(self.c, "commit", "-qam", "spec")
        rep = self.run_night({"T001": ["hang", "hang", "hang"]})
        self.assertEqual(rep.attempts[0].outcome, "timeout")

    def test_five_failures_in_a_row_end_the_night(self):
        for t in ("T001", "T002", "T003"):
            self.add(t)
        bad = commit_file("hello.py", "raise SystemExit(1)\n")
        rep = self.run_night({t: [bad, bad, bad] for t in ("T001", "T002", "T003")})
        self.assertEqual(len([a for a in rep.attempts if a.outcome != "interrupted"]), 5)
        self.assertIn("5 failed", rep.stopped_because)
        self.assertTrue(all(self.status(t) in ("todo", "blocked") for t in ("T001", "T002", "T003")))

    def test_stop_file(self):
        self.add("T001")
        (self.house / "STOP").write_text("")
        rep = self.run_night({"T001": [commit_file("hello.py", "X = 1\n")]})
        self.assertEqual(rep.attempts, [])
        self.assertIn("STOP", rep.stopped_because)


def tamper_frozen(cwd):
    import json as _json
    contest = cwd.parents[1]
    cf = contest / ".house/checks/T001.json"
    data = _json.loads(cf.read_text())
    data["frozen"]["criteria"][0]["run"] = "true"
    cf.write_text(_json.dumps(data))
    tf = contest / ".house/tasks/T001-x.md"
    tf.write_text(tf.read_text().replace("python3 -c 'import hello'", "true"))
    commit_file("junk.txt", "x")(cwd)


def forge_register(cwd):
    (cwd.parents[1] / ".house/attempts.jsonl").write_text('{"task": "T001", "outcome": "merged"}\n')
    commit_file("junk.txt", "x")(cwd)


def forge_register_binary(cwd):
    (cwd.parents[1] / ".house/attempts.jsonl").write_bytes(b"\xff\xfe\n")
    commit_file("junk.txt", "x")(cwd)


def set_done(cwd):
    tf = cwd.parents[1] / ".house/tasks/T001-x.md"
    tf.write_text(tf.read_text().replace("status: doing", "status: done"))


def secret_then_delete(cwd):
    (cwd / "creds.txt").write_text('API_KEY = "sk-abcdefghijklmnopqrstuvwxyz0123"\n')
    git(cwd, "add", "-A")
    git(cwd, "commit", "-q", "-m", "creds")
    (cwd / "creds.txt").unlink()
    (cwd / "hello.py").write_text("X = 1\n")
    git(cwd, "add", "-A")
    git(cwd, "commit", "-q", "-m", "remove creds")


def uncommitted_work(cwd):
    (cwd / "hello.py").write_text("X = 1\n")


class SafetyTest(LoopTest):
    def test_worker_tampering_is_not_merged(self):
        self.add("T001")
        rep = self.run_night({"T001": [tamper_frozen, None, None]})
        self.assertEqual(rep.attempts[0].outcome, "tampered")
        self.assertNotEqual(self.status("T001"), "done")
        frozen = json.loads((self.house / "checks/T001.json").read_text())["frozen"]["criteria"][0]["run"]
        self.assertEqual(frozen, "python3 -c 'import hello'")

    def test_worker_cannot_forge_attempt_register(self):
        self.add("T001")
        rep = self.run_night({"T001": [forge_register, None, None]})
        self.assertEqual(rep.attempts[0].outcome, "tampered")
        reg = self.house / "attempts.jsonl"
        self.assertNotIn('"outcome": "merged"', reg.read_text() if reg.exists() else "")

    def test_non_utf8_register_is_tampering_not_a_crash(self):
        self.add("T001")
        rep = self.run_night({"T001": [forge_register_binary, None, None]})
        self.assertEqual(rep.attempts[0].outcome, "tampered")
        reg = self.house / "attempts.jsonl"
        self.assertNotIn(b"\xff\xfe", reg.read_bytes() if reg.exists() else b"")

    def test_worker_cannot_mark_done(self):
        self.add("T001")
        self.add("T002", deps="[T001]")
        rep = self.run_night({"T001": [set_done, None, None], "T002": []})
        self.assertNotEqual(self.status("T001"), "done")
        self.assertNotIn("T002", [a.task for a in rep.attempts])

    def test_secret_in_branch_history_refused(self):
        self.add("T001")
        rep = self.run_night({"T001": [secret_then_delete, None, None]})
        self.assertNotEqual(rep.attempts[0].outcome, "merged")
        self.assertIn("creds.txt", rep.attempts[0].message)

    def test_git_error_does_not_end_the_night(self):
        from unittest import mock
        self.add("T001")
        self.add("T002")
        real = night.prepare_worktree

        def flaky(contest, tid):
            if tid == "T001":
                raise RuntimeError("git worktree add failed")
            return real(contest, tid)
        with mock.patch.object(night, "prepare_worktree", side_effect=flaky):
            rep = self.run_night({"T001": [], "T002": [commit_file("hello.py", "X = 1\n")]})
        self.assertIn(("T001", "error"), [(a.task, a.outcome) for a in rep.attempts])
        self.assertIn(("T002", "merged"), [(a.task, a.outcome) for a in rep.attempts])

    def test_interrupt_resets_running_tasks(self):
        self.add("T001")

        def launch(cmd, cwd, log):
            proc = FakeProc("hang", cwd)
            proc.poll = lambda: (_ for _ in ()).throw(KeyboardInterrupt())
            return proc
        rep = night.run_night(self.c, hours=1, launcher=launch, sleep=lambda s: None)
        self.assertEqual(rep.stopped_because, "interrupted")
        self.assertEqual(self.status("T001"), "todo")
        self.assertEqual([a.outcome for a in rep.attempts], ["interrupted"])
        self.assertIsNotNone(rep.report_path)

    def test_reverted_task_is_not_retried(self):
        self.add("T001", run="python3 -c 'import hello; assert hello.X == 1'")
        self.assertEqual([a.outcome for a in self.run_night({"T001": [commit_file("hello.py", "X = 1\n")]}).attempts],
                         ["merged"])
        self.add("T002", run="true")
        rep = self.run_night({"T002": [commit_file("hello.py", "X = 2\n"), None, None]})
        self.assertEqual([a.outcome for a in rep.attempts], ["reverted"])
        self.assertEqual(self.status("T002"), "blocked")

    def test_uncommitted_work_is_committed_and_merged(self):
        self.add("T001")
        rep = self.run_night({"T001": [uncommitted_work]})
        self.assertEqual([a.outcome for a in rep.attempts], ["merged"])

    def test_retry_gets_the_handoff_brief_not_the_failure_text(self):
        self.add("T001")
        briefs, logs = [], []
        report = ("## Goal\nMake hello importable.\n\n## Next\nTry the stub.\n\n## Dead ends\n"
                  "- tried the stub module; imports still fail. Do not repeat.\n")

        def launch(cmd, cwd, log):
            briefs.append(cmd[-1])
            logs.append(log)
            first = len(briefs) == 1

            def act(wt):
                if first:
                    (self.house / "reports" / "T001.md").write_text(report)
                commit_file("hello.py", "raise SystemExit(1)\n")(wt)
            return FakeProc(act, cwd)
        night.run_night(self.c, hours=1, launcher=launch, sleep=lambda s: None, max_tasks=2)
        self.assertNotIn("Previous attempt", briefs[0])
        self.assertNotIn("Hand-off from earlier attempts", briefs[0])
        self.assertIn("Implement exactly one task", briefs[1])  # the first-attempt text stays
        self.assertIn("Hand-off from earlier attempts:", briefs[1])
        self.assertIn("tried the stub module; imports still fail. Do not repeat. (worker report, attempt 1)", briefs[1])
        self.assertNotIn("Previous attempt failed", briefs[1])
        path = logs[1].with_suffix(".brief.md")
        self.assertRegex(path.name, r"^T001-\d{6}-2\.brief\.md$")
        self.assertEqual(path.parent, self.house / "tmp/night" / logs[1].parent.name)
        self.assertTrue(path.is_file())
        self.assertIn("tried the stub module", path.read_text())
        self.assertIn(path.read_text().strip(), briefs[1])
        self.assertFalse(logs[0].with_suffix(".brief.md").exists())

    def test_first_attempt_of_a_later_night_gets_the_brief_when_the_register_has_rows(self):
        self.add("T001")
        night.record_attempt(self.c, attempts.Row(task="T001", n=1, source="night", agent="coder", tool="claude",
                                                  outcome="failed", start="2026-11-16T01:00:00",
                                                  end="2026-11-16T01:30:00"))
        briefs = []

        def launch(cmd, cwd, log):
            briefs.append((cmd[-1], log))
            return FakeProc(None, cwd)
        night.run_night(self.c, hours=1, launcher=launch, sleep=lambda s: None, max_tasks=1)
        self.assertIn("Hand-off from earlier attempts:", briefs[0][0])
        self.assertTrue(briefs[0][1].with_suffix(".brief.md").is_file())

    def test_codex_retry_gets_brief_file_and_fallback_claude_gets_claude_done_when(self):
        self.add("T001", extra="agent: coder-codex\n")
        (self.house / "tools.json").write_text(json.dumps({"codex": True}))
        cmds = []

        def launch(cmd, cwd, log):
            cmds.append(cmd)
            return FakeProc(commit_file("hello.py", "raise SystemExit(1)\n"), cwd)
        night.run_night(self.c, hours=1, launcher=launch, sleep=lambda s: None, max_tasks=2)
        self.assertNotIn("--brief-file", cmds[0])
        path = Path(cmds[1][cmds[1].index("--brief-file") + 1])
        self.assertTrue(path.is_file())
        self.assertEqual(path.parent.parent, self.house / "tmp/night")
        self.assertIn("the driver does", path.read_text())  # the Codex done-when
        # Codex disappears: the retry runs as Claude and the brief says what Claude must do
        (self.house / "tools.json").write_text(json.dumps({"codex": False}))
        cmds.clear()
        night.run_night(self.c, hours=1, launcher=launch, sleep=lambda s: None, max_tasks=1)
        self.assertEqual(cmds[0][0], "claude")
        self.assertIn("prints GREEN", cmds[0][-1])
        self.assertNotIn("the driver does", cmds[0][-1])

    def test_failing_next_brief_falls_back_to_previous_text(self):
        self.add("T001")
        briefs = []

        def launch(cmd, cwd, log):
            briefs.append(cmd[-1])
            return FakeProc(commit_file("hello.py", "raise SystemExit(1)\n"), cwd)
        with mock.patch.object(night, "next_brief", side_effect=RuntimeError("boom")):
            night.run_night(self.c, hours=1, launcher=launch, sleep=lambda s: None, max_tasks=2)
        self.assertEqual(len(briefs), 2)
        self.assertIn("Previous attempt failed", briefs[1])
        self.assertIn("AC-1", briefs[1])



def write_stream(path: Path, *records) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")


class RegisterTest(Base):
    """The attempt register `.house/attempts.jsonl` and the short evidence in the morning report."""
    launcher = LoopTest.launcher
    run_night = LoopTest.run_night

    def rows(self, tid="T001"):
        return attempts.rows(self.house, tid)

    def porcelain(self):
        return git(self.c, "status", "--porcelain", "--", ".house/attempts.jsonl").strip()

    def test_register_rows_from_night(self):
        self.add("T001")
        self.add("T002", extra="agent: coder-codex\n", run="true")
        bad = commit_file("hello.py", "raise SystemExit(1)\n")
        rep = self.run_night({"T001": [bad, bad, bad], "T002": [commit_file("other.py", "X = 1\n")]})
        t1 = self.rows("T001")
        self.assertEqual([r.n for r in t1], [1, 2, 3])
        self.assertEqual([a.n for a in rep.attempts if a.task == "T001"], [1, 2, 3])
        for r in t1:
            self.assertEqual((r.source, r.agent, r.tool, r.report), ("night", "coder", "claude", ""))
            self.assertTrue(r.log.startswith(".house/tmp/night/") and r.log.endswith(f"-{r.n}.jsonl"), r.log)
            uuid.UUID(r.session)
            self.assertTrue(r.start and r.end and r.base and r.head)
            self.assertNotEqual(r.base, r.head)
        self.assertNotIn("merged", {r.outcome for r in t1})
        (t2,) = self.rows("T002")
        self.assertEqual((t2.source, t2.n, t2.agent, t2.outcome), ("night", 1, "coder-codex", "merged"))
        self.assertEqual(self.porcelain(), "")

    def test_merged_row_has_head_read_before_the_merge(self):
        self.add("T001")
        self.run_night({"T001": [commit_file("hello.py", "X = 1\n")]})
        (row,) = self.rows("T001")
        self.assertEqual((row.outcome, row.n), ("merged", 1))
        self.assertEqual(git(self.c, "rev-parse", "--verify", row.head + "^{commit}").strip(), row.head)
        self.assertNotEqual(row.base, row.head)
        git(self.c, "merge-base", "--is-ancestor", row.head, "HEAD")

    def test_codex_row_takes_thread_id_from_the_log(self):
        self.add("T001", extra="agent: coder-codex\n")
        (self.house / "tools.json").write_text(json.dumps({"codex": True}))
        git(self.c, "add", ".house")
        git(self.c, "commit", "-qm", "tools")

        def launch(cmd, cwd, log):
            write_stream(Path(cmd[cmd.index("--json-log") + 1]), {"type": "thread.started", "thread_id": "th-123"})
            return FakeProc(commit_file("hello.py", "X = 1\n"), cwd)
        night.run_night(self.c, hours=1, launcher=launch, sleep=lambda s: None)
        (row,) = self.rows("T001")
        self.assertEqual((row.tool, row.session, row.agent), ("codex", "th-123", "coder-codex"))

    def test_could_not_start_and_interrupted_are_recorded(self):
        from unittest import mock
        self.add("T001")
        with mock.patch.object(night, "prepare_worktree", side_effect=RuntimeError("boom")):
            self.run_night({"T001": []}, max_tasks=1)
        (row,) = self.rows("T001")
        self.assertEqual((row.outcome, row.log, row.head, row.n), ("error", "", "", 1))
        self.assertIn("could not start", row.message)
        self.assertEqual(self.porcelain(), "")

    def test_interrupted_attempt_is_recorded_and_committed(self):
        self.add("T001")

        def launch(cmd, cwd, log):
            proc = FakeProc("hang", cwd)
            proc.poll = lambda: (_ for _ in ()).throw(KeyboardInterrupt())
            return proc
        night.run_night(self.c, hours=1, launcher=launch, sleep=lambda s: None)
        (row,) = self.rows("T001")
        self.assertEqual((row.outcome, row.n, row.source), ("interrupted", 1, "night"))
        self.assertTrue(row.head)
        self.assertEqual(self.porcelain(), "")

    def test_numbers_continue_over_two_nights(self):
        self.add("T001")
        bad = commit_file("hello.py", "raise SystemExit(1)\n")
        self.run_night({"T001": [bad, bad]}, max_tasks=2)
        self.assertEqual([r.n for r in self.rows()], [1, 2])
        self.assertEqual(self.status("T001"), "todo")
        rep = self.run_night({"T001": [bad, bad, bad, bad]})
        self.assertEqual([r.n for r in self.rows()], [1, 2, 3, 4, 5])
        self.assertEqual([a.n for a in rep.attempts], [3, 4, 5])
        self.assertEqual(self.status("T001"), "blocked")  # still 3 failures within one night
        self.assertEqual(self.porcelain(), "")

    def test_interactive_merge_row_shifts_night_numbers(self):
        self.add("T001")
        attempts.record(self.house, attempts.Row(task="T001", n=1, source="merge", agent="coder", tool="claude"))
        git(self.c, "add", ".house/attempts.jsonl")
        git(self.c, "commit", "-qm", "row")
        self.run_night({"T001": [commit_file("hello.py", "X = 1\n")]})
        self.assertEqual([(r.source, r.n) for r in self.rows()], [("merge", 1), ("night", 2)])

    def test_night_report_has_short_form_and_command(self):
        self.add("T001")
        self.add("T002")

        def launch(cmd, cwd, log):
            tid = cwd.name.split("-", 1)[0]
            write_stream(log, *_edit("e1", "hello.py", ok=False))
            return FakeProc(commit_file("hello.py", "raise SystemExit(1)\n" if tid == "T001" else "X = 1\n"), cwd)
        rep = night.run_night(self.c, hours=1, launcher=launch, sleep=lambda s: None, max_tasks=2)
        text = rep.report_path.read_text()
        self.assertIn("| T001 | 1 |", text)
        self.assertIn("T001 attempt 1:", text)
        self.assertIn("T002 attempt 1:", text)
        self.assertIn("check after last edit: no", text)
        self.assertIn("house evidence c1 T001 --attempt 1", text)
        self.assertIn("house evidence c1 T002 --attempt 1", text)
        self.assertEqual(git(self.c, "status", "--porcelain", "--", ".house/reports").strip(), "")

    def test_evidence_failure_does_not_break_the_report(self):
        from unittest import mock
        self.add("T001")
        with mock.patch("houselib.evidence.state_facts", side_effect=RuntimeError("boom")):
            rep = self.run_night({"T001": [commit_file("hello.py", "X = 1\n")]})
        text = rep.report_path.read_text()
        self.assertIn("T001 attempt 1:", text)
        self.assertIn("evidence unavailable", text)

    def test_canaries_in_night_report(self):
        self.add("T001")
        token = "sk-abcdefghijklmnopqrstuvwxyz123456"

        def launch(cmd, cwd, log):
            write_stream(log, _text(f"CANARY-TEXT {token}"), *_bash("b1", f"echo CANARY-CMD {token}", False,
                                                                     f"CANARY-OUT {token}"),
                         *_edit("e1", "tests/test_x.py", ok=False))
            return FakeProc(commit_file("hello.py", "raise SystemExit(1)\n"), cwd)
        rep = night.run_night(self.c, hours=1, launcher=launch, sleep=lambda s: None, max_tasks=1)
        text = rep.report_path.read_text()
        self.assertIn("T001 attempt 1:", text)
        for canary in ("CANARY-TEXT", "CANARY-CMD", "CANARY-OUT", "CANARY-EDIT", token):
            self.assertNotIn(canary, text)
        self.assertNotIn(token, (self.house / "attempts.jsonl").read_text())

    def test_following_merge_is_not_refused_as_tampering(self):
        self.add("T001")
        self.run_night({"T001": [commit_file("hello.py", "X = 1\n")]})
        self.assertEqual(self.porcelain(), "")
        self.assertEqual(guard.frozen_tampering(self.c), [])
        self.add("T002", run="true")
        wt = night.prepare_worktree(self.c, "T002")
        commit_file("two.py", "Y = 2\n")(wt)
        night._set(checks.load_task(self.house, "T002"), status="review")
        night._commit_paths(self.c, [self.house / "tasks/T002-x.md"], "review")
        out = merge.merge_task(self.c, "T002", wt)
        self.assertTrue(out.ok, out.message)

    def test_forged_register_is_not_committed_when_recording(self):
        self.add("T001")
        reg = self.house / "attempts.jsonl"
        night.record_attempt(self.c, attempts.Row(task="T001", n=1, source="night", agent="coder", tool="claude"),
                             "x")
        reg.write_text(reg.read_text() + '{"task": "T001", "n": 9, "outcome": "merged"}\n')
        with redirect_stderr(io.StringIO()) as err:
            ok = night.record_attempt(self.c, attempts.Row(task="T001", n=2, source="night", agent="coder",
                                                           tool="claude"), "x")
        self.assertIn("attempt register", err.getvalue())
        self.assertFalse(ok)
        self.assertNotIn('"n": 9', git(self.c, "show", "HEAD:.house/attempts.jsonl"))

    def row(self, n):
        return attempts.Row(task="T001", n=n, source="night", agent="coder", tool="claude")

    def test_failed_commit_leaves_no_row_behind(self):
        self.add("T001")
        hook = self.c / ".git/hooks/pre-commit"
        hook.write_text("#!/bin/sh\nexit 1\n")
        hook.chmod(0o755)
        with redirect_stderr(io.StringIO()) as err:
            ok = night.record_attempt(self.c, self.row(1), "row 1")
        self.assertFalse(ok)
        self.assertIn("attempt register", err.getvalue())
        self.assertFalse(guard.register_changed(self.c))
        self.assertEqual(guard.frozen_tampering(self.c), [])
        hook.unlink()
        self.assertTrue(night.record_attempt(self.c, self.row(2), "row 2"))
        self.assertEqual([r.n for r in self.rows()], [2])
        self.assertEqual(self.porcelain(), "")
        self.assertEqual(guard.frozen_tampering(self.c), [])

    def test_failed_commit_in_a_night_does_not_tamper_the_next_attempt(self):
        from unittest import mock
        self.add("T001")
        real = night._commit_paths
        calls = []

        def flaky(contest, paths, message):
            if any(p.name == "attempts.jsonl" for p in paths) and not calls:
                calls.append(message)
                return  # the commit "fails" once: index.lock, a hook, ...
            real(contest, paths, message)
        bad = commit_file("hello.py", "raise SystemExit(1)\n")
        with mock.patch.object(night, "_commit_paths", side_effect=flaky), redirect_stderr(io.StringIO()):
            rep = self.run_night({"T001": [bad, bad, bad]})
        self.assertEqual(len(calls), 1)
        self.assertNotIn("tampered", [a.outcome for a in rep.attempts])
        self.assertEqual(len(rep.attempts), 3)  # every attempt was evaluated, none was costed by the lost row
        self.assertEqual(self.status("T001"), "blocked")
        self.assertEqual([r.n for r in self.rows()], [2, 3])  # the first attempt has no row; its number is not reused
        self.assertEqual([a.n for a in rep.attempts], [1, 2, 3])
        logs = [a.log for a in rep.attempts]
        self.assertEqual(len(set(logs)), 3)  # log names never repeat
        self.assertEqual(self.porcelain(), "")

    def test_concurrent_records_get_consecutive_numbers(self):
        import threading
        self.add("T001")
        results = []

        def go():
            results.append(night.record_attempt(self.c, self.row(1), None))
        threads = [threading.Thread(target=go) for _ in range(2)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        self.assertEqual(sorted(results), [1, 2])
        self.assertEqual(sorted(r.n for r in self.rows()), [1, 2])
        self.assertEqual(self.porcelain(), "")
        self.assertEqual(guard.frozen_tampering(self.c), [])

    def test_lock_timeout_skips_the_row(self):
        import fcntl
        from unittest import mock
        self.add("T001")
        other = open(attempts.lock_path(self.c), "a+")  # noqa: SIM115 — a second holder of the lock
        self.addCleanup(other.close)
        fcntl.flock(other, fcntl.LOCK_EX)
        with mock.patch.object(attempts, "LOCK_TIMEOUT", 0.2), redirect_stderr(io.StringIO()) as err:
            ok = night.record_attempt(self.c, self.row(1), "row 1")
        fcntl.flock(other, fcntl.LOCK_UN)
        self.assertIsNone(ok)
        self.assertIn("attempt register", err.getvalue())
        self.assertFalse((self.house / "attempts.jsonl").exists())
        self.assertEqual(self.porcelain(), "")

    def test_question_from_a_blocking_worker_is_in_the_row(self):
        from houselib import evidence
        self.add("T001")

        def ask(cwd):
            q = self.house / "QUESTIONS.md"
            q.write_text(q.read_text() + "\n## Q-007 [open] Which API?\n- **Poziom:** krytyczny\n")
            task = self.house / "tasks/T001-x.md"
            task.write_text(task.read_text().replace("status: doing", "status: blocked"))
        rep = self.run_night({"T001": [ask]})
        self.assertEqual([a.outcome for a in rep.attempts], ["blocked-by-worker"])
        (row,) = self.rows()
        self.assertEqual(row.questions, ["Q-007"])
        facts = evidence.state_facts(self.c, row)
        self.assertEqual(facts.questions_added, ["Q-007"])
        self.assertIn("questions added:\n  Q-007", evidence.render_full(facts, row))

    def test_failure_commit_names_the_register_number(self):
        self.add("T001")
        night.record_attempt(self.c, attempts.Row(task="T001", n=1, source="merge", agent="coder", tool="claude"),
                             "row")
        self.run_night({"T001": [commit_file("hello.py", "raise SystemExit(1)\n")]}, max_tasks=1)
        subjects = git(self.c, "log", "--format=%s")
        self.assertIn("chore(house): night T001 attempt 2 ", subjects)
        self.assertNotIn("chore(house): night T001 attempt 1 ", subjects)

    def report_launcher(self, plan):
        """Launcher whose worker writes `$HOUSE_DIR/reports/T###.md` (bytes from `plan[tid]`, popped per attempt)
        and then behaves as `act` says ("hang" or a callable)."""
        def launch(cmd, cwd, log):
            tid = cwd.name.split("-", 1)[0]
            data, act = plan[tid].pop(0)
            if data is not None:
                (self.house / "reports").mkdir(exist_ok=True)
                (self.house / "reports" / f"{tid}.md").write_bytes(data)
            return FakeProc(act, cwd)
        return launch

    def committed(self, rel):
        return git(self.c, "ls-files", "--error-unmatch", rel).strip() == rel and \
            git(self.c, "status", "--porcelain", "--", rel).strip() == ""

    def test_report_written_then_timeout_is_copied_and_committed(self):
        self.add("T001")
        (self.house / "spec.yaml").write_text("night:\n  task_minutes: 0\n")
        git(self.c, "commit", "-qam", "spec")
        data = "# Report\n\nżółć \u2713\r\n".encode()
        night.run_night(self.c, hours=1, launcher=self.report_launcher({"T001": [(data, "hang")] * 3}),
                        sleep=lambda s: None, max_tasks=1)
        (row,) = self.rows("T001")
        self.assertEqual((row.outcome, row.report), ("timeout", ".house/reports/T001-attempt-1.md"))
        self.assertEqual((self.c / row.report).read_bytes(), data)
        self.assertTrue(self.committed(row.report))
        self.assertEqual(self.porcelain(), "")
        self.assertEqual(guard.frozen_tampering(self.c), [])

    def test_untouched_report_is_not_copied(self):
        self.add("T001")
        bad = commit_file("hello.py", "raise SystemExit(1)\n")
        self.run_night({"T001": [bad, bad, bad]})
        self.assertEqual([r.report for r in self.rows("T001")], ["", "", ""])
        self.assertEqual(list((self.house / "reports").glob("T001-attempt-*")), [])

    def test_stale_report_from_before_the_attempt_is_not_copied(self):
        self.add("T001")
        (self.house / "reports").mkdir(exist_ok=True)
        (self.house / "reports" / "T001.md").write_text("old\n")
        self.run_night({"T001": [commit_file("hello.py", "raise SystemExit(1)\n")] * 3}, max_tasks=1)
        (row,) = self.rows("T001")
        self.assertEqual(row.report, "")
        self.assertEqual(list((self.house / "reports").glob("T001-attempt-*")), [])

    def test_two_attempts_two_copies_with_own_numbers(self):
        self.add("T001")
        bad = commit_file("hello.py", "raise SystemExit(1)\n")
        plan = {"T001": [(b"first\n", bad), (b"second\n", bad), (None, bad)]}
        night.run_night(self.c, hours=1, launcher=self.report_launcher(plan), sleep=lambda s: None)
        rows = self.rows("T001")
        self.assertEqual([r.report for r in rows], [".house/reports/T001-attempt-1.md",
                                                    ".house/reports/T001-attempt-2.md", ""])
        self.assertEqual((self.c / rows[0].report).read_bytes(), b"first\n")
        self.assertEqual((self.c / rows[1].report).read_bytes(), b"second\n")
        self.assertFalse((self.house / "reports" / "T001-attempt-3.md").exists())
        for r in rows[:2]:
            self.assertTrue(self.committed(r.report))

    def test_copy_uses_the_number_the_register_moved_to(self):
        from unittest import mock
        self.add("T001")
        real = night.record_attempt
        fired = []

        def moved(contest, row, message=None, **kw):
            if not fired:  # an interactive merge takes number 1 first
                fired.append(1)
                real(contest, attempts.Row("T001", 1, "merge", "coder", "claude", outcome="merged"))
            return real(contest, row, message, **kw)
        plan = {"T001": [(b"body\n", commit_file("hello.py", "X = 1\n"))]}
        with mock.patch.object(night, "record_attempt", moved):
            night.run_night(self.c, hours=1, launcher=self.report_launcher(plan), sleep=lambda s: None)
        night_row = [r for r in self.rows("T001") if r.source == "night"][0]
        self.assertEqual((night_row.n, night_row.report), (2, ".house/reports/T001-attempt-2.md"))
        self.assertEqual((self.c / night_row.report).read_bytes(), b"body\n")
        self.assertFalse((self.house / "reports" / "T001-attempt-1.md").exists())

    def test_interrupted_attempt_with_a_report_is_copied(self):
        self.add("T001")

        def launch(cmd, cwd, log):
            (self.house / "reports").mkdir(exist_ok=True)
            (self.house / "reports" / "T001.md").write_bytes(b"half done\n")
            proc = FakeProc("hang", cwd)
            proc.poll = lambda: (_ for _ in ()).throw(KeyboardInterrupt())
            return proc
        night.run_night(self.c, hours=1, launcher=launch, sleep=lambda s: None)
        (row,) = self.rows("T001")
        self.assertEqual((row.outcome, row.report), ("interrupted", ".house/reports/T001-attempt-1.md"))
        self.assertEqual((self.c / row.report).read_bytes(), b"half done\n")
        self.assertTrue(self.committed(row.report))
        self.assertEqual(self.porcelain(), "")

    def test_failed_register_commit_leaves_no_copy(self):
        from unittest import mock
        self.add("T001")
        real = night._commit_paths

        def broken(contest, paths, message):
            if any(p.name == "attempts.jsonl" for p in paths):
                raise RuntimeError("commit failed")
            return real(contest, paths, message)
        plan = {"T001": [(b"body\n", commit_file("hello.py", "raise SystemExit(1)\n"))] * 3}
        with mock.patch.object(night, "_commit_paths", broken):
            night.run_night(self.c, hours=1, launcher=self.report_launcher(plan), sleep=lambda s: None,
                            max_tasks=1)
        self.assertEqual(self.rows("T001"), [])
        self.assertEqual(list((self.house / "reports").glob("T001-attempt-*")), [])

    def test_failing_commit_hook_leaves_no_staged_copy(self):
        """The copy is already `git add`ed when the register commit fails: it must leave index and disk."""
        self.add("T001")
        hook = self.c / ".git" / "hooks" / "pre-commit"
        hook.write_text("#!/bin/sh\ngit diff --cached --name-only | grep -q attempts.jsonl && exit 1\nexit 0\n")
        hook.chmod(0o755)
        plan = {"T001": [(b"body\n", commit_file("hello.py", "raise SystemExit(1)\n"))] * 3}
        with redirect_stderr(io.StringIO()):
            night.run_night(self.c, hours=1, launcher=self.report_launcher(plan), sleep=lambda s: None,
                            max_tasks=1)
        self.assertEqual(self.rows("T001"), [])
        self.assertEqual(list((self.house / "reports").glob("T001-attempt-*")), [])
        self.assertNotIn("attempt-", git(self.c, "status", "--porcelain"))
        self.assertNotIn("attempts.jsonl", git(self.c, "status", "--porcelain"))

    def test_existing_copy_with_other_content_is_not_overwritten(self):
        self.add("T001")
        (self.house / "reports").mkdir(exist_ok=True)
        other = self.house / "reports" / "T001-attempt-1.md"
        other.write_bytes(b"someone else\n")
        plan = {"T001": [(b"body\n", commit_file("hello.py", "raise SystemExit(1)\n"))] * 3}
        err = io.StringIO()
        with redirect_stderr(err):
            night.run_night(self.c, hours=1, launcher=self.report_launcher(plan), sleep=lambda s: None,
                            max_tasks=1)
        (row,) = self.rows("T001")
        self.assertEqual(row.report, "")
        self.assertEqual(other.read_bytes(), b"someone else\n")
        self.assertIn("exists with other content", err.getvalue())

    def test_symlinked_report_is_not_copied(self):
        self.add("T001")
        secret = self.c.parent / f"{self.c.name}-outside.txt"
        secret.write_text("outside the contest\n")
        self.addCleanup(secret.unlink, missing_ok=True)

        def launch(cmd, cwd, log):
            (self.house / "reports").mkdir(exist_ok=True)
            link = self.house / "reports" / "T001.md"
            link.unlink(missing_ok=True)
            link.symlink_to(secret)
            return FakeProc(commit_file("hello.py", "raise SystemExit(1)\n"), cwd)
        with redirect_stderr(io.StringIO()):
            night.run_night(self.c, hours=1, launcher=launch, sleep=lambda s: None, max_tasks=1)
        (row,) = self.rows("T001")
        self.assertEqual(row.report, "")
        self.assertEqual(list((self.house / "reports").glob("T001-attempt-*")), [])

    def test_report_copy_is_redacted(self):
        self.add("T001")
        data = b"# Report\nrun with GITHUB_TOKEN=ghp_abcdefghijklmnopqrstuvwxyz0123456789 set\n"
        plan = {"T001": [(data, commit_file("hello.py", "raise SystemExit(1)\n"))] * 3}
        night.run_night(self.c, hours=1, launcher=self.report_launcher(plan), sleep=lambda s: None, max_tasks=1)
        (row,) = self.rows("T001")
        text = (self.c / row.report).read_text()
        self.assertIn("[REDACTED]", text)
        self.assertNotIn("ghp_abcdefghijklmnop", text)
        self.assertNotIn("ghp_abcdefghijklmnop", git(self.c, "show", f"HEAD:{row.report}"))
        self.assertTrue(self.committed(row.report))

    def test_large_report_copy_is_truncated(self):
        self.add("T001")
        (self.house / "reports").mkdir(exist_ok=True)
        big = self.house / "reports" / "T001.md"
        big.write_bytes(b"line of the report\n" * 40_000)  # 760 KB
        n = night.record_attempt(self.c, self.row(1), "row 1", report_from=big)
        (row,) = self.rows("T001")
        data = (self.c / row.report).read_bytes()
        self.assertEqual(n, 1)
        self.assertLessEqual(len(data), night.MAX_REPORT_COPY + 64)
        self.assertTrue(data.endswith("\u2026 truncated\n".encode()))
        self.assertTrue(data.startswith(b"line of the report\n"))

    def test_report_copy_never_writes_through_a_symlinked_target(self):
        self.add("T001")
        (self.house / "reports").mkdir(exist_ok=True)
        target = self.c.parent / f"{self.c.name}-target.txt"
        target.write_text("keep\n")
        self.addCleanup(target.unlink, missing_ok=True)
        (self.house / "reports" / "T001-attempt-1.md").symlink_to(target)
        src = self.house / "reports" / "T001.md"
        src.write_text("body\n")
        with redirect_stderr(io.StringIO()):
            night.record_attempt(self.c, self.row(1), "row 1", report_from=src)
        self.assertEqual(target.read_text(), "keep\n")
        (row,) = self.rows("T001")
        self.assertEqual(row.report, "")

    def test_interrupt_inside_the_register_commit_restores_the_register(self):
        from unittest import mock
        self.add("T001")
        self.assertTrue(night.record_attempt(self.c, self.row(1), "row 1"))

        def stop_after_staging(contest, paths, message):
            git(contest, "add", "--", *[str(p.relative_to(contest)) for p in paths])
            raise KeyboardInterrupt
        with mock.patch.object(night, "_commit_paths", side_effect=stop_after_staging), \
                self.assertRaises(KeyboardInterrupt):
            night.record_attempt(self.c, self.row(2), "row 2")
        self.assertEqual(self.porcelain(), "")  # neither modified nor staged
        self.assertEqual([r.n for r in self.rows()], [1])
        self.assertEqual(guard.frozen_tampering(self.c), [])

    def test_interrupt_inside_the_first_register_commit_removes_the_file(self):
        from unittest import mock
        self.add("T001")
        with mock.patch.object(night, "_commit_paths", side_effect=KeyboardInterrupt), \
                self.assertRaises(KeyboardInterrupt):
            night.record_attempt(self.c, self.row(1), "row 1")
        self.assertFalse((self.house / "attempts.jsonl").exists())
        self.assertEqual(self.porcelain(), "")

    def test_row_head_is_the_leftovers_commit_the_gate_evaluated(self):
        self.add("T001")
        rep = self.run_night({"T001": [uncommitted_work]})
        self.assertEqual([a.outcome for a in rep.attempts], ["merged"])
        (row,) = self.rows()
        self.assertNotEqual(row.head, row.base)
        self.assertIn("uncommitted work", git(self.c, "show", "-s", "--format=%s", row.head))

    def test_could_not_start_restores_a_forged_register_and_numbers_from_head(self):
        from unittest import mock
        self.add("T001", extra="agent: coder-codex\n")
        (self.house / "attempts.jsonl").write_text('{"task": "T001", "n": 1000, "source": "night"}\n')
        with mock.patch.object(night, "prepare_worktree", side_effect=RuntimeError("boom")):
            rep = self.run_night({"T001": []}, max_tasks=1)
        (row,) = self.rows()
        self.assertEqual((row.n, row.outcome, row.agent), (1, "error", "coder-codex"))
        self.assertNotIn("1000", git(self.c, "show", "HEAD:.house/attempts.jsonl"))
        self.assertEqual(self.porcelain(), "")
        self.assertIn("restored from HEAD", rep.report_path.read_text())
        self.assertIn("Tampering found", rep.report_path.read_text())

    def test_interrupted_restores_a_forged_register_and_keeps_the_row(self):
        self.add("T001")

        def launch(cmd, cwd, log):
            (self.house / "attempts.jsonl").write_text('{"task": "T001", "n": 1000, "outcome": "merged"}\n')
            proc = FakeProc("hang", cwd)
            proc.poll = lambda: (_ for _ in ()).throw(KeyboardInterrupt())
            return proc
        rep = night.run_night(self.c, hours=1, launcher=launch, sleep=lambda s: None)
        (row,) = self.rows()
        self.assertEqual((row.n, row.outcome), (1, "interrupted"))
        self.assertEqual(self.porcelain(), "")
        self.assertIn("restored from HEAD", rep.report_path.read_text())

    def test_heal_error_in_finally_keeps_the_report(self):
        from unittest import mock
        self.add("T001")

        def launch(cmd, cwd, log):
            proc = FakeProc("hang", cwd)
            proc.poll = lambda: (_ for _ in ()).throw(KeyboardInterrupt())
            return proc
        real = guard.register_changed
        calls = []

        def flaky(contest):
            calls.append(1)
            if len(calls) > 1:  # fine while the attempt starts, broken from the end of the night on
                raise OSError("git is gone")
            return real(contest)
        with mock.patch.object(guard, "register_changed", side_effect=flaky), redirect_stderr(io.StringIO()):
            rep = night.run_night(self.c, hours=1, launcher=launch, sleep=lambda s: None)
        self.assertIsNotNone(rep.report_path)
        self.assertTrue(rep.report_path.is_file())
        self.assertEqual([a.outcome for a in rep.attempts], ["interrupted"])


class NextBriefTest(Base):
    """`night.next_brief`: the hand-off brief of attempt 2 and 3 (A2b)."""
    ERROR = "ImportError: cannot import name 'frobnicate' from 'hello' (/x/hello.py)"
    HEADERS = ("Goal", "Next", "Critical context", "Constraints and human decisions", "Dead ends",
               "Decisions and facts", "Errors and fixes", "Done")

    def setUp(self):
        super().setUp()
        self.add("T001")
        self.wt = self.c / ".worktrees" / "T001-x"

    def attempt(self, n, report=None, records=None, outcome="failed", tool="claude", message="", commit=True,
                start="2026-11-17T01:00:00", **kw):
        log = ""
        if records is not None:
            path = self.house / "tmp" / "night" / "d" / f"T001-{n}.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("\n".join(json.dumps(r) for r in records) + "\n")
            log = path.relative_to(self.c).as_posix()
        rel = ""
        if report is not None:
            copy = self.house / "reports" / f"T001-attempt-{n}.md"
            copy.parent.mkdir(exist_ok=True)
            copy.write_text(report)
            rel = copy.relative_to(self.c).as_posix()
            if commit:  # night commits the copy with the register row; the brief reads it from HEAD
                git(self.c, "add", "--", rel)
                git(self.c, "commit", "-q", "--allow-empty", "-m", f"copy {n}", "--", rel)
        attempts.record(self.house, attempts.Row(
            task="T001", n=n, source="night", agent="coder", tool=tool, log=log, report=rel, outcome=outcome,
            message=message, start=start, end="2026-11-17T01:30:00", **kw))

    def failing_log(self, error=None):
        check = f"house check c1 T001 --worktree {self.wt}"
        return [*_edit("e1", f"{self.c}/hello.py"), *_bash("c1", check, err=True, out=f"noise\n{error or self.ERROR}"),
                _text("CANARY-TEXT said hello"), *_bash("b1", "ls", out="CANARY-OUT")]

    def brief(self, n=3, tool="claude"):
        return night.next_brief(self.c, "T001", self.wt, n, tool)

    def section(self, text, name):
        """The text of `## name` up to the next `## ` heading."""
        head = f"## {name}\n"
        start = text.index(head) + len(head)
        ends = [e for e in (text.find("\n## ", start), text.find("\n\nFull record: ", start)) if e >= 0]
        return text[start:min(ends, default=len(text))]

    def test_third_attempt_has_dead_ends_of_both_earlier_attempts(self):
        self.attempt(1, "## Goal\nMake hello importable.\n\n## Next\nTry the stub.\n\n## Dead ends\n"
                        "- tried the stub module; imports still fail. Do not repeat.\n"
                        "- mocked the clock; it hid the race. 2 tries. Do not repeat.\n")
        # attempt 2 timed out; its report was written mid-work and holds only what was known by then
        self.attempt(2, "## Goal\nMake hello importable.\n\n## Next\nRename frobnicate.\nNext action: "
                        "grep -n frobnicate hello.py\nDone when: it passes\n\n## Dead ends\n"
                        "- slept in the loop; flaky. Do not repeat.\n- mocked the clock; it hid the race. "
                        "2 tries.   Do not repeat.\n", self.failing_log(), outcome="timeout", message="timed out")
        text = self.brief()
        headers = [ln[3:] for ln in text.splitlines() if ln.startswith("## ")]
        self.assertEqual(headers, list(self.HEADERS))
        dead = self.section(text, "Dead ends")
        self.assertIn("slept in the loop; flaky. Do not repeat. (worker report, attempt 2)", dead)
        self.assertIn("tried the stub module; imports still fail. Do not repeat. (worker report, attempt 1)", dead)
        # the same entry in both reports after whitespace normalisation: kept once, with the oldest attempt
        self.assertEqual(dead.count("mocked the clock"), 1)
        self.assertIn("2 tries. Do not repeat. (worker report, attempt 1)", dead)
        self.assertIn("```\nnoise\n" + self.ERROR + "\n```", self.section(text, "Errors and fixes"))
        done_when = (f"Done when: `{night.HOUSE_ROOT}/bin/house check c1 T001 --worktree {self.wt}` prints GREEN, "
                     "then set `status: review`")
        self.assertIn(done_when, self.section(text, "Next"))
        # attempt 2 timed out: code writes the action, the worker's plan stays as a labelled line
        self.assertIn("Next action: attempt 2 ended `timeout`", self.section(text, "Next"))
        self.assertIn("The earlier worker planned (worker report, attempt 2): grep -n frobnicate hello.py",
                      self.section(text, "Next"))
        self.assertIn(str(next((self.house / "tasks").glob("T001-*.md"))), self.section(text, "Next"))
        self.assertTrue(self.section(text, "Next").rstrip().endswith(done_when))
        self.assertIn("Make hello importable.", self.section(text, "Goal"))
        self.assertIn("check after last edit: yes, RED", self.section(text, "Critical context"))

    def test_distinct_entries_keep_their_own_attempt_numbers(self):
        self.attempt(1, "## Dead ends\n- one\n  continued line\n- two\n")
        self.attempt(2, "## Dead ends\nnone\n")
        dead = self.section(self.brief(), "Dead ends")
        self.assertIn("- one\n  continued line (worker report, attempt 1)\n- two (worker report, attempt 1)", dead)
        self.assertNotIn("attempt 2", dead)

    def test_no_report_gives_code_sections_and_the_placeholder(self):
        self.attempt(1, outcome="interrupted")
        self.attempt(2, records=self.failing_log())
        text = self.brief()
        for name in ("Constraints and human decisions", "Decisions and facts"):
            self.assertEqual(self.section(text, name).strip(), "no report from attempt 2")
        self.assertTrue(self.section(text, "Goal").startswith("no report from attempt 2; task title: x"))
        self.assertIn("no report from attempt 2", self.section(text, "Next"))
        self.assertIn("Next action: run the Done-when check and fix what it reports, starting from the newest error "
                      "under Errors and fixes", self.section(text, "Next"))
        self.assertEqual(self.section(text, "Dead ends").strip(), "none")
        self.assertIn(self.ERROR, self.section(text, "Errors and fixes"))
        self.assertIn("check after last edit: yes, RED", self.section(text, "Critical context"))
        self.assertEqual(self.section(text, "Done").strip(), "none")  # no commits recorded: no changed files
        self.assertIn("### Unverified\nno report from attempt 2", self.section(text, "Critical context"))

    def test_older_report_is_used_and_named_when_the_previous_attempt_has_none(self):
        self.attempt(1, "## Goal\nOld goal.\n\n## Constraints and human decisions\nD-007: keep the API.\n\n"
                        "## Critical context\n### Observed\n- x\n### Unverified\n- the cache is stale\n")
        self.attempt(2, records=self.failing_log())
        text = self.brief()
        goal = self.section(text, "Goal")
        self.assertIn("no report from attempt 2", goal)
        self.assertIn("attempt 1", goal)
        self.assertIn("Old goal.", goal)
        self.assertIn("D-007: keep the API.", self.section(text, "Constraints and human decisions"))
        unverified = self.section(text, "Critical context").split("### Unverified")[1]
        self.assertIn("the cache is stale", unverified)
        self.assertNotIn("- x", self.section(text, "Critical context"))  # the worker's Observed is not carried over

    def test_done_lists_changed_files_and_verification(self):
        base = night.rev(self.c, "HEAD")
        (self.c / "hello.py").write_text("x = 1\n")
        git(self.c, "add", "hello.py")
        git(self.c, "commit", "-qm", "work")
        head = night.rev(self.c, "HEAD")
        green = [*_edit("e1", f"{self.c}/hello.py"),
                 *_bash("c1", f"house check c1 T001 --worktree {self.wt}", err=False, out="GREEN")]
        self.attempt(1, records=green, base=base, head=head)
        text = self.brief(2)
        done = self.section(text, "Done")
        self.assertIn("hello.py", done)
        self.assertIn("(verified)", done)
        self.assertNotIn("not verified", done)
        self.assertIn("check after last edit: yes, GREEN", self.section(text, "Critical context"))
        red = [*_edit("e1", f"{self.c}/hello.py"),
               *_bash("c1", f"house check c1 T001 --worktree {self.wt}", err=True, out="RED")]
        self.attempt(2, records=red, base=base, head=head)
        done = self.section(self.brief(3), "Done")
        self.assertIn("hello.py", done)
        self.assertIn("(not verified)", done)

    def test_over_the_ceiling_is_cut_from_the_end(self):
        decisions = "".join(f"- D-{i:03d}: " + "x" * 300 + "\n" for i in range(40))
        self.attempt(1, "## Goal\nKeep this goal.\n\n## Next\nNext action: run the thing\n\n"
                        "## Dead ends\n- early dead end. Do not repeat.\n\n"
                        f"## Decisions and facts\n{decisions}\n## Done\nLATE-MARKER\n", self.failing_log())
        text = self.brief(2)
        self.assertLessEqual(len(text), night.MAX_BRIEF)
        self.assertEqual(night.MAX_BRIEF, 8000)
        self.assertIn("Keep this goal.", self.section(text, "Goal"))
        self.assertIn("Next action: run the thing", text)
        self.assertIn("Done when:", text)
        self.assertNotIn("## Done", text)
        self.assertEqual(text.rstrip().splitlines()[-2],
                         f"(cut to 8000 characters; full reports: {self.house}/reports/T001-attempt-*.md)")
        self.assertTrue(text.rstrip().splitlines()[-1].startswith("Full record: "))
        self.assertIn("early dead end", text)
        self.assertIn("D-000:", text)

    def test_cut_never_leaves_a_code_fence_open(self):
        body = "```\n" + "".join(f"line {i} " + "y" * 90 + "\n" for i in range(150)) + "```\n"
        self.attempt(1, f"## Goal\ng\n\n## Decisions and facts\n{body}", self.failing_log())
        text = self.brief(2)
        self.assertIn("(cut to 8000 characters;", text)
        self.assertLessEqual(len(text), night.MAX_BRIEF)
        self.assertEqual(night._close_fences(text), text)  # every fence is closed
        self.assertIn("```\n\n(cut to", text)

    def test_errors_are_listed_newest_first(self):
        check = f"house check c1 T001 --worktree {self.wt}"
        log = [*_bash("g1", "grep -n nothing hello.py", err=True, out="grep: no match OLDEST-ERROR"),
               *_edit("e1", f"{self.c}/hello.py"),
               *_bash("r1", "cat missing.txt", err=True, out="cat: missing.txt: No such file MIDDLE-ERROR"),
               *_bash("c1", check, err=True, out=self.ERROR)]
        self.attempt(1, records=log)
        text = self.brief(2)
        errors = self.section(text, "Errors and fixes")
        self.assertTrue(errors.startswith("log of attempt 1, newest first (source: /"))
        self.assertLess(errors.index(self.ERROR), errors.index("MIDDLE-ERROR"))
        self.assertLess(errors.index("MIDDLE-ERROR"), errors.index("OLDEST-ERROR"))
        self.assertIn("starting from the newest error under Errors and fixes", self.section(text, "Next"))
        self.assertNotIn("first error", self.section(text, "Next"))

    def test_dead_end_dedup_ignores_the_list_marker(self):
        self.attempt(1, "## Dead ends\n1. tried the cache\n2. tried the proxy\n")
        self.attempt(2, "## Dead ends\n- tried   the cache\n* tried the proxy\n")
        dead = self.section(self.brief(3), "Dead ends")
        self.assertEqual(dead.count("tried the cache") + dead.count("tried   the cache"), 1)
        self.assertEqual(dead.count("tried the proxy"), 1)
        self.assertIn("(worker report, attempt 1)", dead)
        self.assertNotIn("attempt 2", dead)

    def test_dead_end_ending_with_a_fence_gets_its_suffix_on_its_own_line(self):
        self.attempt(1, "## Dead ends\n- the loop\n  ```\n  for x in y:\n\n      pass\n  ```\n- next one\n"
                        "- unclosed\n  ```\n  code\n")
        dead = self.section(self.brief(2), "Dead ends")
        self.assertIn("  ```\n  (worker report, attempt 1)\n- next one (worker report, attempt 1)", dead)
        self.assertIn("      pass", dead)  # a blank line inside the fence did not split the entry
        self.assertEqual(night._close_fences(dead), dead)
        self.assertIn("```\n  (worker report, attempt 1)", dead.split("- unclosed")[1])

    def test_dead_ends_are_capped_and_do_not_push_out_the_code_facts(self):
        dead = "".join(f"- approach {i}: " + "x" * 300 + " Do not repeat.\n" for i in range(60))
        self.attempt(1, f"## Goal\ng\n\n## Dead ends\n{dead}", self.failing_log())
        text = self.brief(2)
        section = self.section(text, "Dead ends")
        self.assertLessEqual(len(section), 2700)
        self.assertIn("approach 0:", section)
        self.assertRegex(section, r"\u2026 \d+ more dead ends in /\S+/\.house/reports/T001-attempt-\*\.md\n?$")
        self.assertIn(self.ERROR, text)
        self.assertNotIn("(cut to", text)

    def test_capped_goal_and_next_do_not_leave_a_fence_open(self):
        code = "```\n" + "z" * 5000 + "\n```\n"
        self.attempt(1, f"## Goal\n{code}\n## Next\n{code}Next action: go\n")
        text = self.brief(2)
        goal, nxt = self.section(text, "Goal"), self.section(text, "Next")
        self.assertEqual(night._close_fences(goal), goal)
        self.assertEqual(night._close_fences(nxt), nxt)
        self.assertTrue(nxt.index("Task file:") > nxt.rindex("```"))
        self.assertEqual(night._close_fences(text), text)

    def test_unclosed_fence_in_a_report_section_does_not_swallow_the_next_heading(self):
        self.attempt(1, "## Goal\n```\nnever closed\n")
        text = self.brief(2)
        self.assertEqual(night._close_fences(text), text)
        self.assertIn("## Next", text)

    def test_next_action_needs_its_colon(self):
        self.attempt(1, "## Next\nNext actions to try:\n- a\n- b\n")
        nxt = self.section(self.brief(2), "Next")
        self.assertIn("Next actions to try:", nxt)
        self.assertIn("Next action: run the Done-when check", nxt)
        self.attempt(2, "## Next\n**Next action:** do it\nNext action**: ignored second\n")
        self.assertIn("Next action: do it\n", self.section(self.brief(3), "Next"))

    def test_fallback_brief_keeps_task_file_and_sub_headings(self):
        from unittest import mock
        with mock.patch.object(night, "_next_brief", side_effect=RuntimeError("boom")):
            text = self.brief(2)
        self.assertEqual([ln[3:] for ln in text.splitlines() if ln.startswith("## ")], list(self.HEADERS))
        self.assertIn("### Observed", text)
        self.assertIn("### Unverified", text)
        self.assertIn("Task file: " + str(next((self.house / "tasks").glob("T001-*.md"))), text)
        self.assertIn("Done when:", self.section(text, "Next"))

    def test_long_diffstat_names_the_command_for_the_full_list(self):
        base = night.rev(self.c, "HEAD")
        for i in range(12):
            (self.c / f"f{i}.py").write_text("x = 1\n")
        git(self.c, "add", ".")
        git(self.c, "commit", "-qm", "many")
        head = night.rev(self.c, "HEAD")
        self.attempt(1, records=self.failing_log(), base=base, head=head)
        done = self.section(self.brief(2), "Done")
        self.assertIn("12 files changed", done)
        self.assertIn(f"- full list: git diff --stat {base}..{head}", done)

    def test_huge_goal_and_next_still_fit_the_ceiling(self):
        self.attempt(1, f"## Goal\n{'g' * 20000}\n\n## Next\n{'n' * 20000}\nNext action: {'a' * 5000}\n")
        text = self.brief(2)
        self.assertLessEqual(len(text), night.MAX_BRIEF)
        self.assertIn("Done when:", text)
        self.assertIn("Next action:", text)

    def test_short_brief_has_no_cut_line(self):
        self.attempt(1, "## Goal\nshort\n")
        self.assertNotIn("cut to", self.brief(2))

    def test_codex_done_when_differs(self):
        self.attempt(1, "## Goal\ng\n\n## Next\nDone when: all good\n", tool="codex")
        claude, codex = self.brief(2, "claude"), self.brief(2, "codex")
        want = ("Done when: the acceptance criteria pass in the working directory and the report is written "
                "(do not commit; the driver does)")
        self.assertIn(want, self.section(codex, "Next"))
        self.assertNotIn("status: review", codex)
        self.assertNotIn("all good", codex)  # the report's own condition is replaced
        self.assertIn("prints GREEN", claude)
        self.assertNotIn("the driver does", claude)

    def test_old_template_report_is_parsed(self):
        self.attempt(1, "# Report T001\n\n## Goal\nport the parser\n\n## What was done\nCODE-DONE-CLAIM\n\n"
                        "## Tested and did not work\n- regex only; breaks on nesting\n\n## Next steps\n"
                        "Handle nesting.\n\n## Evidence\nEVIDENCE-TEXT\n")
        text = self.brief(2)
        self.assertIn("regex only; breaks on nesting (worker report, attempt 1)", self.section(text, "Dead ends"))
        self.assertIn("port the parser", self.section(text, "Goal"))
        self.assertIn("Handle nesting.", self.section(text, "Next"))
        self.assertNotIn("CODE-DONE-CLAIM", text)  # Done comes from code, not from the report
        self.assertNotIn("EVIDENCE-TEXT", text)

    def test_nothing_from_the_session_log_enters_the_brief_but_error_tails(self):
        key = "sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789"
        self.attempt(1, f"## Goal\ng {key}\n", self.failing_log())
        text = self.brief(2)
        self.assertNotIn("CANARY", text)
        self.assertNotIn(key, text)
        self.assertIn("[REDACTED]", text)
        self.assertIn(self.ERROR, text)

    def test_report_outside_the_contest_or_missing_is_no_report(self):
        outside = Path(self._tmp.name) / "evil.md"
        outside.write_text("## Goal\nOUTSIDE\n")
        self.attempt(1, outcome="failed", report=None)
        attempts.record(self.house, attempts.Row(task="T001", n=2, source="night", agent="coder", tool="claude",
                                                 report="../evil.md"))
        attempts.record(self.house, attempts.Row(task="T001", n=3, source="night", agent="coder", tool="claude",
                                                 report=".house/reports/T001-attempt-3.md"))
        text = self.brief(4)
        self.assertNotIn("OUTSIDE", text)
        self.assertTrue(self.section(text, "Goal").startswith("no report from attempt 3;"))

    def test_never_raises(self):
        text = night.next_brief(self.c, "T999", self.wt, 5, "claude")  # no task file, no rows
        self.assertEqual([ln[3:] for ln in text.splitlines() if ln.startswith("## ")], list(self.HEADERS))
        self.assertIn("no report from attempt 4", text)
        self.assertIn("Done when:", text)
        first = night.next_brief(self.c, "T001", self.wt, 1, "claude")
        self.assertNotIn("no report from attempt 0", first)


    # --- final-review fixes (A2b) ---
    def fail_gate(self, tails, at="2026-11-17T01:20:00+01:00"):
        """Record a red gate run in the contest's checks file: {criterion id: output tail} all failing."""
        path = self.house / "checks" / "T001.json"
        data = json.loads(path.read_text())
        for cid, tail in tails.items():
            data["results"][cid] = {"id": cid, "passes": False, "exit": 1, "output_tail": tail, "ran_at": at,
                                    "commit": None}
        data["gate"] = {"green": False, "init_ok": True, "mismatch": False, "at": at}
        path.write_text(json.dumps(data))

    # B1
    def test_placeholder_and_none_actions_are_not_actions(self):
        for action in ("<a concrete next command or edit>", "**none**", "`n/a`", "-", ""):
            with self.subTest(action=action):
                self.attempt(1, f"## Goal\ng\n\n## Next\nNext action: {action}\nDone when tests pass\n")
                nxt = self.section(self.brief(2), "Next")
                self.assertIn("Next action: run the Done-when check", nxt)
                self.assertNotIn("tests pass", nxt)
                self.assertEqual(nxt.count("Next action:"), 1)

    def test_gate_outcome_writes_the_action(self):
        for outcome in ("merge-refused", "reverted", "no-commits", "error", "tampered", "timeout"):
            with self.subTest(outcome=outcome):
                self.attempt(1, "## Goal\ng\n\n## Next\nNext action: polish the docs\n", outcome=outcome)
                nxt = self.section(self.brief(2), "Next")
                self.assertIn(f"Next action: attempt 1 ended `{outcome}` (see Observed and Errors and fixes): fix "
                              "that first, then run the Done-when check", nxt)
                self.assertNotIn("Next action: polish the docs", nxt)
        self.attempt(1, "## Goal\ng\n\n## Next\nNext action: polish the docs\n", outcome="blocked-by-worker")
        self.assertIn("Next action: polish the docs", self.section(self.brief(2), "Next"))

    # B2
    def test_gate_output_is_the_newest_error_and_the_default_action_starts_there(self):
        key = "sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789"
        self.fail_gate({"AC-1": "q" * 3000 + f"\nAssertionError: GATE-TAIL-1 {key}"})
        self.attempt(1, "## Goal\ng\n", self.failing_log())
        text = self.brief(2)
        errors = self.section(text, "Errors and fixes")
        self.assertTrue(errors.startswith("last check"), errors[:200])
        self.assertIn(f"(source: {self.house}/checks/T001.json)", errors)
        self.assertIn("AC-1 (exit 1):\n```", errors)
        self.assertLess(errors.index("GATE-TAIL-1"), errors.index(self.ERROR))
        self.assertNotIn(key, text)
        self.assertLess(len(errors.split(self.ERROR)[0]), 1900)
        self.assertEqual(night._close_fences(errors), errors)
        self.assertIn("last check: RED (AC-1) at 2026-11-17T01:20:00+01:00", self.section(text, "Critical context"))
        self.assertIn("Next action: run the Done-when check and fix what it reports, starting from the failing "
                      "criteria of the last check", self.section(text, "Next"))

    def test_last_check_older_than_the_attempt_says_so(self):
        self.fail_gate({"AC-1": "boom"}, at="2026-11-17T00:10:00")
        self.attempt(1, "## Goal\ng\n", self.failing_log())  # started 01:00
        self.assertIn("at 2026-11-17T00:10:00, before attempt 1 started",
                      self.section(self.brief(2), "Critical context"))

    # B3
    def test_pointers_are_absolute_and_the_full_record_ends_the_brief(self):
        self.attempt(1, "## Goal\ng\n", self.failing_log())
        text = self.brief(2)
        log = self.house / "tmp" / "night" / "d" / "T001-1.jsonl"
        last = text.rstrip().splitlines()[-1]
        self.assertEqual(last, f"Full record: {log}; {night.HOUSE_ROOT}/bin/house evidence c1 T001 --attempt 1")
        self.assertIn(f"{self.house}/attempts.jsonl", text)
        self.assertNotRegex(text, r"(?<![\w/.-])\.house/")
        decisions = "".join(f"- D-{i:03d}: " + "x" * 300 + "\n" for i in range(40))
        self.attempt(2, f"## Goal\ng\n\n## Decisions and facts\n{decisions}", self.failing_log())
        text = self.brief(3)
        lines = text.rstrip().splitlines()
        self.assertLessEqual(len(text), night.MAX_BRIEF)
        self.assertTrue(lines[-1].startswith("Full record: "), lines[-1])
        self.assertTrue(lines[-1].endswith("--attempt 2"))
        self.assertIn(f"(cut to 8000 characters; full reports: {self.house}/reports/T001-attempt-*.md)", text)

    # B4
    def test_dead_end_keeps_the_oldest_attempt_and_ignores_a_copied_suffix(self):
        self.attempt(1, "## Dead ends\n- tried the cache. Do not repeat.\n")
        self.attempt(2, "## Dead ends\n- tried the cache. Do not repeat. (worker report, attempt 1)\n- new one\n")
        dead = self.section(self.brief(3), "Dead ends")
        self.assertEqual(dead.count("tried the cache"), 1)
        self.assertIn("tried the cache. Do not repeat. (worker report, attempt 1)", dead)
        self.assertNotIn("(worker report, attempt 1) (worker report", dead)
        self.assertLess(dead.index("new one (worker report, attempt 2)"), dead.index("tried the cache"))

    def test_dead_end_subheading_and_bare_bullet(self):
        self.attempt(1, "## Dead ends\n### Attempt 1\n-\n  the proxy route; 2 tries.\n  do not repeat.\n- other\n")
        dead = self.section(self.brief(2), "Dead ends")
        self.assertNotIn("Attempt 1 (worker report", dead)
        self.assertNotIn("### Attempt 1", dead)
        self.assertIn("- the proxy route; 2 tries.\n  do not repeat. (worker report, attempt 1)", dead)
        self.assertIn("- other (worker report, attempt 1)", dead)

    # B5
    def test_dead_ends_are_not_budgeted_when_the_brief_fits(self):
        dead = "".join(f"- approach {i}: " + "x" * 300 + " Do not repeat.\n" for i in range(12))  # ~4 KB
        self.attempt(1, f"## Goal\ng\n\n## Dead ends\n{dead}")
        text = self.brief(2)
        self.assertLessEqual(len(text), night.MAX_BRIEF)
        self.assertIn("approach 11:", text)
        self.assertNotIn("more dead ends", text)

    def test_budgeted_dead_ends_truncate_long_entries_and_keep_every_attempt(self):
        self.attempt(1, "## Goal\ng\n\n## Dead ends\n- OLDEST short. Do not repeat.\n")
        big = "- HUGE " + "h" * 5000 + "\n- SHORT-AFTER-HUGE. Do not repeat.\n"
        many = "".join(f"- filler {i}: " + "f" * 400 + "\n" for i in range(15))
        decisions = "".join(f"- D-{i:03d}: " + "x" * 200 + "\n" for i in range(10))
        self.attempt(2, f"## Goal\ng\n\n## Dead ends\n{big}{many}\n## Decisions and facts\n{decisions}")
        text = self.brief(3)
        dead = self.section(text, "Dead ends")
        self.assertLessEqual(len(text), night.MAX_BRIEF)
        self.assertIn("HUGE", dead)
        self.assertIn("\u2026", dead.split("HUGE")[1].split("\n")[0])
        self.assertLess(len(dead.split("HUGE")[1].split("\n")[0]), 700)
        self.assertIn("SHORT-AFTER-HUGE", dead)
        self.assertIn("OLDEST short", dead)
        self.assertIn("more dead ends", dead)

    # B6
    def test_report_without_goal_next_or_dead_ends_is_no_report(self):
        self.attempt(1, "## Goal\nOld goal.\n\n## Next\nOld next.\n")
        self.attempt(2, "# Report\n\nall fine, nothing to say\n\n## Constraints and human decisions\nD-1\n")
        text = self.brief(3)
        goal, nxt = self.section(text, "Goal"), self.section(text, "Next")
        self.assertIn("no report from attempt 2", goal)
        self.assertIn("Old goal.", goal)
        self.assertIn("attempt 1", goal)
        self.assertIn("Old next.", nxt)

    def test_goal_from_the_newest_report_that_has_one_else_the_task_title(self):
        self.attempt(1, "## Goal\nOld goal.\n")
        self.attempt(2, "## Next\nNew next.\n")
        text = self.brief(3)
        self.assertIn("Old goal.", self.section(text, "Goal"))
        self.assertIn("attempt 1", self.section(text, "Goal"))
        self.assertIn("New next.", self.section(text, "Next"))
        self.attempt(1, "## Next\nonly next\n")
        self.attempt(2, "## Next\nNew next.\n")
        goal = self.section(self.brief(3), "Goal")
        self.assertIn("task title: x", goal)
        self.assertIn(str(next((self.house / "tasks").glob("T001-*.md"))), goal)

    # B7
    def test_done_covers_the_whole_branch_and_uncommitted_edits(self):
        wt = night.prepare_worktree(self.c, "T001")
        self.wt = wt
        (wt / "early.py").write_text("x = 1\n")
        git(wt, "add", "early.py")
        git(wt, "commit", "-qm", "attempt 1 work")
        (wt / "late.py").write_text("y = 2\n")  # left uncommitted by a timeout
        log = [*_edit("e1", f"{wt}/late.py"),
               *_bash("c1", f"house check c1 T001 --worktree {wt}", err=False, out="GREEN"),
               *_bash("r1", "grep -n `x` hello.py", err=True, out="grep: x: No such file"),
               *_bash("r2", "grep -n `x` hello.py", err=True, out="grep: x: No such file"),
               *_bash("r3", "grep -n `x` hello.py", err=True, out="grep: x: No such file")]
        self.attempt(1, "## Goal\ng\n", log, outcome="timeout")
        text = self.brief(2)
        done = self.section(text, "Done")
        self.assertIn("The worktree already holds the work of earlier attempts.", done)
        self.assertIn("early.py", done)
        self.assertIn("late.py", done)
        self.assertRegex(done, r"\n ?1 file changed[^\n]*\n|\n ?1 file changed[^\n]*$")
        self.assertNotRegex(done, r"file changed[^\n]*\((not )?verified\)")
        self.assertIn("``grep -n `x` hello.py``", self.section(text, "Critical context").replace("`` ", "``")
                      .replace(" ``", "``"))

    # B8
    def test_message_is_redacted_and_codex_action_names_the_check(self):
        key = "sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789"
        self.attempt(1, "## Goal\ng\n", self.failing_log(), message=f"merge said {key}", tool="codex")
        text = self.brief(2, "codex")
        self.assertNotIn(key, text)
        self.assertIn(f"Next action: run `{night.HOUSE_ROOT}/bin/house check c1 T001 --worktree .` and fix what it "
                      "reports", self.section(text, "Next"))

    # B9
    def test_copy_only_in_the_working_tree_is_not_read(self):
        self.attempt(1, "## Goal\nUNCOMMITTED-GOAL\n", commit=False)
        text = self.brief(2)
        self.assertNotIn("UNCOMMITTED-GOAL", text)
        self.assertIn("no report from attempt 1", self.section(text, "Goal"))

    def test_live_report_stands_in_for_the_previous_attempt_without_a_copy(self):
        self.attempt(1, "## Goal\nCopy goal one.\n")
        live = self.house / "reports" / "T001.md"
        live.write_text("## Goal\nLIVE-GOAL\n\n## Dead ends\n- live dead end\n")
        self.attempt(2, start="2000-01-01T00:00:00")
        text = self.brief(3)
        goal = self.section(text, "Goal")
        self.assertIn("LIVE-GOAL", goal)
        self.assertIn(f"live report {live}", goal)
        self.assertIn("live dead end", self.section(text, "Dead ends"))
        # a live report older than the attempt's start is not taken for it
        import os
        os.utime(live, (0, 0))
        self.assertNotIn("LIVE-GOAL", self.brief(3))


class SignalReportLineTest(unittest.TestCase):
    def test_night_report_signal_line(self):
        import os
        import tempfile
        from unittest import mock

        from houselib import signalstore
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"HOUSE_DATA_DIR": d + "/data"}):
            def ev(ts, chat, dir_="in"):
                return {"v": 1, "key": f"{chat}:a:{ts}", "kind": "message", "dir": dir_, "chat": chat, "ts": ts,
                        "text": "TAJNA treść"}
            signalstore.append(ev(1, "gT"))
            start = signalstore.last_seq()
            for e in (ev(2, "gT"), ev(3, "gT"), ev(4, "self"), ev(5, "gT", "out")):
                signalstore.append(e)
            c = Path(d) / "c"
            (c / ".house").mkdir(parents=True)
            rep = night.NightReport(stopped_because="time limit", signal_seq=start)
            text = night._write_report(c, rep, datetime(2026, 11, 20, 22, 0), []).read_text()
        self.assertIn("Signal: 3 nowe wiadomości w 2 rozmowach, nieprzeczytane: `house signal unread`", text)
        self.assertNotIn("TAJNA", text)


if __name__ == "__main__":
    unittest.main()
