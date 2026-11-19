"""`house sources` command line: table of contents and `add` (file and stdin), exit codes."""
import hashlib
import io
import json
import tempfile
import types
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from houselib import cli, scaffold, sources
from tests.helpers import FAKE_TOOLS, make_root


class SourcesCliTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.root = make_root(self.tmp)
        self.c = scaffold.new_contest("c1", None, root=self.root, detect=lambda: FAKE_TOOLS)
        self.dir = self.c / ".house" / "sources"
        patch = mock.patch.object(cli, "HOUSE_ROOT", self.root)
        patch.start()
        self.addCleanup(patch.stop)

    def run_cli(self, *args, stdin: bytes = b"") -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        fake_stdin = types.SimpleNamespace(buffer=io.BytesIO(stdin))
        with redirect_stdout(out), redirect_stderr(err), mock.patch("sys.stdin", fake_stdin):
            try:
                code = cli.main(list(args))
            except SystemExit as e:
                code = e.code
        return code, out.getvalue(), err.getvalue()

    def write(self, name: str, data: bytes) -> Path:
        path = self.tmp / name
        path.write_bytes(data)
        return path

    def test_add_from_file_then_list(self):
        data = "# Regulamin\nzażółć\nline\n".encode("utf-8")
        src = self.write("Rules.md", data)
        code, out, err = self.run_cli("sources", "add", "c1", str(src))
        self.assertEqual((code, err), (0, ""))
        sha = hashlib.sha256(data).hexdigest()
        self.assertEqual(len(out.strip().splitlines()), 2, out)
        first = out.splitlines()[0]
        for part in ("rules.md", f"{len(data)} bytes", "3 lines", sha[:12]):
            self.assertIn(part, first)
        self.assertIn(".house/sources/ is committed with .house/", out.splitlines()[1])
        self.assertEqual((self.dir / "rules.md").read_bytes(), data)
        code, out, _ = self.run_cli("sources", "c1")
        self.assertEqual(code, 0)
        self.assertIn("rules.md: ", out)
        self.assertIn("L1  # Regulamin", out)

    def test_add_from_stdin_needs_name(self):
        code, out, err = self.run_cli("sources", "add", "c1", "-", "--name", "task.txt", stdin=b"pasted\ntext\n")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("task.txt", out)
        self.assertEqual((self.dir / "task.txt").read_bytes(), b"pasted\ntext\n")
        self.assertEqual(json.loads((self.dir / "index.json").read_text())["files"][0]["origin"], "stdin")
        code, _, err = self.run_cli("sources", "add", "c1", "-", stdin=b"x")
        self.assertEqual(code, 2)
        self.assertIn("name", err)

    def test_explicit_name_and_replace(self):
        src = self.write("a.txt", b"one\n")
        self.assertEqual(self.run_cli("sources", "add", "c1", str(src), "--name", "rules.md")[0], 0)
        src.write_bytes(b"two\nlines\n")
        code, _, err = self.run_cli("sources", "add", "c1", str(src), "--name", "rules.md")
        self.assertEqual(code, 2)
        self.assertIn("replace", err)
        self.assertEqual((self.dir / "rules.md").read_bytes(), b"one\n")
        code, out, _ = self.run_cli("sources", "add", "c1", str(src), "--name", "rules.md", "--replace")
        self.assertEqual(code, 0)
        self.assertEqual((self.dir / "rules.md").read_bytes(), b"two\nlines\n")

    def test_value_errors_exit_2_with_message(self):
        link = self.tmp / "link.txt"
        link.symlink_to(self.write("t.txt", b"x"))
        cases = [("add", "c1", str(link)), ("add", "c1", str(self.tmp / "nope.txt")),
                 ("add", "c1", str(self.write("ok.txt", b"x")), "--name", "../x"),
                 ("add", "c1", str(self.tmp))]
        for argv in cases:
            code, out, err = self.run_cli("sources", *argv)
            self.assertEqual(code, 2, argv)
            self.assertTrue(err.startswith("error: "), err)
            self.assertEqual(out, "")
        self.assertFalse(self.dir.exists() and any(self.dir.iterdir()))

    def test_unknown_contest_exits_1(self):
        for argv in (("sources", "nope"), ("sources", "add", "nope", "-", "--name", "a"),
                     ("sources", "Bad_Name"), ("sources", "add", "../x", "-", "--name", "a")):
            code, out, err = self.run_cli(*argv, stdin=b"x")
            self.assertEqual(code, 1, argv)
            self.assertIn("error:", err)

    def test_not_a_house_contest_exits_1(self):
        (self.root / "contests" / "plain").mkdir()
        self.assertEqual(self.run_cli("sources", "plain")[0], 1)

    def test_empty_listing_exit_0_with_hint(self):
        code, out, _ = self.run_cli("sources", "c1")
        self.assertEqual(code, 0)
        self.assertIn("no sources yet", out)
        self.assertIn("house sources add c1", out)

    def test_bad_argument_counts_exit_2(self):
        for argv in (("sources", "add", "c1"), ("sources", "c1", "extra"), ("sources", "add", "c1", "a", "b")):
            self.assertEqual(self.run_cli(*argv)[0], 2, argv)

    def test_stdin_larger_than_limit_exits_2_and_writes_nothing(self):
        with mock.patch.object(sources, "STDIN_MAX", 10):
            code, out, err = self.run_cli("sources", "add", "c1", "-", "--name", "a.txt", stdin=b"x" * 11)
            self.assertEqual(code, 2)
            self.assertEqual(out, "")
            self.assertEqual(err.strip(), "error: stdin larger than 1 MB; save it to a file and run "
                                          "house sources add c1 <file>")
            self.assertFalse(self.dir.exists() and any(self.dir.iterdir()))
            code, _, _ = self.run_cli("sources", "add", "c1", "-", "--name", "a.txt", stdin=b"x" * 10)
            self.assertEqual(code, 0)  # exactly at the limit is fine
        self.assertEqual((self.dir / "a.txt").read_bytes(), b"x" * 10)

    def test_stdin_read_is_bounded(self):
        reads = []

        class Spy(io.BytesIO):
            def read(self, size=-1):
                reads.append(size)
                return super().read(size)

        fake = types.SimpleNamespace(buffer=Spy(b"x" * 50))
        with mock.patch.object(sources, "STDIN_MAX", 10), mock.patch("sys.stdin", fake), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            cli.main(["sources", "add", "c1", "-", "--name", "a.txt"])
        self.assertEqual(reads, [11])

    def test_stdin_limit_is_one_megabyte(self):
        self.assertEqual(sources.STDIN_MAX, 1_000_000)

    def test_bare_add_is_usage_error_unless_contest_add_exists(self):
        code, out, err = self.run_cli("sources", "add")
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertEqual(err.strip(), "usage: house sources add <contest> <file|-> [--name N] [--replace]")

    def test_contest_named_add_is_listed(self):
        scaffold.new_contest("add", None, root=self.root, detect=lambda: FAKE_TOOLS)
        code, out, _ = self.run_cli("sources", "add")
        self.assertEqual(code, 0)
        self.assertIn("no sources yet", out)

    def test_add_does_not_commit(self):
        from tests.helpers import git
        before = git(self.c, "rev-parse", "HEAD")
        self.run_cli("sources", "add", "c1", "-", "--name", "a.txt", stdin=b"x\n")
        self.assertEqual(git(self.c, "rev-parse", "HEAD"), before)


if __name__ == "__main__":
    unittest.main()
