"""`house night` A2b: a retry after a merge conflict brings `main` into the task branch (fake workers, real git)."""
import subprocess
import unittest
from pathlib import Path

from unittest import mock

from houselib import HOUSE_ROOT, attempts, githook, merge, night
from tests import test_night as tn
from tests.helpers import git


def _merging(wt) -> bool:
    return subprocess.run(["git", "rev-parse", "-q", "--verify", "MERGE_HEAD"], cwd=wt,
                          capture_output=True).returncode == 0


def _unmerged(wt) -> list[str]:
    return git(wt, "diff", "--name-only", "--diff-filter=U").split()


class ConflictBase(tn.Base):
    """A task whose branch and main both add `app.py`, so the gate's merge conflicts."""

    def setUp(self):
        super().setUp()
        self.add("T001", run="python3 -c 'import app'")
        self.calls: list[list[str]] = []
        self.seen: dict[int, dict] = {}

    def conflicting(self, cwd):
        """Attempt 1: the worker adds app.py on its branch while another task adds a different app.py on main."""
        (cwd / "app.py").write_text("A = 1\n")
        git(cwd, "add", "-A")
        git(cwd, "commit", "-q", "-m", "T001 work")
        (self.c / "app.py").write_text("B = 2\n")
        git(self.c, "add", "app.py")
        git(self.c, "commit", "-q", "-m", "T009 merged meanwhile")

    def observe(self, k):
        """An action that only records what the worker found in its worktree."""
        def act(cwd):
            self.seen[k] = {"merging": _merging(cwd), "unmerged": _unmerged(cwd),
                            "app": (cwd / "app.py").read_text() if (cwd / "app.py").exists() else None,
                            "other": (cwd / "other.py").exists()}
        return act

    def resolve(self, k, stage=False):
        def act(cwd):
            self.observe(k)(cwd)
            (cwd / "app.py").write_text("A = 1\nB = 2\n")
            if stage:
                git(cwd, "add", "app.py")
        return act

    def night(self, plan, **kw):
        def launch(cmd, cwd, log):
            self.calls.append(cmd)
            action = plan.pop(0) if plan else None
            return tn.FakeProc(action, cwd)
        return night.run_night(self.c, hours=1, launcher=launch, sleep=lambda s: None, **kw)

    def rows(self):
        return attempts.rows(self.house, "T001")

    def wt(self):
        return self.c / ".worktrees/T001-x"


class MergeOutcomeTest(ConflictBase):
    def test_conflict_refusal_is_flagged(self):
        wt = night.prepare_worktree(self.c, "T001")
        self.conflicting(wt)
        night._set(night.checks.load_task(self.house, "T001"), status="review")
        git(self.c, "commit", "-qam", "review")
        out = merge.merge_task(self.c, "T001", wt)
        self.assertFalse(out.ok)
        self.assertTrue(out.conflict)

    def test_red_refusal_is_not_a_conflict(self):
        wt = night.prepare_worktree(self.c, "T001")
        (wt / "app.py").write_text("raise SystemExit(1)\n")
        git(wt, "add", "-A")
        git(wt, "commit", "-qm", "red")
        night._set(night.checks.load_task(self.house, "T001"), status="review")
        git(self.c, "commit", "-qam", "review")
        out = merge.merge_task(self.c, "T001", wt)
        self.assertFalse(out.ok)
        self.assertFalse(out.conflict)


class ConflictRetryTest(ConflictBase):
    def test_conflict_refusal_recorded_with_reason(self):
        rep = self.night([self.conflicting], max_tasks=1)
        self.assertEqual([a.outcome for a in rep.attempts], ["merge-refused"])
        self.assertEqual(rep.attempts[0].reason, "conflict")
        self.assertEqual([(r.outcome, r.reason) for r in self.rows()], [("merge-refused", "conflict")])
        report = rep.report_path.read_text()
        self.assertIn("| merge-refused (conflict) |", report)

    def check_resolved(self, stage):
        rep = self.night([self.conflicting, self.resolve(2, stage=stage)])
        self.assertEqual([a.outcome for a in rep.attempts], ["merge-refused", "merged"], rep.attempts)
        self.assertEqual(self.seen[2]["unmerged"], ["app.py"])
        self.assertTrue(self.seen[2]["merging"])
        self.assertIn("<<<<<<<", self.seen[2]["app"])
        self.assertEqual((self.c / "app.py").read_text(), "A = 1\nB = 2\n")
        row2 = self.rows()[-1]
        self.assertEqual((row2.outcome, row2.main_merged), ("merged", "conflict in 1 file"))
        parents = git(self.c, "rev-list", "--parents", "-n", "1", row2.head).split()
        self.assertEqual(len(parents), 3, "the driver's commit on the task branch is a merge commit")
        self.assertIn("main merged: conflict in 1 file", rep.report_path.read_text())

    def test_conflict_resolved_and_staged_by_worker_is_merged(self):
        self.check_resolved(stage=True)

    def test_conflict_resolved_without_staging_is_merged(self):
        self.check_resolved(stage=False)

    def test_clean_merge_of_main_needs_only_the_check(self):
        self.night([self.conflicting], max_tasks=1)
        (self.c / "app.py").write_text("A = 1\n")  # main now agrees with the branch: the merge of main is clean
        (self.c / "other.py").write_text("C = 3\n")
        git(self.c, "add", "-A")
        git(self.c, "commit", "-qm", "main moved on")
        rep = self.night([self.observe(2)])
        self.assertEqual([a.outcome for a in rep.attempts], ["merged"])
        self.assertEqual(self.seen[2], {"merging": True, "unmerged": [], "app": "A = 1\n", "other": True})
        self.assertEqual(self.rows()[-1].main_merged, "clean")
        self.assertEqual(self.status("T001"), "done")

    def test_main_moved_again_merges_main_again(self):
        def resolve_while_main_moves(cwd):
            self.resolve(2)(cwd)
            (self.c / "app.py").write_text("B = 3\n")  # another task changes the same line meanwhile
            git(self.c, "commit", "-qam", "T010 merged meanwhile")

        def resolve_again(cwd):
            self.observe(3)(cwd)
            (cwd / "app.py").write_text("A = 1\nB = 3\n")
        rep = self.night([self.conflicting, resolve_while_main_moves, resolve_again])
        self.assertEqual([(a.outcome, a.reason) for a in rep.attempts],
                         [("merge-refused", "conflict"), ("merge-refused", "conflict"), ("merged", "")])
        self.assertEqual(self.seen[3]["unmerged"], ["app.py"])
        self.assertEqual((self.c / "app.py").read_text(), "A = 1\nB = 3\n")

    def test_unresolved_conflict_keeps_the_merge_for_the_next_attempt(self):
        def partial(cwd):
            self.observe(2)(cwd)
            (cwd / "feature.py").write_text("F = 1\n")  # real work, conflict left unresolved
        rep = self.night([self.conflicting, partial, self.resolve(3)])
        self.assertEqual([a.outcome for a in rep.attempts], ["merge-refused", "no-commits", "merged"])
        a2 = rep.attempts[1]
        self.assertIn("unresolved merge conflict", a2.message)
        self.assertIn("app.py", a2.message)
        self.assertEqual((a2.reason, a2.main_merged), ("conflict", "in progress"))
        self.assertEqual((self.rows()[1].reason, self.rows()[1].main_merged), ("conflict", "in progress"))
        self.assertEqual(self.seen[3]["unmerged"], ["app.py"], "attempt 3 continues the same merge")
        self.assertIn("with conflicts in app.py", self.calls[2][-1])
        self.assertEqual(self.rows()[2].main_merged, "in progress")
        self.assertEqual((self.c / "feature.py").read_text(), "F = 1\n", "the work of attempt 2 is not lost")

    def test_unresolved_conflict_is_not_aborted(self):
        def partial(cwd):
            (cwd / "feature.py").write_text("F = 1\n")
        rep = self.night([self.conflicting, partial], max_tasks=2)
        self.assertEqual([a.outcome for a in rep.attempts], ["merge-refused", "no-commits"])
        self.assertTrue(_merging(self.wt()))
        self.assertEqual(_unmerged(self.wt()), ["app.py"])
        self.assertTrue((self.wt() / "feature.py").exists())

    def test_markers_marked_resolved_are_not_committed(self):
        def stage_markers(cwd):
            git(cwd, "add", "app.py")  # conflict markers still in the file
        rep = self.night([self.conflicting, stage_markers], max_tasks=2)
        self.assertEqual([(a.outcome, a.reason) for a in rep.attempts],
                         [("merge-refused", "conflict"), ("no-commits", "conflict")])
        self.assertIn("app.py", rep.attempts[1].message)
        self.assertTrue(_merging(self.wt()))
        self.assertNotIn("<<<<<<<", (self.c / "app.py").read_text())

    def test_protected_conflicted_path_blocks_without_a_worker(self):
        self.night([self.conflicting], max_tasks=1)
        (self.house / "spec.yaml").write_text("scoring:\n  local_scorer: python3 app.py\n")
        git(self.c, "commit", "-qam", "app.py is the scorer now")
        self.calls.clear()
        rep = self.night([self.observe(2)])
        self.assertEqual([(a.outcome, a.reason) for a in rep.attempts], [("blocked-by-worker", "conflict")])
        self.assertIn("app.py", rep.attempts[0].message)
        self.assertIn("protected", rep.attempts[0].message)
        self.assertEqual(self.calls, [], "no worker is asked to edit a protected file")
        self.assertEqual(self.status("T001"), "blocked")
        self.assertFalse(_merging(self.wt()))
        self.assertEqual(git(self.wt(), "status", "--porcelain"), "")
        self.assertIn("app.py", rep.report_path.read_text())
        progress = (self.house / "PROGRESS.md").read_text()
        self.assertIn("app.py", progress)
        self.assertIn("resolve by hand on branch task/T001-x", progress)

    def test_other_refusals_do_not_merge_main(self):
        def red(cwd):
            (cwd / "app.py").write_text("raise SystemExit(1)\n")
            git(cwd, "add", "-A")
            git(cwd, "commit", "-qm", "red")
        rep = self.night([red, self.observe(2)], max_tasks=2)
        self.assertEqual([a.outcome for a in rep.attempts], ["merge-refused", "merge-refused"])
        self.assertEqual([r.reason for r in self.rows()], ["", ""])
        self.assertFalse(self.seen[2]["merging"])
        self.assertEqual(self.rows()[-1].main_merged, "")


SECRET = 'API_KEY = "sk-abcdefghijklmnopqrstuvwxyz123456"\n'


class ReviewFixTest(ConflictBase):
    """Review round of A2b: what the driver must never commit, and what it must never throw away."""

    def conflict_on(self, name: str, ours: bytes, theirs: bytes, extra: dict | None = None):
        """Attempt 1: the branch adds app.py and `name`; main adds a different `name` (and `extra` files)."""
        def act(cwd):
            (cwd / "app.py").write_text("A = 1\n")
            (cwd / name).write_bytes(ours)
            git(cwd, "add", "-A")
            git(cwd, "commit", "-q", "-m", "T001 work")
            (self.c / name).write_bytes(theirs)
            for rel, text in (extra or {}).items():
                (self.c / rel).write_text(text)
            git(self.c, "add", "-A")
            git(self.c, "commit", "-q", "-m", "T009 merged meanwhile")
        return act

    def test_markers_in_a_file_no_criterion_imports_are_not_committed(self):
        def stage_markers(cwd):
            git(cwd, "add", "notes.md")  # markers stay; the check (import app) stays GREEN
        rep = self.night([self.conflict_on("notes.md", b"ours\n", b"theirs\n"), stage_markers], max_tasks=2)
        self.assertEqual([(a.outcome, a.reason) for a in rep.attempts],
                         [("merge-refused", "conflict"), ("no-commits", "conflict")])
        self.assertIn("notes.md", rep.attempts[1].message)
        self.assertEqual((self.c / "notes.md").read_text(), "theirs\n")

    def test_markers_in_a_new_file_are_found_in_the_staged_diff(self):
        def resolve_but_paste(cwd):
            (cwd / "notes.md").write_text("ours and theirs\n")
            (cwd / "pasted.md").write_text("x\n<<<<<<< HEAD\ny\n")
        rep = self.night([self.conflict_on("notes.md", b"ours\n", b"theirs\n"), resolve_but_paste], max_tasks=2)
        self.assertEqual(rep.attempts[1].outcome, "no-commits")
        self.assertIn("pasted.md", rep.attempts[1].message)
        self.assertFalse((self.c / "pasted.md").exists())

    def test_a_lone_underline_from_main_is_not_a_marker(self):
        underline = {"README.rst": "Title\n=======\n\ntext\n"}  # a legitimate 7-character RST underline
        rep = self.night([self.conflict_on("notes.md", b"ours\n", b"theirs\n", underline),
                          lambda cwd: (cwd / "notes.md").write_text("ours and theirs\n")])
        self.assertEqual([a.outcome for a in rep.attempts], ["merge-refused", "merged"], rep.attempts)
        self.assertEqual((self.c / "README.rst").read_text(), underline["README.rst"])

    def test_a_three_part_marker_block_is_unresolved(self):
        def resolve_but_paste(cwd):
            (cwd / "notes.md").write_text("ours and theirs\n")
            (cwd / "pasted.md").write_text("<<<<<<< HEAD\na\n=======\nb\n>>>>>>> main\n")
        rep = self.night([self.conflict_on("notes.md", b"ours\n", b"theirs\n"), resolve_but_paste], max_tasks=2)
        self.assertEqual([(a.outcome, a.reason) for a in rep.attempts],
                         [("merge-refused", "conflict"), ("no-commits", "conflict")])
        self.assertIn("pasted.md", rep.attempts[1].message)

    def test_untouched_binary_conflict_is_unresolved(self):
        rep = self.night([self.conflict_on("logo.bin", b"\x00ours\x01", b"\x00theirs\x01"), None], max_tasks=2)
        self.assertEqual([(a.outcome, a.reason) for a in rep.attempts],
                         [("merge-refused", "conflict"), ("no-commits", "conflict")])
        self.assertIn("logo.bin", rep.attempts[1].message)
        self.assertEqual((self.c / "logo.bin").read_bytes(), b"\x00theirs\x01")

    def test_binary_conflict_taken_from_main_is_resolved(self):
        def take_theirs(cwd):
            git(cwd, "checkout", "--theirs", "logo.bin")
        rep = self.night([self.conflict_on("logo.bin", b"\x00ours\x01", b"\x00theirs\x01"), take_theirs])
        self.assertEqual([a.outcome for a in rep.attempts], ["merge-refused", "merged"])
        self.assertEqual((self.c / "logo.bin").read_bytes(), b"\x00theirs\x01")

    def test_conflicted_secret_like_path_blocks_without_a_worker(self):
        rep = self.night([self.conflict_on("config.py", b"DEBUG = True\n", SECRET.encode()), self.observe(2)],
                         max_tasks=2)
        self.assertEqual([(a.outcome, a.reason) for a in rep.attempts],
                         [("merge-refused", "conflict"), ("blocked-by-worker", "conflict")])
        self.assertIn("config.py", rep.attempts[1].message)
        self.assertEqual(len(self.calls), 1, "no worker is asked to resolve a secret-looking file")
        self.assertEqual(self.status("T001"), "blocked")

    def test_markers_in_a_merge_the_worker_committed_never_reach_the_gate(self):
        def commit_markers(cwd):
            git(cwd, "add", "notes.md")
            git(cwd, "commit", "-qm", "worker commits the merge with markers")
        rep = self.night([self.conflict_on("notes.md", b"ours\n", b"theirs\n"), commit_markers], max_tasks=2)
        self.assertEqual([(a.outcome, a.reason) for a in rep.attempts],
                         [("merge-refused", "conflict"), ("no-commits", "conflict")])
        self.assertIn("notes.md", rep.attempts[1].message)
        self.assertEqual((self.c / "notes.md").read_text(), "theirs\n")
        row1 = self.rows()[0]
        self.assertEqual(git(self.wt(), "rev-parse", "HEAD").strip(), row1.head, "branch reset to its old head")
        self.assertIn("<<<<<<<", (self.wt() / "notes.md").read_text(), "the work stays in the worktree")
        self.assertTrue(_merging(self.wt()), "the merge is in progress again for the next attempt")

    def test_after_undone_commits_the_next_attempt_finishes_the_merge(self):
        def commit_markers(cwd):
            git(cwd, "add", "notes.md")
            git(cwd, "commit", "-qm", "worker commits the merge with markers")
        rep = self.night([self.conflict_on("notes.md", b"ours\n", b"theirs\n"), commit_markers,
                          lambda cwd: (cwd / "notes.md").write_text("both\n")])
        self.assertEqual([a.outcome for a in rep.attempts], ["merge-refused", "no-commits", "merged"])
        self.assertEqual((self.c / "notes.md").read_text(), "both\n")

    def test_worker_committed_clean_merge_is_merged(self):
        def resolve_and_commit(cwd):
            (cwd / "notes.md").write_text("both\n")
            git(cwd, "add", "notes.md")
            git(cwd, "commit", "-qm", "worker commits the merge")
        rep = self.night([self.conflict_on("notes.md", b"ours\n", b"theirs\n"), resolve_and_commit])
        self.assertEqual([a.outcome for a in rep.attempts], ["merge-refused", "merged"])

    def test_marker_lines_main_carries_are_not_conflicts(self):
        sample = {"git.md": "How a conflict looks:\n\n<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> main\n"}
        rep = self.night([self.conflict_on("notes.md", b"ours\n", b"theirs\n", sample),
                          lambda cwd: (cwd / "notes.md").write_text("both\n")])
        self.assertEqual([a.outcome for a in rep.attempts], ["merge-refused", "merged"], rep.attempts)
        self.assertEqual((self.c / "git.md").read_text(), sample["git.md"])

    def test_keeping_ours_explicitly_is_resolved(self):
        def keep_ours(cwd):
            git(cwd, "checkout", "--ours", "logo.bin")
            git(cwd, "add", "logo.bin")
        rep = self.night([self.conflict_on("logo.bin", b"\x00ours\x01", b"\x00theirs\x01"), keep_ours])
        self.assertEqual([a.outcome for a in rep.attempts], ["merge-refused", "merged"], rep.attempts)
        self.assertEqual((self.c / "logo.bin").read_bytes(), b"\x00ours\x01")

    def test_marker_after_a_plus_plus_line_is_found(self):
        def resolve_but_hide(cwd):
            (cwd / "notes.md").write_text("both\n")
            (cwd / "app.py").write_text("A = 1\nX = \"\"\"\n++ looks like a diff header\n<<<<<<< HEAD\n\"\"\"\n")
        rep = self.night([self.conflict_on("notes.md", b"ours\n", b"theirs\n"), resolve_but_hide], max_tasks=2)
        self.assertEqual([(a.outcome, a.reason) for a in rep.attempts],
                         [("merge-refused", "conflict"), ("no-commits", "conflict")])
        self.assertIn("app.py", rep.attempts[1].message)

    def test_a_stale_merge_record_is_rebuilt(self):
        self.night([self.conflicting], max_tasks=1)
        wt = night.prepare_worktree(self.c, "T001")
        subprocess.run(["git", "merge", "--no-ff", "--no-commit", "main"], cwd=wt, capture_output=True)
        night._save_record(self.c, "T001", {"main": "0" * 40, "paths": {"gone.txt": None}})
        record = night.merge_record(self.c, "T001", wt)
        self.assertEqual(record["main"], git(wt, "rev-parse", "MERGE_HEAD").strip())
        self.assertEqual(list(record["paths"]), ["app.py"])

    def test_refused_merge_commit_ends_no_commits_with_the_reason(self):
        githook.install(self.c, HOUSE_ROOT)  # the real hook: it refuses main's secret-looking fixture
        rep = self.night([self.conflict_on("notes.md", b"ours\n", b"theirs\n", {"fixture.py": SECRET}),
                          lambda cwd: (cwd / "notes.md").write_text("both\n")], max_tasks=2)
        a2 = rep.attempts[1]
        self.assertEqual((a2.outcome, a2.reason, a2.main_merged), ("no-commits", "conflict", "in progress"))
        self.assertIn("looks like a secret", a2.message)
        self.assertNotIn("sk-abcdef", a2.message)
        self.assertTrue(_merging(self.wt()))

    def test_timeout_leaves_the_merge_in_progress(self):
        self.night([self.conflicting], max_tasks=1)
        (self.house / "spec.yaml").write_text("night:\n  task_minutes: 0\n")
        git(self.c, "commit", "-qam", "spec")
        rep = self.night(["hang"], max_tasks=1)
        self.assertEqual([(a.outcome, a.reason, a.main_merged) for a in rep.attempts],
                         [("timeout", "conflict", "in progress")])
        self.assertTrue(_merging(self.wt()))

    def test_exception_in_finish_leaves_the_merge_in_progress(self):
        self.night([self.conflicting], max_tasks=1)
        with mock.patch.object(night, "_finish", side_effect=RuntimeError("boom")):
            rep = self.night([None], max_tasks=1)
        self.assertEqual([(a.outcome, a.reason, a.main_merged) for a in rep.attempts],
                         [("error", "conflict", "in progress")])
        self.assertTrue(_merging(self.wt()))

    def test_interrupt_leaves_the_merge_in_progress(self):
        self.night([self.conflicting], max_tasks=1)

        def interrupt(cwd):
            raise KeyboardInterrupt
        rep = self.night([interrupt], max_tasks=1)
        self.assertEqual([(a.outcome, a.reason, a.main_merged) for a in rep.attempts],
                         [("interrupted", "conflict", "in progress")])
        self.assertTrue(_merging(self.wt()))

    def test_next_attempt_keeps_a_merge_in_progress(self):
        self.night([self.conflicting], max_tasks=1)
        (self.house / "spec.yaml").write_text("night:\n  task_minutes: 0\n")
        git(self.c, "commit", "-qam", "spec")
        self.night(["hang"], max_tasks=1)
        (self.wt() / "app.py").write_text("A = 1\nB = 2\n")  # half-way state a worker left
        (self.house / "spec.yaml").write_text("night:\n  task_minutes: 45\n")
        git(self.c, "commit", "-qam", "spec back")
        rep = self.night([self.observe(3)])
        self.assertEqual(self.seen[3]["app"], "A = 1\nB = 2\n", "the merge in progress was not restarted")
        self.assertEqual([a.outcome for a in rep.attempts], ["merged"])

    def test_failed_merge_of_main_is_an_error_without_a_worker(self):
        self.night([self.conflicting], max_tasks=1)
        (self.wt() / "app.py").write_text("A = 7\n")  # local change in the way of the merge
        self.calls.clear()
        rep = self.night([self.observe(2)], max_tasks=1)
        self.assertEqual(self.calls, [])
        self.assertEqual([a.outcome for a in rep.attempts], ["error"])
        row = self.rows()[-1]
        self.assertTrue(row.main_merged.startswith("failed: "), row.main_merged)
        self.assertIn("merging main", row.message)
        self.assertIn("main merged: failed", rep.report_path.read_text())

    def test_up_to_date_brief_says_main_moved_again(self):
        self.night([self.conflicting], max_tasks=1)
        subprocess.run(["git", "merge", "main"], cwd=self.wt(), capture_output=True)
        (self.wt() / "app.py").write_text("A = 1\nB = 2\n")
        git(self.wt(), "add", "app.py")
        git(self.wt(), "commit", "-qm", "merged by hand")
        rep = self.night([None])
        self.assertEqual(self.rows()[1].main_merged, "up to date")
        self.assertIn("main was already merged; the conflict must come from main moving again; re-run",
                      self.calls[1][-1])
        self.assertEqual([a.outcome for a in rep.attempts], ["merged"])

    def test_error_in_the_blocked_handler_does_not_end_the_night(self):
        self.night([self.conflicting], max_tasks=1)
        (self.house / "spec.yaml").write_text("scoring:\n  local_scorer: python3 app.py\n")
        git(self.c, "commit", "-qam", "app.py is the scorer now")
        with mock.patch.object(night, "_block_for_human", side_effect=RuntimeError("disk full")):
            rep = self.night([None], max_tasks=1)
        self.assertEqual([a.outcome for a in rep.attempts], ["blocked-by-worker"])
        self.assertIsNotNone(rep.report_path)


class ConflictBriefTest(ConflictBase):
    """The hand-off brief of the attempt after a conflict says how to finish the merge of main."""

    @staticmethod
    def next_part(text: str) -> str:
        return text[text.index("## Next\n"):text.index("## Critical context")]

    def test_conflict_brief_names_the_paths_and_says_the_driver_commits(self):
        self.night([self.conflicting, self.resolve(2)])
        prompt = self.calls[1][-1]
        nxt = self.next_part(prompt)
        self.assertIn("Next action: main is merged into the worktree with conflicts in app.py: resolve the "
                      "conflict markers there keeping both sides' behaviour, mark each file resolved with "
                      "`git add <path>`, then run the Done-when check; do not commit, the driver commits the "
                      "merge", nxt)
        self.assertNotIn("fix that first", nxt)
        self.assertIn("attempt 1 ended `merge-refused` (conflict)", prompt)
        self.assertIn("- main merged into the worktree before this attempt: conflict in 1 file (app.py), merge in "
                      "progress, not committed", prompt)

    def test_clean_brief_says_rerun_the_check(self):
        self.night([self.conflicting], max_tasks=1)
        (self.c / "app.py").write_text("A = 1\n")
        (self.c / "other.py").write_text("C = 3\n")  # main brings something new: a clean merge, not "up to date"
        git(self.c, "add", "-A")
        git(self.c, "commit", "-qm", "main agrees and moves on")
        self.night([None])
        nxt = self.next_part(self.calls[1][-1])
        self.assertIn("Next action: main merged cleanly (staged, not committed); re-run the Done-when check and "
                      "fix what it reports; do not commit, the driver commits the merge", nxt)
        self.assertIn("main merged into the worktree before this attempt: clean", self.calls[1][-1])

    def test_codex_brief_names_the_house_check(self):
        task = self.house / "tasks/T001-x.md"
        task.write_text(task.read_text().replace("depends_on: []\n", "depends_on: []\nagent: coder-codex\n"))
        (self.house / "tools.json").write_text('{"codex": true}')
        git(self.c, "commit", "-qam", "codex")
        self.night([self.conflicting, self.resolve(2)])
        cmd = self.calls[1]
        text = Path(cmd[cmd.index("--brief-file") + 1]).read_text()
        self.assertIn("then run `", self.next_part(text))
        self.assertIn("check c1 T001 --worktree .`; do not commit, the driver commits the merge",
                      self.next_part(text))

    def test_other_outcomes_keep_the_gate_action(self):
        wt = night.prepare_worktree(self.c, "T001")
        attempts.record(self.house, attempts.Row(task="T001", n=1, source="night", agent="coder", tool="claude",
                                                 outcome="merge-refused", message="T001 red on its branch"))
        nxt = self.next_part(night.next_brief(self.c, "T001", wt, 2, "claude"))
        self.assertIn("Next action: attempt 1 ended `merge-refused` (see Observed and Errors and fixes): fix that "
                      "first", nxt)
        self.assertNotIn("driver commits the merge", nxt)


if __name__ == "__main__":
    unittest.main()
