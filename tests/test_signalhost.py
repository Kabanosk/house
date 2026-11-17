"""`houselib.signalhost`: this computer's name in house messages and who handles an event (several linked devices)."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from houselib import signalchats, signalhost
from tests import helpers  # noqa: F401  (never the real Signal account)


class HostCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        env = {k: v for k, v in os.environ.items() if k != "HOUSE_HOST"}
        env["HOUSE_SIGNAL_CONFIG"] = str(self.tmp / "signal.toml")
        self.env = mock.patch.dict(os.environ, env, clear=True)
        self.env.start()
        self.node = mock.patch("socket.gethostname", return_value="WRO1-LDL-P32030.corp.example")
        self.node.start()

    def tearDown(self):
        self.node.stop()
        self.env.stop()
        self._tmp.cleanup()

    def toml(self, text):
        Path(os.environ["HOUSE_SIGNAL_CONFIG"]).write_text(text)


class ThisHostTest(HostCase):
    def test_hostname_fallback(self):
        self.assertEqual(signalhost.this_host(), "wro1-ldl-p32030")
        self.assertIsNone(signalhost.agent_host())
        self.assertEqual(signalhost.problems(), [])

    def test_hostname_odd_characters_and_length(self):
        with mock.patch("socket.gethostname", return_value="Mój_Komputer Stacjonarny.local"):
            name = signalhost.this_host()
        self.assertRegex(name, r"^[a-z0-9-]{1,16}$")

    def test_toml_values(self):
        self.toml('host = "dom"\nagent_host = "praca"\n')
        self.assertEqual((signalhost.this_host(), signalhost.agent_host()), ("dom", "praca"))

    def test_env_wins(self):
        self.toml('host = "dom"\n')
        with mock.patch.dict(os.environ, {"HOUSE_HOST": "laptop"}):
            self.assertEqual(signalhost.this_host(), "laptop")

    def test_bad_names_fall_back_and_are_reported(self):
        self.toml('host = "Dom Główny"\nagent_host = "x y"\n')
        self.assertEqual(signalhost.this_host(), "wro1-ldl-p32030")
        self.assertIsNone(signalhost.agent_host())
        problems = signalhost.problems()
        self.assertEqual(len(problems), 2, problems)
        self.assertIn("host", problems[0])

    def test_keys_inside_a_group_table_are_reported(self):
        self.toml('[groups.t]\nid = "x"\nhost = "dom"\nagent_host = "dom"\n')
        self.assertEqual(signalhost.this_host(), "wro1-ldl-p32030")
        problems = signalhost.problems()
        self.assertTrue(any("[groups.t]" in p and "host" in p for p in problems), problems)

    def test_broken_toml_is_not_fatal(self):
        self.toml("host = \n")
        self.assertEqual(signalhost.this_host(), "wro1-ldl-p32030")

    def test_save_config_keeps_host_keys(self):
        self.toml('host = "dom"\nagent_host = "dom"\n')
        signalchats.save_config({"techarena": {"id": "gT", "name": "t", "added": "x"}})
        self.assertEqual((signalhost.this_host(), signalhost.agent_host()), ("dom", "dom"))
        self.assertEqual(list(signalchats.load_config()), ["techarena"])


class PrefixTest(HostCase):
    def test_prefix(self):
        self.toml('host = "dom"\n')
        self.assertEqual(signalhost.prefix("techarena"), "🤖 [techarena@dom]")

    def test_parse_prefix(self):
        self.assertEqual(signalhost.parse_prefix("🤖 [techarena@praca] wynik"), ("techarena", "praca"))
        self.assertEqual(signalhost.parse_prefix("🤖 [house] stare"), ("house", None))
        self.assertIsNone(signalhost.parse_prefix("hej 🤖 [house@dom]"))
        self.assertIsNone(signalhost.parse_prefix(None))


class AddressedTest(HostCase):
    def test_explicit_target(self):
        self.toml('host = "dom"\nagent_host = "praca"\n')
        self.assertTrue(signalhost.addressed_to_me("dom"))
        self.assertFalse(signalhost.addressed_to_me("praca"))

    def test_default_goes_to_agent_host(self):
        self.toml('host = "dom"\nagent_host = "praca"\n')
        self.assertFalse(signalhost.addressed_to_me(None))
        self.toml('host = "dom"\nagent_host = "dom"\n')
        self.assertTrue(signalhost.addressed_to_me(None))

    def test_no_agent_host_handles_untargeted(self):
        self.toml('host = "dom"\n')
        self.assertTrue(signalhost.addressed_to_me(None))
        self.assertFalse(signalhost.addressed_to_me("praca"))


if __name__ == "__main__":
    unittest.main()
