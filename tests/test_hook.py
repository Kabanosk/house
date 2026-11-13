"""`bin/house-hook`: the script Claude Code runs before tools and at session start."""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from houselib import HOUSE_ROOT, checks, scaffold
from tests.helpers import FAKE_TOOLS, git, make_root

HOOK = str(HOUSE_ROOT / "bin/house-hook")


def task(tid: str, status: str, deps: str = "[]", protected: str = "protected: []\n") -> str:
    return (f"---\nid: {tid}\ntitle: x\nstatus: {status}\nowner: null\nbranch: task/{tid}-x\ndepends_on: {deps}\n"
            f"acceptance:\n  - id: AC-1\n    run: \"true\"\n{protected}---\nx\n")


class HookTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_root(Path(self._tmp.name))
        self.c = scaffold.new_contest("c1", None, root=self.root, detect=lambda: FAKE_TOOLS)
        (self.c / "test_a.py").write_text("assert True\n")
        tasks = self.c / ".house/tasks"
        (tasks / "T001-x.md").write_text(task("T001", "merged", protected="protected:\n  - test_a.py\n"))
        (tasks / "T002-x.md").write_text(task("T002", "todo", deps="[T001]"))
        (tasks / "T003-x.md").write_text(task("T003", "todo", deps="[T002]"))
        (self.c / ".house/QUESTIONS.md").write_text("# Q\n\n## Q-001 [open] Which model?\n\n## Q-002 [answered 2026-11-13] x\n")
        (self.c / ".house/spec.yaml").write_text("kind: optimize\nmode: copilot\ngoal: Beat the baseline F1\n")
        git(self.c, "add", "-A")
        git(self.c, "commit", "-q", "-m", "base")
        checks.freeze(self.c / ".house", "T001")

    def tearDown(self):
        self._tmp.cleanup()

    def run_hook(self, mode: str, event, env_extra=None) -> subprocess.CompletedProcess:
        env = {k: v for k, v in os.environ.items() if k != "HOUSE_GUARD"}
        env.update(env_extra or {})
        data = event if isinstance(event, str) else json.dumps(event)
        return subprocess.run([HOOK, mode], input=data, capture_output=True, text=True, env=env, check=False)

    def edit(self, path: str, cwd: Path | None = None) -> dict:
        return {"hook_event_name": "PreToolUse", "session_id": "s1", "cwd": str(cwd or self.c),
                "tool_name": "Edit", "tool_input": {"file_path": path, "old_string": "a", "new_string": "b"}}

    def test_blocked_edit(self):
        r = self.run_hook("pre-tool", self.edit(str(self.c / "test_a.py")))
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("house guard:", r.stderr)
        self.assertIn("T001", r.stderr)

    def test_allowed_edit(self):
        r = self.run_hook("pre-tool", self.edit(str(self.c / "app.py")))
        self.assertEqual((r.returncode, r.stderr), (0, ""))

    def test_bash_secret_add(self):
        (self.c / ".gitignore").write_text("")
        (self.c / ".env").write_text("K=1\n")
        ev = {"hook_event_name": "PreToolUse", "session_id": "s1", "cwd": str(self.c), "tool_name": "Bash",
              "tool_input": {"command": "git add -A && git commit -m wip"}}
        self.assertEqual(self.run_hook("pre-tool", ev).returncode, 2)

    def test_outside_contest_is_silent(self):
        r = self.run_hook("pre-tool", self.edit("/tmp/x.py", cwd=Path(self._tmp.name)))
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))

    def test_malformed_input_fails_open(self):
        r = self.run_hook("pre-tool", "{not json")
        self.assertEqual(r.returncode, 0)
        self.assertIn("house-hook", r.stderr)
        self.assertNotIn("Traceback", r.stderr)

    def test_guard_off(self):
        r = self.run_hook("pre-tool", self.edit(str(self.c / "test_a.py")), {"HOUSE_GUARD": "off"})
        self.assertEqual(r.returncode, 0)
        self.run_hook("pre-tool", self.edit(str(self.c / "test_a.py")), {"HOUSE_GUARD": "off"})
        progress = (self.c / ".house/PROGRESS.md").read_text()
        self.assertEqual(progress.count("HOUSE_GUARD=off"), 1)

    def test_session_start_summary(self):
        r = self.run_hook("session-start", {"hook_event_name": "SessionStart", "session_id": "s1",
                                            "cwd": str(self.c), "source": "startup"})
        self.assertEqual(r.returncode, 0, r.stderr)
        out = r.stdout
        self.assertIn("c1", out)
        self.assertIn("optimize", out)
        self.assertIn("Q-001", out)
        self.assertNotIn("Q-002", out)
        self.assertIn("ready: T002", out)
        self.assertIn("merged 1", out)
        self.assertLessEqual(len(out.splitlines()), 25)


    def test_post_tool_on_questions_sends_new(self):
        from unittest import mock

        from houselib import hook
        ev = {"hook_event_name": "PostToolUse", "cwd": str(self.c), "tool_name": "Edit",
              "tool_input": {"file_path": str(self.c / ".house/QUESTIONS.md")}}
        with mock.patch("houselib.notify.ask_new", return_value=["Q-001"]) as ask:
            self.assertEqual(hook.post_tool(ev), "house: sent Q-001 to Signal")
            ask.assert_called_once()
        ev["tool_input"] = {"file_path": str(self.c / "app.py")}
        with mock.patch("houselib.notify.ask_new") as ask:
            self.assertEqual(hook.post_tool(ev), "")
            ask.assert_not_called()

    def test_doctor_alias_collisions(self):
        from houselib import doctor
        a, b, old = (Path(self._tmp.name) / n for n in ("a", "b", "old"))
        for d, end in ((a, "null"), (b, "null"), (old, "2020-01-01")):
            (d / ".house").mkdir(parents=True)
            (d / ".house/spec.yaml").write_text(f"timeline:\n  end: {end}\nnotify:\n  alias: fr\n")
        self.assertEqual(doctor.alias_collisions([a, b, old]), {"fr": ["a", "b"]})


if __name__ == "__main__":
    unittest.main()
