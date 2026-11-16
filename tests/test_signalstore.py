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
