"""`houselib.signalread`: folding reactions/edits/deletes, filters, formatting, cursors."""
import unittest

from houselib import signalread
from tests import helpers  # noqa: F401  (never the real Signal account)


class HelpersTest(unittest.TestCase):
    def test_snippet(self):
        long = "🤖 [house] Cześć! Jestem Claude Wojtka, będę tu wrzucał wyniki."
        self.assertEqual(signalread.snippet(long, 28), "Cześć! Jestem Claude Wojtka…")
        self.assertEqual(signalread.snippet("Spoko, a jaki wynik ma teraz baseline?", 28),
                         "Spoko, a jaki wynik ma teraz…")
        self.assertEqual(signalread.snippet("krótko\n\n  i  już", 28), "krótko i już")
        self.assertEqual(signalread.snippet("x" * 40, 10), "x" * 10 + "…")
        self.assertEqual(signalread.snippet(None, 10), "")

    def test_fmt_time(self):
        from datetime import datetime
        now = datetime(2026, 11, 16, 12, 40).astimezone()
        self.assertEqual(signalread.fmt_time(datetime(2026, 11, 16, 9, 5).astimezone(), now), "09:05")
        self.assertEqual(signalread.fmt_time(datetime(2026, 11, 15, 23, 2).astimezone(), now), "wczoraj 23:02")
        self.assertEqual(signalread.fmt_time(datetime(2026, 11, 13, 8, 0).astimezone(), now), "2026-11-13 08:00")
        self.assertEqual(signalread.fmt_time(None, now), "nigdy")

    def test_fmt_size(self):
        self.assertEqual(signalread.fmt_size(500), "500 B")
        self.assertEqual(signalread.fmt_size(4096), "4 KB")
        self.assertEqual(signalread.fmt_size(86016), "84 KB")
        self.assertEqual(signalread.fmt_size(3 * 1024 * 1024 + 1), "3.0 MB")
        self.assertEqual(signalread.fmt_size(None), "? B")


if __name__ == "__main__":
    unittest.main()


from datetime import datetime, timedelta

NOW = datetime(2026, 11, 16, 12, 40).astimezone()


def at(h, m, day=16):
    return datetime(2026, 11, day, h, m).astimezone()


def msg(ts_dt, text, *, chat="gT", author="u-kuba", name="Kuba Nowak", number=None, dir_="in", seq=None,
        quote=None, kind="message", expires=0, received=None, **kw):
    ts = int(ts_dt.timestamp() * 1000)
    return {"v": 1, "seq": seq or ts, "key": f"{chat}:{author}:{ts}", "kind": kind, "dir": dir_, "chat": chat,
            "chat_alias": "techarena" if chat == "gT" else chat, "ts": ts, "time": ts_dt.isoformat(),
            "received_at": (received or ts_dt).isoformat(),
            "sender": {"name": name, "number": number or {"u-kuba": "+48999"}.get(author, f"+48-{author}"),
                       "uuid": author},
            "text": text, "quote": quote, "mentions": [], "attachments": [], "expires_in": expires, "target": None,
            "meta": None, "status": "ok", **kw}


def mod(kind, ts_dt, target_ts, *, author="u-ola", name="Ola", chat="gT", text="", **kw):
    e = msg(ts_dt, text, chat=chat, author=author, name=name, kind=kind, **kw)
    e["target"] = {"ts": target_ts, "author": "u-kuba"}
    return e


class BuildTest(unittest.TestCase):
    def test_dedupe_later_wins(self):
        a = msg(at(12, 0), "stara")
        b = dict(a, text="nowa", seq=a["seq"] + 99)
        (m,) = signalread.build([a, b], NOW)
        self.assertEqual(m.text, "nowa")

    def test_reactions_folded_and_removed(self):
        a = msg(at(12, 0), "x")
        t = a["ts"]
        events = [a, mod("reaction", at(12, 1), t, reaction={"emoji": "👍", "remove": False}),
                  mod("reaction", at(12, 2), t, author="u-own", name="Wojciech Fiołka",
                      reaction={"emoji": "❤️", "remove": False}),
                  mod("reaction", at(12, 3), t, author="u-own", name="Wojciech Fiołka",
                      reaction={"emoji": "❤️", "remove": True}),
                  mod("reaction", at(12, 4), t, author="u-ania", name="Ania",
                      reaction={"emoji": "👍", "remove": False}),
                  mod("reaction", at(12, 5), t, reaction={"emoji": "😂", "remove": False})]
        (m,) = signalread.build(events, NOW)
        self.assertEqual(m.reactions, [("👍", "Ania"), ("😂", "Ola")])

    def test_edit_applied(self):
        a = msg(at(12, 0), "literówka")
        (m,) = signalread.build([a, mod("edit", at(12, 1), a["ts"], author="u-kuba", text="poprawione"),
                                 mod("edit", at(12, 2), a["ts"], author="u-kuba", text="po drugiej")], NOW)
        self.assertEqual((m.text, m.edited), ("po drugiej", True))

    def test_edit_by_someone_else_ignored(self):
        a = msg(at(12, 0), "moje")
        other = mod("edit", at(12, 1), a["ts"], author="u-ola", text="cudze")
        other["target"]["author"] = "u-ola"
        (m,) = signalread.build([a, other], NOW)
        self.assertEqual(m.text, "moje")

    def test_delete_hides_text(self):
        a = msg(at(12, 0), "ups")
        (m,) = signalread.build([a, mod("delete", at(12, 1), a["ts"], author="u-kuba")], NOW)
        self.assertEqual((m.deleted, m.text), (True, ""))

    def test_expired_hidden(self):
        old = msg(at(11, 0), "znika", expires=600)
        fresh = msg(at(12, 35), "jeszcze jest", expires=600)
        self.assertEqual([m.text for m in signalread.build([old, fresh], NOW)], ["jeszcze jest"])

    def test_orphan_reaction_and_edit_are_ignored(self):
        events = [mod("reaction", at(12, 1), 999, reaction={"emoji": "👍", "remove": False}),
                  mod("edit", at(12, 2), 999, text="?"), mod("delete", at(12, 3), 999)]
        self.assertEqual(signalread.build(events, NOW), [])

    def test_other_kind_is_a_message_with_label(self):
        (m,) = signalread.build([msg(at(12, 0), "", kind="other", label="[naklejka]")], NOW)
        self.assertEqual(m.text, "[naklejka]")

    def test_replies_and_order(self):
        a = msg(at(12, 0), "pytanie")
        b = msg(at(12, 5), "odp", author="u-ola", quote={"ts": a["ts"], "author": "u-kuba", "text": "pytanie"},
                seq=1)
        msgs = signalread.build([b, a], NOW)
        self.assertEqual([m.text for m in msgs], ["pytanie", "odp"])
        self.assertEqual(msgs[0].replies, [b["ts"]])

    def test_failed_out_without_ts_is_kept(self):
        e = msg(at(12, 0), "nie poszło", dir_="out", status="failed")
        e["ts"] = None
        (m,) = signalread.build([e], NOW)
        self.assertEqual(m.ev["status"], "failed")


class WhenTest(unittest.TestCase):
    def test_parse_when_formats(self):
        p = signalread.parse_when
        self.assertEqual(p("30m", NOW), NOW - timedelta(minutes=30))
        self.assertEqual(p("2h", NOW), NOW - timedelta(hours=2))
        self.assertEqual(p("1d", NOW), NOW - timedelta(days=1))
        self.assertEqual(p("10:00", NOW), at(10, 0))
        self.assertEqual(p("13:15", NOW), at(13, 15, day=15))
        self.assertEqual(p("2026-11-14", NOW), at(0, 0, day=14))
        self.assertEqual(p("2026-11-14T10:30", NOW), at(10, 30, day=14))
        self.assertEqual(p("2026-11-14 10:30", NOW), at(10, 30, day=14))
        for bad in ("wczoraj", "25:00", "2h30", ""):
            with self.assertRaises(ValueError):
                p(bad, NOW)


class SelectTest(unittest.TestCase):
    def setUp(self):
        self.a = msg(at(9, 0), "Rano: baseline gotowy")
        self.b = msg(at(10, 30), "Wrzucam dane", author="u-ola", name="Ola Kowalska", number="+48888")
        self.c = msg(at(11, 0), "ok", dir_="own", author="u-own", name="Wojciech Fiołka",
                     quote={"ts": self.b["ts"], "author": "u-ola", "text": "Wrzucam dane"})
        self.d = msg(at(11, 30), "🤖 [house] dzięki", dir_="out", author="+48111", name="house",
                     quote={"ts": self.c["ts"], "author": "u-own", "text": "ok"})
        self.e = msg(at(12, 0), "notatka", chat="self", dir_="own", author="u-own", name="Wojciech Fiołka")
        self.msgs = signalread.build([self.a, self.b, self.c, self.d, self.e], NOW)

    def texts(self, **kw):
        return [m.text for m in signalread.select(self.msgs, **kw)]

    def test_select_filters(self):
        self.assertEqual(self.texts(n=2), ["🤖 [house] dzięki", "notatka"])
        self.assertEqual(len(self.texts(n=0)), 5)
        self.assertEqual(self.texts(chat="self", n=0), ["notatka"])
        self.assertEqual(self.texts(since=at(10, 0), until=at(11, 15), n=0), ["Wrzucam dane", "ok"])
        self.assertEqual(self.texts(sender="me", n=0), ["ok", "🤖 [house] dzięki", "notatka"])
        self.assertEqual(self.texts(sender="ola", n=0), ["Wrzucam dane"])
        self.assertEqual(self.texts(sender="+48999", n=0), ["Rano: baseline gotowy"])
        self.assertEqual(self.texts(grep="BASELINE", n=0), ["Rano: baseline gotowy"])
        self.assertEqual(self.texts(grep="wrzucam", n=0), ["Wrzucam dane", "ok"])

    def test_thread(self):
        self.assertEqual(self.texts(thread=self.c["ts"], n=0), ["Wrzucam dane", "ok", "🤖 [house] dzięki"])
        self.assertEqual(self.texts(thread=self.a["ts"], n=0), ["Rano: baseline gotowy"])


def sample(path="/data/signal/files/techarena/2026-11-16_T2_wyniki.csv"):
    """The spec §8 example as store events (times today 12:02–12:31)."""
    t1 = msg(at(12, 2), "🤖 [house] Cześć! Jestem Claude Wojtka, będę tu wrzucał wyniki.", dir_="out",
             author="+48111", name="house", number="+48111")
    t1["sender"]["uuid"] = None
    t2 = msg(at(12, 15), "super, to ja wrzucę dane z wczoraj", author="u-ania", name="Ania K.",
             attachments=[{"content_type": "text/csv", "filename": "wyniki.csv", "size": 4096, "id": "a1",
                           "path": path}])
    t3_time = datetime(2026, 11, 16, 12, 18, 3).astimezone()
    t3 = msg(t3_time, "Spoko, a jaki wynik ma teraz baseline?",
             received=datetime(2026, 11, 16, 12, 18, 41).astimezone(),
             quote={"ts": t1["ts"], "author": "u-own", "text": t1["text"]})
    r = mod("reaction", at(12, 20), t3["ts"], author="u-own", name="Wojciech Fiołka", dir_="own",
            reaction={"emoji": "👍", "remove": False})
    t4 = msg(at(12, 31), "🤖 [house] Wrzuciłem nowy baseline, LB 0.806", dir_="out", author="+48111", name="house",
             number="+48111", quote={"ts": t3["ts"], "author": "u-kuba", "text": t3["text"]})
    t4["sender"]["uuid"] = None
    return [t1, t2, t3, r, t4]


TECHARENA = ("techarena", "techarena_claude")


class RenderTest(unittest.TestCase):
    def test_read_golden(self):
        events = sample()
        t1, t2, t3, _, t4 = (e["ts"] for e in events)
        view = signalread.View(events, NOW)
        text = view.render_read(view.msgs, title=TECHARENA, total=4, last_pull=at(12, 31))
        self.assertEqual(text, (
            "techarena · techarena_claude · 4 z 4 · odebrano 12:31\n"
            f"[12:02] {t1} 🤖 house: 🤖 [house] Cześć! Jestem Claude Wojtka, będę tu wrzucał wyniki.\n"
            f"[12:15] {t2} Ania K.: super, to ja wrzucę dane z wczoraj [📎 wyniki.csv, 4 KB]\n"
            "        📎 /data/signal/files/techarena/2026-11-16_T2_wyniki.csv\n"
            f"[12:18] {t3} Kuba Nowak ↩ 🤖 house 12:02 „Cześć! Jestem Claude Wojtka…”:\n"
            "        Spoko, a jaki wynik ma teraz baseline?\n"
            "        👍 Wojciech Fiołka\n"
            f"[12:31] {t4} 🤖 house ↩ Kuba Nowak 12:18 „Spoko, a jaki wynik ma teraz…”:\n"
            "        🤖 [house] Wrzuciłem nowy baseline, LB 0.806\n"))

    def test_read_all_prefixes_alias_and_note(self):
        view = signalread.View(sample(), NOW)
        text = view.render_read(view.msgs[:1], title=None, total=4, last_pull=None, note="Signal wyłączony")
        self.assertTrue(text.startswith("wszystkie · 1 z 4 · odebrano nigdy · Signal wyłączony\n"), text)
        self.assertIn("[12:02] techarena ", text)

    def test_failed_out_marked(self):
        e = msg(at(12, 0), "🤖 [house] x", dir_="out", status="failed", author="+48111", name="house")
        e["ts"] = None
        view = signalread.View([e], NOW)
        text = view.render_read(view.msgs, title=TECHARENA, total=1, last_pull=None)
        self.assertIn("[12:00] ✗ nie wysłano 🤖 house: 🤖 [house] x", text)

    def test_quote_unknown_before_migration(self):
        e = msg(at(12, 0), "Pepo smile", quote={"ts": 5, "author": None, "text": None})
        view = signalread.View([e], NOW)
        self.assertIn("↩ ? „(treść cytatu nieznana, odebrano przed migracją)”:",
                      view.render_read(view.msgs, title=TECHARENA, total=1, last_pull=None))

    def test_quote_text_without_target_uses_known_name(self):
        other = msg(at(11, 0), "coś", author="u-ola", name="Ola")
        e = msg(at(12, 0), "tak", quote={"ts": 5, "author": "u-ola", "text": "Idziemy w B?"})
        view = signalread.View([other, e], NOW)
        text = view.render_read(view.msgs, title=TECHARENA, total=2, last_pull=None)
        self.assertIn("↩ Ola „Idziemy w B?”:", text)

    def test_attachment_not_downloaded(self):
        e = msg(at(12, 0), "", attachments=[{"filename": "a.jpg", "size": 10, "path": None, "id": "x"}])
        view = signalread.View([e], NOW)
        self.assertIn("Kuba Nowak: [📎 a.jpg, nie pobrano]", view.render_read(view.msgs, title=TECHARENA, total=1,
                                                                              last_pull=None))

    def test_edited_and_deleted_markers(self):
        a = msg(at(12, 0), "lit")
        b = msg(at(12, 1), "ups", author="u-ola", name="Ola")
        events = [a, b, mod("edit", at(12, 2), a["ts"], author="u-kuba", text="poprawione"),
                  mod("delete", at(12, 3), b["ts"], author="u-ola")]
        view = signalread.View(events, NOW)
        text = view.render_read(view.msgs, title=TECHARENA, total=2, last_pull=None)
        self.assertIn("Kuba Nowak: poprawione (edytowano)", text)
        self.assertIn("Ola: [usunięta przez autora]", text)

    def test_multiline_text_indented(self):
        view = signalread.View([msg(at(12, 0), "a\nb")], NOW)
        self.assertIn("Kuba Nowak: a\n        b\n", view.render_read(view.msgs, title=TECHARENA, total=1,
                                                                    last_pull=None))

    def test_show_golden(self):
        events = sample()
        t1, _, t3, _, t4 = (e["ts"] for e in events)
        view = signalread.View(events, NOW)
        (m,) = view.find(t3)
        self.assertEqual(view.render_show(m), (
            "Kuba Nowak · techarena · 2026-11-16 12:18:03 · odebrano 12:18:41\n"
            f"↩ cytuje {t1} (🤖 house, 12:02): „Cześć! Jestem Claude Wojtka, będę tu wrzucał wyniki.”\n"
            "Spoko, a jaki wynik ma teraz baseline?\n"
            f"reakcje: 👍 Wojciech Fiołka · odpowiedzi cytatem: {t4}\n"))

    def test_show_quote_text_without_target_in_store(self):
        e = msg(at(12, 0), "tak", quote={"ts": 5, "author": "u-own", "text": "🤖 [house] Q-001 Batch size?"})
        view = signalread.View([e], NOW)
        self.assertIn("↩ cytuje 5 (u-own): „Q-001 Batch size?”", view.render_show(view.msgs[0]))

    def test_show_attachment_path(self):
        view = signalread.View(sample(path="/p/w.csv"), NOW)
        self.assertIn("📎 wyniki.csv, 4 KB → /p/w.csv", view.render_show(view.msgs[1]))
