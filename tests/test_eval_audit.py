"""The house-read audit of an eval run (K5 wave 2): tool calls whose input names the agent-visible house export.

`results.house_reads` counts them in a stream log, `runner.run_case` stores the count in `RunResult.house_reads`,
`house eval run` writes `audit: {"house_reads": N}` into the row and `house eval compare` flags `house-read`.
Synthetic logs only; nothing starts.
"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from houselib import cli
from houselib.evals import results
from houselib.evals.runner import RunResult
from tests.test_eval_cli import EvalCliBase, FakeRoot
from tests.test_eval_runner import Popens, RunnerCase, make_case

EXPORT = Path("/tmp/house-eval-ab12/house")
CONTEST = EXPORT / "contests/fx"


def call(name: str, tool_id: str, parent: str | None = None, **inp) -> str:
    """One stream-json assistant record with one `tool_use` block (a sub-agent's when `parent` is set)."""
    return json.dumps({"type": "assistant", "parent_tool_use_id": parent, "session_id": "s",
                       "message": {"id": f"m-{tool_id}", "content": [
                           {"type": "tool_use", "id": tool_id, "name": name, "input": inp}]}})


def codex_call(item_id: str, command: str) -> str:
    return json.dumps({"type": "item.completed", "item": {"id": item_id, "type": "command_execution",
                                                          "command": command, "exit_code": 0,
                                                          "aggregated_output": ""}})


class HouseReadsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.log = Path(self._tmp.name) / "run.jsonl"

    def count(self, *lines: str, tool: str = "claude") -> int:
        self.log.write_text("".join(line + "\n" for line in lines))
        return results.house_reads(self.log, tool, EXPORT, CONTEST)

    def test_reads_of_the_export_count_in_the_main_thread_and_in_sub_agents(self):
        self.assertEqual(self.count(
            call("Bash", "t1", command=f"cat {EXPORT}/houselib/evals/specfx.py"),
            call("Read", "t2", parent="t0", file_path=f"{EXPORT}/evals/cases/long-input.json"),
            call("Grep", "t3", pattern="E2E", path=str(EXPORT)),
            call("Glob", "t4", pattern="**/*.json", path=f"{EXPORT}/")), 4)

    def test_the_contest_and_commands_through_path_do_not_count(self):
        self.assertEqual(self.count(
            call("Read", "t1", file_path=f"{CONTEST}/ledger/refs.py"),
            call("Edit", "t2", file_path=f"{CONTEST}/validate.py", old_string="a", new_string="b"),
            call("Bash", "t3", command="house check fx --all && house sources fx"),
            call("Bash", "t4", command=f"cd {CONTEST} && python3 -m unittest"),
            call("Grep", "t5", pattern="house", path=str(CONTEST)),
            call("Task", "t6", prompt=f"Work in {CONTEST}; read {CONTEST}/README.md")), 0)

    def test_other_names_with_the_same_prefix_do_not_count(self):
        self.assertEqual(self.count(
            call("Bash", "t1", command=f"ls {EXPORT}-old {EXPORT}x"),
            call("Read", "t2", file_path=f"{EXPORT.parent}/eval.log")), 0)

    def test_leaving_the_contest_with_dotdot_or_a_sibling_counts(self):
        self.assertEqual(self.count(
            call("Bash", "t1", command=f"cat {CONTEST}/../../evals/cases/long-chain.json"),
            call("Read", "t2", file_path=f"{CONTEST}-other/x"),
            call("Bash", "t3", command=f"ls {EXPORT}/contests")), 3)

    def test_a_call_counts_once_and_a_repeated_record_once(self):
        line = call("Bash", "t1", command=f"cat {EXPORT}/a {EXPORT}/b; ls '{EXPORT}'")
        self.assertEqual(self.count(line, line), 1)

    def test_nested_input_values_are_searched(self):
        self.assertEqual(self.count(call("MultiEdit", "t1", edits=[{"file_path": f"{EXPORT}/README.md"}])), 1)

    def test_codex_commands(self):
        self.assertEqual(self.count(codex_call("i1", f"sed -n 1,9p {EXPORT}/houselib/evals/chainfx.py"),
                                    codex_call("i2", f"cat {CONTEST}/README.md"), tool="codex"), 1)

    def test_missing_or_broken_log_is_zero(self):
        self.assertEqual(results.house_reads(Path(self._tmp.name) / "none", "claude", EXPORT, CONTEST), 0)
        self.assertEqual(self.count("not json", '{"type": "assistant", "message": 3}'), 0)


class RunCaseAuditTest(RunnerCase):
    def test_run_result_carries_the_count(self):
        export = self.root.root
        lines = [call("Bash", "t1", command=f"cat {export}/evals/cases/x.json"),
                 call("Read", "t2", file_path=f"{self.root.contest}/a.py")]
        result, *_ = self.go(make_case(), Popens(stdout="".join(line + "\n" for line in lines)))
        self.assertEqual(result.house_reads, 1)

    def test_default_is_zero(self):
        self.assertEqual(RunResult(Path("x"), 0, 0.0, None, False, "").house_reads, 0)


class RowAuditTest(EvalCliBase):
    def test_row_holds_the_audit_count(self):
        orig = self.fake_run

        def reading(*a, **kw):
            run = orig(*a, **kw)
            run.house_reads = 2
            return run

        with mock.patch.object(cli.evalrunner, "run_case", side_effect=reading):
            self.assertEqual(self.call("run")[0], 0)
        [row] = self.rows()
        self.assertEqual(row["audit"], {"house_reads": 2})

    def test_row_audit_is_zero_by_default_and_on_a_setup_failure(self):
        self.assertEqual(self.call("run")[0], 0)
        FakeRoot.fail_setup = [RuntimeError("no fixture")]
        self.call("run")
        self.assertEqual([r["audit"] for r in self.rows()], [{"house_reads": 0}, {"house_reads": 0}])


class CompareFlagTest(unittest.TestCase):
    def row(self, variant: str, reads=None) -> dict:
        row = {"case": "c", "case_hash": "h", "variant": variant, "commit": "a", "model": "m", "cli": "1",
               "result": "pass", "score": 1.0}
        if reads is not None:
            row["audit"] = {"house_reads": reads}
        return row

    def test_house_read_flag_on_a_variant_with_any_reading_run(self):
        rows = [self.row("house", 0), self.row("house", 3), self.row("control", 0), self.row("control"),
                self.row("pasted", True)]
        flags = {g["variant"]: g["flags"] for g in results.compare(rows, "c", "h")}
        self.assertIn("house-read", flags["house"])
        self.assertNotIn("house-read", flags["control"])
        self.assertNotIn("house-read", flags["pasted"])  # numbers only: a bool is not a count
        lines = results.render_compare(results.compare(rows, "c", "h"))
        self.assertIn("house-read", next(line for line in lines if line.startswith("house")))


if __name__ == "__main__":
    unittest.main()
