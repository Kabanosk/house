"""`house check` command line."""
import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from houselib import cli, scaffold
from tests.helpers import FAKE_TOOLS, make_root


def task(task_id: str, run: str, status: str = "todo") -> str:
    return (f"---\nid: {task_id}\ntitle: x\nstatus: {status}\nowner: null\nbranch: null\ndepends_on: []\n"
            f"acceptance:\n  - id: AC-1\n    run: \"{run}\"\n  - id: AC-2\n    manual: demo works\n"
            "protected: []\n---\nx\n")


class CheckCliTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_root(Path(self._tmp.name))
        self.c = scaffold.new_contest("c1", None, root=self.root, detect=lambda: FAKE_TOOLS)
        self.patch = mock.patch.object(cli, "HOUSE_ROOT", self.root)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self._tmp.cleanup()

    def put(self, task_id: str, run: str, status: str = "todo"):
        (self.c / ".house/tasks" / f"{task_id}-x.md").write_text(task(task_id, run, status))

    def run_cli(self, *args) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            try:
                code = cli.main(list(args))
            except SystemExit as e:
                code = e.code
        return code, out.getvalue(), err.getvalue()

    def test_freeze_then_green(self):
        self.put("T001", "true")
        code, out, _ = self.run_cli("check", "c1", "T001", "--freeze")
        self.assertEqual(code, 0, out)
        self.assertTrue((self.c / ".house/checks/T001.json").is_file())
        code, out, _ = self.run_cli("check", "c1", "T001")
        self.assertEqual(code, 0, out)
        self.assertIn("PASS AC-1", out)
        self.assertIn("MANUAL AC-2 pending", out)
        self.assertEqual(out.strip().splitlines()[-1], "GREEN")

    def test_red_exit_code(self):
        self.put("T001", "exit 1")
        self.run_cli("check", "c1", "T001", "--freeze")
        code, out, _ = self.run_cli("check", "c1", "T001")
        self.assertEqual(code, 1)
        self.assertIn("FAIL AC-1", out)
        self.assertEqual(out.strip().splitlines()[-1], "RED")

    def test_mismatch_reported(self):
        self.put("T001", "exit 1")
        self.run_cli("check", "c1", "T001", "--freeze")
        self.put("T001", "true")
        code, out, _ = self.run_cli("check", "c1", "T001")
        self.assertEqual(code, 1)
        self.assertIn("criteria changed since freeze", out)

    def test_manual_and_refreeze(self):
        self.put("T001", "true")
        self.run_cli("check", "c1", "T001", "--freeze")
        code, _, _ = self.run_cli("check", "c1", "T001", "--manual", "AC-2", "--evidence", "reports/judge-1.md")
        self.assertEqual(code, 0)
        code, _, err = self.run_cli("check", "c1", "T001", "--refreeze")
        self.assertEqual(code, 2, err)
        self.put("T001", "exit 0")
        code, out, _ = self.run_cli("check", "c1", "T001", "--refreeze", "--reason", "simpler")
        self.assertEqual(code, 0)
        self.assertIn("D-001", out)

    def test_all_covers_merged_tasks(self):
        self.put("T001", "true", status="merged")
        self.put("T002", "exit 1", status="done")
        self.put("T003", "exit 1", status="todo")
        for t in ("T001", "T002", "T003"):
            self.run_cli("check", "c1", t, "--freeze")
        code, out, _ = self.run_cli("check", "c1", "--all")
        self.assertEqual(code, 1)
        self.assertIn("T001 GREEN", out)
        self.assertIn("T002 RED", out)
        self.assertNotIn("T003", out)


if __name__ == "__main__":
    unittest.main()
