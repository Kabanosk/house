"""`house eval compare` (K5): aggregation of the rows of one case per variant, flags and the CLI. Synthetic rows only."""
import unittest
from unittest import mock

from houselib.evals import results
from houselib.evals.grade import Grade
from tests import test_eval_cli as tcli


def run(variant="default", result="pass", score=1.0, weighted=100_000, peak=50_000, compactions=0,
        digest="h1", day="2026-11-17", predicates=None, note="", case="a"):
    row = {"case": case, "case_hash": digest, "variant": variant, "result": result, "score": score, "note": note,
           "date": f"{day}T12:00:00+00:00", "kind": "capability", "commit": "c1", "model": "m",
           "metrics": {"weighted": weighted, "peak": peak, "calls": 5, "compactions": compactions, "output": 10}}
    if predicates is not None:
        row["predicates"] = predicates
    return row


def by_variant(groups):
    return {g["variant"]: g for g in groups}


class CompareTest(unittest.TestCase):
    def test_aggregates_per_variant(self):
        rows = [run("control", score=1.0, weighted=100_000, peak=40_000),
                run("control", result="fail", score=0.5, weighted=200_000, peak=60_000),
                run("control", score=1.0, weighted=300_000, peak=50_000),
                run("prod", score=0.5, result="fail", weighted=90_000, peak=20_000, compactions=2),
                run("prod", score=1.0, weighted=110_000, peak=30_000, compactions=1)]
        got = by_variant(results.compare(rows, "a", "h1"))
        c = got["control"]
        self.assertEqual((c["n"], c["passes"], c["infra"]), (3, 2, 0))
        self.assertAlmostEqual(c["score_mean"], 5 / 6)
        self.assertEqual((c["score_min"], c["score_max"]), (0.5, 1.0))
        self.assertEqual((c["weighted_mean"], c["weighted_min"], c["weighted_max"]), (200_000, 100_000, 300_000))
        self.assertEqual(c["peak_median"], 50_000)
        self.assertEqual(c["compactions_mean"], 0)
        p = got["prod"]
        self.assertEqual((p["n"], p["passes"]), (2, 1))
        self.assertEqual(p["compactions_mean"], 1.5)
        self.assertEqual(p["peak_median"], 25_000)

    def test_variants_keep_first_seen_order_and_a_row_without_variant_is_default(self):
        row = run()
        del row["variant"]
        got = results.compare([run("zeta"), row], "a", "h1")
        self.assertEqual([g["variant"] for g in got], ["zeta", "default"])

    def test_only_the_given_case_and_hash(self):
        rows = [run(), run(digest="old", result="fail", score=0.0), run(case="b", result="fail", score=0.0)]
        got = results.compare(rows, "a", "h1")
        self.assertEqual((got[0]["n"], got[0]["passes"]), (1, 1))

    def test_since_filters_by_local_date_of_the_row(self):
        rows = [run(day="2026-11-10", result="fail", score=0.0), run(day="2026-11-17")]
        got = results.compare(rows, "a", "h1", since="2026-11-15")
        self.assertEqual((got[0]["n"], got[0]["passes"]), (1, 1))
        self.assertEqual(results.compare(rows, "a", "h1", since="2026-11-18"), [])

    def test_row_with_unreadable_date_is_dropped_under_since(self):
        bad = run()
        bad["date"] = "yesterday"
        self.assertEqual(results.compare([bad], "a", "h1", since="2026-01-01"), [])
        self.assertEqual(len(results.compare([bad], "a", "h1")), 1)

    def test_infra_and_incomplete_are_counted_apart_and_leave_means_alone(self):
        rows = [run("v", score=1.0, weighted=100_000),
                run("v", result="infra_error", score=None, weighted=0, peak=0),
                run("v", result="fail", score=None, weighted=0, peak=0, note="incomplete"),
                run("v", result="fail", score=None, weighted=0, peak=0, note="setup: no docker")]
        v = results.compare(rows, "a", "h1")[0]
        self.assertEqual((v["n"], v["passes"], v["infra"]), (1, 1, 3))
        self.assertEqual((v["score_mean"], v["weighted_mean"], v["peak_median"]), (1.0, 100_000, 50_000))

    def test_only_infra_rows_leave_the_numbers_empty(self):
        v = results.compare([run("v", result="infra_error", score=None)], "a", "h1")[0]
        self.assertEqual((v["n"], v["infra"]), (0, 1))
        for key in ("score_mean", "score_min", "weighted_mean", "weighted_max", "peak_median", "compactions_mean"):
            self.assertIsNone(v[key])

    def test_rows_without_metrics_or_score_are_skipped_in_those_means(self):
        old = run("v", score=None)
        del old["metrics"]
        v = results.compare([old, run("v", score=0.5, weighted=40_000)], "a", "h1")[0]
        self.assertEqual((v["n"], v["score_mean"], v["weighted_mean"]), (2, 0.5, 40_000))

    def test_flag_control_compacted(self):
        rows = [run("control"), run("control", compactions=1), run("control"),
                run("control-short", compactions=0), run("control-short"), run("control-short"),
                run("prod", compactions=3), run("prod"), run("prod")]
        got = by_variant(results.compare(rows, "a", "h1"))
        self.assertEqual(got["control"]["flags"], ["control compacted"])
        self.assertEqual(got["control-short"]["flags"], [])
        self.assertEqual(got["prod"]["flags"], [])  # only variants named control* are controls

    def test_flag_n_below_three(self):
        rows = [run("a1"), run("a1"), run("a1"), run("b1"), run("b1", result="infra_error", score=None)]
        got = by_variant(results.compare(rows, "a", "h1"))
        self.assertEqual(got["a1"]["flags"], [])
        self.assertEqual(got["b1"]["flags"], ["n<3"])

    def test_predicate_pass_counts_per_variant(self):
        rows = [run("control", predicates={"fact-a": True, "decision-b": True}),
                run("control", predicates={"fact-a": True, "decision-b": False}),
                run("prod", predicates={"fact-a": False, "decision-b": True}),
                run("prod", result="infra_error", score=None, predicates={}),
                run("prod")]  # a row without per-predicate booleans adds nothing
        got = by_variant(results.compare(rows, "a", "h1"))
        self.assertEqual(got["control"]["predicates"], {"fact-a": (2, 2), "decision-b": (1, 2)})
        self.assertEqual(got["prod"]["predicates"], {"fact-a": (0, 1), "decision-b": (1, 1)})

    def test_no_matching_rows(self):
        self.assertEqual(results.compare([run(case="b")], "a", "h1"), [])


class RenderTest(unittest.TestCase):
    def render(self, rows):
        return results.render_compare(results.compare(rows, "a", "h1"))

    def test_header_columns_in_order(self):
        header = self.render([run()])[0].split()
        self.assertEqual(header, ["variant", "n", "pass", "infra", "score", "score_min–max", "weighted",
                                  "weighted_min–max", "peak", "compactions", "flags"])

    def test_numbers_are_rounded_like_house_cost(self):
        rows = [run("control", score=1.0, weighted=100_000, peak=40_000, compactions=0),
                run("control", result="fail", score=0.5, weighted=1_200_000, peak=60_000, compactions=1),
                run("control", score=1.0, weighted=5_400, peak=50_000, compactions=0)]
        line = self.render(rows)[1]
        self.assertEqual(line.split(), ["control", "3", "2", "0", "0.83", "0.50–1.00", "435K", "5.4K–1200K",
                                        "50K", "0.3", "control", "compacted"])

    def test_empty_numbers_are_dashes(self):
        line = self.render([run("v", result="infra_error", score=None)])[1]
        self.assertEqual(line.split()[:5], ["v", "0", "0", "1", "-"])
        self.assertIn("n<3", line)

    def test_predicate_section_below_the_table(self):
        rows = [run("control", predicates={"fact-a": True}), run("control", predicates={"fact-a": False}),
                run("prod", predicates={"fact-a": True})]
        lines = self.render(rows)
        i = lines.index("predicates (passed/runs per variant):")
        self.assertEqual(lines[i + 1].split(), ["fact-a", "control", "1/2", "prod", "1/1"])

    def test_no_predicate_section_without_predicates(self):
        self.assertFalse([ln for ln in self.render([run()]) if ln.startswith("predicates")])


class CompareCliTest(tcli.EvalCliBase):
    def put(self, *rows):
        for row in rows:
            results.append(self.results_path, row)

    def digest(self, name="alpha"):
        return tcli.cli.evalcases.case_hash(self.root / f"evals/cases/{name}.json")

    def test_unknown_case_exits_2(self):
        code, out, err = self.call("compare", "nope")
        self.assertEqual(code, 2)
        self.assertIn("nope", err)

    def test_missing_case_argument_exits_2(self):
        self.assertEqual(self.call("compare")[0], 2)

    def test_no_rows_exits_0(self):
        code, out, _ = self.call("compare", "alpha")
        self.assertEqual(code, 0)
        self.assertIn("no rows", out)

    def test_prints_one_line_per_variant_for_the_current_hash_only(self):
        d = self.digest()
        self.put(run("control", case="alpha", digest=d), run("prod", case="alpha", digest=d, compactions=2),
                 run("stale", case="alpha", digest="oldhash"))
        code, out, _ = self.call("compare", "alpha")
        self.assertEqual(code, 0)
        names = [ln.split()[0] for ln in out.splitlines()]
        self.assertIn("control", names)
        self.assertIn("prod", names)
        self.assertNotIn("stale", names)

    def test_only_stale_rows_say_no_rows(self):
        self.put(run("stale", case="alpha", digest="oldhash"))
        code, out, _ = self.call("compare", "alpha")
        self.assertEqual((code, "no rows" in out), (0, True))

    def test_since_filter_and_bad_date(self):
        d = self.digest()
        self.put(run("control", case="alpha", digest=d, day="2026-11-01"))
        self.assertIn("no rows", self.call("compare", "alpha", "--since", "2026-11-10")[1])
        self.assertEqual(self.call("compare", "alpha", "--since", "11/10")[0], 2)

    def test_run_rows_carry_the_per_predicate_booleans(self):
        with mock.patch.object(tcli.cli.evalgrade, "grade", return_value=Grade(
                "fail", score=0.5, predicates={"fact-a": True, "outcome[0] git_clean": False})):
            self.call("run")
        self.assertEqual(self.rows()[0]["predicates"], {"fact-a": True, "outcome[0] git_clean": False})

    def test_positional_case_is_for_compare_only(self):
        self.assertEqual(self.call("status", "alpha")[0], 2)
        self.assertEqual(self.runs, [])


if __name__ == "__main__":
    unittest.main()
