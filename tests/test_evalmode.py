"""`HOUSE_EVAL=1`: every outward side effect (Signal, notifications, inbox) is a logged no-op."""
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from houselib import evalmode, hook, inbox, night, notify, scaffold, signalstore
from houselib.signal import Signal, from_config
from tests.helpers import FAKE_TOOLS, git, make_root
from tests.test_night import FakeProc, commit_file

FAKE_CLI = """#!/bin/sh
echo "$@" >> "{calls}"
case "$*" in
  *listGroups*) echo '[]' ;;
  *send*) echo 1700000000000 ;;
esac
"""

QUESTIONS = "# Q\n\n## Q-001 [open] Which one?\n- **Poziom:** krytyczny\n- **Odpowiedź:** —\n"


class EvalBase(unittest.TestCase):
    """A temp data dir, a configured Signal account and a fake `signal-cli` first on PATH that records its calls."""

    eval_flag: str | None = "1"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.data = self.tmp / "data"
        self.calls = self.tmp / "calls.txt"
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        cli = bin_dir / "signal-cli"
        cli.write_text(FAKE_CLI.format(calls=self.calls))
        cli.chmod(cli.stat().st_mode | stat.S_IEXEC)
        cfg = self.tmp / "notify.env"
        cfg.write_text("HOUSE_NOTIFY_CHANNEL=signal\nHOUSE_SIGNAL_ACCOUNT=+48123456789\n")
        env = {"HOUSE_DATA_DIR": str(self.data), "HOUSE_NOTIFY_CONFIG": str(cfg),
               "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("HOUSE_EVAL_LOG", None)
        os.environ.pop("HOUSE_EVAL", None)
        if self.eval_flag is not None:
            os.environ["HOUSE_EVAL"] = self.eval_flag
        self.root = make_root(self.tmp)
        self.c = scaffold.new_contest("c1", None, root=self.root, detect=lambda: FAKE_TOOLS)
        (self.c / ".house/QUESTIONS.md").write_text(QUESTIONS)
        git(self.c, "add", "-A")
        git(self.c, "commit", "-q", "-m", "questions")

    def tearDown(self):
        self._tmp.cleanup()

    def cli_calls(self) -> list[str]:
        return self.calls.read_text().splitlines() if self.calls.exists() else []

    def store_files(self) -> list[str]:
        """Files in the Signal store (readers may create the empty directory, nothing may be written to it)."""
        return sorted(str(p.relative_to(self.data)) for p in (self.data / "signal").rglob("*") if p.is_file())

    def log_lines(self) -> list[dict]:
        path = self.data / "eval.log"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def events(self) -> list[str]:
        return [e["event"] for e in self.log_lines()]


class EvalOnTest(EvalBase):
    def test_notify_send_is_a_logged_noop(self):
        self.assertEqual(notify.send(self.c, "secret body"), ([], []))
        self.assertEqual(self.cli_calls(), [])
        (line,) = self.log_lines()
        self.assertEqual((line["event"], line["chars"], line["origin"]), ("notify.send", len("secret body"), "notify"))
        self.assertNotIn("secret body", json.dumps(line))
        self.assertIn("time", line)

    def test_ask_new_sends_nothing_and_marks_nothing(self):
        self.assertEqual(notify.ask_new(self.c), [])
        self.assertEqual(self.cli_calls(), [])
        self.assertEqual(self.events(), ["notify.ask_new"])
        self.assertFalse((self.c / ".house/state.json").exists()
                         and "sent_questions" in (self.c / ".house/state.json").read_text())

    def test_post_tool_hook_sends_nothing(self):
        out = hook.post_tool({"cwd": str(self.c), "tool_input": {"file_path": str(self.c / ".house/QUESTIONS.md")}})
        self.assertEqual(out, "")
        self.assertEqual(self.cli_calls(), [])
        self.assertEqual(self.events(), ["notify.ask_new"])

    def test_inbox_process_returns_nothing_and_touches_no_store(self):
        self.assertEqual(inbox.process([self.c], from_config(), scope=[self.c]), [])
        self.assertEqual(self.cli_calls(), [])
        self.assertEqual(self.events(), ["inbox.process"])
        self.assertEqual(self.store_files(), [])

    def test_session_start_in_a_contest_pulls_and_sends_nothing(self):
        hook.session_start({"cwd": str(self.c), "source": "startup"})
        self.assertEqual(self.cli_calls(), [])
        self.assertEqual(self.events(), ["signal.pull"])
        self.assertEqual(self.log_lines()[0]["via"], "hook.session_start")
        self.assertEqual(self.store_files(), [])

    def test_end_of_night_notification_is_logged_not_sent(self):
        (self.c / ".house/STOP").write_text("")
        rep = night.run_night(self.c, hours=1, launcher=lambda cmd, cwd, log: FakeProc(commit_file("a", "b"), cwd),
                              sleep=lambda s: None)
        self.assertIn("STOP", rep.stopped_because)
        self.assertEqual(self.cli_calls(), [])
        sends = [e for e in self.log_lines() if e["event"] == "notify.send"]
        self.assertEqual([e["origin"] for e in sends], ["night"])
        self.assertEqual(self.store_files(), [])

    def test_signal_client_is_inert_whatever_the_caller(self):
        sig = from_config()
        self.assertIsNotNone(sig)
        self.assertIsNone(sig.send("hello 🤖", group_id="g1"))
        self.assertEqual(sig.list_groups(), [])
        self.assertEqual(sig.group_members("g1"), [])
        res = sig.receive(lambda obj: self.fail("nothing may be received"))
        self.assertEqual((res.objects, res.error), (0, None))
        pulled = signalstore.pull(sig)
        self.assertEqual((pulled.kept, pulled.skipped), (0, "eval"))
        self.assertEqual(self.cli_calls(), [])
        self.assertEqual(self.store_files(), [])
        events = self.log_lines()
        self.assertEqual([e["event"] for e in events], ["signal.send", "signal.pull", "signal.pull"])
        self.assertEqual((events[0]["chat"], events[0]["chars"]), ("g1", len("hello 🤖")))
        self.assertNotIn("hello", json.dumps(events[0]))

    def test_signal_client_with_an_injected_runner_never_calls_it(self):
        def boom(*a, **k):
            raise AssertionError("signal-cli must not run")
        sig = Signal("+48123456789", "signal-cli", run=boom, popen=boom)
        self.assertIsNone(sig.send("x"))
        self.assertEqual(sig.list_groups(), [])
        sig.receive(lambda obj: None)
        self.assertEqual(self.events(), ["signal.send", "signal.pull"])
        self.assertEqual(self.log_lines()[0]["chat"], "self")


class EvalOffTest(EvalBase):
    eval_flag = None

    def test_only_unset_empty_or_zero_is_not_eval(self):
        self.assertFalse(evalmode.active())
        for value in ("", "0"):
            os.environ["HOUSE_EVAL"] = value
            self.assertFalse(evalmode.active(), value)

    def test_any_other_value_is_eval_fail_safe(self):
        for value in ("1", "true", "yes", "on", "2", " 0", "off"):
            os.environ["HOUSE_EVAL"] = value
            self.assertTrue(evalmode.active(), value)

    def test_without_the_flag_signal_cli_is_called_as_before(self):
        sent, _ = notify.send(self.c, "hello")
        self.assertEqual(len(sent), 1)
        self.assertTrue(any("send" in c and "hello" in c for c in self.cli_calls()))
        self.assertFalse((self.data / "eval.log").exists())
        self.assertNotEqual(self.store_files(), [])

    def test_without_the_flag_ask_new_sends_the_question(self):
        self.assertEqual(notify.ask_new(self.c), ["Q-001"])
        self.assertTrue(any("Q-001" in c for c in self.cli_calls()))
        self.assertFalse((self.data / "eval.log").exists())


class EvalLogTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        patcher = mock.patch.dict(os.environ, {"HOUSE_DATA_DIR": str(self.tmp / "data")})
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("HOUSE_EVAL_LOG", None)
        self.addCleanup(self._tmp.cleanup)

    def test_active_reads_the_given_env(self):
        self.assertTrue(evalmode.active({"HOUSE_EVAL": "1"}))
        self.assertFalse(evalmode.active({}))

    def test_log_appends_json_lines_to_the_default_path(self):
        evalmode.log("a.b", x=1)
        evalmode.log("c.d", y="z")
        lines = [json.loads(line) for line in (self.tmp / "data/eval.log").read_text().splitlines()]
        self.assertEqual([(e["event"]) for e in lines], ["a.b", "c.d"])
        self.assertEqual((lines[0]["x"], lines[1]["y"]), (1, "z"))
        self.assertTrue(all(e["time"] for e in lines))

    def test_log_path_can_be_set(self):
        target = self.tmp / "out" / "my.log"
        with mock.patch.dict(os.environ, {"HOUSE_EVAL_LOG": str(target)}):
            evalmode.log("e")
        self.assertEqual(json.loads(target.read_text())["event"], "e")
        self.assertFalse((self.tmp / "data/eval.log").exists())

    def test_values_are_cut_and_redacted(self):
        evalmode.log("e", long="x" * 1000, cmd="GITHUB_TOKEN=abcdef1234567890 run")
        (line,) = [json.loads(ln) for ln in (self.tmp / "data/eval.log").read_text().splitlines()]
        self.assertEqual(len(line["long"]), 300)
        self.assertNotIn("abcdef1234567890", line["cmd"])
        self.assertIn("[REDACTED]", line["cmd"])

    def test_log_never_raises(self):
        blocker = self.tmp / "file"
        blocker.write_text("")
        with mock.patch.dict(os.environ, {"HOUSE_EVAL_LOG": str(blocker / "sub" / "x.log")}):
            evalmode.log("e", obj=object())


if __name__ == "__main__":
    unittest.main()
