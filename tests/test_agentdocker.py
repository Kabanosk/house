"""`houselib.agentdocker`: Signal jobs in Docker (image, networks, proxy, token, job command) with a fake runner."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from houselib import agentdocker as dk
from tests import helpers  # noqa: F401


class FakeDocker:
    def __init__(self, info=True, image=True, network=True, proxy_running=True, proxy_status="running"):
        self.calls, self.info, self.image, self.network, self.proxy = [], info, image, network, proxy_running
        self.proxy_status = proxy_status

    def __call__(self, argv, **kw):
        self.calls.append(argv)
        rc, out = 0, ""
        if argv[:2] == ["docker", "info"]:
            rc = 0 if self.info else 1
        elif argv[:3] == ["docker", "image", "inspect"]:
            rc = 0 if self.image else 1
        elif argv[:3] == ["docker", "network", "inspect"]:
            rc = 0 if self.network else 1
        elif argv[:2] == ["docker", "inspect"]:
            out = self.proxy_status + "\n" if self.proxy else ""
            rc = 0 if self.proxy else 1
        return subprocess.CompletedProcess(argv, rc, out, "")


class DockerCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.env = mock.patch.dict(os.environ, {"HOUSE_DATA_DIR": str(self.tmp / "data"),
                                                "HOUSE_SIGNAL_CONFIG": str(self.tmp / "cfg/signal.toml")})
        self.env.start()
        # hermetic: the machine running the tests may have snap docker (which forces "local")
        self.docker_which = mock.patch("houselib.agentdocker.shutil.which",
                                       lambda name, path=None: "/usr/bin/docker")
        self.docker_which.start()

    def tearDown(self):
        self.docker_which.stop()
        self.env.stop()
        self._tmp.cleanup()


class SetupTest(DockerCase):
    def test_image_name_pins_claude_version(self):
        self.assertEqual(dk.image_name(), f"house-agent:{dk.CLAUDE_VERSION}")

    def test_build_image(self):
        run = FakeDocker()
        dk.build_image(run)
        argv = run.calls[-1]
        self.assertEqual(argv[:4], ["docker", "build", "-t", dk.image_name()])
        self.assertIn(f"CLAUDE_VERSION={dk.CLAUDE_VERSION}", argv)
        self.assertTrue(argv[-1].endswith("core/docker/agent"))

    def test_networks_created_once(self):
        run = FakeDocker(network=False)
        dk.ensure_networks(run)
        self.assertIn(["docker", "network", "create", "--internal", dk.INTERNAL_NET], run.calls)
        run = FakeDocker(network=True)
        dk.ensure_networks(run)
        self.assertFalse(any(c[:3] == ["docker", "network", "create"] for c in run.calls))

    def test_proxy_started_when_missing(self):
        run = FakeDocker(proxy_running=False)
        dk.ensure_proxy(run)
        start = next(c for c in run.calls if c[:3] == ["docker", "run", "-d"])
        for flag in ("--read-only", "--cap-drop", "ALL", "no-new-privileges", "--restart", "unless-stopped"):
            self.assertIn(flag, start)
        self.assertEqual(start[-4:], ["python3", "-m", "houselib.agentproxy", "3128"])
        self.assertIn(["docker", "network", "connect", dk.INTERNAL_NET, dk.PROXY], run.calls)
        run = FakeDocker(proxy_running=True)
        dk.ensure_proxy(run)
        self.assertFalse(any(c[:3] == ["docker", "run", "-d"] for c in run.calls))

    def test_token_roundtrip_is_private(self):
        self.assertFalse(dk.token_present())
        dk.save_token("sk-ant-oat01-abc\n")
        path = dk.token_path()
        self.assertEqual(oct(path.stat().st_mode & 0o777), "0o600")
        self.assertEqual(dk.read_token(), "sk-ant-oat01-abc")
        self.assertTrue(dk.token_present())

    def test_runtime(self):
        dk.save_token("t")
        self.assertEqual(dk.runtime(FakeDocker()), "docker")
        self.assertEqual(dk.runtime(FakeDocker(image=False)), "local")
        self.assertEqual(dk.runtime(FakeDocker(info=False)), "local")
        dk.token_path().unlink()
        self.assertEqual(dk.runtime(FakeDocker()), "local")

    def test_snap_docker_stays_local(self):
        # snap's dockerd AppArmor profile refuses exec under no-new-privileges, so no job container could start
        dk.save_token("t")
        self.assertEqual(dk.runtime(FakeDocker(), which=lambda _: "/snap/bin/docker"), "local")
        self.assertEqual(dk.runtime(FakeDocker(), which=lambda _: "/usr/bin/docker"), "docker")

    def test_restarting_proxy_is_not_running(self):
        self.assertTrue(dk.proxy_running(FakeDocker()))
        self.assertFalse(dk.proxy_running(FakeDocker(proxy_status="restarting")))


class CommandTest(DockerCase):
    def test_job_command_isolation(self):
        wt, repo, d = self.tmp / "repo/.worktrees/signal-x", self.tmp / "repo", self.tmp / "job"
        d.mkdir()
        dk.save_token("sk-ant-oat01-SECRET")
        env_file = dk.write_env_file(d, job_id="x", wt=wt)
        self.assertEqual(oct(env_file.stat().st_mode & 0o777), "0o600")
        self.assertIn("CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-SECRET", env_file.read_text())
        self.assertIn(f"HTTPS_PROXY=http://{dk.PROXY}:3128", env_file.read_text())
        argv = dk.container_command("x", wt, repo, d / "prompt.md", d / "settings.json", env_file, ["claude", "-p"])
        joined = " ".join(argv)
        self.assertNotIn("SECRET", joined)
        for flag in ("--rm", "--init", "--read-only", "no-new-privileges", "--network", dk.INTERNAL_NET,
                     f"{os.getuid()}:{os.getgid()}", "--memory", "--pids-limit", "--env-file"):
            self.assertIn(flag, argv)
        self.assertIn(f"{wt}:{wt}", argv)
        self.assertIn(f"{repo / '.git'}:{repo / '.git'}:ro", argv)
        self.assertIn(f"{dk.CODE_ROOT}:/house:ro", argv)
        self.assertNotIn(str(Path.home()) + ":", joined.replace(str(wt), "").replace(str(repo), ""))
        self.assertNotIn("docker.sock", joined)
        self.assertNotIn(".claude:", joined)
        self.assertEqual(argv[-2:], ["claude", "-p"])
        self.assertEqual(argv[argv.index("--name") + 1], "house-agent-x")
        self.assertIn(f"/home/agent:exec,uid={os.getuid()},gid={os.getgid()},mode=0700", argv)
        self.assertIn("CLAUDE_CODE_TMPDIR=/home/agent", env_file.read_text())


class DoctorTest(DockerCase):
    def test_lines(self):
        lines = dk.doctor_lines(FakeDocker(image=False))
        self.assertTrue(any("obraz" in x and x.startswith("✗") for x in lines), lines)
        self.assertTrue(any("token" in x and x.startswith("✗") for x in lines), lines)
        dk.save_token("t")
        lines = dk.doctor_lines(FakeDocker(), which=self.which, unit=self.unit("/usr/bin:/bin"))
        self.assertTrue(all(x.startswith("✓") for x in lines), lines)

    def which(self, name, path=None):
        return "/usr/bin/docker" if path is None or "/usr/bin" in path.split(":") else None

    def unit(self, path):
        unit = self.tmp / "house-signal-pull.service"
        unit.write_text(f"[Service]\nEnvironment=PATH={path}\nExecStart=/x/bin/house signal pull\n")
        return unit

    def test_snap_docker_reported(self):
        dk.save_token("t")
        lines = dk.doctor_lines(FakeDocker(), which=lambda name, path=None: "/snap/bin/docker",
                                unit=self.unit("/snap/bin:/usr/bin"))
        self.assertTrue(any(x.startswith("✗") and "snap" in x and "docker-ce" in x for x in lines), lines)

    def test_timer_without_docker_in_path_reported(self):
        dk.save_token("t")
        lines = dk.doctor_lines(FakeDocker(), which=self.which, unit=self.unit("/home/u/.local/bin"))
        self.assertTrue(any(x.startswith("✗") and "PATH" in x and "agent on" in x for x in lines), lines)

    def test_missing_timer_unit_is_not_an_error(self):
        dk.save_token("t")
        lines = dk.doctor_lines(FakeDocker(), which=self.which, unit=self.tmp / "nope.service")
        self.assertTrue(all(x.startswith("✓") for x in lines), lines)

    def test_restarting_proxy_reported(self):
        dk.save_token("t")
        lines = dk.doctor_lines(FakeDocker(proxy_status="restarting"), which=self.which,
                                unit=self.unit("/usr/bin"))
        self.assertTrue(any(x.startswith("✗") and "proxy" in x and "docker logs" in x for x in lines), lines)


if __name__ == "__main__":
    unittest.main()
