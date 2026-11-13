"""`house review`: independent review by another model family, blind brief, daily Codex budget, merge gate."""
import json
import re
import tempfile
import unittest
from datetime import date
from pathlib import Path

from houselib import checks, merge, review, scaffold
from houselib.fm import set_fields
from tests.helpers import FAKE_TOOLS, git, make_root

GOOD = ("1. MAJOR calc.py:2 — subtraction instead of addition\n2. NIT calc.py:1 — missing docstring\n"
        "VERDICT: fix the major before merging\n")
BLOCK = "BLOCKER score.py:10 — leaks the test labels into training\nVERDICT: do not merge\n"


class FakeLauncher:
    """Answers like codex (writes -o file, handles the nonce check) or claude (stdout)."""

    def __init__(self, text=GOOD, codex_alive=True, hang=False):
        self.text, self.codex_alive, self.hang, self.calls = text, codex_alive, hang, []

    def __call__(self, cmd, cwd, stdin_text, timeout):
        self.calls.append((cmd, stdin_text))
        if self.hang:
            return None, ""
        if Path(cmd[0]).name == "codex":
            out = Path(cmd[cmd.index("-o") + 1])
            nonce = re.search(r"cat (\S+/nonce)", stdin_text or "")
            if nonce:
                out.write_text(Path(nonce.group(1)).read_text() if self.codex_alive else "")
            else:
                out.write_text(self.text)
            return 0, "codex log"
        return 0, self.text

    def kinds(self):
        return [Path(c[0]).name for c, _ in self.calls]


class ReviewTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_root(Path(self._tmp.name))
        self.c = scaffold.new_contest("c1", None, root=self.root, detect=lambda: FAKE_TOOLS)
        self.house = self.c / ".house"
        tools = json.loads((self.house / "tools.json").read_text())
        (self.house / "tools.json").write_text(json.dumps({**tools, "codex": True}))
        git(self.c, "commit", "-qam", "codex available")

    def tearDown(self):
        self._tmp.cleanup()

    def task(self, tid="T001", extra="", files=None):
        branch = f"task/{tid}-x"
        (self.house / "tasks" / f"{tid}-x.md").write_text(
            f"---\nid: {tid}\ntitle: Add calc\nstatus: review\nowner: coder\nbranch: {branch}\ndepends_on: []\n{extra}"
            "acceptance:\n  - id: AC-1\n    run: \"true\"\nprotected: []\n---\n## Objective\nAdd `calc.add`.\n")
        checks.freeze(self.house, tid)
        git(self.c, "add", ".house")
        git(self.c, "commit", "-q", "-m", f"plan {tid}")
        wt = self.c / ".worktrees" / f"{tid}-x"
        git(self.c, "worktree", "add", "-q", str(wt), "-b", branch)
        for rel, text in (files or {"calc.py": "def add(a, b):\n    return a - b\n"}).items():
            (wt / rel).write_text(text)
        git(wt, "add", "-A")
        git(wt, "commit", "-q", "-m", f"{tid} work")
        return wt

    def test_brief_is_blind_and_scoped(self):
        b = review.brief("Add calc", ["AC-1: true"], "abc123", "def456")
        self.assertIn("git diff abc123...def456", b)
        self.assertIn("VERDICT:", b)
        self.assertIn("only reviewer", b)
        for phrase in ("second opinion", "counterpart", "blind review"):
            self.assertNotIn(phrase, b.lower())

    def test_codex_reviews_claude_work_within_budget(self):
        self.task()
        fake = FakeLauncher()
        res = review.review_task(self.c, "T001", launcher=fake)
        self.assertTrue(res.ok, res.why)
        self.assertEqual(res.reviewer, "codex")
        self.assertEqual((res.findings.majors, res.findings.nits, res.findings.blockers), (1, 1, 0))
        report = (self.house / "reports/review-T001.md").read_text()
        head = git(self.c, "rev-parse", "task/T001-x").strip()
        self.assertIn(f"- **Head:** {head}", report)
        self.assertIn("- **Reviewer:** codex", report)
        state = json.loads((self.house / "state.json").read_text())
        self.assertEqual(state["codex_reviews"][date.today().isoformat()], 1)

    def test_codex_work_is_reviewed_by_claude(self):
        self.task(extra="agent: coder-codex\n")
        fake = FakeLauncher()
        res = review.review_task(self.c, "T001", launcher=fake)
        self.assertEqual((res.reviewer, fake.kinds()), ("claude", ["claude"]))

    def test_budget_used_falls_back_to_claude(self):
        self.task()
        (self.house / "state.json").write_text(json.dumps({"codex_reviews": {date.today().isoformat(): 4}}))
        res = review.review_task(self.c, "T001", launcher=FakeLauncher())
        self.assertEqual(res.reviewer, "claude")
        self.assertIn("weaker independence", (self.house / "reports/review-T001.md").read_text())

    def test_dead_codex_falls_back(self):
        self.task()
        res = review.review_task(self.c, "T001", launcher=FakeLauncher(codex_alive=False))
        self.assertEqual(res.reviewer, "claude")
        self.assertIn("nonce", res.note)

    def test_apology_or_hang_is_a_failure(self):
        self.task()
        res = review.review_task(self.c, "T001", launcher=FakeLauncher(text="Sorry, I couldn't complete this."))
        self.assertFalse(res.ok)
        self.assertIn("VERDICT", res.why)
        res = review.review_task(self.c, "T001", launcher=FakeLauncher(hang=True))
        self.assertFalse(res.ok)

    def test_review_since(self):
        self.task()
        base = git(self.c, "rev-parse", "HEAD").strip()
        git(self.c, "merge", "-q", "--no-ff", "--no-edit", "task/T001-x")
        res = review.review_since(self.c, base, launcher=FakeLauncher())
        self.assertTrue(res.ok)
        self.assertEqual(json.loads((self.house / "state.json").read_text())["last_review_ref"],
                         git(self.c, "rev-parse", "HEAD").strip())


class ReviewGateTest(ReviewTest):
    def always(self):
        return self.task(extra="review: always\n", files={"calc.py": "def add(a, b):\n    return a + b\n"})

    def test_refused_without_review(self):
        self.always()
        out = merge.merge_task(self.c, "T001")
        self.assertFalse(out.ok)
        self.assertIn("review", out.message)

    def test_refused_with_stale_head(self):
        wt = self.always()
        review.review_task(self.c, "T001", launcher=FakeLauncher(text="VERDICT: fine\n"))
        (wt / "more.py").write_text("X = 1\n")
        git(wt, "add", "-A")
        git(wt, "commit", "-q", "-m", "more")
        out = merge.merge_task(self.c, "T001")
        self.assertFalse(out.ok)
        self.assertIn("head", out.message)

    def test_blocker_refused_then_accepted_with_reason(self):
        self.always()
        review.review_task(self.c, "T001", launcher=FakeLauncher(text=BLOCK))
        out = merge.merge_task(self.c, "T001")
        self.assertFalse(out.ok)
        self.assertIn("BLOCKER", out.message)
        out = merge.merge_task(self.c, "T001", accept_review="false positive: labels are the public split")
        self.assertTrue(out.ok, out.message)
        self.assertIn("false positive", (self.house / "DECISIONS.md").read_text())

    def test_major_also_blocks_review_always_tasks(self):
        self.always()
        review.review_task(self.c, "T001", launcher=FakeLauncher(text=GOOD))
        out = merge.merge_task(self.c, "T001")
        self.assertFalse(out.ok)
        self.assertIn("MAJOR", out.message)

    def test_clean_review_passes(self):
        self.always()
        review.review_task(self.c, "T001", launcher=FakeLauncher(text="Nothing significant.\nVERDICT: good\n"))
        tf = self.house / "tasks/T001-x.md"
        tf.write_text(set_fields(tf.read_text(), {"status": "review"}))
        self.assertTrue(merge.merge_task(self.c, "T001").ok)


if __name__ == "__main__":
    unittest.main()
