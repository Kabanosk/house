"""`house signal …` commands (fake Signal, temporary data dir; never the real account or config)."""
import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from houselib import cli, signalstore
from tests import helpers  # noqa: F401  (never the real Signal account)


class CliCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.env = mock.patch.dict(os.environ, {"HOUSE_DATA_DIR": str(self.tmp / "data"),
                                                "HOUSE_SIGNAL_CONFIG": str(self.tmp / "cfg/signal.toml")})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self._tmp.cleanup()

    def run_cli(self, *argv) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = cli.main(["signal", *argv])
            except SystemExit as e:
                code = e.code
        return code, out.getvalue(), err.getvalue()


class PullMigrateTest(CliCase):
    def test_pull_without_signal(self):
        with mock.patch("houselib.signal.from_config", return_value=None):
            code, out, _ = self.run_cli("pull")
        self.assertEqual(code, 0)
        self.assertIn("Signal wyłączony", out)

    def test_pull_reports_counts(self):
        from tests import signal_fixtures as fx
        from tests.test_signalstore import FakeSig
        sig = FakeSig([fx.self_note(100, "a"), fx.dm(101, "b")])
        with mock.patch("houselib.signal.from_config", return_value=sig):
            code, out, _ = self.run_cli("pull", "--timeout", "5")
        self.assertEqual(code, 0)
        self.assertIn("odebrano 1, odrzucono 1 (spoza dozwolonych)", out)

    def test_migrate_command(self):
        from houselib import notify
        (notify.data_dir() / "inbox.jsonl").write_text('{"ts": 1, "chat": "self", "sender": "W", "text": "x"}\n')
        code, out, _ = self.run_cli("migrate")
        self.assertEqual(code, 0)
        self.assertEqual(len(list(signalstore.iter_events())), 1)
        code, out, _ = self.run_cli("migrate")
        self.assertIn("nic do migracji", out)


if __name__ == "__main__":
    unittest.main()


class FakeRun:
    def __init__(self, stdout="1794822101442\n", rc=0):
        self.calls, self.stdout, self.rc = [], stdout, rc

    def __call__(self, argv, **kw):
        import subprocess
        self.calls.append(argv)
        return subprocess.CompletedProcess(argv, self.rc, self.stdout, "")


class SendTest(CliCase):
    def setUp(self):
        super().setUp()
        from houselib.signal import Signal
        self.run = FakeRun()
        self.sig = Signal("+48111", "sc", run=self.run)
        self.patch = mock.patch("houselib.signal.from_config", return_value=self.sig)
        self.patch.start()
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.home_patch = mock.patch("pathlib.Path.home", return_value=self.home)
        self.home_patch.start()

    def tearDown(self):
        self.home_patch.stop()
        self.patch.stop()
        super().tearDown()

    def argv(self):
        return self.run.calls[-1]

    def kuba(self, ts=50, author="u-kuba", name="Kuba Nowak", text="Spoko, a jaki wynik ma teraz baseline?"):
        from datetime import datetime
        t = datetime(2026, 11, 16, 12, 18).astimezone().isoformat()
        signalstore.append({"v": 1, "key": f"self:{author}:{ts}", "kind": "message", "dir": "in", "chat": "self",
                            "ts": ts, "time": t, "text": text,
                            "sender": {"name": name, "number": "+48999", "uuid": author}})

    def test_send_prefixes_and_logs(self):
        code, out, _ = self.run_cli("send", "self", "hej", "tam")
        self.assertEqual(code, 0)
        self.assertEqual(self.argv()[3:], ["send", "--note-to-self", "--notify-self", "-m", "🤖 [house] hej tam"])
        self.assertEqual(out.strip(), "✓ self 1794822101442")
        out_events = [e for e in signalstore.iter_events() if e["dir"] == "out"]
        self.assertEqual(out_events[0]["meta"], {"origin": "cli", "contest": None})

    def test_prefix_not_doubled(self):
        self.run_cli("send", "self", "🤖 [house] hej")
        self.assertEqual(self.argv()[-1], "🤖 [house] hej")

    def test_prefix_in_contest_repo(self):
        c = self.tmp / "fr-2026"
        (c / ".house").mkdir(parents=True)
        (c / ".git").mkdir()
        (c / ".house/spec.yaml").write_text("notify:\n  alias: fr\n")
        old = os.getcwd()
        os.chdir(c)
        try:
            self.run_cli("send", "self", "hej")
        finally:
            os.chdir(old)
        self.assertEqual(self.argv()[-1], "🤖 [fr] hej")

    def test_reply_to(self):
        self.kuba()
        code, out, _ = self.run_cli("send", "self", "LB 0.806", "--reply-to", "50")
        self.assertEqual(code, 0)
        self.assertEqual(self.argv()[-4:], ["--quote-timestamp", "50", "--quote-author", "u-kuba"])
        self.assertIn("(cytat: Kuba Nowak 12:18 „Spoko, a jaki wynik ma teraz…”)", out)

    def test_reply_to_unknown_ts(self):
        code, _, err = self.run_cli("send", "self", "x", "--reply-to", "77")
        self.assertEqual(code, 2)
        self.assertIn("77", err)
        self.assertEqual(self.run.calls, [])

    def test_reply_to_ambiguous_ts(self):
        self.kuba()
        self.kuba(author="u-ola", name="Ola")
        code, _, err = self.run_cli("send", "self", "x", "--reply-to", "50")
        self.assertEqual(code, 2)
        self.assertIn("--from", err)
        self.assertEqual(self.run.calls, [])
        code, _, _ = self.run_cli("send", "self", "x", "--reply-to", "50", "--from", "ola")
        self.assertEqual(code, 0)
        self.assertEqual(self.argv()[-1], "u-ola")

    def test_phone_number_refused(self):
        code, _, err = self.run_cli("send", "+48123456789", "x")
        self.assertEqual(code, 2)
        self.assertIn("DM", err)

    def test_unknown_alias(self):
        code, _, err = self.run_cli("send", "nieznana", "x")
        self.assertEqual(code, 2)
        self.assertIn("nieznana", err)
        self.assertEqual(self.run_cli("send", "all", "x")[0], 2)

    def test_file_rules(self):
        ok = self.tmp / "lb.png"
        ok.write_bytes(b"x" * 86016)
        code, out, _ = self.run_cli("send", "self", "wykres", "--file", str(ok))
        self.assertEqual(code, 0)
        self.assertIn("--attachment", self.argv())
        self.assertIn("(📎 lb.png, 84 KB)", out)
        big = self.tmp / "big.bin"
        with big.open("wb") as f:
            f.truncate(100 * 1024 * 1024 + 1)
        bad = [big, self.home / ".ssh/id", self.home / ".config/x", self.home / ".local/share/signal-cli/x",
               self.tmp / "a.env", self.tmp / ".env", self.tmp / ".env.local", self.tmp / "k.pem", self.tmp / "k.key",
               self.tmp / "missing.txt"]
        for p in bad[1:-1]:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("s")
        calls = len(self.run.calls)
        for p in bad:
            code, _, _ = self.run_cli("send", "self", "x", "--file", str(p))
            self.assertEqual(code, 2, p)
        self.assertEqual(len(self.run.calls), calls)

    def test_file_without_text(self):
        f = self.tmp / "a.csv"
        f.write_text("1")
        self.assertEqual(self.run_cli("send", "self", "--file", str(f))[0], 0)
        self.assertEqual(self.argv()[self.argv().index("-m") + 1], "🤖 [house]")

    def test_empty_message_refused(self):
        self.assertEqual(self.run_cli("send", "self")[0], 2)

    def test_failed_send(self):
        self.run.rc = 1
        code, out, _ = self.run_cli("send", "self", "x")
        self.assertEqual(code, 1)
        self.assertIn("✗ nie wysłano", out)

    def test_not_configured(self):
        with mock.patch("houselib.signal.from_config", return_value=None):
            code, _, err = self.run_cli("send", "self", "x")
        self.assertEqual(code, 1)
        self.assertIn("Signal nie jest skonfigurowany", err)


class GroupSig:
    account = "+48111"

    def __init__(self, groups):
        self.groups = groups

    def list_groups(self):
        return self.groups


class AllowDenyTest(CliCase):
    GROUPS = ({"id": "JsNv4TnmFZy5=", "name": "techarena_claude"}, {"id": "gA1=", "name": "Dup"},
              {"id": "gA2=", "name": "Dup"}, {"id": "gPriv=", "name": "Rodzina"})

    def setUp(self):
        super().setUp()
        self.patch = mock.patch("houselib.signal.from_config", return_value=GroupSig(list(self.GROUPS)))
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        super().tearDown()

    def tty(self, answer="t"):
        return mock.patch.multiple("houselib.signalcli", _tty=lambda: True, _ask=lambda prompt: answer)

    def config(self):
        from houselib import signalchats
        return signalchats.load_config()

    def test_allow_without_tty_refused(self):
        with mock.patch("houselib.signalcli._tty", return_value=False):
            code, _, err = self.run_cli("allow", "techarena_claude", "--alias", "techarena")
        self.assertEqual(code, 2)
        self.assertIn("decyzja człowieka", err)
        self.assertIn("poproś Wojtka", err)
        from houselib import signalchats
        self.assertFalse(signalchats.config_path().exists())

    def test_allow_with_tty(self):
        with self.tty("t"):
            code, out, _ = self.run_cli("allow", "techarena_claude", "--alias", "techarena")
        self.assertEqual(code, 0)
        self.assertIn("✓ techarena → techarena_claude (JsNv4Tnm…)", out)
        entry = self.config()["techarena"]
        self.assertEqual((entry["id"], entry["name"]), ("JsNv4TnmFZy5=", "techarena_claude"))
        self.assertTrue(entry["added"])

    def test_allow_declined(self):
        with self.tty("n"):
            code, _, _ = self.run_cli("allow", "techarena_claude")
        self.assertEqual(code, 1)
        self.assertEqual(self.config(), {})

    def test_allow_default_alias(self):
        with self.tty():
            self.run_cli("allow", "techarena_claude")
        self.assertIn("techarena-claude", self.config())

    def test_allow_ambiguous_name_lists_ids(self):
        with self.tty():
            code, _, err = self.run_cli("allow", "Dup")
        self.assertEqual(code, 2)
        self.assertIn("gA1=", err)
        self.assertIn("gA2=", err)
        self.assertIn("--id", err)

    def test_allow_by_id(self):
        with self.tty():
            code, _, _ = self.run_cli("allow", "Dup", "--id", "gA2=", "--alias", "dup")
        self.assertEqual(code, 0)
        self.assertEqual(self.config()["dup"]["id"], "gA2=")

    def test_allow_unknown_group(self):
        with self.tty():
            code, _, err = self.run_cli("allow", "Nieznana")
        self.assertEqual(code, 2)
        self.assertIn("Nieznana", err)

    def test_allow_bad_alias(self):
        with self.tty():
            self.assertEqual(self.run_cli("allow", "techarena_claude", "--alias", "self")[0], 2)

    def test_deny_removes_entry_keeps_history(self):
        with self.tty():
            self.run_cli("allow", "techarena_claude", "--alias", "techarena")
        signalstore.append({"v": 1, "key": "k", "kind": "message", "dir": "in", "chat": "JsNv4TnmFZy5=", "ts": 1})
        with self.tty():
            code, _, _ = self.run_cli("deny", "techarena")
        self.assertEqual(code, 0)
        self.assertEqual(self.config(), {})
        self.assertEqual(len(list(signalstore.iter_events())), 1)

    def test_deny_without_tty_refused(self):
        with self.tty():
            self.run_cli("allow", "techarena_claude", "--alias", "techarena")
        with mock.patch("houselib.signalcli._tty", return_value=False):
            self.assertEqual(self.run_cli("deny", "techarena")[0], 2)
        self.assertIn("techarena", self.config())

    def test_deny_contest_group_refused(self):
        from houselib import signalchats
        contest = signalchats.Chat("gFR", "fr", "Team", "konkurs fr-2026")
        with self.tty(), mock.patch("houselib.signalchats.allowed",
                                    return_value={"self": signalchats.Chat("self", "self", "n", "zawsze"),
                                                  "gFR": contest}):
            code, _, err = self.run_cli("deny", "fr")
        self.assertEqual(code, 2)
        self.assertIn("notify.signal_group", err)


class GroupsTest(CliCase):
    def setUp(self):
        super().setUp()
        from houselib import signalchats
        signalchats.save_config({"techarena": {"id": "gT", "name": "techarena_claude", "added": "x"}})
        from datetime import datetime
        for i, chat in enumerate(["gT", "gT", "self"]):
            t = datetime(2026, 11, 16, 12, 18 + i).astimezone().isoformat()
            signalstore.append({"v": 1, "key": f"{chat}:a:{i}", "kind": "message", "dir": "in", "chat": chat,
                                "ts": i, "time": t, "text": "x", "sender": {"name": "A"}})
        self.patch = mock.patch("houselib.signal.from_config", return_value=GroupSig(
            [{"id": "gT", "name": "techarena_claude"}, {"id": "gP1", "name": "Rodzina"},
             {"id": "gP2", "name": "Szkoła"}]))
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        super().tearDown()

    def test_groups_table(self):
        code, out, _ = self.run_cli("groups")
        self.assertEqual(code, 0)
        lines = out.splitlines()
        self.assertEqual(lines[0].split(), ["alias", "grupa", "źródło", "wiadomości", "ostatnia"])
        row = next(x for x in lines if x.startswith("techarena"))
        self.assertIn("signal.toml", row)
        self.assertEqual(row.split()[3], "2")
        self.assertTrue(any(x.startswith("self") and "zawsze" in x for x in lines))
        self.assertNotIn("Rodzina", out)

    def test_groups_all_without_tty_hides_names(self):
        with mock.patch("houselib.signalcli._tty", return_value=False):
            code, out, _ = self.run_cli("groups", "--all")
        self.assertEqual(code, 0)
        self.assertIn("+ 2 innych grup konta (nazwy tylko w terminalu)", out)
        self.assertNotIn("Rodzina", out)
        self.assertNotIn("gP1", out)

    def test_groups_all_with_tty_lists_names(self):
        with mock.patch("houselib.signalcli._tty", return_value=True):
            _, out, _ = self.run_cli("groups", "--all")
        self.assertIn("Rodzina", out)
        self.assertIn("[dozwolona]", out)


class ReadShowTest(CliCase):
    def setUp(self):
        super().setUp()
        from houselib import signalchats
        signalchats.save_config({"techarena": {"id": "gT", "name": "techarena_claude", "added": "x"}})
        from tests.test_signalread import sample
        self.events = sample()
        for e in self.events:
            signalstore.append(e)
        self.ts = [e["ts"] for e in self.events]
        self.noconf = mock.patch("houselib.signal.from_config", return_value=None)
        self.noconf.start()

    def tearDown(self):
        self.noconf.stop()
        super().tearDown()

    def test_read_chat(self):
        code, out, _ = self.run_cli("read", "techarena", "-n", "2", "--offline")
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("techarena · techarena_claude · 2 z 4 · odebrano nigdy"), out)
        self.assertIn(str(self.ts[4]), out)
        self.assertNotIn(str(self.ts[1]), out)

    def test_read_json_has_no_raw(self):
        import json
        _, out, _ = self.run_cli("read", "techarena", "--json", "--offline", "--from", "kuba")
        rows = [json.loads(x) for x in out.splitlines()]
        self.assertEqual(len(rows), 1)
        self.assertNotIn("raw", rows[0])
        self.assertEqual(rows[0]["reactions"], [["👍", "Wojciech Fiołka"]])
        self.assertEqual(rows[0]["text"], "Spoko, a jaki wynik ma teraz baseline?")

    def test_read_offline_never_pulls(self):
        with mock.patch("houselib.signalstore.pull") as pull:
            self.run_cli("read", "--offline")
        pull.assert_not_called()

    def test_read_without_signal_notes_header(self):
        code, out, _ = self.run_cli("read")
        self.assertEqual(code, 0)
        self.assertIn("Signal wyłączony, pokazuję zapisane", out.splitlines()[0])

    def test_read_pull_error_keeps_exit_0(self):
        from houselib.signalstore import PullResult
        with mock.patch("houselib.signal.from_config", return_value=object()), \
                mock.patch("houselib.signalstore.pull", return_value=PullResult(error="timeout")):
            code, out, _ = self.run_cli("read")
        self.assertEqual(code, 0)
        self.assertIn("błąd odbioru: timeout", out.splitlines()[0])

    def test_read_skips_pull_within_60s(self):
        from datetime import datetime
        signalstore.update_state(last_pull_ok=datetime.now().astimezone().isoformat(timespec="seconds"))
        with mock.patch("houselib.signal.from_config", return_value=object()), \
                mock.patch("houselib.signalstore.pull") as pull:
            self.run_cli("read")
        pull.assert_not_called()

    def test_unknown_chat_exit_2(self):
        self.assertEqual(self.run_cli("read", "nieznana", "--offline")[0], 2)

    def test_bad_since_exit_2(self):
        self.assertEqual(self.run_cli("read", "--since", "wczoraj", "--offline")[0], 2)

    def test_show(self):
        code, out, _ = self.run_cli("show", str(self.ts[2]), "--offline")
        self.assertEqual(code, 0)
        self.assertIn("↩ cytuje", out)
        self.assertIn("odpowiedzi cytatem", out)

    def test_show_unknown_exit_2(self):
        self.assertEqual(self.run_cli("show", "123", "--offline")[0], 2)

    def test_show_ambiguous(self):
        from tests.test_signalread import msg
        e = dict(self.events[2])
        e.update(key=f"self:u-x:{e['ts']}", chat="self", sender={"name": "Inny", "number": "+1", "uuid": "u-x"})
        signalstore.append(e)
        code, _, err = self.run_cli("show", str(self.ts[2]), "--offline")
        self.assertEqual(code, 2)
        self.assertIn("--chat", err)
        self.assertEqual(self.run_cli("show", str(self.ts[2]), "--chat", "techarena", "--offline")[0], 0)
        self.assertEqual(self.run_cli("show", str(self.ts[2]), "--from", "inny", "--offline")[0], 0)
        del msg

    def test_show_raw(self):
        _, out, _ = self.run_cli("show", str(self.ts[2]), "--raw", "--offline")
        self.assertIn('"kind": "message"', out)


class UnreadStatusTest(CliCase):
    def setUp(self):
        super().setUp()
        from houselib import signalchats
        signalchats.save_config({"techarena": {"id": "gT", "name": "techarena_claude", "added": "x"}})
        from datetime import datetime, timedelta

        from tests.test_signalread import msg
        now = datetime.now().astimezone()
        for i in range(3):
            signalstore.append(msg(now - timedelta(minutes=10 - i), f"m{i}"))
        self.noconf = mock.patch("houselib.signal.from_config", return_value=None)
        self.noconf.start()

    def tearDown(self):
        self.noconf.stop()
        super().tearDown()

    def test_unread_as_and_peek(self):
        code, out, _ = self.run_cli("unread", "--as", "proj", "--peek")
        self.assertEqual(code, 0)
        self.assertIn("3 nowe", out)
        self.assertIn("(dla: proj)", out)
        self.assertIn("3 nowe", self.run_cli("unread", "--as", "proj")[1])
        self.assertIn("brak nowych", self.run_cli("unread", "--as", "proj")[1])

    def test_unread_json(self):
        import json
        _, out, _ = self.run_cli("unread", "--as", "p", "--json")
        self.assertEqual([json.loads(x)["text"] for x in out.splitlines()], ["m0", "m1", "m2"])

    def test_status_lines(self):
        signalstore.update_state(last_pull_error="timeout", dropped_last_pull=4)
        code, out, _ = self.run_cli("status")
        self.assertEqual(code, 0)
        for needle in ("Signal wyłączony", "ostatni odbiór", "timeout", "odrzucono w ostatnim odbiorze: 4",
                       "magazyn: 3 zdarzeń", "techarena 3"):
            self.assertIn(needle, out)
