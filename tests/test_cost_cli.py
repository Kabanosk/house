"""`house cost` command line (synthetic contest, register and stream logs only)."""
import io
import json
import re
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from houselib import attempts, cli, cost, scaffold
from tests.helpers import FAKE_TOOLS, make_root

CANARY = "SECRET-CANARY-do-not-leak"
FOOTER = "estimate of API cost (relative prices), not a bill and not subscription usage"


def usage(inp, read, w5, w1, out) -> dict:
    return {"input_tokens": inp, "cache_read_input_tokens": read, "cache_creation_input_tokens": w5 + w1,
            "cache_creation": {"ephemeral_5m_input_tokens": w5, "ephemeral_1h_input_tokens": w1},
            "output_tokens": out}


def call(sid: str, mid: str, u: dict) -> dict:
    return {"type": "assistant", "session_id": sid, "request_id": "r" + mid, "parent_tool_use_id": None,
            "message": {"id": mid, "model": "claude-opus-4-7", "role": "assistant", "stop_reason": "end_turn",
                        "usage": u, "content": [{"type": "text", "text": CANARY}]}}


class CostCliTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp = Path(self._tmp.name)
        self.root = make_root(tmp)
        self.c = scaffold.new_contest("c1", None, root=self.root, detect=lambda: FAKE_TOOLS)
        self.house = self.c / ".house"
        for patch in (mock.patch.object(cli, "HOUSE_ROOT", self.root),
                      mock.patch.object(cost, "PROJECTS", tmp / "no-projects"),
                      mock.patch.dict("os.environ", {"HOUSE_DATA_DIR": str(tmp / "no-data")})):
            patch.start()
            self.addCleanup(patch.stop)

    def night(self, task: str, sid: str, start: str, *records) -> None:
        log = self.house / "logs" / f"{task}-1.jsonl"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text("\n".join(json.dumps(r) for r in records) + "\n")
        attempts.record(self.house, attempts.Row(task=task, n=1, source="night", agent="worker", tool="claude",
                                                 session=sid, log=log.relative_to(self.c).as_posix(), start=start))

    def fixture(self) -> None:
        # 11-10: one call, 1000 + 2000 + 500 + 0 + 100 tokens; 11-17: two calls
        self.night("T001", "sid-old", "2026-11-10T22:00:00+01:00", call("sid-old", "m1", usage(1000, 2000, 500, 0, 100)))
        self.night("T002", "sid-new", "2026-11-17T22:00:00+01:00",
                   call("sid-new", "m2", usage(10, 0, 40000, 0, 200)), call("sid-new", "m3", usage(5, 40000, 100, 0, 50)))

    def run_cli(self, *args) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            try:
                code = cli.main(list(args))
            except SystemExit as e:
                code = e.code
        return code, out.getvalue(), err.getvalue()

    def test_table_with_rows_totals_and_footer(self):
        self.fixture()
        code, out, err = self.run_cli("cost", "c1")
        self.assertEqual((code, err), (0, ""))
        lines = out.splitlines()
        self.assertEqual(re.split(r"  +", lines[0].strip())[:3], ["date", "kind", "model"])
        first, second = re.split(r"  +", lines[1].strip()), re.split(r"  +", lines[2].strip())
        self.assertEqual(first[:5], ["2026-11-10", "night", "claude-opus-4-7", "1", "3.5K"])
        self.assertEqual(second[:7], ["2026-11-17", "night", "claude-opus-4-7", "2", "40K", "40K", "40K"])
        self.assertEqual(re.split(r"  +", lines[3].strip())[:2], ["total", "2 sessions"])
        self.assertEqual(lines[-1], FOOTER)
        # weighted of 11-10: 1000 + 200 + 625 + 0 + 500 = 2325
        self.assertIn("2325", lines[1])

    def test_since_keeps_later_sessions_and_empty_result_says_so(self):
        self.fixture()
        code, out, _ = self.run_cli("cost", "c1", "--since", "2026-11-15")
        self.assertEqual(code, 0)
        self.assertIn("2026-11-17", out)
        self.assertNotIn("2026-11-10", out)
        self.assertEqual(self.run_cli("cost", "c1", "--since", "2027-01-01"), (0, "no sessions\n", ""))

    def test_contest_without_sessions(self):
        self.assertEqual(self.run_cli("cost", "c1"), (0, "no sessions\n", ""))

    def test_bad_since_is_a_usage_error(self):
        self.fixture()
        for bad in ("2026-13-01", "yesterday", "2026-1-5"):
            code, out, err = self.run_cli("cost", "c1", "--since", bad)
            self.assertEqual((code, out), (2, ""), bad)
            self.assertIn("--since", err)

    def test_unknown_contest_is_an_error(self):
        code, out, err = self.run_cli("cost", "nosuch")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("not a house contest", err)
        self.assertEqual(self.run_cli("cost", "../x")[0], 1)
        self.assertEqual(self.run_cli("cost")[0], 2)

    def spec(self, text: str) -> None:
        path = self.house / "spec.yaml"
        path.write_text(path.read_text() + "\n" + text)

    def test_weights_from_spec_override_the_defaults(self):
        self.fixture()
        self.spec("cost:\n  weights:\n    output: 0\n    cache_read: 1\n    unknown_key: 7\n")
        code, out, err = self.run_cli("cost", "c1")
        self.assertEqual((code, err), (0, ""))
        # 11-10: 1000 + 2000*1 + 500*1.25 + 0 + 100*0 = 3625
        self.assertIn("3625", out.splitlines()[1])
        self.assertIn("cache_read 1", out)
        self.assertIn("output 0", out)

    def test_bad_weight_warns_once_on_stderr_and_uses_the_default(self):
        self.fixture()
        self.spec("cost:\n  weights:\n    output: lots\n")
        code, out, err = self.run_cli("cost", "c1")
        self.assertEqual(code, 0)
        self.assertEqual(len(err.splitlines()), 1)
        self.assertIn("output", err)
        self.assertIn("2325", out.splitlines()[1])

    def test_output_holds_no_session_content(self):
        self.fixture()
        code, out, err = self.run_cli("cost", "c1")
        self.assertNotIn(CANARY, out + err)
        self.assertNotIn(str(self.c), out + err)


if __name__ == "__main__":
    unittest.main()
