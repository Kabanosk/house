"""`house evidence` command line."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from houselib import attempts, cli, scaffold
from houselib.attempts import Row
from tests.helpers import FAKE_TOOLS, make_root
from tests.test_evidence import SESSION, _bash, _edit, _text, _tr, _tu

TOKEN = "sk-abcdefghijklmnopqrstuvwxyz123456"


class EvidenceCliTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = make_root(Path(self._tmp.name))
        self.c = scaffold.new_contest("c1", None, root=self.root, detect=lambda: FAKE_TOOLS)
        self.house = self.c / ".house"
        patch = mock.patch.object(cli, "HOUSE_ROOT", self.root)
        patch.start()
        self.addCleanup(patch.stop)

    def write_log(self, name: str, *records) -> str:
        path = self.house / "logs" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(json.dumps(r) for r in records) + "\n")
        return path.relative_to(self.c).as_posix()

    def record(self, n: int, log: str = "", outcome: str = "failed", task: str = "T001") -> None:
        attempts.record(self.house, Row(task=task, n=n, source="night", agent="worker", tool="claude", log=log,
                                        outcome=outcome))

    def run_cli(self, *args) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            try:
                code = cli.main(list(args))
            except SystemExit as e:
                code = e.code
        return code, out.getvalue(), err.getvalue()

    def test_cli_evidence_last_attempt(self):
        self.record(1, self.write_log("a1.jsonl", *_bash("c1", "ls")), outcome="failed")
        self.record(2, self.write_log("a2.jsonl", *_bash("c1", "ls"), *_bash("c2", "pwd")), outcome="merged")
        code, out, err = self.run_cli("evidence", "c1", "T001")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out.splitlines()[0], "T001 attempt 2 · night · claude · merged")
        self.assertIn("number of calls: 2", out)

    def test_cli_evidence_explicit_attempt(self):
        self.record(1, self.write_log("a1.jsonl", *_bash("c1", "ls")), outcome="failed")
        self.record(2, self.write_log("a2.jsonl", *_bash("c1", "ls"), *_bash("c2", "pwd")), outcome="merged")
        code, out, _ = self.run_cli("evidence", "c1", "T001", "--attempt", "1")
        self.assertEqual(code, 0)
        self.assertEqual(out.splitlines()[0], "T001 attempt 1 · night · claude · failed")
        self.assertIn("number of calls: 1", out)

    def test_cli_evidence_unknown_attempt_exit_1(self):
        self.record(1)
        for args in (("T001", "--attempt", "9"), ("T002",)):
            code, out, err = self.run_cli("evidence", "c1", *args)
            self.assertEqual((code, out), (1, ""))
            self.assertTrue(err.strip())

    def test_cli_evidence_task_and_session_are_exclusive(self):
        self.record(1)
        self.assertEqual(self.run_cli("evidence", "c1", "T001", "--session", "abc")[0], 2)
        self.assertEqual(self.run_cli("evidence", "c1")[0], 2)
        self.assertEqual(self.run_cli("evidence", "c1", "--session", "abc", "--attempt", "1")[0], 2)

    def test_cli_evidence_canaries(self):
        records = [*_edit("e1", "/w/a.py", ok=False), _text("CANARY-TEXT"),
                   _tu("t3", "Bash", {"command": f'curl -H "Authorization: Bearer {TOKEN}" x',
                                      "description": "CANARY-DESC"}),
                   _tr("t3", "CANARY-RESULT", is_error=True)]
        self.record(1, self.write_log("a1.jsonl", *records))
        code, out, _ = self.run_cli("evidence", "c1", "T001")
        self.assertEqual(code, 0)
        for bad in ("CANARY-TEXT", "CANARY-DESC", "CANARY-EDIT", TOKEN, "sk-abc"):
            self.assertNotIn(bad, out)
        self.assertIn("[REDACTED]", out)

    # --session
    def session_file(self, *records) -> Path:
        d = Path(self._tmp.name) / "projects" / "c1"
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{SESSION}.jsonl"
        path.write_text("\n".join(json.dumps(r) for r in records) + "\n")
        return path

    def sessions(self, *paths: Path):
        return mock.patch.object(cli.transcript, "contest_sessions", lambda contest: list(paths))

    def tree(self) -> list[str]:
        return sorted(p.relative_to(self.c).as_posix() for p in self.c.rglob("*") if ".git/" not in p.as_posix())

    def test_session_mode_prints_facts(self):
        path = self.session_file(*_bash("c1", "pytest", err=True, out="boom"),
                                 _tu("g1", "Edit", {"file_path": "/w/a.py"}),
                                 _tr("g1", "house guard: no", is_error=True))
        with self.sessions(path):
            for ident in (SESSION, SESSION[:8]):
                code, out, err = self.run_cli("evidence", "c1", "--session", ident)
                self.assertEqual((code, err), (0, ""))
                self.assertIn(SESSION, out.splitlines()[0])
                self.assertIn("guard refusals: 1", out)
                self.assertIn("Bash: pytest", out)
                self.assertIn("number of calls: 2", out)
                self.assertNotIn("last check", out)

    def test_session_mode_writes_nothing(self):
        path = self.session_file(*_bash("c1", "pytest", err=True, out="boom"))
        self.record(1)
        before = self.tree()
        with self.sessions(path):
            self.assertEqual(self.run_cli("evidence", "c1", "--session", SESSION)[0], 0)
        self.assertEqual(self.tree(), before)

    def test_session_mode_unknown_id_exit_1(self):
        path = self.session_file(*_bash("c1", "ls"))
        with self.sessions(path):
            code, out, err = self.run_cli("evidence", "c1", "--session", "zzzz")
        self.assertEqual((code, out), (1, ""))
        self.assertTrue(err.strip())
        with self.sessions():
            self.assertEqual(self.run_cli("evidence", "c1", "--session", SESSION)[0], 1)

    def test_session_mode_ambiguous_prefix_exit_1(self):
        a = self.session_file(*_bash("c1", "ls"))
        b = a.with_name("11111111-9999-3333-4444-555555555555.jsonl")
        b.write_text(a.read_text())
        with self.sessions(a, b):
            code, _, err = self.run_cli("evidence", "c1", "--session", "11111111")
            self.assertEqual(code, 1)
            self.assertIn("ambiguous", err)
            self.assertEqual(self.run_cli("evidence", "c1", "--session", SESSION)[0], 0)


if __name__ == "__main__":
    unittest.main()
