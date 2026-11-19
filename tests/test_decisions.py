"""`houselib.decisions`: parse DECISIONS.md, human and superseded entries."""
import unittest
from pathlib import Path

from houselib import decisions

TEMPLATE = Path(__file__).resolve().parent.parent / "core/templates/house/DECISIONS.md"

LOG = """# Dziennik decyzji

## D-001 Wybór modelu

- **Decyzja:** lgbm zamiast ensemble
- **Dlaczego:** szybszy
- **Kto:** człowiek
- **Data:** 2026-11-10 09:00

## D-002 Q-003: Który język?

- **Decyzja:** Python
- **Dlaczego:** odpowiedź na Q-003 (signal)
- **Kto:** człowiek (Wojtek, Signal)
- **Data:** 2026-11-11 10:00

## D-3 Zmiana kryteriów T005

- **Decyzja:** nowe kryteria akceptacji T005: C1 (cmd)
- **Dlaczego:** zły próg
- **Kto:** judge
- **Data:** 2026-11-12 11:00

## D-004 Bez autora

- **Decyzja:** coś
"""


class ParseTests(unittest.TestCase):
    def test_template_header_is_not_an_entry(self):
        self.assertEqual(decisions.parse(TEMPLATE.read_text()), [])

    def test_fields_and_by(self):
        ds = decisions.parse(LOG)
        self.assertEqual([d.id for d in ds], ["D-001", "D-002", "D-003", "D-004"])
        self.assertEqual([d.by for d in ds], ["human", "human", "agent", "unknown"])
        self.assertEqual(ds[0].title, "Wybór modelu")
        self.assertEqual(ds[0].fields["Decyzja"], "lgbm zamiast ensemble")
        self.assertEqual(ds[1].fields["Kto"], "człowiek (Wojtek, Signal)")
        self.assertEqual([d.line for d in ds], [3, 10, 17, 24])

    def test_supersedes_hides_old_human_entry(self):
        text = LOG.replace("## D-3 ", "## D-003 ") + (
            "\n## D-005 Zmiana planu\n\n- **Decyzja:** inaczej\n- **Kto:** człowiek\n"
            "- **Zastępuje:** D-1 (zmiana planu)\n")
        ds = decisions.parse(text)
        new = ds[-1]
        self.assertEqual(new.supersedes, ["D-001"])
        self.assertEqual([d.id for d in decisions.active_human(ds)], ["D-002", "D-005"])

    def test_text_is_whole_block(self):
        ds = decisions.parse(LOG)
        text = ds[1].text
        self.assertTrue(text.startswith("## D-002"))
        for line in ("- **Decyzja:** Python", "- **Dlaczego:** odpowiedź na Q-003 (signal)",
                     "- **Kto:** człowiek (Wojtek, Signal)", "- **Data:** 2026-11-11 10:00"):
            self.assertIn(line, text)
        self.assertNotIn("D-3", text)
        self.assertFalse(text.endswith("\n"))

    def test_merge_gate_entry_is_agent(self):
        # exact format written by merge._review_gate
        text = ("# Dziennik decyzji\n\n\n## D-006 Akceptacja recenzji T007 mimo BLOCKER\n\n"
                "- **Decyzja:** scalić T007 mimo 1 uwag BLOCKER/MAJOR w recenzji\n"
                "- **Dlaczego:** fałszywy alarm\n- **Kto:** coordinator\n- **Data:** 2026-11-13 12:00\n")
        (d,) = decisions.parse(text)
        self.assertEqual((d.id, d.by), ("D-006", "agent"))

    def test_unbalanced_fence_does_not_hide_later_entries(self):
        text = "## D-001 ...\n- **Decyzja:**\n```py\nx=1\n\n## D-002 B\n- **Kto:** człowiek"
        self.assertEqual([d.id for d in decisions.parse(text)], ["D-001", "D-002"])

    def test_lower_case_supersede_id(self):
        text = ("## D-001 A\n- **Kto:** człowiek\n\n"
                "## D-002 B\n- **Kto:** człowiek\n- **Zastępuje:** d-1 (zmiana)\n")
        ds = decisions.parse(text)
        self.assertEqual(ds[1].supersedes, ["D-001"])
        self.assertEqual([d.id for d in decisions.active_human(ds)], ["D-002"])

    def test_supersedes_ignores_ids_in_the_bracketed_reason(self):
        text = ("## D-001 A\n- **Kto:** człowiek\n\n## D-002 B\n- **Kto:** człowiek\n\n"
                "## D-003 C\n- **Kto:** człowiek\n- **Zastępuje:** D-2 (bo wcześniej D-1 błędne)\n")
        ds = decisions.parse(text)
        self.assertEqual(ds[2].supersedes, ["D-002"])
        self.assertEqual([d.id for d in decisions.active_human(ds)], ["D-001", "D-003"])

    def test_supersedes_several_ids_before_the_bracket(self):
        text = "## D-004 C\n- **Kto:** człowiek\n- **Zastępuje:** D-2, D-3 (zmiana)\n"
        (d,) = decisions.parse(text)
        self.assertEqual(d.supersedes, ["D-002", "D-003"])

    def test_other_h2_ends_block(self):
        text = "## D-001 A\n- **Kto:** człowiek\n\n## Uwagi\n- **Kto:** judge\n"
        (d,) = decisions.parse(text)
        self.assertEqual(d.fields["Kto"], "człowiek")
        self.assertNotIn("Uwagi", d.text)

    def test_agent_entry_cannot_retire_a_human_decision(self):
        text = ("## D-001 A\n- **Kto:** człowiek\n- **Zastępuje:** D-001\n\n"
                "## D-002 B\n- **Kto:** człowiek\n\n"
                "## D-003 C\n- **Kto:** coordinator\n- **Zastępuje:** D-002\n\n"
                "## D-004 D\n- **Kto:** judge\n- **Zastępuje:** D-001\n\n"
                "## D-005 E\n- **Decyzja:** no Kto\n- **Zastępuje:** D-002\n")
        ds = decisions.parse(text)
        self.assertEqual([d.id for d in decisions.active_human(ds)], ["D-001", "D-002"])
        retired, challenged = decisions.supersession(ds)
        self.assertEqual(retired, {})
        self.assertEqual(challenged, {"D-002": ["D-003", "D-005"], "D-001": ["D-004"]})

    def test_a_duplicate_agent_id_does_not_make_a_human_decision_retirable(self):
        text = ("## D-001 A\n- **Kto:** człowiek\n\n"
                "## D-001 Forged\n- **Kto:** coordinator\n\n"
                "## D-002 B\n- **Kto:** coordinator\n- **Zastępuje:** D-001\n")
        ds = decisions.parse(text)
        retired, challenged = decisions.supersession(ds)
        self.assertEqual((retired, challenged), ({}, {"D-001": ["D-002"]}))
        self.assertEqual([(d.id, d.title) for d in decisions.active_human(ds)], [("D-001", "A")])
        # the same with the agent duplicate first
        text2 = ("## D-001 Forged\n- **Kto:** judge\n\n## D-001 A\n- **Kto:** człowiek\n\n"
                 "## D-002 B\n- **Kto:** judge\n- **Zastępuje:** D-001\n")
        self.assertEqual([d.title for d in decisions.active_human(decisions.parse(text2))], ["A"])

    def test_a_human_entry_retires_a_human_entry_and_an_agent_entry_retires_an_agent_entry(self):
        text = ("## D-001 A\n- **Kto:** człowiek\n\n## D-002 B\n- **Kto:** judge\n\n"
                "## D-003 C\n- **Kto:** człowiek\n- **Zastępuje:** D-001, D-002\n\n"
                "## D-004 D\n- **Kto:** coordinator\n\n## D-005 E\n- **Kto:** coordinator\n- **Zastępuje:** D-004\n")
        ds = decisions.parse(text)
        retired, challenged = decisions.supersession(ds)
        self.assertEqual((retired, challenged), ({"D-001": ["D-003"], "D-002": ["D-003"], "D-004": ["D-005"]}, {}))
        self.assertEqual([d.id for d in decisions.active_human(ds)], ["D-003"])

    def test_a_decision_challenged_by_an_agent_and_retired_by_a_human_is_retired(self):
        text = ("## D-001 A\n- **Kto:** człowiek\n\n"
                "## D-002 B\n- **Kto:** judge\n- **Zastępuje:** D-001\n\n"
                "## D-003 C\n- **Kto:** człowiek\n- **Zastępuje:** D-001\n")
        ds = decisions.parse(text)
        retired, challenged = decisions.supersession(ds)
        self.assertEqual((retired, challenged), ({"D-001": ["D-003"]}, {"D-001": ["D-002"]}))
        self.assertEqual([d.id for d in decisions.active_human(ds)], ["D-003"])

    def test_an_agent_entry_superseding_an_unknown_one_retires_it_but_a_self_reference_does_nothing(self):
        text = ("## D-001 A\n- **Decyzja:** x\n\n## D-002 B\n- **Kto:** judge\n- **Zastępuje:** D-001, D-002\n")
        retired, challenged = decisions.supersession(decisions.parse(text))
        self.assertEqual((retired, challenged), ({"D-001": ["D-002"]}, {}))


if __name__ == "__main__":
    unittest.main()
