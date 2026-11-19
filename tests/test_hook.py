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

    # --- compaction hooks (A2a) ---

    DECISIONS_HEAD = ("# Dziennik decyzji\n\nFormat wpisu:\n\n```\n- **Decyzja:** …\n"
                      "- **Kto:** człowiek | judge | coordinator\n```\n")

    def decision(self, n: int, kto: str, body: str = "x", extra: str = "") -> str:
        return (f"\n## D-{n:03d} Title {n}\n\n- **Decyzja:** {body}\n- **Dlaczego:** y\n- **Kto:** {kto}\n"
                f"- **Data:** 2026-11-17 10:00\n{extra}")

    def test_compact_mode_keeps_a_human_decision_that_only_an_agent_superseded(self):
        from houselib import hook
        (self.c / ".house/DECISIONS.md").write_text(
            self.DECISIONS_HEAD + self.decision(1, "człowiek", "HUMAN-CHOICE")
            + self.decision(2, "judge", "AGENT-OPINION", "- **Zastępuje:** D-001\n"))
        out = hook.session_start({"cwd": str(self.c), "source": "compact"})
        self.assertIn("HUMAN-CHOICE", out)
        self.assertNotIn("AGENT-OPINION", out)  # an agent entry is not a human decision

    def test_compact_mode_restores_human_decisions_first(self):
        from houselib import hook
        (self.c / ".house/DECISIONS.md").write_text(
            self.DECISIONS_HEAD + self.decision(1, "człowiek", "OLD-CHOICE")
            + self.decision(2, "judge", "JUDGE-CHOICE")
            + self.decision(3, "człowiek", "NEW-CHOICE", "- **Zastępuje:** D-001\n"))
        (self.c / ".house/QUESTIONS.md").write_text(
            "# Q\n\n## Q-001 [answered 2026-11-13] Which model?\n\n- **Odpowiedź:** lgbm\n\n"
            "## Q-002 [open] Which split?\n\n- **Blokuje:** T003\n")
        out = hook.session_start({"cwd": str(self.c), "source": "compact"})
        self.assertTrue(out.startswith("house: state restored after compaction (contest c1)\n"), out)
        self.assertLessEqual(len(out), 10_000)
        self.assertIn("NEW-CHOICE", out)
        self.assertIn(self.decision(3, "człowiek", "NEW-CHOICE", "- **Zastępuje:** D-001").strip(), out)
        for gone in ("OLD-CHOICE", "JUDGE-CHOICE", "człowiek | judge | coordinator"):
            self.assertNotIn(gone, out)
        self.assertIn("Q-001 Which model? — answer: lgbm", out)
        self.assertGreater(out.index("Q-002"), out.index("Q-001"))
        self.assertGreater(out.index("Q-002"), out.index("NEW-CHOICE"))
        self.assertIn("T003", out[out.index("Q-002"):])
        self.assertNotIn("More:", out)

    def answered(self, qid: str, title: str, answer: str) -> str:
        return f"\n## {qid} [answered 2026-11-13] {title}\n\n- **Odpowiedź:** {answer}\n"

    def test_compact_mode_answered_question_is_not_restored_twice(self):
        from houselib import hook
        qdec = lambda n, qid, kto, answer, extra="": (  # noqa: E731 — the entry questions.answer() writes
            f"\n## D-{n:03d} {qid}: Which language?\n\n- **Decyzja:** {answer}\n- **Dlaczego:** y\n"
            f"- **Kto:** {kto}\n- **Data:** 2026-11-17 10:00\n{extra}")
        (self.c / ".house/DECISIONS.md").write_text(
            self.DECISIONS_HEAD + qdec(2, "Q-003", "człowiek (W, Signal)", "Python")
            + self.decision(10, "człowiek", "Rust", "- **Zastępuje:** D-002\n")
            + qdec(11, "Q-007", "judge (deadline)", "JUDGE-DEFAULT"))
        (self.c / ".house/QUESTIONS.md").write_text(
            "# Q\n" + self.answered("Q-003", "Which language?", "Python")
            + self.answered("Q-007", "Which split?", "JUDGE-DEFAULT")
            + self.answered("Q-009", "Which metric?", "auc"))
        out = hook.session_start({"cwd": str(self.c), "source": "compact"})
        self.assertIn("Rust", out)
        self.assertNotIn("## D-002", out)
        self.assertNotIn("Q-003", out)
        self.assertNotIn("Q-007", out)
        self.assertNotIn("JUDGE-DEFAULT", out)
        self.assertIn("Q-009 Which metric? — answer: auc", out)

    def test_compact_mode_shows_tasks_and_red_checks(self):
        from houselib import hook
        (self.c / ".house/tasks/T002-x.md").write_text(task("T002", "doing", deps="[T001]"))
        checks_file = self.c / ".house/checks/T002.json"
        checks_file.write_text(json.dumps({"results": {"AC-1": {"passes": False}, "AC-2": {"passes": True}}}))
        out = hook.session_start({"cwd": str(self.c), "source": "compact"})
        self.assertRegex(out, r"T002.*doing.*RED.*AC-1")
        self.assertIn("red checks: T002 (AC-1)", out)
        self.assertNotIn("T003", out)  # todo tasks are not restored

    def test_compact_mode_last_check_green_needs_every_run_criterion_passing(self):
        from houselib import hook
        (self.c / ".house/tasks/T002-x.md").write_text(task("T002", "doing", deps="[T001]"))
        crit = [{"id": "AC-1", "kind": "run", "run": "true"}, {"id": "AC-2", "kind": "run", "run": "true"},
                {"id": "AC-3", "kind": "manual"}]
        cases = {
            "no check result": {},
            "incomplete (AC-2)": {"AC-1": {"passes": True}},
            "incomplete (AC-1, AC-2)": {"AC-3": {"passes": True}},
            "GREEN": {"AC-1": {"passes": True}, "AC-2": {"passes": True}},
        }
        for want, results in cases.items():
            with self.subTest(want=want):
                (self.c / ".house/checks/T002.json").write_text(
                    json.dumps({"task": "T002", "frozen": {"criteria": crit}, "results": results}))
                out = hook.session_start({"cwd": str(self.c), "source": "compact"})
                self.assertIn(f"last check: {want}", out)
        self.assertNotIn("red checks", out)

    def test_compact_mode_budget_keeps_order(self):
        from houselib import hook
        text = self.DECISIONS_HEAD + "".join(
            self.decision(n, "człowiek", f"B{n:03d} " + "w" * 330) for n in range(1, 41))
        (self.c / ".house/DECISIONS.md").write_text(text)
        out = hook.session_start({"cwd": str(self.c), "source": "compact"})
        self.assertLessEqual(len(out), 10_000)
        self.assertTrue(out.rstrip().endswith("More: .house/DECISIONS.md, .house/QUESTIONS.md, .house/tasks/"),
                        out[-200:])
        kept = [n for n in range(1, 41) if f"B{n:03d} " in out]
        self.assertGreater(len(kept), 5)
        self.assertLess(len(kept), 40)
        self.assertEqual(kept, list(range(1, len(kept) + 1)))

    def test_compact_mode_cuts_a_huge_first_decision(self):
        from houselib import hook
        (self.c / ".house/DECISIONS.md").write_text(
            self.DECISIONS_HEAD + self.decision(1, "człowiek", "H" * 20_000)
            + self.decision(2, "człowiek", "SMALL-ONE"))
        out = hook.session_start({"cwd": str(self.c), "source": "compact"})
        self.assertLessEqual(len(out), 10_000)
        self.assertIn("## D-001 Title 1", out)
        self.assertIn("…\n\nMore:", out)  # cut, and the rest is reported as left out
        self.assertGreater(len(out), 9_000)

    def test_compact_mode_stops_at_first_miss(self):
        from houselib import hook
        (self.c / ".house/DECISIONS.md").write_text(
            self.DECISIONS_HEAD + self.decision(1, "człowiek", "FIRST-SMALL")
            + self.decision(2, "człowiek", "LONG-ONE " + "L" * 9_900) + self.decision(3, "człowiek", "LATER-SMALL"))
        (self.c / ".house/QUESTIONS.md").write_text("# Q\n\n## Q-005 [open] Still open?\n")
        out = hook.session_start({"cwd": str(self.c), "source": "compact"})
        self.assertLessEqual(len(out), 10_000)
        self.assertIn("FIRST-SMALL", out)
        self.assertIn("LONG-ONE", out)
        self.assertIn("…\n\nMore:", out)
        self.assertNotIn("LATER-SMALL", out)
        self.assertNotIn("Q-005", out)
        self.assertTrue(out.endswith(hook.MORE_LINE + "\n"))

    def test_compact_mode_miss_with_little_room_is_not_cut(self):
        from houselib import hook
        (self.c / ".house/DECISIONS.md").write_text(
            self.DECISIONS_HEAD + self.decision(1, "człowiek", "A" * 9_680)
            + self.decision(2, "człowiek", "NEXT-ONE " + "N" * 300) + self.decision(3, "człowiek", "LATER-SMALL"))
        out = hook.session_start({"cwd": str(self.c), "source": "compact"})
        self.assertLessEqual(len(out), 10_000)
        self.assertNotIn("NEXT-ONE", out)
        self.assertNotIn("LATER-SMALL", out)
        self.assertTrue(out.endswith(hook.MORE_LINE + "\n"))

    def test_compact_mode_never_calls_signal(self):
        from unittest import mock

        from houselib import hook
        with mock.patch.object(hook, "_signal_lines", side_effect=AssertionError("signal")) as sig:
            out = hook.session_start({"cwd": str(self.c), "source": "compact"})
            self.assertNotIn("last briefing", out)
            self.assertEqual(hook.session_start({"cwd": self._tmp.name, "source": "compact"}), "")
            sig.assert_not_called()
        with mock.patch.object(hook, "_signal_lines", return_value=[]):
            today = hook.session_start({"cwd": str(self.c)})
            startup = hook.session_start({"cwd": str(self.c), "source": "startup"})
        self.assertEqual(startup, today)
        self.assertIn("last briefing", startup)
        self.assertLessEqual(len(startup.splitlines()), 25)

    def test_house_hook_routes_new_modes(self):
        ev = {"session_id": "abc", "transcript_path": "/x/abc.jsonl", "cwd": str(self.c), "trigger": "auto",
              "compact_summary": "<summary>## Goal\ng\n</summary>"}
        for mode, name in (("pre-compact", "PreCompact"), ("post-compact", "PostCompact")):  # removed after K5:
            r = self.run_hook(mode, dict(ev, hook_event_name=name))  # silent no-ops for settings of an old adapt
            self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""), mode)
            r = self.run_hook(mode, "{not json")
            self.assertEqual(r.returncode, 0)
        self.assertFalse((self.c / ".house/tmp/compactions.jsonl").exists())
        r = self.run_hook("session-start", dict(ev, hook_event_name="SessionStart", source="compact"))
        self.assertIn("state restored after compaction", r.stdout)

    def test_doctor_alias_collisions(self):
        from houselib import doctor
        a, b, old = (Path(self._tmp.name) / n for n in ("a", "b", "old"))
        for d, end in ((a, "null"), (b, "null"), (old, "2020-01-01")):
            (d / ".house").mkdir(parents=True)
            (d / ".house/spec.yaml").write_text(f"timeline:\n  end: {end}\nnotify:\n  alias: fr\n")
        self.assertEqual(doctor.alias_collisions([a, b, old]), {"fr": ["a", "b"]})


if __name__ == "__main__":
    unittest.main()
