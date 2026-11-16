"""`houselib.signalstore`: the durable message store (append with seq, locks, pending fallback, pull, migration)."""
import fcntl
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from houselib import signalstore as store
from tests import helpers  # noqa: F401  (never the real Signal account)


def ev(ts, chat="gT", author="u-kuba", text="x", **kw):
    return {"v": 1, "key": f"{chat}:{author}:{ts}", "kind": "message", "dir": "in", "chat": chat,
            "chat_alias": "techarena", "ts": ts, "text": text, **kw}


class StoreCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.data = Path(self._tmp.name) / "data"
        self.env = mock.patch.dict(os.environ, {"HOUSE_DATA_DIR": str(self.data)})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self._tmp.cleanup()

    def messages(self) -> Path:
        return store.store_dir() / "messages.jsonl"


class CoreTest(StoreCase):
    def test_append_assigns_increasing_seq_and_perms(self):
        self.assertEqual(store.append(ev(1, text="żółw 🐢")), 1)
        self.assertEqual(store.append(ev(2)), 2)
        self.assertEqual(stat.S_IMODE(self.messages().stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(store.store_dir().stat().st_mode), 0o700)
        raw = self.messages().read_bytes().decode()
        self.assertIn("żółw 🐢", raw)
        self.assertNotIn("\\u", raw)
        self.assertEqual([e["seq"] for e in store.iter_events()], [1, 2])
        self.assertEqual(store.last_seq(), 2)
        self.assertEqual(stat.S_IMODE((store.store_dir() / "state.json").stat().st_mode), 0o600)

    def test_reader_skips_partial_and_bad_lines(self):
        store.store_dir()
        self.messages().write_text('{"v":1,"seq":1,"key":"a"}\n{bad\n{"v":1,"seq":2,"key":"b"}\n[1]\n{"v":1')
        self.assertEqual([e["key"] for e in store.iter_events()], ["a", "b"])
        self.assertEqual(store.bad_lines(), [2, 4])

    def test_append_after_partial_line_starts_a_new_line(self):
        store.store_dir()
        self.messages().write_text('{"v":1,"seq":1,"key":"a"}\n{"v":1,"se')
        store.append(ev(5))
        self.assertEqual([e["ts"] for e in store.iter_events() if "ts" in e], [5])

    def test_seq_survives_lost_state(self):
        store.append(ev(1))
        store.append(ev(2))
        (store.store_dir() / "state.json").unlink()
        self.assertEqual(store.append(ev(3)), 3)

    def test_raw_dropped_on_request(self):
        store.append(ev(1, raw={"envelope": {}}))
        self.assertIn("raw", next(store.iter_events()))
        self.assertNotIn("raw", next(store.iter_events(raw=False)))

    def test_append_falls_back_to_pending_when_locked(self):
        store.store_dir()
        with (store.store_dir() / "store.lock").open("a") as f, mock.patch.object(store, "LOCK_TIMEOUT", 0.1):
            fcntl.flock(f, fcntl.LOCK_EX)
            self.assertIsNone(store.append(ev(7)))
        pending = store.store_dir() / "pending.jsonl"
        self.assertEqual(json.loads(pending.read_text())["ts"], 7)
        self.assertEqual(stat.S_IMODE(pending.stat().st_mode), 0o600)
        self.assertEqual(list(store.iter_events()), [])
        self.assertEqual(store.ingest_pending(), 1)
        self.assertEqual([(e["ts"], e["seq"]) for e in store.iter_events()], [(7, 1)])
        self.assertEqual(pending.read_text(), "")

    def test_find_returns_latest_version_per_key(self):
        store.append(ev(5, text="old"))
        store.append(ev(5, text="new"))
        store.append(ev(5, author="u-ola", text="ola"))
        store.append(ev(5, chat="self", author="u-own"))
        found = store.find("gT", 5)
        self.assertEqual(sorted(e["text"] for e in found), ["new", "ola"])

    def test_state_roundtrip(self):
        self.assertEqual(store.read_state(), {})
        store.update_state(last_pull_ok="2026-11-16T12:00:00+01:00")
        store.update_state(dropped_last_pull=3)
        self.assertEqual(store.read_state(), {"last_pull_ok": "2026-11-16T12:00:00+01:00", "dropped_last_pull": 3})


if __name__ == "__main__":
    unittest.main()


class FakeSig:
    """Callback-style stand-in for `Signal` (receive hands each object to `handle`, like the real one)."""

    def __init__(self, objs, die_after=None, attachments_dir=None, on_object=None):
        from tests import signal_fixtures as fx
        self.account, self.objs, self.die_after, self.on_object = fx.OWN, objs, die_after, on_object
        self.attachments_dir = Path(attachments_dir or "/nonexistent")
        self.calls = 0

    def receive(self, handle, timeout=1, max_messages=None, should_stop=lambda: False):
        from houselib.signal import ReceiveResult
        self.calls += 1
        res = ReceiveResult()
        for i, o in enumerate(self.objs):
            if self.die_after is not None and i >= self.die_after:
                res.error = "read: pipe broken"
                break
            res.objects += 1
            handle(o)
            if self.on_object:
                self.on_object(i)
            if not res.stopped and should_stop():
                res.stopped = True
        return res

    def list_groups(self):
        return []


def chats(**extra):
    from houselib.signalchats import Chat
    out = {"self": Chat("self", "self", "Notatka do siebie", "zawsze"),
           "gT": Chat("gT", "techarena", "techarena_claude", "signal.toml")}
    out.update(extra)
    return out


class PullTest(StoreCase):
    def test_pull_stores_allowed_and_counts_dropped(self):
        from tests import signal_fixtures as fx
        sig = FakeSig([fx.group_in(100, "a"), fx.self_note(101, "b"), fx.dm(102, "c"), fx.group_in(103, "d", gid="gX"),
                       fx.receipt(104)])
        res = store.pull(sig, chats())
        self.assertEqual((res.kept, res.dropped, res.error, res.skipped), (2, 2, None, None))
        self.assertEqual([e["text"] for e in store.iter_events()], ["a", "b"])
        state = store.read_state()
        self.assertEqual(state["dropped_last_pull"], 2)
        self.assertIsNone(state["last_pull_error"])
        self.assertTrue(state["last_pull_ok"])

    def test_privacy_nothing_written(self):
        import contextlib
        import io

        from tests import signal_fixtures as fx
        secret = [fx.dm(102, "UNIKALNY-DM-tekst", number="+48555000111", name="Tajny Znajomy"),
                  fx.group_in(103, "UNIKALNA-grupa", gid="gSEKRETNA", name="Ktoś Obcy", number="+48555000222")]
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            store.pull(FakeSig([fx.group_in(100, "jawne"), *secret]), chats())
        blobs = [p.read_bytes() for p in self.data.rglob("*") if p.is_file()]
        blobs += [out.getvalue().encode(), err.getvalue().encode()]
        for needle in ("UNIKALN", "+48555000111", "+48555000222", "Tajny", "Obcy", "gSEKRETNA"):
            self.assertFalse(any(needle.encode() in b for b in blobs), needle)

    def test_pull_interrupted_keeps_lines(self):
        from tests import signal_fixtures as fx
        res = store.pull(FakeSig([fx.group_in(100 + i, str(i)) for i in range(5)], die_after=3), chats())
        self.assertEqual(res.kept, 3)
        self.assertEqual(len(list(store.iter_events())), 3)
        self.assertEqual(store.read_state()["last_pull_error"], "read: pipe broken")
        self.assertNotIn("last_pull_ok", store.read_state())

    def test_pull_skips_when_receive_locked(self):
        sig = FakeSig([])
        with store.lock("receive.lock", blocking=False) as held:
            self.assertTrue(held)
            res = store.pull(sig, chats())
        self.assertEqual(res.skipped, "inny odbiór trwa")
        self.assertEqual(sig.calls, 0)

    def test_pull_without_signal(self):
        self.assertEqual(store.pull(None, chats()).skipped, "Signal wyłączony")

    def test_pull_moves_pending_in(self):
        store.store_dir()
        (store.store_dir() / "pending.jsonl").write_text(json.dumps(ev(9)) + "\n")
        store.pull(FakeSig([]), chats())
        self.assertEqual([e["ts"] for e in store.iter_events()], [9])

    def test_attachment_copied_for_group_only(self):
        from datetime import datetime

        from tests import signal_fixtures as fx
        src = Path(self._tmp.name) / "att"
        src.mkdir()
        (src / "x.jpg").write_bytes(b"JPEG")
        (src / "dm.jpg").write_bytes(b"PRIVATE")
        ts = int(datetime(2026, 11, 16, 12, 0).timestamp() * 1000)
        objs = [fx.group_in(ts, "zdjęcie", attachments=[fx.attachment("x.jpg", "wykres LB.jpg")]),
                fx.group_in(ts + 1, "brak", attachments=[fx.attachment("gone.jpg", "b.jpg")]),
                fx.dm(ts + 2, "prywatne", attachments=[fx.attachment("dm.jpg", "dm.jpg")])]
        store.pull(FakeSig(objs, attachments_dir=src), chats())
        events = list(store.iter_events())
        path = Path(events[0]["attachments"][0]["path"])
        self.assertEqual(path, store.store_dir() / "files/techarena" / f"2026-11-16_{ts}_wykres_LB.jpg")
        self.assertEqual(path.read_bytes(), b"JPEG")
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertIsNone(events[1]["attachments"][0]["path"])
        self.assertFalse(any(b"PRIVATE" in p.read_bytes() for p in self.data.rglob("*") if p.is_file()))

    def test_attachment_id_cannot_escape(self):
        from tests import signal_fixtures as fx
        src = Path(self._tmp.name) / "att"
        src.mkdir()
        (Path(self._tmp.name) / "secret").write_bytes(b"S")
        store.pull(FakeSig([fx.group_in(100, "x", attachments=[fx.attachment("../secret", "s")])],
                           attachments_dir=src), chats())
        self.assertIsNone(next(store.iter_events())["attachments"][0]["path"])

    def test_sigterm_finishes_line_then_reraises(self):
        import signal as sigmod

        from tests import signal_fixtures as fx
        got = []
        old = sigmod.signal(sigmod.SIGTERM, lambda n, f: got.append(n))
        try:
            def on_object(i):
                if i == 0:
                    os.kill(os.getpid(), sigmod.SIGTERM)
            res = store.pull(FakeSig([fx.group_in(100 + i, str(i)) for i in range(3)], on_object=on_object), chats())
        finally:
            sigmod.signal(sigmod.SIGTERM, old)
        self.assertTrue(res.interrupted)
        self.assertEqual(len(list(store.iter_events())), 3)
        self.assertEqual(got, [sigmod.SIGTERM])

    def test_bad_envelope_counted(self):
        from tests import signal_fixtures as fx
        bad = fx.group_in(99, "x", quote=(1, "u", "q"))
        bad["envelope"]["dataMessage"]["quote"]["id"] = "abc"
        res = store.pull(FakeSig([bad, fx.group_in(100, "ok")]), chats())
        self.assertEqual((res.kept, res.bad_lines), (1, 1))
        self.assertEqual(store.read_state()["bad_lines"], 1)


SELF_LINES = [
    {"ts": 300, "chat": "self", "sender": "Wojciech Fiołka", "text": "B", "quote_ts": 250},
    {"ts": 100, "chat": "self", "sender": "Wojciech Fiołka", "text": "A odpisze", "quote_ts": None},
    {"ts": 200, "chat": "gT", "sender": "Patryk Zieliński", "text": "Pepo smile", "quote_ts": 150},
    {"ts": 400, "chat": "self", "sender": "Wojciech Fiołka", "text": "notatka"},
]


class MigrateTest(StoreCase):
    def write_old(self, lines=SELF_LINES, done=(100,), sent=True):
        from houselib import notify
        d = notify.data_dir()
        (d / "inbox.jsonl").write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in lines))
        (d / "inbox_done.json").write_text(json.dumps(list(done)) + "\n")
        if sent:
            (d / "sent.jsonl").write_text(json.dumps({"ts": 250, "contest": "fr-2026", "question": "Q-001",
                                                      "chat": "self"}) + "\n")
        return d

    def test_migrate_inbox(self):
        d = self.write_old(sent=False)
        before = (d / "inbox.jsonl").read_bytes()
        log = store.migrate()
        events = list(store.iter_events())
        self.assertEqual([e["ts"] for e in events], [100, 200, 300, 400])
        self.assertEqual([e["seq"] for e in events], [1, 2, 3, 4])
        self.assertEqual([e["dir"] for e in events], ["own", "in", "own", "own"])
        e = events[2]
        self.assertEqual((e["kind"], e["chat"], e["key"], e["raw"], e["meta"]),
                         ("message", "self", "self:migrated:300", None, {"migrated": True}))
        self.assertEqual(e["quote"], {"ts": 250, "author": None, "text": None})
        self.assertIsNone(events[0]["quote"])
        self.assertEqual(events[1]["sender"], {"name": "Patryk Zieliński", "number": None, "uuid": None})
        self.assertFalse((d / "inbox.jsonl").exists())
        renamed = list(d.glob("inbox.jsonl.migrated-*"))
        self.assertEqual(len(renamed), 1)
        self.assertRegex(renamed[0].name, r"^inbox\.jsonl\.migrated-\d{8}-\d{6}$")
        self.assertEqual(renamed[0].read_bytes(), before)
        self.assertTrue(store.read_state()["migrated"])
        self.assertTrue(any("4" in line for line in log), log)

    def test_migrate_done_keys(self):
        d = self.write_old(sent=False)
        store.migrate()
        self.assertEqual(json.loads((store.store_dir() / "inbox_done.json").read_text()),
                         {"self:migrated:100": None})
        self.assertFalse((d / "inbox_done.json").exists())
        self.assertEqual(len(list(d.glob("inbox_done.json.migrated-*"))), 1)

    def test_migrate_sent(self):
        self.write_old()
        store.migrate()
        out = [e for e in store.iter_events() if e["dir"] == "out"]
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["meta"], {"origin": "notify", "contest": "fr-2026", "question": "Q-001",
                                          "migrated": True})
        self.assertEqual((out[0]["ts"], out[0]["chat"], out[0]["sender"]["name"]), (250, "self", "house"))

    def test_migrate_twice_is_noop(self):
        self.write_old()
        store.migrate()
        before = self.messages().read_bytes()
        self.assertEqual(store.migrate(), [])
        self.assertEqual(store.ensure_migrated(), [])
        self.assertEqual(self.messages().read_bytes(), before)

    def test_reappearing_inbox_is_migrated_again(self):
        d = self.write_old(sent=False)
        store.migrate()
        (d / "inbox.jsonl").write_text(json.dumps(SELF_LINES[0]) + "\n" + json.dumps(
            {"ts": 500, "chat": "self", "sender": "Wojciech Fiołka", "text": "nowa", "quote_ts": None}) + "\n")
        self.assertTrue(store.ensure_migrated())
        self.assertEqual([e["ts"] for e in store.iter_events()], [100, 200, 300, 400, 500])
        self.assertFalse((d / "inbox.jsonl").exists())

    def test_bad_old_lines_are_skipped(self):
        d = self.write_old(sent=False)
        with (d / "inbox.jsonl").open("a") as f:
            f.write("{broken\n" + json.dumps({"chat": "self"}) + "\n")
        store.migrate()
        self.assertEqual(len(list(store.iter_events())), 4)
