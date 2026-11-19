"""K5 variant key `followup_text`: load validation, the runner's stdin feed, and a `/compact` slot turn graded as
a normal turn (completion, one compaction in the metrics). Fakes and synthetic logs only."""
import json
import unittest
from unittest import mock

from houselib.evals import cases, grade, results, runner
from houselib.evals.cases import FollowupText, Variant
from tests import test_eval_followups as tfol
from tests import test_eval_grade as tgrade
from tests import test_eval_runner as trun
from tests import test_eval_variants as tvar

FOLLOWUPS = ["one", "two", "three"]


def case_with(variant: dict, followups=FOLLOWUPS):
    return tvar.load_case(followups=followups, variants={"v": variant})


class LoadTest(unittest.TestCase):
    def test_followup_text_is_kept(self):
        v = case_with({"followup_text": {"index": 1, "text": "/compact"}}).variants["v"]
        self.assertEqual(v.followup_text, FollowupText(1, "/compact"))
        self.assertEqual(v, Variant("v", followup_text=FollowupText(1, "/compact")))

    def test_default_is_none(self):
        self.assertIsNone(case_with({}).variants["v"].followup_text)

    def check_invalid(self, expect, variant, **kw):
        with self.assertRaises(ValueError) as cm:
            case_with(variant, **kw)
        self.assertIn("variants.v.followup_text", str(cm.exception))
        self.assertIn(expect, str(cm.exception))

    def test_index_must_be_in_range(self):
        for bad in (-1, 3, 99, True, 1.0, "1", None):
            with self.subTest(index=bad):
                self.check_invalid("index", {"followup_text": {"index": bad, "text": "x"}})

    def test_the_last_followup_is_in_range(self):
        self.assertEqual(case_with({"followup_text": {"index": 2, "text": "x"}}).variants["v"].followup_text.index, 2)

    def test_a_case_without_followups_has_no_slot(self):
        self.check_invalid("index", {"followup_text": {"index": 0, "text": "x"}}, followups=[])

    def test_text_must_be_a_non_empty_string(self):
        for bad in ("", "   ", 1, None, ["x"]):
            with self.subTest(text=bad):
                self.check_invalid("text", {"followup_text": {"index": 0, "text": bad}})

    def test_shape_is_checked(self):
        self.check_invalid("text: missing", {"followup_text": {"index": 0}})
        self.check_invalid("index: missing", {"followup_text": {"text": "x"}})
        self.check_invalid("unknown key(s) extra", {"followup_text": {"index": 0, "text": "x", "extra": 1}})

    def test_at_most_one_per_variant(self):
        self.check_invalid("must be an object",
                           {"followup_text": [{"index": 0, "text": "a"}, {"index": 1, "text": "b"}]})
        self.check_invalid("must be an object", {"followup_text": "x"})


class FeedTest(trun.RunnerCase):
    def setUp(self):
        super().setUp()
        self.root.commit_patch = lambda: None

    def sent(self, variant, followups=("a", "b", "c")):
        _, popens, _, _ = self.go(trun.make_case(followups=list(followups)), tfol.StdinPopens(), variant=variant)
        return [m["message"]["content"] for m in popens.procs[0].stdin.lines()]

    def test_the_slot_is_replaced_and_the_others_stay(self):
        v = Variant("v", followup_text=FollowupText(1, "/compact"))
        self.assertEqual(self.sent(v), ["do the thing", "a", "/compact", "c"])

    def test_every_arm_sends_the_same_number_of_turns(self):
        neutral = Variant("control", followup_text=FollowupText(1, "ok?"))
        compact = Variant("house", followup_text=FollowupText(1, "/compact"))
        self.assertEqual(len(self.sent(neutral)), len(self.sent(compact)))
        self.assertEqual(self.sent(Variant("empty")), ["do the thing", "a", "b", "c"])

    def test_the_case_is_not_changed(self):
        case = trun.make_case(followups=["a", "b"])
        self.go(case, tfol.StdinPopens(), variant=Variant("v", followup_text=FollowupText(0, "z")))
        self.assertEqual(case.followups, ["a", "b"])

    def test_an_index_out_of_range_is_infra_and_nothing_starts(self):
        for index in (2, -1):
            with self.subTest(index=index):
                v = Variant("v", followup_text=FollowupText(index, "z"))
                result, popens, docker, _ = self.go(trun.make_case(followups=["a", "b"]), variant=v)
                self.assertEqual(popens.procs, [])
                self.assertIn("variant v: followup_text", result.infra)
                self.assertFalse(any(c[:2] == ["docker", "run"] for c in docker.calls))
                self.assertFalse(runner.is_setup_error(result.infra))


def boundary(trigger="manual") -> str:
    return json.dumps({"type": "system", "subtype": "compact_boundary", "session_id": "s",
                       "compact_metadata": {"trigger": trigger, "pre_tokens": 150_000, "post_tokens": 9000}}) + "\n"


class CompactSlotTest(trun.RunnerCase):
    """A `/compact` user turn answers with a `compact_boundary` (trigger manual) and a normal `result`."""

    def setUp(self):
        super().setUp()
        self.root.commit_patch = lambda: None

    def test_the_run_completes_and_counts_one_compaction(self):
        case = trun.make_case(followups=["slot", "last"])
        results_ = [tfol.result_line(), boundary() + tfol.result_line(text=""), tfol.result_line()]
        v = Variant("house", followup_text=FollowupText(0, "/compact"))
        result, popens, _, _ = self.go(case, tfol.StdinPopens(results=results_), variant=v)
        self.assertIsNone(result.infra)
        self.assertEqual([m["message"]["content"] for m in popens.procs[0].stdin.lines()],
                         ["do the thing", "/compact", "last"])
        self.assertEqual((result.turns_sent, result.turns_ok), (3, 3))
        self.assertEqual(results.run_metrics(result.log)["compactions"], 1)


class CompactSlotGradeTest(tgrade.GradeBase):
    def test_a_compact_turn_counts_as_a_completed_turn(self):
        case = tgrade.make_case(followups=["slot", "last"], outcome=[{"type": "file_exists", "path": "keep.txt",
                                                                        "exists": True}])
        log = tgrade.Log().bash("ls").result("one", False)
        log.lines.append(json.loads(boundary()))
        log.result("", False).bash("ls").result("three", False)
        g = self.grade(case, log)
        self.assertEqual((g.result, g.score), ("pass", 1.0))
        self.assertEqual(results.run_metrics(self.tmp / "run.jsonl")["compactions"], 1)


if __name__ == "__main__":
    unittest.main()
