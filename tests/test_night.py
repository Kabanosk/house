"""`house night`: preflight, task picking, worker commands and the unattended loop (no real agents)."""
import json
import subprocess
import tempfile
import time
import unittest
from collections import namedtuple
from datetime import UTC, datetime, timedelta
from pathlib import Path

from houselib import adapters, checks, night, scaffold
from houselib.fm import parse_frontmatter
from tests.helpers import FAKE_TOOLS, git, make_root

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

    def test_retry_brief_carries_previous_failure(self):
        self.add("T001")
        briefs = []

        def launch(cmd, cwd, log):
            briefs.append(cmd[-1])
            return FakeProc(commit_file("hello.py", "raise SystemExit(1)\n"), cwd)
        night.run_night(self.c, hours=1, launcher=launch, sleep=lambda s: None, max_tasks=2)
        self.assertNotIn("Previous attempt", briefs[0])
        self.assertIn("Previous attempt", briefs[1])
        self.assertIn("AC-1", briefs[1])


if __name__ == "__main__":
    unittest.main()
