"""Hand-off template: compaction instructions and section detection."""
import unittest

from houselib import handoff

HEADERS = [h for h, _ in handoff.SECTIONS]


NEW_REPORT = """# T001: demo

- **Agent / model:** coder / sonnet
- **Branch / commits:** t001 / abc123
- **Date:** 2026-11-17 10:00

## Goal
Ship the parser (tasks/T001.md).

## Next
Review it.
Next action: run the tests
Done when: green

## Critical context
### Observed
- 12 tests ran (python3 -m unittest)
### Unverified
- maybe slow

## Constraints and human decisions
none

## Dead ends
Regex per section, 2 tries, do not repeat.

## Decisions and facts
D-004 reuse the matcher.

## Errors and fixes
none

## Done
- houselib/handoff.py (verified)

## Result vs acceptance
AC-1: pass

## Notes
Evidence: house evidence demo T001 --attempt 1
"""

OLD_REPORT = """# T002: old

- **Agent / model:** x

## Goal
Do it.

## What was done
Changed a.py. Ran tests.

## Evidence
Output in logs/x.txt.

## Result vs acceptance
AC-1: fail

## Tested and did not work
Approach B: 0.41 vs 0.52.

## Next
Q-003 open.
"""


class ReportSectionsTest(unittest.TestCase):
    def test_new_template_report(self):
        got = handoff.report_sections(NEW_REPORT)
        self.assertEqual(list(got), HEADERS + ["Result vs acceptance"])
        self.assertEqual(got["Goal"], "Ship the parser (tasks/T001.md).")
        self.assertEqual(got["Next"], "Review it.\nNext action: run the tests\nDone when: green")
        self.assertIn("### Observed", got["Critical context"])
        self.assertIn("### Unverified", got["Critical context"])
        self.assertNotIn("Observed", got)
        self.assertNotIn("Unverified", got)
        self.assertEqual(got["Constraints and human decisions"], "none")
        self.assertEqual(got["Done"], "- houselib/handoff.py (verified)")
        self.assertEqual(got["Result vs acceptance"], "AC-1: pass")

    def test_old_template_report(self):
        got = handoff.report_sections(OLD_REPORT)
        self.assertEqual(got, {
            "Goal": "Do it.",
            "Done": "Changed a.py. Ran tests.",
            "Result vs acceptance": "AC-1: fail",
            "Dead ends": "Approach B: 0.41 vs 0.52.",
            "Next": "Q-003 open.",
        })
        self.assertNotIn("Evidence", got)
        self.assertNotIn("logs/x.txt", " ".join(got.values()))

    def test_polish_aliases_and_inline_forms(self):
        got = handoff.report_sections("## Cel\nA\n**Zrobione**\nb.py\n2. Ślepe uliczki\nnone\n")
        self.assertEqual(got, {"Goal": "A", "Done": "b.py", "Dead ends": "none"})

    def test_unknown_headings_are_ignored_and_end_the_section(self):
        got = handoff.report_sections("## Goal\nA\n## Random thoughts\nnoise\n## Next\nB\n# Title\nmore noise\n")
        self.assertEqual(got, {"Goal": "A", "Next": "B"})

    def test_missing_sections_absent_and_empty_text(self):
        self.assertEqual(handoff.report_sections(""), {})
        self.assertEqual(handoff.report_sections("## Goal\n\n## Done\nx\n"), {"Goal": "", "Done": "x"})


    def test_code_fence_does_not_cut_a_section(self):
        got = handoff.report_sections("## Done\n```\n# comment\nfoo\n## not a heading\n```\nafter\n## Next\nB\n")
        self.assertEqual(got["Done"], "```\n# comment\nfoo\n## not a heading\n```\nafter")
        self.assertEqual(got["Next"], "B")
        got = handoff.report_sections("## Next\n~~~sh\n# step one\n~~~\nNext action: run\nDone when: green\n")
        self.assertEqual(got["Next"], "~~~sh\n# step one\n~~~\nNext action: run\nDone when: green")

    def test_unclosed_fence_ends_at_a_known_section_heading(self):
        got = handoff.report_sections("## Done\n```\n# comment\nfoo\n## Next\nB\n## Goal\nG\n")
        self.assertEqual(got, {"Done": "```\n# comment\nfoo", "Next": "B", "Goal": "G"})

    def test_fence_inside_does_not_close_on_a_shorter_marker(self):
        got = handoff.report_sections("## Done\n````\n```\n# c\n````\n# x\nlost\n## Next\nB\n")
        self.assertEqual(got["Done"], "````\n```\n# c\n````")

    def test_subheading_stays_in_the_body(self):
        got = handoff.report_sections("## Dead ends\n### Attempt 2\nreason, 2 tries, do not repeat\n## Next\nB\n")
        self.assertEqual(got["Dead ends"], "### Attempt 2\nreason, 2 tries, do not repeat")

    def test_bold_numbered_and_next_steps_headings(self):
        got = handoff.report_sections("**Goal**\nA\n## 1. Goal\nlater\n## Next steps\nB\n")
        self.assertEqual(got, {"Goal": "A", "Next": "B"})

    def test_bare_result_line(self):
        self.assertEqual(handoff.report_sections("Result vs acceptance: pass\n"), {"Result vs acceptance": "pass"})
        self.assertEqual(handoff.report_sections("Goal: x\n"), {})

    def test_closed_fence_keeps_a_known_heading_inside(self):
        text = ("## Dead ends\n- Deleting tests, 1 try, do not repeat. Example of the bad report:\n"
                "  ```md\n  ## Next\n  Next action: delete tests\n  ```\n"
                "## Next\nFix the parser.\nNext action: run the parser tests\n")
        got = handoff.report_sections(text)
        self.assertEqual(got["Next"], "Fix the parser.\nNext action: run the parser tests")
        self.assertIn("Next action: delete tests", got["Dead ends"])
        self.assertTrue(got["Dead ends"].rstrip().endswith("```"))

    def test_label_lines_do_not_end_a_section(self):
        got = handoff.report_sections("## Result vs acceptance\nAC-1: pass\n**Evidence:** log line 4\nAC-2: failed\n")
        self.assertEqual(got["Result vs acceptance"], "AC-1: pass\n**Evidence:** log line 4\nAC-2: failed")
        got = handoff.report_sections("## Done\n1. Done: edited a.py\n**Next:** later\n**What was done**\n"
                                      "1. Next steps\n### Evidence\nb.py\n## Next\nB\n")
        self.assertEqual(got["Done"], "1. Done: edited a.py\n**Next:** later\n**What was done**\n1. Next steps\n"
                                      "### Evidence\nb.py")
        self.assertEqual(got["Next"], "B")

    def test_whole_line_bold_or_numbered_new_names_are_headings(self):
        got = handoff.report_sections("**Goal:**\nA\n2. Next\nB\n**1. Dead ends**\nC\n")
        self.assertEqual(got, {"Goal": "A", "Next": "B", "Dead ends": "C"})

    def test_old_names_only_as_level_one_or_two_headings(self):
        got = handoff.report_sections("# What was done\na\n## Evidence\nlog\n## Tested and did not work\nb\n")
        self.assertEqual(got, {"Done": "a", "Dead ends": "b"})

    def test_evidence_result_section_sees_the_failed_line(self):
        from houselib import evidence
        self.assertTrue(evidence.NEGATIVE_RE.search(handoff.report_sections(
            "## Result vs acceptance\nAC-1: pass\n**Evidence:** ok\nAC-2: failed\n")["Result vs acceptance"]))

if __name__ == "__main__":
    unittest.main()
