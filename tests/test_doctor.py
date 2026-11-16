import json
import tempfile
import unittest
from pathlib import Path

from houselib import doctor


def fake_run(outputs):
    return lambda cmd: outputs.get(cmd[0])


class DetectTest(unittest.TestCase):
    def test_nothing_installed(self):
        tools = doctor.detect(which=lambda _: None, run=lambda _: None)
        self.assertEqual(
            tools,
            {"claude": False, "codex": False, "cursor": False, "grok_bot": False, "uv": False, "ruff": False,
             "ollama": {"installed": False, "models": []}, "gpu": None},
        )

    def test_ollama_models_parsed(self):
        tools = doctor.detect(
            which=lambda name: f"/usr/bin/{name}" if name == "ollama" else None,
            run=fake_run({"ollama": "NAME ID SIZE MODIFIED\nqwen3:8b abc 5.2GB 1 day ago\n"}),
        )
        self.assertEqual(tools["ollama"], {"installed": True, "models": ["qwen3:8b"]})

    def test_gpu_parsed(self):
        tools = doctor.detect(
            which=lambda _: None,
            run=fake_run({"nvidia-smi": "NVIDIA RTX 500 Ada Generation Laptop GPU, 4094\n"}),
        )
        self.assertEqual(tools["gpu"], {"name": "NVIDIA RTX 500 Ada Generation Laptop GPU", "vram_mib": 4094})

    def test_grok_bot_via_dpkg(self):
        tools = doctor.detect(which=lambda _: None, run=fake_run({"dpkg-query": "grok-bot\t0.78.5\n"}))
        self.assertTrue(tools["grok_bot"])

    def test_garbage_gpu_output_is_none(self):
        tools = doctor.detect(which=lambda _: None, run=fake_run({"nvidia-smi": "No devices were found\n"}))
        self.assertIsNone(tools["gpu"])


class WriteTest(unittest.TestCase):
    def test_write_tools_json(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / ".house").mkdir()
            path = doctor.write(Path(d), {"claude": True})
            data = json.loads(path.read_text())
            self.assertEqual(path, Path(d) / ".house" / "tools.json")
            self.assertTrue(data["claude"])
            self.assertIn("checked_at", data)


if __name__ == "__main__":
    unittest.main()


class SignalStatusTest(unittest.TestCase):
    def setUp(self):
        import os
        from unittest import mock
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.patches = [mock.patch.dict(os.environ, {"HOUSE_DATA_DIR": str(self.tmp / "data"),
                                                     "HOUSE_SIGNAL_CONFIG": str(self.tmp / "signal.toml")}),
                        mock.patch("houselib.signal.read_config", return_value={"HOUSE_NOTIFY_CHANNEL": "signal"}),
                        mock.patch("houselib.signal.from_config",
                                   return_value=type("S", (), {"binary": "/x/signal-cli", "account": "+1"})())]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self._tmp.cleanup()

    def test_never_pulled(self):
        out = "\n".join(doctor.signal_status())
        self.assertIn("✓ signal: /x/signal-cli", out)
        self.assertIn("nie było udanego odbioru", out)

    def test_doctor_last_pull_and_error(self):
        from datetime import datetime, timedelta

        from houselib import signalstore
        signalstore.update_state(last_pull_ok=datetime.now().astimezone().isoformat(timespec="seconds"))
        out = "\n".join(doctor.signal_status())
        self.assertIn("ostatni odbiór", out)
        self.assertNotIn("!", out)
        old = (datetime.now() - timedelta(days=9)).astimezone().isoformat(timespec="seconds")
        signalstore.update_state(last_pull_ok=old, last_pull_error="ERROR User +1 is not registered.")
        out = "\n".join(doctor.signal_status())
        self.assertIn("odłączyć bezczynne", out)
        self.assertIn("urządzenie odłączone", out)

    def test_doctor_bad_lines_and_perms(self):
        from houselib import signalchats, signalstore
        signalstore.append({"v": 1, "key": "k", "kind": "message", "chat": "self", "ts": 1})
        path = signalstore.store_dir() / "messages.jsonl"
        with path.open("a") as f:
            f.write("{broken\n")
        path.chmod(0o644)
        signalchats.save_config({"fr": {"id": "gX", "name": "x", "added": "x"}})
        out = "\n".join(doctor.signal_status())
        self.assertIn("uszkodzone linie magazynu: 2", out)
        self.assertIn("messages.jsonl", out)
        self.assertIn("0o644", out)

    def test_doctor_reports_config_problems(self):
        (self.tmp / "signal.toml").write_text("[groups.x\n")
        self.assertIn("signal.toml", "\n".join(doctor.signal_status()))
