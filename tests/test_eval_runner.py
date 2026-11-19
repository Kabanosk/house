"""`houselib.evals.runner`: Claude-in-container and Codex runners, infra classification (fakes only, nothing starts)."""
import json
import os
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from houselib import HOUSE_ROOT
from houselib import agentdocker as dk
from houselib.evals import runner
from houselib.evals.cases import Case
from tests import helpers
from tests.test_agentdocker import FakeDocker

ASSISTANT = json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "hi"}]}})
CODEX_DONE = json.dumps({"type": "item.completed", "item": {"id": "i1", "type": "agent_message", "text": "hi"}})


def make_case(**kw) -> Case:
    base = dict(name="c", path=Path("c.json"), agent="coder", skill=None, model="haiku", fixture={"name": "x"},
                prompt="do the thing", max_turns=7, timeout_seconds=30, tool="claude")
    base.update(kw)
    return Case(**base)


class Sink:
    """A child's stdin pipe that keeps what was written (the stream-json user lines of a Claude run)."""

    def __init__(self):
        self.data, self.closed = b"", False

    def write(self, data):
        if self.closed:
            raise ValueError("write to closed file")
        self.data += data
        return len(data)

    def flush(self):
        pass

    def close(self):
        self.closed = True

    def lines(self) -> list[dict]:
        return [json.loads(line) for line in self.data.decode().splitlines()]


class FakeProc:
    """Stands in for `subprocess.Popen`: optionally writes to its stdout/stderr, exits with `code` or times out."""

    def __init__(self, argv, kw, spec):
        self.argv, self.kw, self.spec, self.pid, self.waits = argv, kw, spec, 424242, []
        self.returncode = None
        self.stdin = Sink() if kw.get("stdin") == subprocess.PIPE else None
        self.pipes = {}  # stream -> write end, when the caller asked for `subprocess.PIPE`
        for stream in ("stdout", "stderr"):
            if kw.get(stream) == subprocess.PIPE:
                r, w = os.pipe()
                setattr(self, stream, open(r, "rb"))
                self.pipes[stream] = open(w, "wb")

    def finish(self):
        spec = self.spec
        for stream in ("stdout", "stderr"):
            if stream in self.pipes:
                if spec.get(stream):
                    self.pipes[stream].write(spec[stream].encode())
                self.pipes.pop(stream).close()
            elif spec.get(stream):
                self.kw[stream].write(spec[stream])
                self.kw[stream].flush()

    def wait(self, timeout=None):
        self.waits.append(timeout)
        if self.spec.get("interrupt") and timeout is not None:
            self.spec["interrupt"] = False  # only the first wait is interrupted
            raise KeyboardInterrupt
        if self.spec.get("hang") and timeout is not None:
            self.spec["hang"] = False
            raise subprocess.TimeoutExpired(self.argv, timeout)
        self.finish()
        self.returncode = self.spec.get("code", 0)
        return self.returncode


class Popens:
    """Factory that records every started process."""

    def __init__(self, **spec):
        self.spec, self.procs = spec, []

    def __call__(self, argv, **kw):
        proc = FakeProc(argv, kw, self.spec)
        self.procs.append(proc)
        return proc


class RunnerCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name).resolve()
        self.env = mock.patch.dict(os.environ, {"HOUSE_DATA_DIR": str(self.tmp / "realdata"),
                                                "HOUSE_SIGNAL_CONFIG": str(self.tmp / "cfg/signal.toml")})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(self._tmp.cleanup)
        self.tmpdir = self.tmp / "house-eval-x"
        self.tmpdir.mkdir()
        self.root = SimpleNamespace(tmp=self.tmpdir, root=helpers.make_root(self.tmpdir), name="calc")
        self.root.contest = self.root.root / "contests/calc"
        (self.root.contest / ".house").mkdir(parents=True)
        (self.root.contest / ".git/info").mkdir(parents=True)
        self.root.env = lambda: {"HOUSE_EVAL": "1", "HOUSE_DATA_DIR": str(self.tmpdir / "data"),
                                 "HOUSE_EVAL_LOG": str(self.tmpdir / "eval.log"),
                                 "HOUSE_NOTIFY_CONFIG": str(self.tmpdir / "none.env"),
                                 "HOUSE_SIGNAL_CONFIG": str(self.tmpdir / "none.toml"),
                                 "HOUSE_SIGNAL_ATTACHMENTS": str(self.tmpdir / "none"),
                                 "HOUSE_DIR": str(self.root.contest / ".house"),
                                 "PATH": f"{self.root.root / 'bin'}:/usr/bin", "HOME": str(Path.home()),
                                 "SECRET_HOST_VAR": "leak", "OPENAI_API_KEY": "sk-leak", "LANG": "en_US.UTF-8",
                                 "LC_ALL": "C", "TERM": "xterm", "GIT_CONFIG_GLOBAL": "/home/x/.gitconfig"}
        dk.save_token("sk-ant-oat01-SECRET")
        self.codex_src = self.tmp / "real-codex"  # stands in for ~/.codex: never the real one
        (self.codex_src / "sessions").mkdir(parents=True)
        (self.codex_src / "auth.json").write_text('{"token": "CODEX-SECRET"}')
        (self.codex_src / "config.toml").write_text("model = 'x'\n")
        (self.codex_src / "sessions/old.jsonl").write_text("history\n")
        codex_env = mock.patch.dict(os.environ, {"CODEX_HOME": str(self.codex_src)})
        codex_env.start()
        self.addCleanup(codex_env.stop)
        self.outbase = self.tmp / "state/house/eval-out"
        patch = mock.patch.object(runner, "out_base", return_value=self.outbase)
        patch.start()
        self.addCleanup(patch.stop)

    def install(self, case):
        """What `house adapt` leaves in the contest for this case."""
        path = (self.root.contest / ".claude/agents" / f"{case.agent}.md" if case.agent
                else self.root.contest / ".claude/skills" / case.skill / "SKILL.md")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("profile\n")

    def go(self, case, popens=None, docker=None, installed=True, **kw):
        popens = popens if popens is not None else Popens()
        docker = docker if docker is not None else FakeDocker()
        if installed:
            self.install(case)
        with mock.patch("houselib.evals.runner.os.killpg") as killpg:
            result = runner.run_case(case, self.root, run=docker, popen=popens, clock=iter(range(0, 1000, 5)).__next__,
                                     **kw)
        if result.out_dir:  # the caller owns the out-dir
            self.addCleanup(shutil.rmtree, result.out_dir, ignore_errors=True)
        return result, popens, docker, killpg


def option(argv, flag):
    return argv[argv.index(flag) + 1]


class ClaudeArgvTest(RunnerCase):
    def test_agent_case(self):
        popens = Popens(stdout=ASSISTANT + "\n")
        result, popens, _, _ = self.go(make_case(), popens)
        self.assertIsNone(result.infra)
        self.assertEqual(result.exit, 0)
        self.assertFalse(result.timed_out)
        self.assertEqual(result.duration, 5)
        argv = popens.procs[0].argv
        self.assertEqual(argv[:2], ["docker", "run"])
        image = argv.index(dk.image_name())
        self.assertEqual(argv[image + 1:image + 5], ["timeout", "-k", "10", "60"])  # timeout_seconds 30 + 30
        claude = argv[image + 5:]
        self.assertEqual(claude[:2], ["claude", "-p"])
        self.assertEqual(option(claude, "--agent"), "coder")
        self.assertEqual(option(claude, "--model"), "haiku")
        self.assertEqual(option(claude, "--max-turns"), "7")
        self.assertEqual(option(claude, "--session-id"), result.session_id)
        self.assertEqual(option(claude, "--output-format"), "stream-json")
        self.assertIn("--verbose", claude)
        self.assertEqual(option(claude, "--settings"), f"{self.root.contest}/.claude/settings.local.json")
        self.assertEqual(option(claude, "--permission-mode"), "auto")
        self.assertEqual(option(claude, "--permission-prompts"), "none")
        self.assertEqual(option(claude, "--disallowedTools"), "Bash(git push:*)")
        self.assertEqual(claude[-4:], ["--input-format", "stream-json", "--disallowedTools", "Bash(git push:*)"])
        self.assertNotIn("do the thing", claude)  # the prompt goes through stdin (C2 of K5)
        self.assertEqual(popens.procs[0].stdin.lines(),
                         [{"type": "user", "message": {"role": "user", "content": "do the thing"}}])
        self.assertEqual(option(argv, "--name"), f"house-eval-{result.session_id}")
        self.assertEqual(option(argv, "-w"), str(self.root.contest))

    def test_skill_case(self):
        case = make_case(agent=None, skill="merge", model="sonnet")
        _, popens, _, _ = self.go(case)
        claude = popens.procs[0].argv[popens.procs[0].argv.index(dk.image_name()) + 5:]
        self.assertNotIn("--agent", claude)
        self.assertEqual(option(claude, "--model"), "sonnet")
        self.assertEqual(popens.procs[0].stdin.lines()[0]["message"]["content"], "/merge do the thing")

    def test_isolation(self):
        _, popens, _, _ = self.go(make_case())
        argv = popens.procs[0].argv
        joined = " ".join(argv)
        mounts = [argv[i + 1] for i, a in enumerate(argv) if a == "-v"]
        t, c = self.tmpdir, self.root.contest
        self.assertEqual(mounts, [f"{t}:{t}", f"{t}/house:{t}/house:ro", f"{c}:{c}", f"{t}/origin.git:{t}/origin.git:ro"])
        for real in (str(HOUSE_ROOT), str(Path.home())):
            self.assertNotIn(real + ":", joined)
            self.assertNotIn(f"{real}/", joined.replace(str(self.tmp), ""))
        self.assertNotIn("--dangerously-skip-permissions", argv)
        self.assertNotIn("docker.sock", joined)
        for flag in ("--rm", "--init", "--read-only", "--cap-drop", "--pids-limit", "--memory"):
            self.assertIn(flag, argv)
        self.assertEqual(option(argv, "--network"), "house-agent-int")
        self.assertEqual(option(argv, "--security-opt"), "no-new-privileges")
        self.assertEqual(option(argv, "--user"), f"{os.getuid()}:{os.getgid()}")
        self.assertEqual(popens.procs[0].kw["stdin"], subprocess.PIPE)  # only the stream-json turns
        self.assertTrue(popens.procs[0].kw["start_new_session"])

    def test_env_file_has_token_never_in_argv(self):
        seen = {}

        def popen(argv, **kw):
            path = Path(option(argv, "--env-file"))
            seen["mode"] = oct(path.stat().st_mode & 0o777)
            seen["text"] = path.read_text()
            seen["argv"] = argv
            seen["parent"] = path.parent
            return FakeProc(argv, kw, {})

        self.go(make_case(), popen)
        self.assertEqual(seen["mode"], "0o600")
        self.assertNotEqual(seen["parent"], self.tmpdir)
        self.assertFalse(seen["parent"].is_relative_to(self.tmpdir))
        lines = dict(line.split("=", 1) for line in seen["text"].splitlines())
        self.assertEqual(lines["CLAUDE_CODE_OAUTH_TOKEN"], "sk-ant-oat01-SECRET")
        self.assertEqual(lines["HOUSE_EVAL"], "1")
        self.assertEqual(lines["HOUSE_DATA_DIR"], str(self.tmpdir / "data"))
        self.assertEqual(lines["HOUSE_DIR"], str(self.root.contest / ".house"))
        self.assertEqual(lines["HOUSE_EVAL_LOG"], str(self.tmpdir / "eval.log"))
        self.assertEqual(lines["PATH"], f"{self.root.root / 'bin'}:{dk.IMAGE_PATH}")
        self.assertEqual(lines["HTTPS_PROXY"], f"http://{dk.PROXY}:{dk.PROXY_PORT}")
        self.assertEqual(lines["HOME"], "/home/agent")
        self.assertNotIn("SECRET_HOST_VAR", lines)
        self.assertNotIn("SECRET", " ".join(seen["argv"]))
        self.assertFalse((seen["parent"] / "container.env").exists(), "the token file is removed after the run")
        self.assertFalse((self.tmpdir / "container.env").exists())

    def test_streams_go_to_files_in_out_dir(self):
        popens = Popens(stdout=ASSISTANT + "\n", stderr="warn\n")
        result, popens, _, _ = self.go(make_case(), popens)
        self.assertEqual(result.log, result.out_dir / "run.jsonl")
        self.assertEqual(result.log.read_text(), ASSISTANT + "\n")
        self.assertEqual((result.out_dir / "run.err").read_text(), "warn\n")

    def test_out_dir_is_outside_the_mount_and_the_workspace(self):
        popens = Popens(stdout=ASSISTANT + "\n")
        result, popens, _, _ = self.go(make_case(), popens)
        out = result.out_dir
        self.assertTrue(out.is_dir())
        for inside in (self.tmpdir, self.root.contest):
            self.assertFalse(result.log.is_relative_to(inside))
            self.assertFalse(out.is_relative_to(inside))
            self.assertFalse(inside.is_relative_to(out))
        self.assertNotIn(str(out), " ".join(popens.procs[0].argv).replace(option(popens.procs[0].argv, "--env-file"), ""))
        self.assertEqual(result.log.parent, out)
        self.assertFalse((self.tmpdir / "run.jsonl").exists())
        self.assertFalse((self.tmpdir / "run.err").exists())

    def test_out_dir_is_a_0700_child_of_out_base(self):
        result, _, _, _ = self.go(make_case())
        self.assertEqual(result.out_dir.parent, self.outbase)
        self.assertEqual(oct(result.out_dir.stat().st_mode & 0o777), "0o700")

    def test_networks_and_proxy_prepared_first(self):
        docker = FakeDocker(network=False, proxy_running=False)
        self.go(make_case(), docker=docker)
        flat = [" ".join(c) for c in docker.calls]
        self.assertTrue(any(c.startswith("docker network create --internal house-agent-int") for c in flat))
        self.assertTrue(any(c.startswith("docker run -d --name house-agent-proxy") for c in flat))


class OutBaseTest(unittest.TestCase):
    def test_default_is_the_state_dir_never_tmp(self):
        with mock.patch.dict(os.environ, {"HOME": "/home/someone"}):
            os.environ.pop("XDG_STATE_HOME", None)
            base = runner.out_base()
        self.assertEqual(base, Path("/home/someone/.local/state/house/eval-out"))
        self.assertFalse(base.is_relative_to(tempfile.gettempdir()))

    def test_xdg_state_home(self):
        with mock.patch.dict(os.environ, {"XDG_STATE_HOME": "/var/state"}):
            self.assertEqual(runner.out_base(), Path("/var/state/house/eval-out"))

    def test_make_out_dir_creates_private_dirs(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"XDG_STATE_HOME": d}):
            out = runner.make_out_dir()
            self.assertEqual(out.parent, Path(d) / "house/eval-out")
            self.assertEqual(oct(out.stat().st_mode & 0o777), "0o700")
            self.assertEqual(oct(out.parent.stat().st_mode & 0o777), "0o700")


class CodexTest(RunnerCase):
    def test_argv_and_stdin(self):
        case = make_case(agent="coder-codex", tool="codex", model=None)
        popens = Popens(stdout=CODEX_DONE + "\n")
        with mock.patch("houselib.evals.runner.shutil.which", return_value="/usr/bin/codex"):
            result, popens, docker, _ = self.go(case, popens)
        proc = popens.procs[0]
        self.assertEqual(proc.argv, ["codex", "exec", "--json", "--sandbox", "workspace-write",
                                     "-c", "sandbox_workspace_write.exclude_slash_tmp=true",
                                     "-c", "sandbox_workspace_write.exclude_tmpdir_env_var=true",
                                     "-c", "sandbox_workspace_write.network_access=false",
                                     "-C", str(self.root.contest), "--", "do the thing"])
        tmpdir = Path(proc.kw["env"]["TMPDIR"])
        self.assertEqual(tmpdir, self.root.contest / ".tmp-eval")
        self.assertTrue(tmpdir.is_dir())
        self.assertIn("/.tmp-eval/", (self.root.contest / ".git/info/exclude").read_text().splitlines())
        self.assertEqual(proc.kw["stdin"], subprocess.DEVNULL)
        self.assertTrue(proc.kw["start_new_session"])
        self.assertEqual(proc.kw["env"]["HOUSE_EVAL"], "1")
        self.assertEqual(proc.kw["cwd"], self.root.contest)
        self.assertEqual(result.log.read_text(), CODEX_DONE + "\n")
        self.assertFalse(result.log.is_relative_to(self.root.contest))
        self.assertFalse(result.log.is_relative_to(self.tmpdir))
        self.assertIsNone(result.infra)
        self.assertEqual(docker.calls, [], "codex does not touch docker")

    def test_missing_codex_is_infra(self):
        case = make_case(agent="coder-codex", tool="codex", model=None)
        with mock.patch("houselib.evals.runner.shutil.which", return_value=None):
            result, popens, _, _ = self.go(case)
        self.assertEqual(popens.procs, [])
        self.assertIn("codex", result.infra)

    def test_thread_id_becomes_session_id(self):
        case = make_case(agent="coder-codex", tool="codex", model=None)
        out = json.dumps({"type": "thread.started", "thread_id": "t-123"}) + "\n" + CODEX_DONE + "\n"
        with mock.patch("houselib.evals.runner.shutil.which", return_value="/usr/bin/codex"):
            result, _, _, _ = self.go(case, Popens(stdout=out))
        self.assertEqual(result.session_id, "t-123")


class CodexIsolationTest(RunnerCase):
    """W2.1: Codex gets an allowlisted environment, a throwaway HOME and a CODEX_HOME holding only auth.json."""

    def run_codex(self, **kw):
        case = make_case(agent="coder-codex", tool="codex", model=None)
        seen = {}

        class Spy(Popens):
            def __call__(spy, argv, **pkw):
                env = pkw["env"]
                seen["env"] = dict(env)
                home = Path(env["CODEX_HOME"])
                seen["files"] = sorted(p.name for p in home.iterdir()) if home.is_dir() else None
                seen["auth"] = (home / "auth.json").read_text() if (home / "auth.json").exists() else None
                seen["mode"] = (home / "auth.json").stat().st_mode & 0o777 if (home / "auth.json").exists() else None
                return super().__call__(argv, **pkw)

        with mock.patch("houselib.evals.runner.shutil.which", return_value="/usr/bin/codex"):
            result, popens, docker, _ = self.go(case, Spy(stdout=CODEX_DONE + "\n"), **kw)
        return result, seen

    def test_environment_is_an_allowlist(self):
        result, seen = self.run_codex()
        env = seen["env"]
        self.assertIsNone(result.infra)
        for leaked in ("SECRET_HOST_VAR", "OPENAI_API_KEY", "GIT_CONFIG_GLOBAL"):
            self.assertNotIn(leaked, env)
        self.assertTrue(set(env) <= {"PATH", "LANG", "LC_ALL", "TERM", "TMPDIR", "HOME", "CODEX_HOME",
                                     *runner.CONTAINER_ENV_KEYS, "GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL",
                                     "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"}, sorted(env))
        for kept in ("PATH", "LANG", "LC_ALL", "TERM", "HOUSE_EVAL", "HOUSE_DATA_DIR", "HOUSE_EVAL_LOG", "HOUSE_DIR"):
            self.assertEqual(env[kept], self.root.env()[kept])
        self.assertEqual(env["GIT_AUTHOR_NAME"], "House Eval")

    def test_home_and_codex_home_are_throwaway_dirs_in_the_temp_root(self):
        _, seen = self.run_codex()
        home, codex_home = Path(seen["env"]["HOME"]), Path(seen["env"]["CODEX_HOME"])
        for d in (home, codex_home):
            self.assertTrue(d.is_relative_to(self.tmpdir), d)
            self.assertFalse(d.is_relative_to(self.root.contest), "outside the agent's workspace")
        self.assertNotEqual(home, Path.home())
        self.assertNotEqual(codex_home, self.codex_src)
        self.assertNotEqual(home, codex_home)

    def test_codex_home_holds_only_a_private_copy_of_auth_json(self):
        _, seen = self.run_codex()
        self.assertEqual(seen["files"], ["auth.json"], "no config.toml, sessions, history or logs")
        self.assertEqual(seen["auth"], '{"token": "CODEX-SECRET"}')
        self.assertEqual(seen["mode"], 0o600)
        self.assertEqual((self.codex_src / "auth.json").read_text(), '{"token": "CODEX-SECRET"}')

    def test_real_codex_home_defaults_to_dot_codex(self):
        with mock.patch.dict(os.environ):
            os.environ.pop("CODEX_HOME")
            self.assertEqual(runner.real_codex_home(), Path.home() / ".codex")

    def test_missing_login_is_infra_before_start(self):
        (self.codex_src / "auth.json").unlink()
        case = make_case(agent="coder-codex", tool="codex", model=None)
        with mock.patch("houselib.evals.runner.shutil.which", return_value="/usr/bin/codex"):
            result, popens, _, _ = self.go(case)
        self.assertEqual(popens.procs, [])
        self.assertIn("auth.json", result.infra)
        self.assertIn("codex", result.infra)

    def test_codex_argv_turns_the_sandbox_network_off(self):
        argv = runner.codex_argv(make_case(tool="codex"), self.root)
        i = argv.index("sandbox_workspace_write.network_access=false")
        self.assertEqual(argv[i - 1], "-c")


class InstalledTest(RunnerCase):
    def test_agent_profile_not_installed(self):
        result, popens, docker, _ = self.go(make_case(), installed=False)
        self.assertEqual(popens.procs, [])
        self.assertEqual(docker.calls, [])
        self.assertIn("profile not installed", result.infra)
        self.assertIn(".claude/agents/coder.md", result.infra)
        self.assertTrue(result.log.exists())

    def test_skill_not_installed(self):
        result, popens, _, _ = self.go(make_case(agent=None, skill="merge", model="sonnet"), installed=False)
        self.assertEqual(popens.procs, [])
        self.assertIn("profile not installed", result.infra)
        self.assertIn(".claude/skills/merge/SKILL.md", result.infra)


class PrerequisiteTest(RunnerCase):
    def check(self, docker, expect):
        result, popens, d, _ = self.go(make_case(), docker=docker)
        self.assertEqual(popens.procs, [])
        self.assertTrue(result.infra.startswith("docker: "), result.infra)
        self.assertIn(expect, result.infra)
        self.assertIsNone(result.exit)
        self.assertFalse(result.timed_out)
        self.assertFalse(any(c[:2] == ["docker", "run"] for c in d.calls))
        self.assertFalse(any(c[:3] == ["docker", "network", "create"] for c in d.calls))

    def test_docker_unusable(self):
        self.check(FakeDocker(info=False), "not usable")

    def test_image_missing(self):
        self.check(FakeDocker(image=False), dk.image_name())

    def test_token_missing(self):
        dk.token_path().unlink()
        self.check(FakeDocker(), "token")


class TimeoutTest(RunnerCase):
    def test_claude_timeout_kills_group_and_container(self):
        popens = Popens(hang=True, stdout=ASSISTANT + "\n")
        result, popens, docker, killpg = self.go(make_case(timeout_seconds=12), popens)
        self.assertTrue(result.timed_out)
        self.assertIsNone(result.exit)
        self.assertIsNone(result.infra)
        self.assertEqual(popens.procs[0].waits[0], 12)
        killpg.assert_called_once_with(424242, signal.SIGKILL)
        removes = [c for c in docker.calls if c[:3] == ["docker", "rm", "-f"] and c[3].startswith("house-eval-")]
        self.assertEqual(removes, [["docker", "rm", "-f", f"house-eval-{result.session_id}"]])  # same as _abort
        self.assertEqual([c for c in docker.calls if c[:2] == ["docker", "kill"]], [])

    def test_failed_container_removal_on_timeout_keeps_the_out_dir_and_says_so(self):
        class Docker(FakeDocker):
            def __call__(self, argv, **kw):
                if argv[:3] == ["docker", "rm", "-f"] and argv[3].startswith("house-eval-"):
                    self.calls.append(argv)
                    return subprocess.CompletedProcess(argv, 1, "", "permission denied")
                return super().__call__(argv, **kw)

        with mock.patch("sys.stderr"):
            result, _, _, _ = self.go(make_case(timeout_seconds=12), Popens(hang=True), docker=Docker())
        self.assertTrue(result.timed_out)
        self.assertTrue(result.cleanup_failed)
        self.assertIn("may still run", (result.out_dir / "run.err").read_text())

    def test_successful_timeout_cleanup_is_not_flagged(self):
        result, _, _, _ = self.go(make_case(timeout_seconds=12), Popens(hang=True))
        self.assertFalse(result.cleanup_failed)

    def test_codex_timeout_kills_group_only(self):
        case = make_case(agent="coder-codex", tool="codex", model=None)
        with mock.patch("houselib.evals.runner.shutil.which", return_value="/usr/bin/codex"):
            result, _, docker, killpg = self.go(case, Popens(hang=True))
        self.assertTrue(result.timed_out)
        killpg.assert_called_once_with(424242, signal.SIGKILL)
        self.assertEqual([c for c in docker.calls if c[:2] in (["docker", "kill"], ["docker", "rm"])], [])


class InterruptTest(RunnerCase):
    def test_keyboard_interrupt_kills_group_removes_container_and_out_dir(self):
        popens = Popens(interrupt=True)
        docker = FakeDocker()
        with mock.patch("houselib.evals.runner.os.killpg") as killpg, \
                self.assertRaises(KeyboardInterrupt):
            self.install(make_case())
            runner.run_case(make_case(), self.root, run=docker, popen=popens)
        killpg.assert_called_once_with(424242, signal.SIGKILL)
        removes = [c for c in docker.calls if c[:3] == ["docker", "rm", "-f"] and c[3].startswith("house-eval-")]
        self.assertEqual(len(removes), 1)
        self.assertEqual(removes[0][3], option(popens.procs[0].argv, "--name"))
        self.assertEqual(list(self.outbase.iterdir()), [], "out-dir removed")
        self.assertFalse(any(self.tmpdir.glob("*.env")))

    def test_codex_interrupt_kills_group_only(self):
        case = make_case(agent="coder-codex", tool="codex", model=None)
        docker = FakeDocker()
        with mock.patch("houselib.evals.runner.shutil.which", return_value="/usr/bin/codex"), \
                mock.patch("houselib.evals.runner.os.killpg") as killpg, self.assertRaises(KeyboardInterrupt):
            runner.run_case(case, self.root, run=docker, popen=Popens(interrupt=True))
        killpg.assert_called_once()
        self.assertEqual([c for c in docker.calls if c[:2] in (["docker", "rm"], ["docker", "kill"])], [])

    def test_failed_container_removal_keeps_a_note(self):
        class Docker(FakeDocker):
            def __call__(self, argv, **kw):
                if argv[:3] == ["docker", "rm", "-f"] and argv[3].startswith("house-eval-"):
                    self.calls.append(argv)
                    return subprocess.CompletedProcess(argv, 1, "", "permission denied")
                return super().__call__(argv, **kw)

        with mock.patch("houselib.evals.runner.os.killpg"), self.assertRaises(KeyboardInterrupt), \
                mock.patch("sys.stderr"):
            self.install(make_case())
            runner.run_case(make_case(), self.root, run=Docker(), popen=Popens(interrupt=True))
        (out,) = list(self.outbase.iterdir())
        self.assertIn("permission denied", (out / "run.err").read_text())
        self.assertIn("may still run", (out / "run.err").read_text())

    def test_unexpected_error_before_start_removes_out_dir(self):
        self.install(make_case())
        with mock.patch.object(runner, "write_container_env", side_effect=RuntimeError("boom")), \
                self.assertRaises(RuntimeError):
            runner.run_case(make_case(), self.root, run=FakeDocker(), popen=Popens())
        self.assertEqual(list(self.outbase.iterdir()), [])


class SetupErrorTest(unittest.TestCase):
    def test_which_infra_texts_are_setup_errors(self):
        self.assertTrue(runner.is_setup_error("profile not installed: .claude/agents/x.md is missing in the contest"))
        self.assertTrue(runner.is_setup_error("model: unknown agent profile 'x'"))
        self.assertTrue(runner.is_setup_error("model: skill case without a model"))
        for infra in (None, "", "docker: image missing", "codex: `codex` is not on PATH", "codex: no login (x)",
                      "claude exit 1 without any assistant event: usage limit"):
            self.assertFalse(runner.is_setup_error(infra), infra)

    def test_the_runner_still_reports_them_as_infra_texts(self):
        # the CLI, not the runner, decides to record them as `fail`; the texts must keep their prefixes
        self.assertTrue(runner._Infra("model: x").args[0].startswith(runner.SETUP_PREFIXES))


class TermSignalTest(unittest.TestCase):
    """W2.2: SIGTERM and SIGHUP during a run behave like Ctrl-C, so the cleanup in `run_case` runs."""

    def test_sigterm_and_sighup_raise_keyboard_interrupt_and_the_handlers_are_restored(self):
        before = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGHUP)}
        with runner.term_as_interrupt():
            for sig in (signal.SIGTERM, signal.SIGHUP):
                with self.assertRaises(KeyboardInterrupt):
                    os.kill(os.getpid(), sig)
                    time.sleep(1)  # the handler runs before the sleep returns
        self.assertEqual({s: signal.getsignal(s) for s in before}, before)

    def test_a_second_term_signal_does_not_interrupt_the_abort(self):
        sent = []

        def docker(argv, **kw):  # the signal arrives while `docker rm -f` runs
            for sig in (signal.SIGTERM, signal.SIGHUP):
                os.kill(os.getpid(), sig)
                time.sleep(0.2)
            sent.append(argv)
            return subprocess.CompletedProcess(argv, 0, "", "")

        before = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGHUP)}
        with tempfile.TemporaryDirectory() as tmp, runner.term_as_interrupt():
            self.assertTrue(runner._abort(None, "house-eval-x", docker, Path(tmp) / "run.err"))
            self.assertEqual(len(sent), 1)
            with self.assertRaises(KeyboardInterrupt):  # outside the abort a SIGTERM interrupts again
                os.kill(os.getpid(), signal.SIGTERM)
                time.sleep(1)
        self.assertEqual({s: signal.getsignal(s) for s in before}, before)

    def test_handlers_are_restored_after_an_exception_too(self):
        before = signal.getsignal(signal.SIGTERM)
        with self.assertRaises(RuntimeError), runner.term_as_interrupt():
            raise RuntimeError("boom")
        self.assertEqual(signal.getsignal(signal.SIGTERM), before)

    def test_off_the_main_thread_it_does_nothing(self):
        errors = []

        def body():
            try:
                with runner.term_as_interrupt():
                    pass
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        t = threading.Thread(target=body)
        t.start()
        t.join()
        self.assertEqual(errors, [])


class SweepDocker:
    """A fake `docker` that lists containers and records `rm -f`."""

    def __init__(self, names=(), usable=True, fail=()):
        self.names, self.usable, self.fail, self.calls = list(names), usable, set(fail), []

    def __call__(self, argv, **kw):
        self.calls.append(argv)
        if not self.usable:
            raise FileNotFoundError("docker")
        if argv[:2] == ["docker", "ps"]:
            return subprocess.CompletedProcess(argv, 0, "".join(n + "\n" for n in self.names), "")
        if argv[:3] == ["docker", "rm", "-f"]:
            return subprocess.CompletedProcess(argv, 1 if argv[3] in self.fail else 0, "", "denied")
        return subprocess.CompletedProcess(argv, 0, "", "")


class SweepTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.out = self.tmp / "eval-out"
        self.temp = self.tmp / "tmp"
        self.out.mkdir()
        self.temp.mkdir()
        patch = mock.patch.object(runner, "out_base", return_value=self.out)
        patch.start()
        self.addCleanup(patch.stop)
        self.now = 1_000_000.0

    def make(self, parent, name, age):
        d = parent / name
        d.mkdir()
        (d / "f").write_text("x")
        os.utime(d, (self.now - age, self.now - age))
        return d

    def sweep(self, docker=None, **kw):
        docker = docker or SweepDocker()
        return runner.sweep_stale(run=docker, now=self.now, tmp_base=self.temp, **kw), docker

    def test_removes_every_house_eval_container_and_nothing_else(self):
        docker = SweepDocker(["house-eval-aaa", "other-thing", "house-eval-bbb", "xhouse-eval-ccc"])
        done, docker = self.sweep(docker)
        ps = next(c for c in docker.calls if c[:2] == ["docker", "ps"])
        self.assertIn("-a", ps)
        self.assertIn("--format", ps)
        removed = [c[3] for c in docker.calls if c[:3] == ["docker", "rm", "-f"]]
        self.assertEqual(removed, ["house-eval-aaa", "house-eval-bbb"])
        self.assertEqual(sum("container" in line for line in done), 2)

    def test_old_out_dirs_and_temp_roots_go_new_ones_and_other_names_stay(self):
        old_out = self.make(self.out, "run-old", 3601)
        new_out = self.make(self.out, "run-new", 60)
        foreign_out = self.make(self.out, "keepme", 99999)
        old_root = self.make(self.temp, "house-eval-old", 7200)
        new_root = self.make(self.temp, "house-eval-new", 10)
        foreign = self.make(self.temp, "unrelated", 99999)
        done, _ = self.sweep()
        self.assertFalse(old_out.exists() or old_root.exists())
        self.assertTrue(new_out.exists() and new_root.exists() and foreign_out.exists() and foreign.exists())
        self.assertEqual(len(done), 2)

    def test_symlinks_are_not_followed_or_removed(self):
        target = self.make(self.tmp, "precious", 99999)
        (self.out / "run-link").symlink_to(target)
        (self.temp / "house-eval-link").symlink_to(target)
        self.sweep()
        self.assertTrue((target / "f").exists())
        self.assertTrue((self.out / "run-link").is_symlink())

    def test_age_limit_is_a_parameter(self):
        d = self.make(self.out, "run-x", 120)
        self.sweep(max_age=60)
        self.assertFalse(d.exists())

    def test_docker_missing_still_sweeps_the_dirs(self):
        d = self.make(self.out, "run-old", 99999)
        done, _ = self.sweep(SweepDocker(usable=False))
        self.assertFalse(d.exists())
        self.assertEqual(len(done), 1)

    def test_a_container_that_cannot_be_removed_is_reported_and_the_rest_goes_on(self):
        docker = SweepDocker(["house-eval-aaa", "house-eval-bbb"], fail=["house-eval-aaa"])
        done, docker = self.sweep(docker)
        self.assertEqual([c[3] for c in docker.calls if c[:3] == ["docker", "rm", "-f"]],
                         ["house-eval-aaa", "house-eval-bbb"])
        self.assertTrue(any("could not remove" in line and "house-eval-aaa" in line for line in done), done)

    def test_nothing_to_do_returns_nothing(self):
        done, _ = self.sweep()
        self.assertEqual(done, [])

    def test_missing_out_base_is_fine(self):
        shutil.rmtree(self.out)
        done, _ = self.sweep()
        self.assertEqual(done, [])


class InfraTest(RunnerCase):
    def classify(self, code, stdout="", stderr=""):
        result, _, _, _ = self.go(make_case(), Popens(code=code, stdout=stdout, stderr=stderr))
        return result.infra

    def test_table(self):
        rows = [
            ("usage limit in stderr", 1, "", "Claude AI usage limit reached", True),
            ("rate limit in log", 1, json.dumps({"type": "result", "result": "API rate limit hit"}) + "\n", "", True),
            ("credit balance", 1, "", "Credit balance is too low", True),
            ("auth 401", 1, "", "API Error: 401 Unauthorized", True),
            ("auth 403", 1, "", "request failed with 403", True),
            ("OAuth", 1, "", "OAuth token has expired", True),
            ("authentication", 1, "", "authentication_error", True),
            ("docker daemon", 125, "", "docker: Error response from daemon: no such image", True),
            ("crash without events", 1, "", "Segmentation fault", True),
            ("crash with empty output", 137, "", "", True),
            ("limit text but exit 0, no events", 0, "", "usage limit warning", True),
            ("assistant events and exit 1", 1, ASSISTANT + "\n", "", False),
            ("assistant events and limit text", 1, ASSISTANT + "\n", "usage limit", False),
            ("clean run", 0, ASSISTANT + "\n", "", False),
            ("exit 0, no events, nothing suspicious", 0, json.dumps({"type": "system"}) + "\n", "", False),
            ("a number that merely contains 401", 0, json.dumps({"type": "system", "n": 14012}) + "\n", "", False),
        ]
        for label, code, out, err, infra in rows:
            with self.subTest(label):
                got = self.classify(code, out, err)
                self.assertEqual(got is not None, infra, got)

    def test_synthetic_api_errors_are_not_agent_events(self):
        def synthetic(text, **extra):
            return json.dumps({"type": "assistant", **extra,
                               "message": {"model": "<synthetic>", "content": [{"type": "text", "text": text}]}}) + "\n"
        rows = [
            ("synthetic 401", 1, synthetic("API Error: 401 Invalid authentication credentials")),
            ("synthetic usage limit, exit 0", 0, synthetic("Claude AI usage limit reached|1700000000")),
            ("synthetic rate limit", 1, synthetic("API Error: rate limit exceeded")),
            ("error field", 1, json.dumps({"type": "assistant", "error": "rate_limit",
                                            "message": {"model": "sonnet", "content": []}}) + "\n"),
            ("error result event", 1, json.dumps({"type": "result", "is_error": True,
                                                   "result": "OAuth token has expired"}) + "\n"),
            ("error result, exit 0", 0, json.dumps({"type": "result", "is_error": True,
                                                     "result": "Credit balance is too low"}) + "\n"),
        ]
        for label, code, out in rows:
            with self.subTest(label):
                self.assertIsNotNone(self.classify(code, out))

    def test_synthetic_message_without_a_mention_still_needs_a_failure_exit(self):
        out = json.dumps({"type": "assistant", "message": {"model": "<synthetic>", "content": []}}) + "\n"
        self.assertIsNone(self.classify(0, out))
        self.assertIsNotNone(self.classify(1, out))

    def test_genuine_assistant_after_a_synthetic_one_is_not_infra(self):
        synth = json.dumps({"type": "assistant", "message": {"model": "<synthetic>", "content": [
            {"type": "text", "text": "API Error: 429 rate limit"}]}}) + "\n"
        self.assertIsNone(self.classify(1, synth + ASSISTANT + "\n"))

    def test_codex_error_items_are_not_agent_events(self):
        item = json.dumps({"type": "item.completed", "item": {"id": "i", "type": "error",
                                                              "message": "401 Unauthorized"}}) + "\n"
        case = make_case(agent="coder-codex", tool="codex", model=None)
        with mock.patch("houselib.evals.runner.shutil.which", return_value="/usr/bin/codex"):
            result, _, _, _ = self.go(case, Popens(code=1, stdout=item))
            self.assertIsNotNone(result.infra)
            result, _, _, _ = self.go(case, Popens(code=0, stdout=item))
            self.assertIsNotNone(result.infra)
            result, _, _, _ = self.go(case, Popens(code=1, stdout=item + CODEX_DONE + "\n"))
            self.assertIsNone(result.infra)

    def test_keyword_scan_ignores_the_agents_own_output(self):
        user = json.dumps({"type": "user", "message": {"content": [{"type": "tool_result",
                                                                     "content": "HTTP 401 usage limit OAuth"}]}}) + "\n"
        self.assertIsNone(self.classify(0, user))
        tool_item = json.dumps({"type": "item.started", "item": {"type": "command_execution",
                                                                "command": "grep 'rate limit' 403"}}) + "\n"
        self.assertIsNone(self.classify(0, tool_item))

    def test_non_json_lines_are_scanned(self):
        self.assertIsNotNone(self.classify(1, "docker: Error response from daemon\n"))

    def test_codex_events_count(self):
        case = make_case(agent="coder-codex", tool="codex", model=None)
        with mock.patch("houselib.evals.runner.shutil.which", return_value="/usr/bin/codex"):
            result, _, _, _ = self.go(case, Popens(code=1, stdout=CODEX_DONE + "\n"))
            self.assertIsNone(result.infra)
            result, _, _, _ = self.go(case, Popens(code=1, stderr="401 unauthorized"))
            self.assertIsNotNone(result.infra)

    def test_reason_names_the_cause(self):
        self.assertIn("usage limit", self.classify(1, "", "Claude AI usage limit reached"))
        self.assertIn("exit 137", self.classify(137))

    def test_malformed_log_lines_are_ignored(self):
        self.assertIsNone(self.classify(1, "not json\n" + ASSISTANT + "\n{broken\n"))


class ModelTest(RunnerCase):
    def model_of(self, case, **kw):
        _, popens, _, _ = self.go(case, **kw)
        argv = popens.procs[0].argv
        return option(argv, "--model")

    def test_case_model(self):
        self.assertEqual(self.model_of(make_case(model="opus")), "opus")

    def test_override_wins(self):
        self.assertEqual(self.model_of(make_case(model="opus"), model="haiku"), "haiku")

    def test_profile_tier(self):
        self.assertEqual(self.model_of(make_case(agent="judge", model=None)), "opus")
        self.assertEqual(self.model_of(make_case(agent="researcher", model=None)), "haiku")

    def test_codex_tier_profile_falls_back(self):
        self.assertEqual(self.model_of(make_case(agent="ideator", model=None)), "opus")

    def test_coder_uses_night_model_default_sonnet(self):
        self.assertEqual(self.model_of(make_case(agent="coder", model=None)), "sonnet")
        (self.root.contest / ".house/spec.yaml").write_text("night:\n  model: opus\n")
        self.assertEqual(self.model_of(make_case(agent="coder", model=None)), "opus")

    def test_unresolvable_model_is_infra_before_start(self):
        result, popens, _, _ = self.go(make_case(agent="no-such-agent", model=None))
        self.assertEqual(popens.procs, [])
        self.assertIn("no-such-agent", result.infra)


if __name__ == "__main__":
    unittest.main()
