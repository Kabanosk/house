"""`house out` command line, and the profile lines that point agents to it."""
import io
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from houselib import HOUSE_ROOT, cli, scaffold
from tests.helpers import FAKE_TOOLS, make_root

PY = sys.executable


class OutCliTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = make_root(Path(self._tmp.name))
        self.c = scaffold.new_contest("c1", None, root=self.root, detect=lambda: FAKE_TOOLS)
        self._cwd = os.getcwd()
        self.addCleanup(os.chdir, self._cwd)
        os.chdir(self.c)
        self._env = os.environ.pop("HOUSE_DIR", None)
        self.addCleanup(lambda: os.environ.__setitem__("HOUSE_DIR", self._env) if self._env else None)

    def run_cli(self, *args) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            try:
                code = cli.main(list(args))
            except SystemExit as e:
                code = e.code
        return code, out.getvalue(), err.getvalue()

    def logs(self) -> list[Path]:
        return sorted((self.c / ".house/tmp/out").glob("*.log"))

    def test_run_prints_envelope_and_log_exists(self):
        code, out, err = self.run_cli("out", "--", PY, "-c", "print('hello cli')")
        self.assertEqual(code, 0, err)
        self.assertIn("house out: exit 0", out)
        self.assertIn("hello cli", out)
        self.assertEqual(self.logs()[0].read_text(), "hello cli\n")
        self.assertIn(f"house out show {self.logs()[0].stem} --grep '<pattern>'", out)

    def test_exit_code_of_the_command_is_passed_on(self):
        code, out, _ = self.run_cli("out", "--", PY, "-c", "import sys; print('bad'); sys.exit(7)")
        self.assertEqual(code, 7)
        self.assertIn("exit 7", out)

    def test_options_after_the_separator_belong_to_the_command(self):
        code, out, _ = self.run_cli("out", "--", PY, "-c", "import sys; print(sys.argv[1:])", "--grep", "--days", "3")
        self.assertEqual(code, 0)
        self.assertIn("['--grep', '--days', '3']", out)

    def test_command_not_found_is_127(self):
        code, out, _ = self.run_cli("out", "--", "no-such-program-xyz")
        self.assertEqual(code, 127)
        self.assertIn("not found", out)

    def test_no_command_is_a_usage_error(self):
        for argv in (["out"], ["out", "--"]):
            code, out, err = self.run_cli(*argv)
            self.assertEqual(code, 2, argv)
            self.assertEqual(self.logs(), [])

    def test_outside_a_contest_exits_1_with_a_message(self):
        os.chdir(self._tmp.name)
        for argv in (["out", "--", PY, "-c", "print(1)"], ["out", "show", "E-001", "--grep", "x"], ["out", "prune"]):
            code, out, err = self.run_cli(*argv)
            self.assertEqual(code, 1, argv)
            self.assertIn("not inside a house contest", err)

    def test_house_dir_env_is_used(self):
        other = Path(self._tmp.name) / "elsewhere"
        (other / ".house").mkdir(parents=True)
        os.chdir(self._tmp.name)
        os.environ["HOUSE_DIR"] = str(other / ".house")
        self.addCleanup(os.environ.pop, "HOUSE_DIR", None)
        code, out, err = self.run_cli("out", "--", PY, "-c", "print(1)")
        self.assertEqual(code, 0, err)
        self.assertEqual(len(list((other / ".house/tmp/out").glob("*.log"))), 1)

    def test_show_and_prune(self):
        self.run_cli("out", "--", PY, "-c", "print('alpha'); print('needle here'); print('omega')")
        log_id = self.logs()[0].stem
        code, out, err = self.run_cli("out", "show", log_id, "--grep", "needle")
        self.assertEqual(code, 0, err)
        self.assertIn("2: needle here", out)
        code, out, _ = self.run_cli("out", "show", log_id, "--grep", "ne+dle", "--regex")
        self.assertIn("2: needle here", out)
        code, out, _ = self.run_cli("out", "show", log_id, "--grep", "ne+dle")
        self.assertIn("no hits", out)
        code, out, err = self.run_cli("out", "prune", "--days", "7")
        self.assertEqual(code, 0)
        self.assertIn("0", out)
        self.assertEqual(len(self.logs()), 1)
        code, out, _ = self.run_cli("out", "prune", "--days", "0")
        self.assertIn("1", out)
        self.assertEqual(self.logs(), [])

    def test_show_errors_exit_2(self):
        for argv in (["out", "show", "../x", "--grep", "a"], ["out", "show", "E-999", "--grep", "a"],
                     ["out", "show", "20261117-100000-abcd", "--grep", "a"],
                     ["out", "show", "20261117-100000-abcd", "--grep", "(", "--regex"],
                     ["out", "prune", "--days", "-1"]):
            code, out, err = self.run_cli(*argv)
            self.assertEqual(code, 2, argv)
            self.assertIn("error:", err)

    def test_show_needs_grep(self):
        code, out, err = self.run_cli("out", "show", "E-001")
        self.assertEqual(code, 2)

    def test_regex_help_says_unbounded(self):
        parser = cli.build_parser()
        out_parser = next(a for a in parser._subparsers._group_actions[0].choices.items() if a[0] == "out")[1]
        show_parser = out_parser._subparsers._group_actions[0].choices["show"]
        self.assertRegex(show_parser.format_help().replace("\n", " "), r"unbounded")

    def test_sigterm_stops_the_command_and_prints_the_envelope(self):
        before = signal.getsignal(signal.SIGTERM)
        before_hup = signal.getsignal(signal.SIGHUP)
        timer = threading.Timer(0.8, lambda: os.kill(os.getpid(), signal.SIGTERM))
        timer.start()
        self.addCleanup(timer.cancel)
        code, out, _ = self.run_cli("out", "--", PY, "-c", "import time; print('up', flush=True); time.sleep(60)")
        self.assertEqual(code, 143)
        self.assertIn("exit 143", out)
        self.assertIn("interrupted (SIGTERM)", out)
        self.assertIn("up", out)
        self.assertIs(signal.getsignal(signal.SIGTERM), before)
        self.assertIs(signal.getsignal(signal.SIGHUP), before_hup)

    def test_envelope_gives_the_log_path_under_house_dir(self):
        code, out, _ = self.run_cli("out", "--", PY, "-c", "print('x')")
        self.assertIn(f"log: $HOUSE_DIR/tmp/out/{self.logs()[0].name}", out)
        self.assertNotIn(str(self.c), out)

    def test_missing_log_error_names_the_id_only(self):
        code, out, err = self.run_cli("out", "show", "20261117-100000-abcd", "--grep", "a")
        self.assertEqual(code, 2)
        self.assertIn("20261117-100000-abcd", err)
        self.assertNotIn(str(self.c), err)

    def test_real_binary_end_to_end(self):
        r = subprocess.run([str(HOUSE_ROOT / "bin/house"), "out", "--", PY, "-c", "print('e2e'); raise SystemExit(4)"],
                           cwd=self.c, capture_output=True, text=True, env={**os.environ, "HOUSE_DIR": ""})
        self.assertEqual(r.returncode, 4, r.stderr)
        self.assertIn("e2e", r.stdout)
        self.assertRegex(r.stdout, r"house out show \d{8}-\d{6}-[0-9a-f]{4} --grep")


class ProfileContentTest(unittest.TestCase):
    def test_coder_profile_points_to_house_out(self):
        text = (HOUSE_ROOT / "core/agents/coder.md").read_text()
        self.assertRegex(text, r"house out --")
        self.assertRegex(text, r"(?is)full test runs?.*builds?|builds?.*full test runs?")
        self.assertRegex(text, r"house out show")

    def test_optimize_playbook_points_to_house_out(self):
        text = (HOUSE_ROOT / "core/playbooks/optimize.md").read_text()
        self.assertRegex(text, r"house out --")
        self.assertRegex(text, r"(?i)training")
        self.assertRegex(text, r"house out show")

    def test_module_doc_and_readme_row(self):
        doc = (HOUSE_ROOT / "docs/modules/out.md").read_text()
        self.assertIn("house out", doc)
        self.assertIn("$HOUSE_DIR/tmp/out/", doc)
        self.assertIn("background processes stopped", doc)
        self.assertIn("SIGKILL samego", doc)  # the documented orphan case
        self.assertNotIn("rm -rf tests/", doc)
        self.assertIn("house out", (HOUSE_ROOT / "docs/modules/guard.md").read_text())
        readme = (HOUSE_ROOT / "README.md").read_text()
        self.assertRegex(readme, r"\| [^|]*house out[^|]*\|[^|]*\| \[`out.md`\]\(docs/modules/out.md\) \|")


if __name__ == "__main__":
    unittest.main()
