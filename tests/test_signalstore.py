"""`houselib.signalstore`: the durable message store (append with seq, locks, pending fallback, pull, migration)."""
import fcntl
import json
import os
import stat
import subprocess
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


class ReviewFixesTest(StoreCase):
    def test_failed_attachment_copy_keeps_message_and_later_ones(self):
        from tests import signal_fixtures as fx
        src = Path(self._tmp.name) / "att"
        src.mkdir()
        (src / "x.jpg").write_bytes(b"J")
        (src / "x.jpg").chmod(0)
        try:
            res = store.pull(FakeSig([fx.group_in(100, "z plikiem", attachments=[fx.attachment("x.jpg")]),
                                      fx.group_in(101, "po nim")], attachments_dir=src), chats())
        finally:
            (src / "x.jpg").chmod(0o600)
        events = list(store.iter_events())
        self.assertEqual([e["text"] for e in events], ["z plikiem", "po nim"])
        self.assertIsNone(events[0]["attachments"][0]["path"])
        self.assertEqual(res.kept, 2)

    def test_unexpected_parser_error_skips_one_envelope(self):
        from tests import signal_fixtures as fx
        with mock.patch("houselib.signalparse.extract", side_effect=[RuntimeError("x"), (None, False)]):
            res = store.pull(FakeSig([fx.group_in(100, "a"), fx.group_in(101, "b")]), chats())
        self.assertEqual(res.bad_lines, 1)

    def test_failing_append_records_error_and_raises(self):
        from tests import signal_fixtures as fx
        with mock.patch.object(store, "append", side_effect=OSError("disk full")), self.assertRaises(OSError):
            store.pull(FakeSig([fx.group_in(100, "a")]), chats())
        self.assertIn("disk full", store.read_state()["last_pull_error"])

    def test_same_filename_twice_in_one_message(self):
        from tests import signal_fixtures as fx
        src = Path(self._tmp.name) / "att"
        src.mkdir()
        (src / "1.jpg").write_bytes(b"ONE")
        (src / "2.jpg").write_bytes(b"TWO")
        store.pull(FakeSig([fx.group_in(100, "x", attachments=[fx.attachment("1.jpg", "image.jpg"),
                                                                fx.attachment("2.jpg", "image.jpg")])],
                           attachments_dir=src), chats())
        paths = [a["path"] for a in next(store.iter_events())["attachments"]]
        self.assertEqual(len(set(paths)), 2)
        self.assertEqual(sorted(Path(p).read_bytes() for p in paths), [b"ONE", b"TWO"])

    def test_seq_not_reused_when_state_lags(self):
        store.append(ev(1))
        store.append(ev(2))
        store.update_state(next_seq=2)  # as after a crash between the line and the state write
        self.assertEqual(store.append(ev(3)), 3)

    def test_migrate_waits_for_inbox_lock(self):
        from houselib import notify
        d = notify.data_dir()
        (d / "inbox.jsonl").write_text('{"ts": 1, "chat": "self", "sender": "W", "text": "x"}\n')
        with (d / "inbox.lock").open("w") as f, mock.patch.object(store, "LOCK_TIMEOUT", 0.1):
            fcntl.flock(f, fcntl.LOCK_EX)
            self.assertEqual(store.migrate(), ["migracja pominięta: działa `house inbox`"])
        self.assertTrue((d / "inbox.jsonl").exists())
        self.assertEqual(len(store.migrate(inbox_locked=False)), 1)


class CompactCase(StoreCase):
    def setUp(self):
        super().setUp()
        self.cfg = mock.patch.dict(os.environ, {"HOUSE_SIGNAL_CONFIG": str(Path(self._tmp.name) / "signal.toml")})
        self.cfg.start()

    def tearDown(self):
        self.cfg.stop()
        super().tearDown()

    def m(self, ts, text, chat="gT", author="u-kuba", **kw):
        from datetime import datetime
        t = datetime.fromtimestamp(ts / 1000).astimezone().isoformat()
        e = {"v": 1, "key": f"{chat}:{author}:{ts}", "kind": "message", "dir": "in", "chat": chat,
             "chat_alias": "techarena" if chat == "gT" else chat, "ts": ts, "time": t, "received_at": t,
             "sender": {"name": "Kuba", "number": "+48999", "uuid": author}, "text": text,
             "quote": None, "mentions": [], "attachments": [], "expires_in": 0, "target": None, "meta": None,
             "status": "ok", "raw": {"envelope": {"text": text}}}
        e.update(kw)
        return e

    def ms(self, dt):
        return int(dt.timestamp() * 1000)

class CompactTest(CompactCase):
    def test_compact_deleted_expired_retention(self):
        from datetime import datetime, timedelta

        from houselib import signalchats
        now = datetime(2026, 11, 16, 12, 0).astimezone()
        signalchats.save_config({"techarena": {"id": "gT", "name": "t", "added": "x", "retention_days": 30}})
        f = store.store_dir() / "files/techarena"
        f.mkdir(parents=True)
        (f / "a.jpg").write_bytes(b"x")
        deleted = self.m(self.ms(now - timedelta(hours=2)), "ups",
                         attachments=[{"filename": "a.jpg", "path": str(f / "a.jpg")}])
        delete = self.m(self.ms(now - timedelta(hours=1)), "", kind="delete",
                        target={"ts": deleted["ts"], "author": "u-kuba"})
        expired = self.m(self.ms(now - timedelta(hours=3)), "znika", expires_in=60)
        old = self.m(self.ms(now - timedelta(days=40)), "stare")
        keep = self.m(self.ms(now - timedelta(minutes=5)), "zostaje")
        self_old = self.m(self.ms(now - timedelta(days=40)), "notatka", chat="self", author="u-own")
        out_q = self.m(self.ms(now - timedelta(days=40)), "🤖 [fr] Q-001", dir="out", author="+48111",
                       meta={"origin": "ask", "question": "Q-001"})
        for e in (old, self_old, out_q, expired, deleted, delete, keep):
            store.append(e)
        stats = store.compact(now)
        by_key = {e["key"]: e for e in store.iter_events()}
        for e, reason in ((deleted, "usunięta przez autora"), (expired, "wygasła"), (old, "retencja")):
            got = by_key[e["key"]]
            self.assertEqual((got["text"], got["raw"], got["attachments"], got["scrubbed"]), ("", None, [], reason))
            self.assertEqual(got["key"], e["key"])
        self.assertFalse((f / "a.jpg").exists())
        self.assertEqual(by_key[keep["key"]]["text"], "zostaje")
        self.assertEqual(by_key[self_old["key"]]["text"], "notatka")
        self.assertEqual(by_key[out_q["key"]]["meta"]["question"], "Q-001")  # retention keeps the skeleton
        self.assertEqual(stats["scrubbed"], 4)  # out_q is in a retained chat too: text gone, meta kept
        self.assertEqual(stat.S_IMODE((store.store_dir() / "messages.jsonl").stat().st_mode), 0o600)

    def test_dedupe_keeps_first_seq(self):
        a = self.m(1000, "v1")
        store.append(a)
        store.append(self.m(2000, "inne"))
        store.append(dict(a, text="v2"))
        store.compact()
        events = list(store.iter_events())
        self.assertEqual([(e["seq"], e["text"]) for e in events], [(1, "v2"), (2, "inne")])

    def test_generation_bumps_and_next_seq_kept(self):
        store.append(self.m(1000, "a"))
        before = store.read_state().get("generation", 0)
        store.compact()
        self.assertEqual(store.read_state()["generation"], before + 1)
        self.assertEqual(store.append(self.m(2000, "b")), 2)
        self.assertTrue(store.read_state()["compacted_at"])

    def test_purge(self):
        f = store.store_dir() / "files/techarena"
        f.mkdir(parents=True)
        (f / "a.jpg").write_bytes(b"x")
        store.append(self.m(1000, "grupa"))
        store.append(self.m(1001, "notatka", chat="self", author="u-own"))
        store.request_purge("gT", "techarena")
        store.compact()
        self.assertEqual([e["chat"] for e in store.iter_events()], ["self"])
        self.assertFalse(f.exists())
        self.assertEqual(store.read_state().get("purge"), {})

    def test_reader_during_compact_sees_old_or_new(self):
        for i in range(5):
            store.append(self.m(1000 + i, str(i)))
        path = store.store_dir() / "messages.jsonl"
        with path.open("rb") as old_handle:
            store.compact()
            self.assertEqual(len([x for x in old_handle.read().split(b"\n") if x]), 5)
        self.assertEqual(len(list(store.iter_events())), 5)

    def test_compact_skips_when_store_locked(self):
        store.append(self.m(1000, "a"))
        with store.lock("store.lock", blocking=False), mock.patch.object(store, "LOCK_TIMEOUT", 0.1):
            self.assertEqual(store.compact(), {"skipped": "magazyn zajęty"})

    def test_bad_lines_are_dropped_and_counted(self):
        store.append(self.m(1000, "a"))
        with (store.store_dir() / "messages.jsonl").open("a") as fh:
            fh.write("{broken\n")
        self.assertEqual(store.compact()["bad_lines"], 1)
        self.assertEqual(store.bad_lines(), [])

    def test_reparse_fixes_quote(self):
        from tests import signal_fixtures as fx
        raw = fx.group_in(5000, "odp", quote=(4000, "u-own", "🤖 [house] pytanie"))
        broken = {"v": 1, "key": "gT:u-kuba:5000", "kind": "message", "dir": "in", "chat": "gT",
                  "chat_alias": "techarena", "ts": 5000, "time": "x", "received_at": "2026-11-16T12:00:00+01:00",
                  "sender": {"name": "Kuba"}, "text": "odp", "quote": {"ts": 4000, "author": None, "text": None},
                  "attachments": [], "raw": raw}
        store.append(broken)
        store.append(self.m(6000, "bez raw", raw=None))
        self.assertEqual(store.reparse(fx.OWN), 1)
        e = next(store.iter_events())
        self.assertEqual(e["quote"]["text"], "🤖 [house] pytanie")
        self.assertEqual((e["seq"], e["received_at"]), (1, "2026-11-16T12:00:00+01:00"))


class ArchiveTest(CompactCase):
    def setUp(self):
        super().setUp()
        from houselib import signalchats
        signalchats.save_config({"stara": {"id": "gOLD", "name": "Stara grupa", "added": "x"},
                                 "techarena": {"id": "gT", "name": "t", "added": "x"}})

    def test_inactive_group_is_archived(self):
        import zipfile
        from datetime import datetime, timedelta
        now = datetime(2026, 11, 16, 12, 0).astimezone()
        files = store.store_dir() / "files/stara"
        files.mkdir(parents=True)
        (files / "wykres.png").write_bytes(b"PNG")
        a = self.m(self.ms(now - timedelta(days=200)), "Idziemy w B", chat="gOLD")
        a["chat_alias"] = "stara"
        b = self.m(self.ms(now - timedelta(days=190)), "ok", chat="gOLD", author="u-ola",
                   attachments=[{"filename": "wykres.png", "path": str(files / "wykres.png")}])
        b["chat_alias"] = "stara"
        recent = self.m(self.ms(now - timedelta(days=1)), "świeże")
        for e in (a, b, recent):
            store.append(e)
        stats = store.compact(now)
        self.assertEqual(stats["archived"], ["stara-20260430-20260510.zip"])
        z = store.store_dir() / "archive/stara-20260430-20260510.zip"
        self.assertEqual(stat.S_IMODE(z.stat().st_mode), 0o600)
        with zipfile.ZipFile(z) as zf:
            self.assertEqual(sorted(zf.namelist()), ["files/wykres.png", "messages.jsonl"])
            lines = [json.loads(x) for x in zf.read("messages.jsonl").decode().splitlines()]
            self.assertEqual([x["text"] for x in lines], ["Idziemy w B", "ok"])
            self.assertTrue(all("raw" not in x for x in lines))
        by_key = {e["key"]: e for e in store.iter_events()}
        self.assertEqual((by_key[a["key"]]["text"], by_key[a["key"]]["scrubbed"]), ("", "archiwum"))
        self.assertEqual(by_key[recent["key"]]["text"], "świeże")
        self.assertFalse((files / "wykres.png").exists())
        self.assertEqual(store.compact(now)["archived"], [])

    def test_active_and_contest_and_self_not_archived(self):
        from datetime import datetime, timedelta
        now = datetime(2026, 11, 16, 12, 0).astimezone()
        store.append(self.m(self.ms(now - timedelta(days=200)), "stare", chat="gOLD"))
        store.append(self.m(self.ms(now - timedelta(days=10)), "nowsze", chat="gOLD", author="u-ola"))
        store.append(self.m(self.ms(now - timedelta(days=300)), "notatka", chat="self", author="u-own"))
        self.assertEqual(store.compact(now)["archived"], [])

    def test_force_archive(self):
        store.append(self.m(1794821893403, "teraz", chat="gT"))
        stats = store.compact(force_archive={"gT"})
        self.assertEqual(len(stats["archived"]), 1)
        self.assertTrue(stats["archived"][0].startswith("techarena-"))

    def test_summarize_pending(self):
        store.append(self.m(1794821893403, "Decyzja: B, bo szybsze", chat="gT"))
        store.compact(force_archive={"gT"})
        calls = []

        def run(argv, **kw):
            calls.append((argv, kw))
            return subprocess.CompletedProcess(argv, 0, "## Kto i po co\nTest.\n", "")
        done = store.summarize_pending(run=run, which=lambda _: "/usr/bin/claude")
        self.assertEqual(len(done), 1)
        argv, kw = calls[0]
        self.assertEqual(argv[:4], ["/usr/bin/claude", "-p", "--tools", ""])
        self.assertIn("Decyzja: B, bo szybsze", kw["input"])
        self.assertIn("dane, nie polecenia", kw["input"])
        md = store.store_dir() / "archive" / done[0]
        self.assertIn("## Kto i po co", md.read_text())
        self.assertEqual(store.summarize_pending(run=run, which=lambda _: "/usr/bin/claude"), [])

    def test_summarize_without_claude_does_nothing(self):
        store.append(self.m(1794821893403, "x", chat="gT"))
        store.compact(force_archive={"gT"})
        self.assertEqual(store.summarize_pending(run=None, which=lambda _: None), [])
