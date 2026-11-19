"""`houselib.decisionlog` and `house decisions`: the decision log across contests (synthetic contests only)."""
import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from houselib import cli, decisionlog, decisions, merge

TEMPLATE = (Path(__file__).resolve().parent.parent / "core/templates/house/DECISIONS.md").read_text()

ALPHA = TEMPLATE.rstrip("\n") + """

## D-001 No big models on this hardware

- **Decyzja:** do not train big models
- **Dlaczego:** one 8 GB GPU only
- **Kto:** człowiek (Wojtek, Signal)
- **Data:** 2026-11-17 14:30

## D-002 Use gradient boosting

- **Decyzja:** lgbm
- **Dlaczego:** fast
- **Kto:** judge
- **Data:** 2026-11-18 09:00

## D-003 Early entry without author

- **Decyzja:** something old

## D-004 Reverse D-002

- **Decyzja:** drop lgbm
- **Dlaczego:** too slow
- **Kto:** człowiek (Wojtek, CLI)
- **Data:** 2026-11-20
- **Zastępuje:** D-002 (too slow, see D-009)
"""

BETA = """# Dziennik decyzji

## D-001 Same id in another contest

- **Decyzja:** use the public split
- **Dlaczego:** nothing else is given
- **Kto:** coordinator
- **Data:** 2026-10-01 08:00

## D-002 Human, bad date

- **Decyzja:** keep the baseline
- **Kto:** człowiek
- **Data:** yesterday

## D-003 Metric: F1 (macro) C++ style

- **Decyzja:** optimise macro F1
- **Kto:** człowiek (Wojtek, Signal)
- **Data:** 2026-11-17 22:10
"""

NOTE = "no .house/DECISIONS.md (skipped)"


class Base(unittest.TestCase):
    registry = "alpha:\nbeta: https://example.invalid/beta.git\n"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / "contests.yaml").write_text(self.registry)
        self.write("alpha", ALPHA)
        self.write("beta", BETA)
        patch = mock.patch.object(cli, "HOUSE_ROOT", self.root)
        patch.start()
        self.addCleanup(patch.stop)

    def write(self, name: str, text: str) -> Path:
        path = self.root / "contests" / name / ".house" / "DECISIONS.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def line_of(self, name: str, header: str) -> int:
        text = (self.root / "contests" / name / ".house" / "DECISIONS.md").read_text().splitlines()
        return text.index(header) + 1

    def run_cli(self, *args) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            try:
                code = cli.main(["decisions", *args])
            except SystemExit as e:
                code = e.code
        return code, out.getvalue(), err.getvalue()

    def ids(self, *args) -> list[str]:
        code, out, err = self.run_cli(*args)
        self.assertEqual((code, err), (0, ""))
        return [" ".join(line.split()[:2]) for line in out.splitlines()]


class EntriesTests(Base):
    def test_entries_in_registry_order_with_contest_path_and_decision(self):
        got = decisionlog.entries(self.root)
        self.assertEqual([(c, d.id) for c, _, d in got],
                         [("alpha", "D-001"), ("alpha", "D-002"), ("alpha", "D-003"), ("alpha", "D-004"),
                          ("beta", "D-001"), ("beta", "D-002"), ("beta", "D-003")])
        self.assertEqual(got[0][1], Path("contests/alpha/.house/DECISIONS.md"))  # relative to the house root
        self.assertEqual(got[0][2].by, "human")

    def test_contest_filter(self):
        self.assertEqual({c for c, _, _ in decisionlog.entries(self.root, "beta")}, {"beta"})

    def test_unknown_contest_is_an_error(self):
        with self.assertRaisesRegex(ValueError, "unknown contest 'nope'"):
            decisionlog.entries(self.root, "nope")

    def test_missing_directory_and_missing_file_are_skipped_with_a_note(self):
        (self.root / "contests.yaml").write_text(self.registry + "ghost:\nempty:\n")
        (self.root / "contests" / "empty" / ".house").mkdir(parents=True)
        notes: list[str] = []
        got = decisionlog.entries(self.root, notes=notes)
        self.assertEqual({c for c, _, _ in got}, {"alpha", "beta"})
        self.assertEqual(notes, [f"note: ghost: {NOTE}", f"note: empty: {NOTE}"])

    def test_registry_name_that_is_not_a_contest_name_is_skipped(self):
        (self.root / "contests.yaml").write_text(self.registry + "Bad_Name:\n")
        notes: list[str] = []
        self.assertEqual({c for c, _, _ in decisionlog.entries(self.root, notes=notes)}, {"alpha", "beta"})
        self.assertEqual(len(notes), 1)
        self.assertIn("Bad_Name", notes[0])

    def test_no_registry_means_no_entries(self):
        (self.root / "contests.yaml").unlink()
        self.assertEqual(decisionlog.entries(self.root), [])


class ReviewGateWriterTests(Base):
    def test_review_gate_entry_is_an_agent_entry(self):
        """Pin the real writer: `_review_gate --accept-review` logs `Kto: coordinator`, which is `agent`."""
        contest = self.root / "contests" / "gamma"
        (contest / ".house").mkdir(parents=True)
        (self.root / "contests.yaml").write_text("gamma:\n")
        report = {"head": "abc123", "status": "ok", "blockers": "1", "majors": "0"}
        with mock.patch.object(merge.review, "report_for", return_value=report), \
                mock.patch.object(merge, "_git", return_value=mock.Mock(stdout="abc123\n")):
            self.assertIsNone(merge._review_gate(contest, "T001", "task/t001", "the finding is wrong"))
        (entry,) = [d for _, _, d in decisionlog.entries(self.root)]
        self.assertEqual(entry.fields["Kto"], "coordinator")
        self.assertEqual(entry.by, "agent")
        self.assertEqual(entry.id, "D-001")
        code, out, _ = self.run_cli("--by", "agent")
        self.assertEqual(code, 0)
        self.assertIn("gamma D-001", out)
        self.assertIn("coordinator: Akceptacja recenzji T001 mimo BLOCKER", out)
        self.assertEqual(self.run_cli("--by", "human")[1].strip(), "no decisions")


class SelectTests(Base):
    def sel(self, **kw) -> list[tuple[str, str]]:
        return [(c, d.id) for c, _, d in decisionlog.select(decisionlog.entries(self.root), **kw)]

    def test_default_hides_superseded_only_inside_its_own_contest(self):
        got = self.sel()
        self.assertNotIn(("alpha", "D-002"), got)
        self.assertIn(("beta", "D-002"), got)  # beta has its own D-002; alpha's D-004 does not touch it
        self.assertIn(("alpha", "D-004"), got)

    def test_all_keeps_superseded(self):
        self.assertIn(("alpha", "D-002"), self.sel(all=True))

    def test_by_human_and_by_agent_leave_out_unknown(self):
        self.assertEqual(self.sel(by="human"),
                         [("alpha", "D-001"), ("alpha", "D-004"), ("beta", "D-002"), ("beta", "D-003")])
        self.assertEqual(self.sel(by="agent", all=True), [("alpha", "D-002"), ("beta", "D-001")])
        self.assertNotIn(("alpha", "D-003"), self.sel(by="human") + self.sel(by="agent", all=True))

    def test_since_compares_the_date_part_and_drops_unparsable_dates(self):
        self.assertEqual(self.sel(since="2026-11-18", all=True), [("alpha", "D-002"), ("alpha", "D-004")])
        self.assertEqual(self.sel(since="2026-11-17"),
                         [("alpha", "D-001"), ("alpha", "D-004"), ("beta", "D-003")])

    def test_unparsable_date_is_kept_without_since(self):
        got = self.sel()
        self.assertIn(("beta", "D-002"), got)
        self.assertIn(("alpha", "D-003"), got)  # no Data field at all

    def test_grep_is_literal_case_insensitive_over_title_and_fields(self):
        self.assertEqual(self.sel(grep="gpu"), [("alpha", "D-001")])  # a field value
        self.assertEqual(self.sel(grep="GRADIENT", all=True), [("alpha", "D-002")])  # the title
        self.assertEqual(self.sel(grep="(macro)"), [("beta", "D-003")])  # regex specials are literal
        self.assertEqual(self.sel(grep="c++"), [("beta", "D-003")])
        self.assertEqual(self.sel(grep="g.u"), [])  # not a regex
        self.assertEqual(self.sel(grep="człowiek | judge"), [])  # field names and the template are not searched

    def test_filters_combine(self):
        self.assertEqual(self.sel(by="human", since="2026-11-18", grep="lgbm"), [("alpha", "D-004")])


class RenderTests(Base):
    def test_line_format_is_exact(self):
        code, out, err = self.run_cli("--contest", "alpha", "--by", "human")
        path = Path("contests/alpha/.house/DECISIONS.md")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out.splitlines(), [
            "alpha D-001 2026-11-17 człowiek (Wojtek, Signal): No big models on this hardware — "
            f"{path}:{self.line_of('alpha', '## D-001 No big models on this hardware')}",
            f"alpha D-004 2026-11-20 człowiek (Wojtek, CLI): Reverse D-002 — "
            f"{path}:{self.line_of('alpha', '## D-004 Reverse D-002')}"])

    def test_paths_are_relative_to_the_house_root_in_every_output(self):
        for args in ((), ("--all", "--full"), ("--by", "human")):
            _, out, _ = self.run_cli(*args)
            self.assertIn(" — contests/", out)
            self.assertNotIn(str(self.root), out)

    def test_unknown_author_and_unparsable_date_are_marked(self):
        _, out, _ = self.run_cli("--contest", "alpha", "--grep", "early")
        path = Path("contests/alpha/.house/DECISIONS.md")
        self.assertEqual(out.strip(), "alpha D-003 ? unknown: Early entry without author — "
                         f"{path}:{self.line_of('alpha', '## D-003 Early entry without author')}")
        _, out, _ = self.run_cli("--contest", "beta", "--grep", "bad date")
        self.assertIn("beta D-002 ? człowiek: Human, bad date — ", out)

    def test_superseded_marker_with_all(self):
        _, out, _ = self.run_cli("--contest", "alpha", "--all")
        (line,) = [x for x in out.splitlines() if x.startswith("alpha D-002")]
        self.assertIn("Use gradient boosting [zastąpiona przez D-004] — ", line)
        self.assertEqual(sum("zastąpiona" in x for x in out.splitlines()), 1)

    def test_marker_survives_a_filter_that_drops_the_superseding_entry(self):
        _, out, _ = self.run_cli("--contest", "alpha", "--by", "agent", "--all")
        self.assertIn("alpha D-002 2026-11-18 judge: Use gradient boosting [zastąpiona przez D-004] — ", out)

    def test_full_adds_the_entry_text(self):
        _, out, _ = self.run_cli("--contest", "alpha", "--grep", "8 GB", "--full")
        self.assertEqual(len(out.splitlines()[0].split(" — ")), 2)
        for expected in ("- **Decyzja:** do not train big models", "- **Dlaczego:** one 8 GB GPU only"):
            self.assertIn("\n    " + expected, out)
        self.assertEqual(out.count("## D-001"), 0)  # the header is already in the line above
        self.assertNotIn("\n    ## ", out)
        self.assertNotIn("D-002", out)

    def test_without_full_there_is_no_entry_text(self):
        _, out, _ = self.run_cli("--contest", "alpha")
        self.assertNotIn("Decyzja", out)
        self.assertEqual(len(out.splitlines()), 3)  # D-001, D-003, D-004 (D-002 superseded)


class AgentChallengeTests(Base):
    LOG = """# Dziennik decyzji

## D-001 No big models

- **Kto:** człowiek (Wojtek, Signal)
- **Data:** 2026-11-01

## D-002 Big models after all

- **Kto:** judge
- **Data:** 2026-11-02
- **Zastępuje:** D-001 (agent opinion)
"""

    def setUp(self):
        super().setUp()
        self.write("alpha", self.LOG)

    def test_an_agent_entry_does_not_hide_a_human_decision(self):
        self.assertEqual(self.ids("--contest", "alpha"), ["alpha D-001", "alpha D-002"])
        self.assertEqual(self.ids("--contest", "alpha", "--by", "human"), ["alpha D-001"])

    def test_the_human_entry_is_marked_as_challenged_not_superseded(self):
        _, out, _ = self.run_cli("--contest", "alpha")
        (line,) = [x for x in out.splitlines() if x.startswith("alpha D-001")]
        self.assertIn("No big models [podważona przez D-002 (agent)] — ", line)
        self.assertNotIn("zastąpiona", out)

    def test_the_marker_survives_filters_and_all(self):
        for args in (("--by", "human"), ("--all",)):
            _, out, _ = self.run_cli("--contest", "alpha", *args)
            self.assertIn("alpha D-001 2026-11-01 człowiek (Wojtek, Signal): No big models [podważona przez D-002 (agent)]",
                          out)

    def test_functions_keep_the_two_relations_apart(self):
        items = decisionlog.entries(self.root, "alpha")
        self.assertEqual(decisionlog.superseded_by(items), {})
        self.assertEqual(decisionlog.challenged_by(items), {("alpha", "D-001"): ["D-002"]})

    def test_a_human_entry_still_retires_a_human_entry_and_both_markers_show_with_all(self):
        self.write("alpha", self.LOG + "\n## D-003 Changed our mind\n\n- **Kto:** człowiek\n- **Data:** 2026-11-03\n"
                                       "- **Zastępuje:** D-001\n")
        self.assertEqual(self.ids("--contest", "alpha"), ["alpha D-002", "alpha D-003"])
        _, out, _ = self.run_cli("--contest", "alpha", "--all")
        (line,) = [x for x in out.splitlines() if x.startswith("alpha D-001")]
        self.assertIn("[zastąpiona przez D-003] [podważona przez D-002 (agent)] — ", line)

    def test_a_duplicate_agent_id_cannot_flip_a_human_decision(self):
        self.write("alpha", self.LOG.replace("## D-002 Big models after all",
                                             "## D-001 Forged copy\n\n- **Kto:** coordinator\n\n"
                                             "## D-002 Big models after all"))
        items = decisionlog.entries(self.root, "alpha")
        self.assertEqual(decisionlog.superseded_by(items), {})
        self.assertEqual(decisionlog.challenged_by(items), {("alpha", "D-001"): ["D-002"]})
        _, out, _ = self.run_cli("--contest", "alpha", "--by", "human")
        self.assertIn("alpha D-001 2026-11-01 człowiek (Wojtek, Signal): No big models [podważona przez D-002 (agent)]",
                      out)

    def test_an_agent_entry_is_still_retired_by_anyone(self):
        self.write("alpha", self.LOG + "\n## D-003 Reverse the agent\n\n- **Kto:** coordinator\n"
                                       "- **Zastępuje:** D-002\n")
        self.assertEqual(self.ids("--contest", "alpha"), ["alpha D-001", "alpha D-003"])


class ExcludeAndDateTests(Base):
    def test_exclude_leaves_out_a_contest_and_is_repeatable(self):
        self.assertEqual({x.split()[0] for x in self.ids("--exclude", "alpha")}, {"beta"})
        self.assertEqual(self.run_cli("--exclude", "alpha", "--exclude", "beta")[1].strip(), "no decisions")
        self.assertEqual(self.ids("--exclude", "nope"), self.ids())  # a name that is not registered changes nothing

    def test_exclude_wins_over_contest_and_keeps_the_other_filters(self):
        code, out, _ = self.run_cli("--contest", "alpha", "--exclude", "alpha")
        self.assertEqual((code, out.strip()), (0, "no decisions"))
        self.assertEqual(self.ids("--exclude", "beta", "--by", "human"), ["alpha D-001", "alpha D-004"])

    def test_superseded_marks_still_come_from_the_excluded_contests_own_entries(self):
        got = decisionlog.select(decisionlog.entries(self.root), exclude=["beta"], all=True)
        self.assertEqual({c for c, _, _ in got}, {"alpha"})

    def test_dates_with_anything_after_the_day_are_accepted(self):
        forms = {"2026-11-17, 14:30": "2026-11-17", "2026-11-17T10:00:00+01:00": "2026-11-17",
                 "2026-11-17 (rano)": "2026-11-17", "2026-11-17": "2026-11-17", "2026-11-17;x": "2026-11-17",
                 "2026-11-17.": "2026-11-17", "2026-13-45, 10:00": None, "2026-11-171": None,
                 "17-11-2026": None, "yesterday": None, "": None}
        for text, want in forms.items():
            body = f"## D-001 T\n\n- **Kto:** człowiek\n- **Data:** {text}\n"
            (d,) = decisions.parse(body)
            self.assertEqual(decisionlog._date(d), want, text)


class ChainAndEncodingTests(Base):
    CHAIN = """# Dziennik decyzji

## D-002 First

- **Kto:** człowiek
- **Data:** 2026-11-01

## D-004 Second

- **Kto:** człowiek
- **Data:** 2026-11-02
- **Zastępuje:** D-002 (changed)

## D-006 Third

- **Kto:** człowiek
- **Data:** 2026-11-03
- **Zastępuje:** D-004 (changed again)
"""

    def test_supersede_chain_hides_the_older_entries_and_marks_each_link(self):
        self.write("alpha", self.CHAIN)
        self.assertEqual(self.ids("--contest", "alpha"), ["alpha D-006"])
        _, out, _ = self.run_cli("--contest", "alpha", "--all")
        lines = {line.split()[1]: line for line in out.splitlines()}
        self.assertIn("First [zastąpiona przez D-004] — ", lines["D-002"])
        self.assertIn("Second [zastąpiona przez D-006] — ", lines["D-004"])
        self.assertNotIn("zastąpiona", lines["D-006"])

    def test_non_utf8_decisions_file_does_not_crash(self):
        path = self.write("alpha", "")
        path.write_bytes("## D-001 Zażółć \xff\xfe tytuł\n\n- **Kto:** człowiek\n- **Data:** 2026-11-17\n"
                         .encode("utf-8").replace(b"\xc3\xbf", b"\xff"))
        code, out, err = self.run_cli("--contest", "alpha")
        self.assertEqual((code, err), (0, ""))
        self.assertIn("alpha D-001 2026-11-17 człowiek: Zażółć", out)


class CliTests(Base):
    def test_every_filter_through_the_cli(self):
        self.assertEqual(self.ids(), ["alpha D-001", "alpha D-003", "alpha D-004", "beta D-001", "beta D-002",
                                      "beta D-003"])
        self.assertEqual(self.ids("--contest", "beta"), ["beta D-001", "beta D-002", "beta D-003"])
        self.assertEqual(self.ids("--by", "human"), ["alpha D-001", "alpha D-004", "beta D-002", "beta D-003"])
        self.assertEqual(self.ids("--by", "agent", "--all"), ["alpha D-002", "beta D-001"])
        self.assertEqual(self.ids("--since", "2026-11-18"), ["alpha D-004"])
        self.assertEqual(self.ids("--grep", "F1"), ["beta D-003"])
        self.assertEqual(self.ids("--all")[1], "alpha D-002")

    def test_same_id_in_two_contests_shows_both(self):
        self.assertEqual([x for x in self.ids() if x.endswith("D-001")], ["alpha D-001", "beta D-001"])

    def test_template_header_is_never_an_entry(self):
        self.assertIn("- **Kto:** człowiek | judge | coordinator", ALPHA)  # the fixture does carry it
        for args in ((), ("--all",), ("--by", "human"), ("--by", "agent", "--all"), ("--grep", "judge")):
            _, out, _ = self.run_cli(*args)
            self.assertNotIn("człowiek | judge", out)
        self.assertEqual(len(self.ids("--contest", "alpha", "--all")), 4)

    def test_missing_directory_and_file_give_one_note_line_each_and_no_crash(self):
        (self.root / "contests.yaml").write_text(self.registry + "ghost:\nempty:\n")
        (self.root / "contests" / "empty" / ".house").mkdir(parents=True)
        code, out, err = self.run_cli("--by", "human")
        self.assertEqual((code, err), (0, ""))
        lines = out.splitlines()
        self.assertEqual(lines[:2], [f"note: ghost: {NOTE}", f"note: empty: {NOTE}"])
        self.assertEqual(len(lines), 6)

    def test_contest_without_file_alone_says_note_and_no_decisions(self):
        (self.root / "contests.yaml").write_text("ghost:\n")
        code, out, err = self.run_cli()
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out.splitlines(), [f"note: ghost: {NOTE}", "no decisions"])

    def test_no_match_prints_no_decisions(self):
        code, out, _ = self.run_cli("--grep", "zzz-not-there")
        self.assertEqual((code, out.strip()), (0, "no decisions"))

    def test_unknown_contest_exits_1_on_stderr(self):
        code, out, err = self.run_cli("--contest", "nope")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("unknown contest 'nope'", err)

    def test_bad_arguments_are_usage_errors(self):
        self.assertEqual(self.run_cli("--by", "robot")[0], 2)
        self.assertEqual(self.run_cli("--since", "17.11.2026")[0], 2)


if __name__ == "__main__":
    unittest.main()
