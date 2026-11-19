"""The eval results file (`houselib/evals/results.py`): append, tolerant read, per-hash aggregation."""
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from houselib import agentdocker
from houselib.evals import results


def row(case="a", digest="h1", result="pass", kind="capability", date="2026-11-17T10:00:00+01:00"):
    return {"case": case, "case_hash": digest, "result": result, "kind": kind, "date": date}


def make(case, digest, *outcomes, kind="capability", commit="", model=""):
    rows = [row(case, digest, r, kind, f"2026-11-17T10:00:{i:02d}+01:00") for i, r in enumerate(outcomes)]
    for r in rows:
        r.update(commit=commit, model=model)
    return rows


class FileTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "evals/results.jsonl"

    def tearDown(self):
        self._tmp.cleanup()

    def test_append_creates_parent_and_writes_one_line_per_row(self):
        results.append(self.path, {"case": "a", "note": "żółć"})
        results.append(self.path, {"case": "b"})
        lines = self.path.read_text(encoding="utf-8").splitlines()
        self.assertEqual([json.loads(x)["case"] for x in lines], ["a", "b"])

    def test_read_missing_file_is_empty(self):
        self.assertEqual(results.read(self.path), [])

    def test_read_skips_garbage_blank_and_non_object_lines(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text('{"case": "a"}\n\nnot json\n[1, 2]\n"x"\n{"case": "b"}\n{"case": "c", "tru')
        self.assertEqual([r["case"] for r in results.read(self.path)], ["a", "b"])


class StatusTest(unittest.TestCase):
    def one(self, rows, **kw):
        out = results.status(rows, **kw)
        self.assertEqual(len(out), 1)
        return out[0]

    def test_pass1_and_pass_k(self):
        s = self.one(make("a", "h", "fail", "pass", "pass", "pass"))
        self.assertEqual((s["runs"], s["passes"]), (4, 3))
        self.assertEqual(s["pass1"], 0.75)
        self.assertTrue(s["passk"])  # the last 3 passed
        self.assertFalse(s["flaky"])
        self.assertFalse(s["zero"])

    def test_pass_k_needs_k_runs(self):
        self.assertFalse(self.one(make("a", "h", "pass", "pass"))["passk"])
        self.assertTrue(self.one(make("a", "h", "pass", "pass"), k=2)["passk"])

    def test_flaky_looks_at_the_last_k_only(self):
        self.assertTrue(self.one(make("a", "h", "pass", "fail", "pass"))["flaky"])
        self.assertFalse(self.one(make("a", "h", "fail", "pass", "pass", "pass"))["flaky"])
        self.assertFalse(self.one(make("a", "h", "pass", "pass", "pass"))["flaky"])
        self.assertFalse(self.one(make("a", "h", "fail", "fail"))["flaky"])

    def test_zero_of_n(self):
        s = self.one(make("a", "h", "fail", "paused", "fail"))
        self.assertTrue(s["zero"])
        self.assertEqual(s["pass1"], 0.0)
        self.assertFalse(s["passk"])

    def test_infra_errors_do_not_count(self):
        s = self.one(make("a", "h", "pass", "infra_error", "pass", "pass", "infra_error"))
        self.assertEqual((s["runs"], s["infra"], s["passes"]), (3, 2, 3))
        self.assertTrue(s["passk"])
        self.assertEqual(s["last_result"], "infra_error")

    def test_only_infra_runs(self):
        s = self.one(make("a", "h", "infra_error", "infra_error"))
        self.assertEqual((s["runs"], s["infra"]), (0, 2))
        self.assertIsNone(s["pass1"])
        self.assertFalse(s["zero"])
        self.assertFalse(s["passk"])

    def test_groups_per_hash_and_marks_the_latest(self):
        rows = make("a", "old", "fail", "fail") + make("b", "h", "pass") + make("a", "new", "pass")
        out = results.status(rows)
        by = {(s["case"], s["case_hash"]): s for s in out}
        self.assertEqual([s["case"] for s in out], ["a", "a", "b"])
        self.assertFalse(by["a", "old"]["latest"])
        self.assertTrue(by["a", "new"]["latest"])
        self.assertTrue(by["b", "h"]["latest"])
        self.assertEqual(by["a", "old"]["runs"], 2)  # histories are not mixed
        self.assertEqual(by["a", "new"]["runs"], 1)

    def test_groups_per_case_hash_commit_and_model(self):
        rows = (make("a", "h", "pass", "pass", commit="c1" * 20, model="sonnet")
                + make("a", "h", "fail", commit="c2" * 20, model="sonnet")
                + make("a", "h", "pass", commit="c2" * 20, model="opus"))
        out = {(g["commit"][:2], g["model"]): g for g in results.status(rows)}
        self.assertEqual(set(out), {("c1", "sonnet"), ("c2", "sonnet"), ("c2", "opus")})
        self.assertEqual(out["c1", "sonnet"]["runs"], 2)  # commits and models are not mixed
        self.assertEqual(out["c2", "sonnet"]["passes"], 0)
        self.assertEqual(out["c2", "opus"]["passes"], 1)
        self.assertEqual(out["c1", "sonnet"]["commit"], "c1" * 20)

    def test_latest_commit_is_the_commit_of_the_cases_latest_row(self):
        rows = (make("a", "h", "pass", commit="old", model="m") + make("a", "h", "fail", commit="new", model="m")
                + make("a", "h", "pass", commit="new", model="other") + make("b", "h", "pass", commit="old", model="m"))
        by = {(g["case"], g["commit"], g["model"]): g for g in results.status(rows)}
        self.assertFalse(by["a", "old", "m"]["latest_commit"])
        self.assertTrue(by["a", "new", "m"]["latest_commit"])
        self.assertTrue(by["a", "new", "other"]["latest_commit"])  # every model of the latest commit
        self.assertTrue(by["b", "old", "m"]["latest_commit"])  # per case, not global

    def test_streak_counts_newest_consecutive_non_infra_passes(self):
        self.assertEqual(self.one(make("a", "h", "fail", "pass", "pass"))["streak"], 2)
        self.assertEqual(self.one(make("a", "h", "pass", "pass", "fail"))["streak"], 0)
        self.assertEqual(self.one(make("a", "h", "pass", "fail", "pass", "infra_error", "pass"))["streak"], 2)
        self.assertEqual(self.one(make("a", "h", "paused", "pass"))["streak"], 1)
        self.assertEqual(self.one(make("a", "h", "infra_error"))["streak"], 0)

    def test_streak_stays_inside_the_hash_and_commit(self):
        rows = (make("a", "h1", "pass", "pass", commit="c1") + make("a", "h2", "pass", commit="c1")
                + make("a", "h2", "pass", "pass", commit="c2"))
        by = {(g["case_hash"], g["commit"]): g["streak"] for g in results.status(rows)}
        self.assertEqual(by, {("h1", "c1"): 2, ("h2", "c1"): 1, ("h2", "c2"): 2})

    def test_rows_without_commit_or_model_still_group(self):
        s = self.one([{"case": "a", "case_hash": "h", "result": "pass"}])
        self.assertEqual((s["commit"], s["model"]), ("", ""))

    def test_kind_and_last_date_come_from_the_latest_row(self):
        s = self.one(make("a", "h", "pass", "fail", kind="regression"))
        self.assertEqual((s["kind"], s["last_result"], s["last_date"]), ("regression", "fail", "2026-11-17T10:00:01+01:00"))

    def test_rows_without_case_are_ignored(self):
        self.assertEqual(results.status([{"result": "pass"}]), [])

    def test_is_pass_k(self):
        self.assertIsNone(results.is_pass_k(["infra_error"]))
        self.assertTrue(results.is_pass_k(["pass", "infra_error", "pass"], 2))
        self.assertFalse(results.is_pass_k(["pass", "fail"], 2))
        self.assertFalse(results.is_pass_k(["pass"], 2))


class CliVersionTest(unittest.TestCase):
    def test_version_from_the_init_event(self):
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "run.jsonl"
            log.write_text('{"type":"x"}\n{"type":"system","subtype":"init","claude_code_version":"2.1.5"}\n')
            self.assertEqual(results.cli_version(log, "claude"), "2.1.5")

    def test_claude_falls_back_to_the_pinned_image_version_never_the_host_cli(self):
        with mock.patch.object(results, "tool_version", return_value="9.9") as tv:
            self.assertEqual(results.cli_version(Path("/nonexistent/run.jsonl"), "claude"), agentdocker.CLAUDE_VERSION)
        tv.assert_not_called()

    def test_codex_falls_back_to_the_tool_version(self):
        with mock.patch.object(results, "tool_version", return_value="9.9") as tv:
            self.assertEqual(results.cli_version(Path("/nonexistent/run.jsonl"), "codex"), "9.9")
        self.assertEqual([c.args for c in tv.call_args_list], [("codex",)])


class EventCountsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.log = Path(self._tmp.name) / "eval.log"

    def write(self, *events, raw=()):
        """Event names become log lines; `raw` lines are written as they are, after them."""
        self.log.write_text("".join(json.dumps({"time": "t", "event": e}) + "\n" for e in events)
                            + "".join(line + "\n" for line in raw))

    def test_counts_by_event_name(self):
        self.write("signal.send", "notify.send", "signal.send", "signal.send")
        self.assertEqual(results.event_counts(self.log), {"notify.send": 1, "signal.send": 3})

    def test_missing_file_and_garbage_lines(self):
        self.assertEqual(results.event_counts(self.log), {})
        self.write("signal.send", raw=["not json", "[1]", '{"no": "event"}', '{"event": 5}'])
        self.assertEqual(results.event_counts(self.log), {"signal.send": 1})

    def test_names_are_agent_controlled_so_odd_ones_count_as_other(self):
        self.write("ok.event", "has space", "x" * 100, "UPPER")
        self.assertEqual(results.event_counts(self.log), {"ok.event": 1, "other": 3})

    def test_distinct_names_are_capped(self):
        self.write(*[f"e{i}" for i in range(40)])
        counts = results.event_counts(self.log)
        self.assertLessEqual(len(counts), results.MAX_EVENT_NAMES + 1)
        self.assertEqual(sum(counts.values()), 40)

    def test_a_symlink_is_not_followed(self):
        target = Path(self._tmp.name) / "host-file"
        target.write_text(json.dumps({"event": "secret.thing"}) + "\n")
        self.log.symlink_to(target)
        self.assertEqual(results.event_counts(self.log), {})

    def test_a_fifo_never_blocks(self):
        os.mkfifo(self.log)
        got = []
        reader = threading.Thread(target=lambda: got.append(results.event_counts(self.log)), daemon=True)
        reader.start()
        reader.join(5)
        if reader.is_alive():  # unblock the stuck open so the thread can end
            os.close(os.open(self.log, os.O_WRONLY | os.O_NONBLOCK))
            reader.join(5)
            self.fail("event_counts blocked on a FIFO")
        self.assertEqual(got, [{}])

    def test_a_directory_is_no_events(self):
        self.log.mkdir()
        self.assertEqual(results.event_counts(self.log), {})

    def test_a_huge_log_is_read_up_to_a_cap(self):
        self.write(*(["a.b"] * 10))
        with mock.patch.object(results, "MAX_LOG_BYTES", 40):
            self.assertEqual(results.event_counts(self.log), {"a.b": 1})  # only the first complete line fits


if __name__ == "__main__":
    unittest.main()
