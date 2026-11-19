"""`house guard`: rules that block edits to protected files, secret commits and unsafe pushes."""
import tempfile
import unittest
from pathlib import Path

from houselib import checks, guard, scaffold
from tests.helpers import FAKE_TOOLS, git, make_root

TASK = ("---\nid: T001\ntitle: x\nstatus: todo\nowner: null\nbranch: task/T001-x\ndepends_on: []\n"
        "acceptance:\n  - id: AC-1\n    run: \"true\"\nprotected:\n  - test_a.py\n  - later.py\n---\nx\n")


class GuardTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = make_root(Path(self._tmp.name))
        self.c = scaffold.new_contest("c1", None, root=self.root, detect=lambda: FAKE_TOOLS)
        (self.c / "test_a.py").write_text("assert True\n")
        (self.c / ".house/tasks/T001-x.md").write_text(TASK)
        git(self.c, "add", "-A")
        git(self.c, "commit", "-q", "-m", "base")
        checks.freeze(self.c / ".house", "T001")
        self.wt = self.c / ".worktrees/T001-x"
        git(self.c, "worktree", "add", "-q", str(self.wt), "-b", "task/T001-x")

    def tearDown(self):
        self._tmp.cleanup()

    def test_contest_root_from_worktree_and_subdir(self):
        (self.wt / "src").mkdir()
        self.assertEqual(guard.contest_root(self.wt / "src"), self.c.resolve())
        self.assertEqual(guard.contest_root(self.c), self.c.resolve())
        self.assertIsNone(guard.contest_root(Path(self._tmp.name)))

    def test_repo_relative(self):
        self.assertEqual(guard.repo_relative(str(self.wt / "test_a.py"), self.wt, self.c), "test_a.py")
        self.assertEqual(guard.repo_relative("test_a.py", self.wt, self.c), "test_a.py")
        self.assertEqual(guard.repo_relative(str(self.c / ".house/checks/T001.json"), self.c, self.c),
                         ".house/checks/T001.json")
        self.assertIsNone(guard.repo_relative("/etc/passwd", self.c, self.c))

    def test_protected_paths_only_hashed(self):
        self.assertEqual(guard.protected_paths(self.c), {"test_a.py": "T001"})

    def test_local_scorer_is_protected(self):
        (self.c / "score.py").write_text("print(1)\n")
        (self.c / "preds.txt").write_text("1\n")
        (self.c / ".house/spec.yaml").write_text("scoring:\n  local_scorer: python3 score.py preds.txt --split val\n")
        git(self.c, "add", "score.py")
        git(self.c, "commit", "-qm", "scorer")
        self.assertIn("score.py", guard.protected_paths(self.c))
        self.assertNotIn("preds.txt", guard.protected_paths(self.c))  # untracked output, not the scorer

    def test_check_edit(self):
        self.assertIn("T001", guard.check_edit("test_a.py", self.c))
        self.assertIsNotNone(guard.check_edit(".house/checks/T001.json", self.c))
        self.assertIsNone(guard.check_edit("later.py", self.c))
        self.assertIsNone(guard.check_edit("src/app.py", self.c))

    def test_edit_attempts_register_refused(self):
        self.assertIn("written only by house", guard.check_edit(".house/attempts.jsonl", self.c))
        self.assertIsNone(guard.check_edit(".house/attempts.jsonl.bak", self.c))

    def test_bash_write_to_attempts_register_refused(self):
        for cmd in ("echo '{}' >> .house/attempts.jsonl", "rm .house/attempts.jsonl",
                    "sed -i 's/a/b/' .house/attempts.jsonl"):
            reason = guard.check_bash(cmd, self.c, self.c)
            self.assertIn("written only by house", reason or "", cmd)
        self.assertIsNone(guard.check_bash("cat .house/attempts.jsonl", self.c, self.c))

    def test_branch_changing_register_is_violation(self):
        (self.wt / ".house").mkdir(exist_ok=True)
        (self.wt / ".house/attempts.jsonl").write_text('{"task": "T001"}\n')
        git(self.wt, "add", "-f", ".house/attempts.jsonl")
        git(self.wt, "commit", "-qm", "forge")
        found = guard.diff_violations(self.c, "T001", self.wt)
        self.assertTrue(any(".house/attempts.jsonl" in v for v in found), found)

    def test_register_tampering_in_contest_checkout(self):
        reg = self.c / ".house/attempts.jsonl"
        self.assertEqual(guard.frozen_tampering(self.c), [])
        reg.write_text('{"task": "T001"}\n')  # not committed: a worker wrote it via $HOUSE_DIR
        found = guard.frozen_tampering(self.c)
        self.assertTrue(any("attempts.jsonl" in v for v in found), found)
        self.assertTrue(any("restore with: git checkout HEAD -- .house/attempts.jsonl" in v for v in found), found)
        git(self.c, "add", ".house/attempts.jsonl")
        git(self.c, "commit", "-qm", "register")  # house commits its own rows at once
        self.assertEqual(guard.frozen_tampering(self.c), [])
        reg.write_text(reg.read_text() + '{"task": "T002"}\n')
        self.assertTrue(any("attempts.jsonl" in v for v in guard.frozen_tampering(self.c)))
        reg.unlink()
        self.assertTrue(any("attempts.jsonl" in v for v in guard.frozen_tampering(self.c)))

    def test_register_changed_and_restore(self):
        reg = self.c / ".house/attempts.jsonl"
        self.assertFalse(guard.register_changed(self.c))
        reg.write_text('{"task": "T001"}\n')
        self.assertTrue(guard.register_changed(self.c))
        git(self.c, "add", ".house/attempts.jsonl")  # staged, not committed: still changed
        self.assertTrue(guard.register_changed(self.c))
        guard.restore_register(self.c)  # not in HEAD: removed from the tree and the index
        self.assertFalse(reg.exists())
        self.assertEqual(git(self.c, "status", "--porcelain", "--", ".house/attempts.jsonl").strip(), "")
        reg.write_text('{"task": "T001"}\n')
        git(self.c, "add", ".house/attempts.jsonl")
        git(self.c, "commit", "-qm", "register")
        reg.write_bytes(b"\xff\n")
        self.assertTrue(guard.register_changed(self.c))
        guard.restore_register(self.c)  # in HEAD: back to its bytes
        self.assertEqual(reg.read_text(), '{"task": "T001"}\n')
        self.assertFalse(guard.register_changed(self.c))

    def test_register_tampering_non_utf8_is_reported(self):
        (self.c / ".house/attempts.jsonl").write_bytes(b"\xff\xfe\n")
        found = guard.frozen_tampering(self.c)
        self.assertTrue(any("attempts.jsonl" in v for v in found), found)

    def test_bash_writes_to_protected(self):
        for cmd in ("echo x >> test_a.py", "sed -i 's/a/b/' test_a.py", "rm -f test_a.py",
                    "cp other.py test_a.py", "cat x | tee test_a.py", "true && mv test_a.py b.py",
                    "echo '{}' > .house/checks/T001.json"):
            self.assertIsNotNone(guard.check_bash(cmd, self.wt, self.c), cmd)
        for cmd in ("cat test_a.py", "python3 -m pytest test_a.py", "cp test_a.py /tmp/copy.py", "grep x test_a.py"):
            self.assertIsNone(guard.check_bash(cmd, self.wt, self.c), cmd)

    def test_house_out_is_checked_like_its_command(self):
        for prefix in ("house out --", "bin/house out --", "$HOUSE_ROOT/bin/house out --", "/opt/x/bin/house out --",
                       "house out", "timeout 5 house out --", "FOO=1 house out --", "house out -- house out --"):
            for cmd in ("rm -rf test_a.py", "sed -i 's/a/b/' test_a.py", "git commit --no-verify -m x"):
                direct = guard.check_bash(cmd, self.wt, self.c)
                self.assertIsNotNone(direct, cmd)
                self.assertEqual(guard.check_bash(f"{prefix} {cmd}", self.wt, self.c), direct, f"{prefix} {cmd}")
        self.assertEqual(guard.check_bash("house out -- rm -rf test_a.py && ls", self.wt, self.c),
                         guard.check_bash("rm -rf test_a.py", self.wt, self.c))

    def test_house_exp_run_is_checked_like_its_command(self):
        for prefix in ("house exp run calc E-001 --", "bin/house exp run calc E-001 --budget 5m --",
                       "$HOUSE_ROOT/bin/house exp run calc E-001 --note x --", "timeout 5 house exp run c E-1 --",
                       "FOO=1 house exp run c E-001 --", "house >/dev/null exp run c E-001 --",
                       "house exp run c E-001 -- house out --", "house out -- house exp run c E-001 --",
                       "house exp --budget 5m run c E-1 --", "house exp --from E-002 --idea I-3 run c E-1 --",
                       "house exp -n 3 --note x run c E-1 --", "house exp --budget=5m run c E-1 --"):
            for cmd in ("rm -rf test_a.py", "rm .house/attempts.jsonl", "sed -i 's/a/b/' test_a.py",
                        "git commit --no-verify -m x", "timeout 60 git push --no-verify"):
                direct = guard.check_bash(cmd, self.wt, self.c)
                self.assertIsNotNone(direct, cmd)
                self.assertEqual(guard.check_bash(f"{prefix} {cmd}", self.wt, self.c), direct, f"{prefix} {cmd}")
        self.assertIn("protected", guard.check_bash("house exp run calc E-001 --budget 5m -- rm test_a.py",
                                                    self.wt, self.c) or "")
        self.assertIn("protected", guard.check_bash("house exp --budget 5m run c E-1 -- rm test_a.py",
                                                    self.wt, self.c) or "")
        # one string after `--` runs through the shell (`exp._command_text`): checked as a script
        self.assertEqual(guard.check_bash("house exp run calc E-001 -- 'true && rm test_a.py'", self.wt, self.c),
                         guard.check_bash("rm test_a.py", self.wt, self.c))

    def test_house_exp_without_a_command_is_not_a_wrapper(self):
        for cmd in ("house exp run calc E-001 -- python3 train.py", "house exp run calc E-001 --budget 5m",
                    "house exp status calc", "house exp new calc --note 'rm test_a.py'", "house exp prune calc",
                    "house exp run calc E-001 -- 'python3 train.py --epochs 3'"):
            self.assertIsNone(guard.check_bash(cmd, self.wt, self.c), cmd)

    def test_house_out_allows_ordinary_commands_and_its_own_subcommands(self):
        for cmd in ("house out -- python3 -m pytest", "bin/house out -- python3 -m pytest test_a.py -q",
                    "house out show 20261117-100000-abcd --grep FAIL", "house out show E-007 --grep x --regex",
                    "house out prune --days 3", "$HOUSE_ROOT/bin/house out -- make build"):
            self.assertIsNone(guard.check_bash(cmd, self.wt, self.c), cmd)

    def test_redirections_around_house_out_do_not_hide_the_command(self):
        cmds = ("rm .house/attempts.jsonl", "rm -rf test_a.py", "git commit --no-verify -m x")
        for prefix in ("house >/dev/null out --", "house > /dev/null out --", "house 2>/dev/null out --",
                       "house 2>&1 out --", "house >>log out --", "house <in out --", "house &>/dev/null out --",
                       "house out >/dev/null --", "house out 2>&1 --", "house out -- >/dev/null",
                       ">/dev/null house out --", "timeout 5 >/dev/null house 2>&1 out --",
                       "house <<<x out --", "bin/house 1>/dev/null out -- 2>/dev/null",
                       "house {fd}>/dev/null out --", "house out {fd}<in --", "{fd}>/dev/null house out --",
                       "house out -- {log}>>/tmp/x"):
            for cmd in cmds:
                direct = guard.check_bash(cmd, self.wt, self.c)
                self.assertIsNotNone(direct, cmd)
                self.assertEqual(guard.check_bash(f"{prefix} {cmd}", self.wt, self.c), direct, f"{prefix} {cmd}")
        # the redirect targets keep their own checks
        self.assertIn("written only by house",
                      guard.check_bash("house >.house/attempts.jsonl out -- ls", self.wt, self.c) or "")
        self.assertIsNotNone(guard.check_bash("house out 2>test_a.py -- ls", self.wt, self.c))
        self.assertIsNone(guard.check_bash("house >/dev/null out -- python3 -m pytest", self.wt, self.c))
        self.assertIsNone(guard.check_bash("house 2>&1 out show 20261117-100000-abcd --grep x", self.wt, self.c))

    def test_fd_redirect_before_the_command_does_not_hide_it(self):
        for cmd in ("2>x rm test_a.py", "2>/dev/null rm .house/attempts.jsonl", "</dev/null rm test_a.py",
                    "{fd}>/dev/null rm test_a.py", "{fd}<file rm .house/attempts.jsonl",
                    "rm {fd}>/dev/null test_a.py"):
            self.assertIsNotNone(guard.check_bash(cmd, self.wt, self.c), cmd)
            self.assertEqual(guard.check_bash(f"house out -- {cmd}", self.wt, self.c),
                             guard.check_bash(cmd, self.wt, self.c), cmd)
        self.assertIsNone(guard.check_bash("echo {a,b} >/dev/null", self.wt, self.c))

    def test_fd_duplication_and_read_write_redirects(self):
        for cmd in ("echo x >& test_a.py", "echo x <> .house/attempts.jsonl", "house out -- sh -c 'echo x >& test_a.py'"):
            self.assertIsNotNone(guard.check_bash(cmd, self.wt, self.c), cmd)
        for cmd in ("python3 x.py 2>&1", "python3 x.py >&2", "python3 x.py 1>&-", "cat <&3"):
            self.assertIsNone(guard.check_bash(cmd, self.wt, self.c), cmd)

    def test_git_hook_bypass_behind_wrappers(self):
        for cmd in ("timeout 60 git push --no-verify", "env X=1 git commit -n -m x", "nice -n 5 git commit --no-verify",
                    "FOO=1 git commit -n -m x", "env -u X git commit -n -m x", "timeout -s KILL 5 git push --no-verify",
                    "timeout -k 2 5 git push --no-verify", "timeout --signal KILL 5 git commit -n -m x",
                    "sudo -u u git commit -n -m x", "sudo -g g -C 5 git commit --no-verify",
                    "stdbuf -o L git commit -n -m x", "stdbuf -oL -e 0 git commit -n -m x",
                    "env -uX git commit -n -m x", "nice -n5 git commit -n -m x"):
            self.assertEqual(guard.check_bash(cmd, self.wt, self.c), guard.HOOKS_REASON, cmd)
            self.assertEqual(guard.check_bash(f"house out -- {cmd}", self.wt, self.c), guard.HOOKS_REASON, cmd)
        self.assertIsNone(guard.check_bash("timeout 60 git commit -m x", self.wt, self.c))

    def test_composite_house_out_cases(self):
        for cmd in ("timeout 5 rm test_a.py", "bash -c 'rm test_a.py'", "sh -c 'rm -f test_a.py'",
                    "bash -euo pipefail -c 'rm test_a.py'", "bash -O extglob -c 'rm test_a.py'",
                    "bash +O extglob -c 'rm test_a.py'", "bash --rcfile f -c 'rm test_a.py'",
                    "bash --init-file f -c 'rm test_a.py'", "bash -o pipefail -c 'rm test_a.py'",
                    "sudo -u u rm test_a.py",
                    "bash -lc 'echo x > .house/attempts.jsonl'", "env X=1 rm test_a.py",
                    "bash -c 'timeout 5 git commit -n -m x'"):
            direct = guard.check_bash(cmd, self.wt, self.c)
            self.assertIsNotNone(direct, cmd)
            for wrapped in (f"house out -- {cmd}", f"cd src && house out -- {cmd}", f"house out -- timeout 5 {cmd}"):
                self.assertEqual(guard.check_bash(wrapped, self.wt, self.c), direct, wrapped)
        for cmd in ("house out -- bash -c 'python3 -m pytest -q'", "house out -- sh -c 'make build'",
                    "cd src && house out -- timeout 5 python3 -m pytest"):
            self.assertIsNone(guard.check_bash(cmd, self.wt, self.c), cmd)

    def test_house_out_does_not_hide_signal_cli(self):
        home = Path(self._tmp.name) / "home"
        for cmd in ("house out -- signal-cli receive", "bin/house out -- timeout 5 signal-cli send x"):
            self.assertIsNotNone(guard.check_signal("Bash", {"command": cmd}, self.wt, home=home), cmd)
        self.assertIsNone(guard.check_signal("Bash", {"command": "house out -- python3 -m pytest"}, self.wt,
                                             home=home))

    def test_operator_runs_do_not_hide_a_command(self):
        for cmd in ("(true); rm test_a.py", "(true);rm test_a.py", "{ true; };rm test_a.py",
                    "(:)&&rm test_a.py", "true |& tee test_a.py", "(:);git commit --no-verify -m x",
                    "(true)|tee test_a.py"):
            self.assertIsNotNone(guard.check_bash(cmd, self.wt, self.c), cmd)
        self.assertEqual(guard._tokens("(true); rm a"), ["(", "true", ")", ";", "rm", "a"])
        self.assertEqual(guard._tokens("a >>b 2>&1 && c <<<x"), ["a", ">>", "b", "2", ">&", "1", "&&", "c", "<<<", "x"])

    def test_git_add_secrets(self):
        (self.wt / ".env").write_text("KEY=1\n")  # .env* is in the contest .gitignore
        self.assertIsNone(guard.check_bash("git add -A", self.wt, self.c))
        (self.wt / ".gitignore").write_text("")
        self.assertIsNotNone(guard.check_bash("git add -A", self.wt, self.c))
        self.assertIsNotNone(guard.check_bash("git add .env", self.wt, self.c))
        (self.wt / ".env").unlink()
        (self.wt / "cfg.py").write_text('API_KEY = "sk-abcdefghijklmnopqrstuvwxyz123456"\n')
        self.assertIsNotNone(guard.check_bash("git add cfg.py", self.wt, self.c))
        self.assertIsNotNone(guard.check_bash("git commit -am wip", self.wt, self.c) or
                             guard.check_bash("git add . && git commit -m x", self.wt, self.c))
        (self.wt / "cfg.py").write_text("X = 1\n")
        self.assertIsNone(guard.check_bash("git add cfg.py && git commit -m ok", self.wt, self.c))

    def test_git_push_rules(self):
        self.assertIsNone(guard.check_bash("git push origin task/T001-x", self.wt, self.c))
        self.assertIsNotNone(guard.check_bash("git push public main", self.c, self.c))
        self.assertIsNotNone(guard.check_bash("git push --force origin main", self.c, self.c))
        self.assertIsNotNone(guard.check_bash("git push origin +main", self.c, self.c))
        self.assertIsNone(guard.check_bash("git push -f origin task/T001-x", self.wt, self.c))
        (self.c / ".house/spec.yaml").write_text("guard:\n  push_allow: [origin, team]\n")
        self.assertIsNone(guard.check_bash("git push team task/T001-x", self.wt, self.c))

    def test_diff_violations(self):
        self.assertEqual(guard.diff_violations(self.c, "T001", self.wt), [])
        (self.wt / "test_a.py").write_text("assert False\n")
        git(self.wt, "commit", "-qam", "loosen")
        (self.wt / ".env.local").write_text("X=1\n")
        found = guard.diff_violations(self.c, "T001", self.wt)
        self.assertTrue(any("test_a.py" in v for v in found), found)

    def test_frozen_tampering(self):
        import json
        cf = self.c / ".house/checks/T001.json"
        data = json.loads(cf.read_text())
        data["frozen"]["criteria"][0]["run"] = "exit 0"
        cf.write_text(json.dumps(data))
        self.assertTrue(guard.frozen_tampering(self.c))


    def test_identifiers_are_not_secrets(self):
        (self.wt / "m.py").write_text("def task_dependencies_are_satisfied(): pass\n"
                                      "attention_mask_for_padding_tokens_long = 1\n")
        self.assertIsNone(guard.check_bash("git add m.py", self.wt, self.c))
        git(self.wt, "add", "m.py")
        git(self.wt, "commit", "-qm", "code")
        self.assertEqual(guard.diff_violations(self.c, "T001", self.wt), [])

    def test_multiline_commands_checked(self):
        self.assertIsNotNone(guard.check_bash("echo hi\nrm test_a.py", self.wt, self.c))
        self.assertIsNotNone(guard.check_bash("cd .\ngit push --force origin main", self.c, self.c))

    def test_git_dash_c_checked(self):
        (self.wt / ".gitignore").write_text("")
        (self.wt / ".env").write_text("K=1\n")
        self.assertIsNotNone(guard.check_bash(f"git -C {self.wt} add -A", self.c, self.c))
        self.assertIsNotNone(guard.check_bash("git -C . push --force origin main", self.c, self.c))
        self.assertIsNotNone(guard.check_bash("git -c core.x=y push -fu origin main", self.c, self.c))

    def test_restoring_protected_file_allowed(self):
        for cmd in ("git checkout test_a.py", "git checkout -- test_a.py", "git checkout HEAD -- test_a.py",
                    "git restore test_a.py", "git checkout -b feature", "git checkout main"):
            self.assertIsNone(guard.check_bash(cmd, self.wt, self.c), cmd)
        self.assertIsNotNone(guard.check_bash("git checkout other -- test_a.py", self.wt, self.c))
        self.assertIsNotNone(guard.check_bash("git restore --source=other test_a.py", self.wt, self.c))

    def test_bare_push_uses_upstream_remote(self):
        bare = Path(self._tmp.name) / "public.git"
        git(Path(self._tmp.name), "init", "-q", "--bare", str(bare))
        git(self.wt, "remote", "add", "public", str(bare))
        git(self.wt, "push", "-q", "-u", "public", "task/T001-x")
        self.assertIsNotNone(guard.check_bash("git push", self.wt, self.c))
        self.assertIsNotNone(guard.check_bash("git push --repo=public", self.wt, self.c))

    def test_one_harmless_segment_does_not_end_the_check(self):
        self.assertIsNotNone(guard.check_bash("echo > .house/checksum; echo x > test_a.py", self.c, self.c))
        self.assertIsNone(guard.check_bash("echo > .house/checksum", self.c, self.c))

    def test_removing_a_committed_env_is_allowed(self):
        (self.wt / ".gitignore").write_text("")
        (self.wt / ".env").write_text("K=1\n")
        git(self.wt, "add", "-f", ".env", ".gitignore")
        git(self.wt, "commit", "-qm", "oops")
        (self.wt / ".env").unlink()
        self.assertIsNone(guard.check_bash("git add -A", self.wt, self.c))
        self.assertIsNone(guard.check_bash("git commit -am 'remove secret'", self.wt, self.c))

    def test_heredoc_body_is_not_a_command(self):
        cmd = "cat <<'EOF' > notes.md\nrun prog > test_a.py\nEOF"
        self.assertIsNone(guard.check_bash(cmd, self.wt, self.c))

    def test_large_and_binary_files_are_cheap(self):
        (self.wt / "data.bin").write_bytes(b"\0" * 2_000_000 + b"sk-abcdefghijklmnopqrstuvwxyz0123")
        self.assertIsNone(guard.check_bash("git add data.bin", self.wt, self.c))

    def test_rename_in_status(self):
        git(self.wt, "mv", "test_a.py", "test_b.py")
        paths = [p for _, p in guard._status(self.wt)]
        self.assertIn("test_b.py", paths)
        self.assertTrue(all(not p.startswith("t_a") for p in paths), paths)


if __name__ == "__main__":
    unittest.main()


class SignalRulesTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name) / "home"
        self.cwd = Path(self._tmp.name) / "repo"
        self.cwd.mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def check(self, tool, **args):
        return guard.check_signal(tool, args, self.cwd, home=self.home)

    def test_bare_signal_cli_blocked(self):
        for cmd in ("signal-cli -a +1 receive", "~/.local/bin/signal-cli -a +1 send -m x",
                    "foo && signal-cli -a +1 -o json receive --timeout 1", "timeout 5 signal-cli -a +1 receive",
                    "cd /tmp\nsignal-cli -a +48 send --note-to-self -m hej"):
            reason = self.check("Bash", command=cmd)
            self.assertIsNotNone(reason, cmd)
            self.assertIn("house signal", reason)

    def test_any_signal_cli_run_blocked(self):
        for cmd in ("signal-cli -a +1 -o json listGroups", "signal-cli -a +1 listContacts", "signal-cli --version"):
            self.assertIsNotNone(self.check("Bash", command=cmd), cmd)

    def test_allow_deny_from_agent_blocked(self):
        for cmd in ("bin/house signal allow techarena_claude --alias t", "house signal deny techarena",
                    'echo t | script -qc "house signal allow techarena --alias x" /dev/null',
                    "unbuffer ~/house/bin/house signal allow x", "house signal agent on",
                    "echo t | script -qc 'house signal agent members techarena' /dev/null"):
            reason = self.check("Bash", command=cmd)
            self.assertIsNotNone(reason, cmd)
            self.assertIn("human", reason)

    def test_house_env_overrides_blocked(self):
        for cmd in ("HOUSE_SIGNAL_CONFIG=/tmp/a.toml house signal pull", "export HOUSE_DATA_DIR=/tmp/x",
                    "env HOUSE_NOTIFY_CONFIG=/tmp/n house inbox", "HOUSE_SIGNAL_ATTACHMENTS=/ house signal pull"):
            self.assertIsNotNone(self.check("Bash", command=cmd), cmd)

    def test_writes_to_house_data_blocked(self):
        data = self.home / ".local/share/house"
        self.assertIsNotNone(self.check("Bash", command="echo x >> ~/.local/share/house/groups.json"))
        self.assertIsNotNone(self.check("Write", file_path=str(data / "signal/messages.jsonl"), content="x"))
        self.assertIsNotNone(self.check("Edit", file_path=str(data / "groups.json")))
        self.assertIsNone(self.check("Read", file_path=str(data / "signal/messages.jsonl")))

    def test_read_signal_cli_dir_blocked(self):
        d = self.home / ".local/share/signal-cli/attachments/x.jpg"
        self.assertIsNotNone(self.check("Read", file_path=str(d)))
        self.assertIsNotNone(self.check("Grep", pattern="x", path=str(d.parent)))
        self.assertIsNotNone(self.check("Glob", pattern="*", path=str(self.home / ".local/share/signal-cli")))
        self.assertIsNotNone(self.check("Bash", command="cat ~/.local/share/signal-cli/attachments/x"))
        self.assertIsNotNone(self.check("Bash", command="ls $HOME/.local/share/signal-cli/data"))
        self.assertIsNotNone(self.check("Edit", file_path=str(d)))

    def test_store_copies_allowed(self):
        self.assertIsNone(self.check("Read", file_path=str(self.home / ".local/share/house/signal/files/t/a.jpg")))

    def test_write_config_blocked(self):
        cfg = self.home / ".config/house/signal.toml"
        self.assertIsNotNone(self.check("Write", file_path=str(cfg), content="x"))
        self.assertIsNotNone(self.check("Edit", file_path=str(cfg)))
        self.assertIsNotNone(self.check("Bash", command="echo x > ~/.config/house/signal.toml"))
        self.assertIsNotNone(self.check("Bash", command="tee -a $HOME/.config/house/notify.env < x"))
        self.assertIsNone(self.check("Read", file_path=str(cfg)))
        self.assertIsNone(self.check("Bash", command="cat ~/.config/house/signal.toml"))

    def test_house_signal_commands_allowed(self):
        for cmd in ("bin/house signal read techarena", "house signal send techarena 'hej' --reply-to 5",
                    "house signal pull", "grep -r signal-cli houselib/"):
            self.assertIsNone(self.check("Bash", command=cmd), cmd)


class JobModeTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name) / "home"
        self.wt = self.home / "house/.worktrees/signal-abc"
        self.wt.mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def check(self, tool, **args):
        return guard.check_job(tool, args, self.wt, self.wt, home=self.home)

    def test_job_mode_blocks_network_git_and_house(self):
        for cmd in ("git push origin x", "git -C . remote add x y", "git config core.hooksPath x", "git commit -m x",
                    "git fetch", "gh pr create", "curl https://x", "wget x", "ssh host", "scp a b:", "nc -l 1",
                    "rsync a b", "house signal send self x", "bin/house exp run x", "python3 x.py && git push",
                    "timeout 5 curl x", "sudo ls"):
            self.assertIsNotNone(self.check("Bash", command=cmd), cmd)

    def test_job_mode_blocks_house_out_too(self):
        for cmd in ("house out -- ls", "bin/house out show E-001 --grep x", "$HOUSE_ROOT/bin/house out prune"):
            self.assertIsNotNone(self.check("Bash", command=cmd), cmd)

    def test_job_mode_allows_work_in_worktree(self):
        for cmd in ("python3 -m pytest -q", "git status", "git diff", "git log --oneline -3", "ls -la",
                    "echo x > wynik.txt", "pip install numpy"):
            self.assertIsNone(self.check("Bash", command=cmd), cmd)
        self.assertIsNone(self.check("Write", file_path=str(self.wt / "a.py"), content="x"))
        self.assertIsNone(self.check("Read", file_path=str(self.wt / ".house-job/request.md")))
        self.assertIsNone(self.check("Read", file_path="/usr/lib/python3/os.py"))

    def test_job_mode_blocks_outside_worktree(self):
        self.assertIsNotNone(self.check("Write", file_path=str(self.home / "house/houselib/guard.py"), content="x"))
        self.assertIsNotNone(self.check("Edit", file_path="../../other/x.py"))
        self.assertIsNotNone(self.check("Read", file_path=str(self.home / ".config/house/signal.toml")))
        self.assertIsNotNone(self.check("Grep", pattern="x", path=str(self.home)))
        self.assertIsNotNone(self.check("Bash", command="echo x > ../../evil.sh"))
        self.assertIsNotNone(self.check("Bash", command="cat ~/.ssh/id_ed25519"))


class JobModeReviewTest(JobModeTest):
    def test_reads_outside_worktree_refused_except_system(self):
        for path in ("/tmp/claude-1001/x/tasks/a.output", "/proc/1234/environ", "/run/user/1001/x",
                     str(self.home / ".claude/x")):
            self.assertIsNotNone(self.check("Read", file_path=path), path)
        self.assertIsNotNone(self.check("Grep", pattern="x", path="/tmp"))
        self.assertIsNotNone(self.check("Glob", pattern="/tmp/**/*.output"))
        for path in ("/usr/lib/python3/os.py", "/etc/ssl/certs/ca.pem", str(self.wt / "a.py")):
            self.assertIsNone(self.check("Read", file_path=path), path)
        self.assertIsNone(self.check("Glob", pattern="**/*.py"))

    def test_git_and_claude_files_in_worktree_refused(self):
        for rel in (".git", ".git/config", ".gitattributes", ".claude/settings.local.json", "sub/.git"):
            self.assertIsNotNone(self.check("Write", file_path=str(self.wt / rel), content="x"), rel)
        self.assertIsNotNone(self.check("Bash", command="printf 'gitdir: /x' > .git"))
        self.assertIsNotNone(self.check("Bash", command="ln -s ~/.local/share/house .house-job/out"))
        self.assertIsNotNone(self.check("Bash", command="python3 -c \"import os; os.symlink('/x', 'y')\""))

    def test_allow_agent_bypass_forms_refused(self):
        for cmd in ("house signal agent --kill on", "h=house; $h signal agent on", "house signal agent 'on'",
                    "house signal 'allow' x", "bin/house signal agent members techarena",
                    "house signal agent token"):
            self.assertIsNotNone(guard.check_signal("Bash", {"command": cmd}, self.wt, home=self.home), cmd)
        self.assertIsNone(guard.check_signal("Bash", {"command": "house signal agent status"}, self.wt,
                                             home=self.home))


class RedactTest(unittest.TestCase):
    # One fake sample per SECRET_RES pattern, each embedded in a sentence.
    CASES = [
        "before sk-fakeFAKE0123456789abcd after",
        "before AIza" + "FAKE0123456789abcdefghijklmnopqrxyz after",  # split: secret scanning flags a whole one
        "before hf_FAKE0123456789abcdefghijklmnopqr after",
        "before github_pat_FAKE0123456789abcdefghijklmnop after",
        "before ghs_FAKE0123456789abcdefghijklmnopqr after",
        "before xoxb-1234567890-FAKEtoken after",
        "before PASSWORD=\"FAKEpassFAKEpass12\" after",
        "before xai-FAKE0123456789abcdefgh after",
        "before ghp_FAKE0123456789abcdefghijklmnop after",
        "before AKIAFAKEFAKEFAKE1234 after",
        "before -----BEGIN RSA PRIVATE KEY-----\nFAKEbodyFAKEbody\n-----END RSA PRIVATE KEY----- after",
    ]

    def test_redact_masks_each_secret_kind(self):
        self.assertEqual(len(self.CASES), len(guard.SECRET_RES))
        for sentence in self.CASES:
            with self.subTest(sentence=sentence):
                out = guard.redact(sentence)
                self.assertNotEqual(out, sentence)
                self.assertIn("[REDACTED]", out)
                self.assertNotIn("FAKE", out)
                self.assertTrue(out.startswith("before ") and out.endswith(" after"), out)

    def test_redact_keeps_plain_text(self):
        self.assertEqual(guard.redact("pytest tests/x.py -k y"), "pytest tests/x.py -k y")

    def test_redact_two_secrets_in_one_command(self):
        cmd = "export A=sk-fakeFAKE0123456789abcd B=ghp_FAKE0123456789abcdefghijklmnop && pytest"
        out = guard.redact(cmd)
        self.assertNotIn("sk-fakeFAKE", out)
        self.assertNotIn("ghp_FAKE", out)
        self.assertEqual(out.count("[REDACTED]"), 2)
        self.assertTrue(out.endswith("&& pytest"), out)


class RedactExtraShapesTest(unittest.TestCase):
    """Redact-only shapes (not in `export.SECRET_RES`, so `house export` blocking is unchanged)."""

    def test_pem_block_body_is_gone(self):
        text = "a\n-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXk\nFAKEBODY\n-----END OPENSSH PRIVATE KEY-----\nz"
        out = guard.redact(text)
        self.assertNotIn("FAKEBODY", out)
        self.assertNotIn("b3BlbnNz", out)
        self.assertEqual(out, "a\n[REDACTED]\nz")

    def test_pem_block_without_end_runs_to_end_of_text(self):
        out = guard.redact("x -----BEGIN PRIVATE KEY-----\nFAKEBODY\nmore")
        self.assertEqual(out, "x [REDACTED]")

    def test_env_assignment_keeps_name_redacts_value(self):
        cases = {
            "GITHUB_TOKEN=abc123 gh pr list": "GITHUB_TOKEN=[REDACTED] gh pr list",
            "export API_KEY=hunter2 && run": "export API_KEY=[REDACTED] && run",
            "DB_PASSWORD=p4ss DB_PASSWD=x2 ls": "DB_PASSWORD=[REDACTED] DB_PASSWD=[REDACTED] ls",
            "AWS_SECRET_ACCESS_KEY=wJalrXUtn/K7MDENG end": "AWS_SECRET_ACCESS_KEY=[REDACTED] end",
            "MY_PAT=ghx1 go": "MY_PAT=[REDACTED] go",
            'SECRET="two words here" go': 'SECRET=[REDACTED] go',
            "GITHUB_TOKEN=sk-fakeFAKE0123456789abcd x": "GITHUB_TOKEN=[REDACTED] x",
        }
        for text, want in cases.items():
            with self.subTest(text=text):
                self.assertEqual(guard.redact(text), want)

    def test_bearer_token(self):
        out = guard.redact("curl -H 'Authorization: Bearer abcdef0123456789XYZ rest' x")
        self.assertEqual(out, "curl -H 'Authorization: Bearer [REDACTED] rest' x")
        self.assertEqual(guard.redact("BEARER abcdef0123456789abcdef"), "Bearer [REDACTED]")
        self.assertEqual(guard.redact("the bearer of bad news"), "the bearer of bad news")

    def test_url_credentials_only_password(self):
        self.assertEqual(guard.redact("git clone https://bob:s3cr3t@host.example/r.git"),
                         "git clone https://bob:[REDACTED]@host.example/r.git")
        self.assertEqual(guard.redact("postgres://u:p@w@db:5432/x"), "postgres://u:[REDACTED]@db:5432/x")

    def test_env_name_matches_by_underscore_segment(self):
        cases = {
            "OPENAI_API_KEY=abc ls": "OPENAI_API_KEY=[REDACTED] ls",
            "APIKEY=abc ls": "APIKEY=[REDACTED] ls",
            "GH_PAT=abc ls": "GH_PAT=[REDACTED] ls",
            "KEY_COUNT=5 ls": "KEY_COUNT=[REDACTED] ls",  # a KEY segment: accepted false positive
            "MY_PASS=abc ls": "MY_PASS=[REDACTED] ls",
        }
        for text, want in cases.items():
            with self.subTest(text=text):
                self.assertEqual(guard.redact(text), want)

    def test_env_name_substrings_are_not_secrets(self):
        for cmd in ("MONKEY=1 make", "KEYBOARD=us make", "TOKENIZERS_PARALLELISM=false python t.py",
                    "export PATH=$PATH:/x", "PASSIVE=1 run", "A" * 70 + "_KEY=x"):
            with self.subTest(cmd=cmd):
                self.assertEqual(guard.redact(cmd), cmd)

    def test_env_secret_nested_after_plain_assignment(self):
        self.assertEqual(guard.redact("X=Y_TOKEN=abc go"), "X=Y_TOKEN=[REDACTED] go")

    def test_env_unquoted_value_stops_at_separators(self):
        cases = {
            "AUTH_TOKEN=abc;ls": "AUTH_TOKEN=[REDACTED];ls",
            'echo "ENV=prod; KEY=1"': 'echo "ENV=prod; KEY=[REDACTED]"',
            "A_KEY=1&&ls": "A_KEY=[REDACTED]&&ls",
            "(A_KEY=1|cat)": "(A_KEY=[REDACTED]|cat)",
            "(A_KEY=1)": "(A_KEY=[REDACTED])",
            'A_KEY="unterminated rest': "A_KEY=[REDACTED]",
        }
        for text, want in cases.items():
            with self.subTest(text=text):
                self.assertEqual(guard.redact(text), want)

    def test_env_value_is_a_whole_shell_word(self):
        cases = {
            'API_KEY=""private-value curl x': "API_KEY=[REDACTED] curl x",
            "TOKEN='a'\"b\"c rest": "TOKEN=[REDACTED] rest",
            "KEY=a\\ b rest": "KEY=[REDACTED] rest",
            "KEY=$'a b'tail rest": "KEY=[REDACTED] rest",
            'KEY="a\\"b"c rest': "KEY=[REDACTED] rest",
            "AUTH_TOKEN=abc;ls": "AUTH_TOKEN=[REDACTED];ls",
        }
        for text, want in cases.items():
            with self.subTest(text=text):
                self.assertEqual(guard.redact(text), want)

    def test_env_value_with_command_substitution(self):
        cases = {
            "API_KEY=$(printf %s private-value) curl x": "API_KEY=[REDACTED] curl x",
            "TOKEN=`printf %s private-value` run": "TOKEN=[REDACTED] run",
            "API_KEY=$(cat $(ls private-file)) run": "API_KEY=[REDACTED] run",
            "API_KEY=$(printf %s 'a) private') run": "API_KEY=[REDACTED] run",
            "A_KEY=pre$(echo private)post rest": "A_KEY=[REDACTED] rest",
            "A_KEY=$((1 + 2)) rest": "A_KEY=[REDACTED] rest",
            "API_KEY=$(printf %s private-value": "API_KEY=[REDACTED]",
            "TOKEN=`printf %s private-value": "TOKEN=[REDACTED]",
            "(A_KEY=1)": "(A_KEY=[REDACTED])",
            "export PATH=$PATH:/x": "export PATH=$PATH:/x",
        }
        for text, want in cases.items():
            with self.subTest(text=text):
                self.assertEqual(guard.redact(text), want)

    def test_env_redact_is_linear_time(self):
        import time
        for text in ("KEY" * 60000 + "=x", "A=" * 60000, "A_KEY=" * 30000, "K_KEY=\"" * 30000,
                     "A_KEY=1\"" * 30000, "A_KEY=1'" * 30000, "A_KEY=a\\\"" * 30000, "A_KEY=''\"\"" * 30000,
                     "A_KEY=$(" * 30000, "A_KEY=`" * 30000, "A_KEY=$((" * 20000, "A_KEY=$(x) " * 30000,
                     "A_KEY=$(" + "(" * 60000, "A_KEY=$(" + "'" * 60000):
            with self.subTest(text=text[:12]):
                t0 = time.perf_counter()
                guard.redact(text)
                self.assertLess(time.perf_counter() - t0, 1.0)

    def test_api_key_header(self):
        cases = {
            "curl -H 'X-Api-Key: abc123' u": "curl -H 'X-Api-Key: [REDACTED]' u",
            'curl -H "X-API-KEY:abc123" u': 'curl -H "X-API-KEY:[REDACTED]" u',
            "x-api-key: abc123": "x-api-key: [REDACTED]",
        }
        for text, want in cases.items():
            with self.subTest(text=text):
                self.assertEqual(guard.redact(text), want)

    def test_url_query_secrets(self):
        cases = {
            "curl https://h/a?token=abc123&x=1": "curl https://h/a?token=[REDACTED]&x=1",
            "https://h/a?x=1&access_token=abc#frag": "https://h/a?x=1&access_token=[REDACTED]#frag",
            "'https://h/a?api_key=abc'": "'https://h/a?api_key=[REDACTED]'",
            "https://h/a?APIKEY=abc d": "https://h/a?APIKEY=[REDACTED] d",
            "https://h/a?key=abc&password=p&secret=s": "https://h/a?key=[REDACTED]&password=[REDACTED]&secret=[REDACTED]",
            "https://h/t?client_secret=c1&refresh_token=r1&x=1": "https://h/t?client_secret=[REDACTED]&refresh_token=[REDACTED]&x=1",
            "https://b/x?sig=abc&signature=def&sign=g": "https://b/x?sig=[REDACTED]&signature=[REDACTED]&sign=g",
        }
        for text, want in cases.items():
            with self.subTest(text=text):
                self.assertEqual(guard.redact(text), want)

    def test_basic_authorization(self):
        self.assertEqual(guard.redact("curl -H 'Authorization: Basic dXNlcjpwYXNzd29yZA==' u"),
                         "curl -H 'Authorization: Basic [REDACTED]' u")
        self.assertEqual(guard.redact("authorization: basic dXNlcjpwYXNzd29yZA=="), "authorization: Basic [REDACTED]")
        self.assertEqual(guard.redact("a basic setup of the thing"), "a basic setup of the thing")

    def test_url_credentials_stop_at_query(self):
        self.assertEqual(guard.redact("http://localhost:3000?email=a@b.c"), "http://localhost:3000?email=a@b.c")
        self.assertEqual(guard.redact("http://localhost:3000#a@b"), "http://localhost:3000#a@b")

    def test_ordinary_commands_unchanged(self):
        for cmd in ("pytest -k TOKEN_LIMIT tests/x.py", "PATH=/usr/bin ls", "https://example.com/a",
                    "ssh://git@host:22/repo.git", "http://host:8080/a@b", "export PATHS=/x TOKEN_LIMIT",
                    "KEY_COUNT 5", "python -m unittest -k test_bearer", "curl https://api.example.com/v1/keys",
                    "http://localhost:3000?email=a@b.c", "curl 'https://h/a?page=2&sort=key'"):
            with self.subTest(cmd=cmd):
                self.assertEqual(guard.redact(cmd), cmd)

    def test_export_secret_res_unchanged(self):
        self.assertEqual(len(guard.SECRET_RES), 11)
        self.assertFalse(any(r.search("GITHUB_TOKEN=abc123 Bearer abcdef0123456789XYZ") for r in guard.SECRET_RES))
