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
            {"claude": False, "codex": False, "cursor": False, "grok_bot": False,
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
