"""Rules that hold across the code base: only the store receives; agents are told how to talk to the team."""
import re
import unittest

from houselib import HOUSE_ROOT


class StaticTest(unittest.TestCase):
    def test_only_store_calls_receive(self):
        callers = {p.name for p in (HOUSE_ROOT / "houselib").glob("*.py") if re.search(r"\.receive\(", p.read_text())}
        self.assertEqual(callers, {"signalstore.py"})

    def test_agents_know_the_commands(self):
        for rel in ("core/protocols/questions.md", "core/agents/coordinator.md"):
            text = (HOUSE_ROOT / rel).read_text()
            self.assertIn("house signal read", text, rel)
            self.assertIn("house signal send", text, rel)
            self.assertIn("signal-cli", text, rel)

    def test_trust_rule_in_protocol(self):
        text = (HOUSE_ROOT / "core/protocols/questions.md").read_text()
        self.assertIn("forwarded", text)

    def test_readme_documents_signal_commands(self):
        text = (HOUSE_ROOT / "README.md").read_text()
        for cmd in ("house signal read", "house signal unread", "house signal allow", "signal.toml"):
            self.assertIn(cmd, text)


if __name__ == "__main__":
    unittest.main()
