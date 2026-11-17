"""`houselib.signalagent`: @claude requests from Signal (parsing, routing, jobs, launch, tick, replies)."""
import unittest
from datetime import datetime

from houselib import signalagent as agent
from houselib import signalparse
from tests import helpers  # noqa: F401  (never the real Signal account)
from tests import signal_fixtures as fx

CHATS = {"self": "self", fx.GID: "techarena"}
NOW = datetime(2026, 11, 16, 12, 40)


def ev(obj, seq=1):
    e, _ = signalparse.extract(obj, fx.OWN, CHATS, now=NOW)
    e["seq"] = seq
    return e


class ParseTest(unittest.TestCase):
    def test_trigger_positions(self):
        for text in ("@claude popraw testy", "hej @claude popraw testy", "popraw testy @claude", "@Claude x",
                     "@CLAUDE x", "(@claude) x"):
            self.assertIsInstance(agent.parse(ev(fx.group_in(1000, text))), agent.Request, text)
        for text in ("napisz na email@claude.ai", "x@claude", "@claudette", "claude zrób"):
            self.assertIsNone(agent.parse(ev(fx.group_in(1000, text))), text)

    def test_request_fields(self):
        obj = fx.group_in(1000, "@claude popraw testy", quote=(900, "u-own", "🤖 [fr] wynik"))
        obj["envelope"]["serverReceivedTimestamp"] = 1234
        r = agent.parse(ev(obj))
        self.assertEqual((r.key, r.chat, r.chat_alias, r.ts, r.author, r.author_name, r.own),
                         (f"{fx.GID}:u-kuba:1000", fx.GID, "techarena", 1000, "u-kuba", "Kuba Nowak", False))
        self.assertEqual((r.text, r.alias_hint, r.quote_ts, r.received_ms), ("popraw testy", None, 900, 1234))

    def test_mention_placeholder_is_not_a_trigger(self):
        obj = fx.group_in(1000, "￼ zobacz to", mentions=[{"name": "claude", "number": "+1", "uuid": "u1",
                                                               "start": 0, "length": 1}])
        e = ev(obj)
        self.assertEqual(e["text"], "@claude zobacz to")
        self.assertIsNone(agent.parse(e))

    def test_quote_text_does_not_trigger(self):
        self.assertIsNone(agent.parse(ev(fx.group_in(1000, "ok", quote=(900, "u-x", "@claude zrób")))))

    def test_house_messages_never_trigger(self):
        own_house = ev(fx.group_own(1000, "🤖 [house] przyjąłem: @claude popraw testy"))
        self.assertIsNone(agent.parse(own_house))
        out = {"v": 1, "seq": 2, "key": "gT:+48111:5", "kind": "message", "dir": "out", "chat": fx.GID,
               "chat_alias": "techarena", "ts": 5, "text": "🤖 [house] @claude", "raw": None,
               "sender": {"name": "house", "number": fx.OWN, "uuid": None}}
        self.assertIsNone(agent.parse(out))

    def test_explicit_host(self):
        for text, host, rest in (("@claude@dom zrób X", "dom", "zrób X"), ("@Claude@DOM x", "dom", "x"),
                                 ("popraw to @claude@praca", "praca", "popraw to"),
                                 ("@claude@ zrób", None, "zrób"), ("@claude zrób", None, "zrób")):
            r = agent.parse(ev(fx.group_in(1000, text)))
            self.assertEqual((r.host, r.text), (host, rest), text)
        r = agent.parse(ev(fx.group_in(1000, "@claude@bardzo-dlugi-komputer-xyz zrób")))
        self.assertEqual((r.host, r.text), ("bardzo-dlugi-komputer-xyz", "zrób"))
        self.assertIsNone(agent.parse(ev(fx.group_in(1000, "pisz na x@claude.ai"))))

    def test_trigger_keeps_other_mentions(self):
        self.assertEqual(agent.parse(ev(fx.group_in(1000, "@claude @kuba zrób"))).text, "@kuba zrób")
        self.assertEqual(agent.parse(ev(fx.group_in(1000, "zrób @claude@ to"))).text, "zrób to")

    def test_command_with_host(self):
        c = agent.parse(ev(fx.group_in(1000, "/status@praca")))
        self.assertEqual((c.name, c.host), ("status", "praca"))
        self.assertIsNone(agent.parse(ev(fx.group_in(1000, "/status"))).host)
        c = agent.parse(ev(fx.self_note(1000, "/agent@dom on")))
        self.assertEqual((c.name, c.host), ("agent-on", "dom"))
        self.assertIsNone(agent.parse(ev(fx.group_in(1000, "/statusy"))))

    def test_edit_never_triggers(self):
        self.assertIsNone(agent.parse(ev(fx.edit(1100, 1000, "@claude zrób"))))

    def test_alias_hint(self):
        r = agent.parse(ev(fx.group_in(1000, "@claude fr: popraw X")))
        self.assertEqual((r.alias_hint, r.text), ("fr", "popraw X"))
        r = agent.parse(ev(fx.group_in(1000, "@claude Uwaga: zrób X")))
        self.assertEqual((r.alias_hint, r.text), ("uwaga", "zrób X"))
        self.assertEqual(r.full_text, "Uwaga: zrób X")

    def test_own_request_in_self(self):
        r = agent.parse(ev(fx.self_note(1000, "@claude sprawdź testy")))
        self.assertTrue(r.own)
        self.assertEqual(r.author, "u-own")

    def test_commands(self):
        c = agent.parse(ev(fx.group_in(1000, "/status")))
        self.assertEqual((type(c), c.name, c.chat, c.own), (agent.Command, "status", fx.GID, False))
        self.assertEqual(agent.parse(ev(fx.group_in(1000, " /LB "))).name, "lb")
        c = agent.parse(ev(fx.group_in(1000, "/stop", quote=(900, "u-own", "🤖 przyjąłem"))))
        self.assertEqual((c.name, c.quote_ts), ("stop", 900))
        self.assertEqual(agent.parse(ev(fx.self_note(1000, "/agent off"))).name, "agent-off")
        self.assertEqual(agent.parse(ev(fx.self_note(1000, "/agent on"))).name, "agent-on")
        self.assertIsNone(agent.parse(ev(fx.group_in(1000, "/agent off"))))
        self.assertIsNone(agent.parse(ev(fx.group_in(1000, "/statusy"))))

    def test_reaction_and_other_kinds_ignored(self):
        self.assertIsNone(agent.parse(ev(fx.reaction(1000, "👍", 900, "u-own"))))
        self.assertIsNone(agent.parse(ev(fx.sticker(1000))))


if __name__ == "__main__":
    unittest.main()


class RouteTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self.fr, self.fi = self.contest(tmp / "fr-2026", "fr"), self.contest(tmp / "fi-2026", "fi")

    def tearDown(self):
        self._tmp.cleanup()

    def contest(self, path, alias):
        (path / ".house").mkdir(parents=True)
        (path / ".house/spec.yaml").write_text(f"notify:\n  alias: {alias}\n")
        return path

    def chats(self, *contests):
        from houselib.signalchats import Chat
        return {"self": Chat("self", "self", "n", "zawsze"),
                fx.GID: Chat(fx.GID, "team", "Team", "konkurs x", tuple(contests))}

    def req(self, text, chat=fx.GID, quote=None):
        obj = fx.group_in(1000, text, gid=chat, quote=quote) if chat != "self" else fx.self_note(1000, text)
        return agent.parse(ev(obj))

    def test_route_single_contest(self):
        r = agent.route(self.req("@claude zrób X"), self.chats(self.fr), {})
        self.assertEqual((r.repo, r.contest, r.text, r.asks), (self.fr, self.fr, "zrób X", []))

    def test_route_quote_of_house_message(self):
        out = {900: {"meta": {"contest": "fi-2026"}}}
        r = agent.route(self.req("@claude zrób X", quote=(900, "u-own", "🤖 [fi] wynik")),
                        self.chats(self.fr, self.fi), out)
        self.assertEqual(r.contest, self.fi)

    def test_route_alias_hint(self):
        r = agent.route(self.req("@claude fi: zrób X"), self.chats(self.fr, self.fi), {})
        self.assertEqual((r.contest, r.text), (self.fi, "zrób X"))

    def test_alias_hint_unknown_is_part_of_the_request(self):
        r = agent.route(self.req("@claude uwaga: zrób X"), self.chats(self.fr), {})
        self.assertEqual((r.contest, r.text), (self.fr, "uwaga: zrób X"))

    def test_route_ambiguous_asks_each_contest(self):
        r = agent.route(self.req("@claude zrób X"), self.chats(self.fr, self.fi), {})
        self.assertIsNone(r.repo)
        self.assertEqual([c for c, _ in r.asks], [self.fr, self.fi])
        self.assertTrue(all("odpowiedz cytatem" in t for _, t in r.asks))

    def test_route_self_and_toml_group_to_house(self):
        from houselib import HOUSE_ROOT
        r = agent.route(self.req("@claude zrób X", chat="self"), self.chats(), {})
        self.assertEqual((r.repo, r.contest), (HOUSE_ROOT, None))
        r = agent.route(self.req("@claude zrób X"), self.chats(), {})
        self.assertEqual(r.repo, HOUSE_ROOT)

    def test_quote_of_other_contest_not_in_chat_ignored(self):
        out = {900: {"meta": {"contest": "obcy"}}}
        r = agent.route(self.req("@claude x", quote=(900, "u-own", "🤖")), self.chats(self.fr), out)
        self.assertEqual(r.contest, self.fr)


class JobStoreCase(unittest.TestCase):
    def setUp(self):
        import os
        import tempfile
        from pathlib import Path
        from unittest import mock
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.env = mock.patch.dict(os.environ, {"HOUSE_DATA_DIR": str(self.tmp / "data"),
                                                "HOUSE_SIGNAL_CONFIG": str(self.tmp / "signal.toml")})
        self.env.start()
        self.store = agent.JobStore()
        self.store.set_enabled(True)
        self.store.set_members(fx.GID, ["u-kuba", "u-ola"])
        self.repo = self.tmp / "repo"

    def tearDown(self):
        self.env.stop()
        self._tmp.cleanup()

    def req(self, text="@claude zrób X", ts=1000, uuid="u-kuba", received=None, chat=fx.GID):
        obj = fx.group_in(ts, text, uuid=uuid, gid=chat) if chat != "self" else fx.self_note(ts, text)
        obj["envelope"]["serverReceivedTimestamp"] = received if received is not None else \
            int(datetime.now().timestamp() * 1000)
        return agent.parse(ev(obj))


class JobStoreTest(JobStoreCase):
    def test_add_is_idempotent(self):
        r = self.req()
        job = self.store.add(r, self.repo, None, "queued")
        self.assertEqual((job.status, job.chat, job.text, job.repo), ("queued", fx.GID, "zrób X", str(self.repo)))
        self.assertIsNone(self.store.add(r, self.repo, None, "queued"))
        self.assertEqual(len(self.store.all()), 1)
        audit = (self.store.dir / "jobs.jsonl").read_text().splitlines()
        self.assertEqual(len(audit), 1)
        self.assertNotIn("zrób X", audit[0])  # audit keeps a hash of the text, not the text

    def test_update_and_queries(self):
        a = self.store.add(self.req(ts=1), self.repo, None, "queued")
        b = self.store.add(self.req(ts=2), self.repo, None, "queued")
        self.store.update(a.id, status="running", unit="house-agent-x")
        self.assertEqual([j.id for j in self.store.running()], [a.id])
        self.assertEqual([j.id for j in self.store.queued(self.repo)], [b.id])
        self.assertEqual(self.store.get(a.id).unit, "house-agent-x")

    def test_admit_rules(self):
        now = datetime(2026, 11, 16, 12, 0).astimezone()
        ms = int(now.timestamp() * 1000)
        self.assertEqual(agent.admit(self.req(received=ms - 60_000), self.store, now), ("queued", None))
        status, why = agent.admit(self.req(received=ms - 3 * 3600_000), self.store, now)
        self.assertEqual(status, "waiting-approval")
        self.assertIn("sprzed 3 h", why)
        status, why = agent.admit(self.req(uuid="u-nowy"), self.store, now)
        self.assertEqual(status, "waiting-approval")
        self.assertIn("nowa osoba", why)
        self.assertEqual(agent.admit(self.req(chat="self"), self.store, now), ("queued", None))
        self.store.set_enabled(False)
        self.assertEqual(agent.admit(self.req(), self.store, now)[0], "refused")

    def test_unknown_member_allowed_without_snapshot_rule(self):
        from unittest import mock
        with mock.patch.object(agent, "REQUIRE_SNAPSHOT", False):
            self.assertEqual(agent.admit(self.req(uuid="u-nowy"), self.store, datetime.now().astimezone()),
                             ("queued", None))

    def test_queue_limit(self):
        for i in range(5):
            self.store.add(self.req(ts=i + 1), self.repo, None, "queued")
        status, why = agent.admit(self.req(ts=99), self.store, datetime.now().astimezone())
        self.assertEqual((status, why), ("refused", "kolejka pełna (5)"))

    def test_breaker(self):
        self.assertFalse(self.store.record_failure())
        self.assertFalse(self.store.record_failure())
        self.assertTrue(self.store.record_failure())
        self.assertFalse(self.store.enabled())
        self.store.set_enabled(True)
        self.store.record_failure()
        self.store.record_success()
        self.assertEqual(self.store.state()["failures"], 0)

    def test_approval_by_owner_reaction(self):
        job = self.store.add(self.req(uuid="u-nowy"), self.repo, None, "waiting-approval")
        self.store.update(job.id, approve_ts=5555)
        own_like = ev(fx.reaction(6000, "👍🏽", 5555, fx.OWN, gid="self"))
        self.assertEqual(agent.approval(own_like, self.store), job.id)
        stranger = ev(fx.reaction(6001, "👍", 5555, fx.OWN))
        self.assertIsNone(agent.approval(stranger, self.store))
        heart = ev(fx.reaction(6002, "❤️", 5555, fx.OWN, gid="self"))
        self.assertIsNone(agent.approval(heart, self.store))

    def test_lock_is_exclusive(self):
        with self.store.lock() as a:
            self.assertTrue(a)
            with agent.JobStore().lock() as b:
                self.assertFalse(b)


class LaunchTest(JobStoreCase):
    def test_local_runtime_without_claude_fails_the_job(self):
        from unittest import mock
        with mock.patch("shutil.which", return_value=None):
            self.assertIsNone(agent.launch(self.job, run=lambda *a, **k: None))
        self.assertEqual(self.store.get(self.job.id).status, "failed")

    def setUp(self):
        super().setUp()
        from houselib import signalstore
        from tests.helpers import git
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-q", "--allow-empty", "-m", "base")
        att = self.tmp / "att.csv"
        att.write_text("a,b\n")
        self.history = [ev(fx.group_in(900 + i, f"wcześniej {i}"), seq=i + 1) for i in range(3)]
        trigger = fx.group_in(1000, "@claude policz to", attachments=[fx.attachment("a1", "dane.csv")])
        self.trigger = ev(trigger, seq=20)
        self.trigger["attachments"][0]["path"] = str(att)
        for e in [*self.history, self.trigger]:
            signalstore.append(e)
        signalstore.append({**ev(fx.self_note(960, "PRYWATNA NOTATKA"), seq=30)})
        req = agent.parse(self.trigger)
        self.job = self.store.add(req, self.repo, None, "queued")

    def test_prepare_workspace(self):
        wt = agent.prepare(self.job)
        self.assertEqual(wt, self.repo / ".worktrees" / f"signal-{self.job.id}")
        from tests.helpers import git
        self.assertEqual(git(wt, "branch", "--show-current").strip(), f"signal/{self.job.id}")
        job_dir = wt / ".house-job"
        self.assertIn("policz to", (job_dir / "request.md").read_text())
        context = (job_dir / "context.md").read_text()
        self.assertIn("wcześniej 2", context)
        self.assertNotIn("PRYWATNA NOTATKA", context)
        self.assertEqual((job_dir / "in/dane.csv").read_text(), "a,b\n")
        self.assertTrue((job_dir / "out").is_dir())
        self.assertEqual(git(wt, "status", "--porcelain"), "")  # .house-job is excluded from git

    def test_env_is_clean(self):
        import os
        from unittest import mock
        with mock.patch.dict(os.environ, {"FOO_TOKEN": "x", "HOUSE_GUARD": "off", "GH_TOKEN": "y"}):
            env = agent.job_env(self.job, self.tmp / "wt", claude="/opt/claude/bin/claude")
        self.assertEqual(set(env), {"PATH", "HOME", "LANG", "HOUSE_AGENT_JOB", "HOUSE_AGENT_WORKTREE",
                                    "CLAUDE_CODE_SUBPROCESS_ENV_SCRUB", "CLAUDE_CODE_DISABLE_AUTO_MEMORY",
                                    "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"})
        self.assertTrue(env["PATH"].startswith("/opt/claude/bin:"))
        self.assertEqual(env["HOUSE_AGENT_JOB"], self.job.id)

    def test_settings(self):
        wt = self.tmp / "wt"
        s = agent.job_settings(self.job, wt)
        self.assertEqual(s["permissions"]["defaultMode"], "dontAsk")
        for tool in ("WebFetch", "WebSearch", "mcp__*"):
            self.assertIn(tool, s["permissions"]["deny"])
        sb = s["sandbox"]
        self.assertEqual((sb["enabled"], sb["failIfUnavailable"], sb["allowUnsandboxedCommands"]), (True, True, False))
        self.assertIn("~/", sb["filesystem"]["denyRead"])
        self.assertIn(str(wt), sb["filesystem"]["allowRead"])
        self.assertEqual(sb["network"]["allowedDomains"], agent.ALLOWED_DOMAINS)
        hook = s["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
        self.assertTrue(hook.endswith("bin/house-hook pre-tool"))

    def test_command(self):
        wt = self.tmp / "wt"
        files = agent.job_files(self.job)
        cmd = agent.job_command(self.job, wt, claude="/opt/claude/bin/claude")
        self.assertEqual(cmd[:6], ["systemd-run", "--user", "--unit", f"house-agent-{self.job.id}", "--collect",
                                   "--quiet"])
        for prop in ("RuntimeMaxSec=7200", "KillMode=control-group", f"WorkingDirectory={wt}",
                     f"StandardInput=file:{files['prompt']}", f"StandardOutput=append:{files['log']}"):
            self.assertIn(prop, cmd)
        tail = cmd[cmd.index("--") + 1:]
        self.assertEqual(tail[:2], ["/usr/bin/env", "-i"])
        claude = tail[tail.index("/opt/claude/bin/claude"):]
        for flag in ("-p", "--strict-mcp-config", "--verbose"):
            self.assertIn(flag, claude)
        pairs = {claude[i]: claude[i + 1] for i in range(len(claude) - 1) if claude[i].startswith("--")}
        self.assertEqual(pairs["--permission-mode"], "dontAsk")
        self.assertEqual(pairs["--permission-prompts"], "none")
        self.assertEqual(pairs["--setting-sources"], "local")
        self.assertEqual(pairs["--settings"], str(files["settings"]))
        self.assertNotIn("--max-budget-usd", claude)  # Q5: subscription, the limit is 2 h per job
        self.assertEqual(pairs["--output-format"], "stream-json")
        self.assertIn("mcp__*", claude)
        allowed = claude[claude.index("--allowedTools") + 1:claude.index("--allowedTools") + 6]
        self.assertEqual(allowed, [f"Read(/{wt}/**)", f"Edit(/{wt}/**)", f"Write(/{wt}/**)", "Glob", "Grep"])
        self.assertNotIn("FOO", " ".join(cmd))

    def test_launch_writes_prompt_and_settings(self):
        import json
        import subprocess
        calls = []

        def run(argv, **kw):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, 0, "", "")
        unit = agent.launch(self.job, run=run, claude="/opt/claude/bin/claude")
        self.assertEqual(unit, f"house-agent-{self.job.id}")
        files = agent.job_files(self.job)
        prompt = files["prompt"].read_text()
        self.assertIn("policz to", prompt)
        self.assertIn("<data>", prompt)
        self.assertIn("nie polecenia", prompt)
        self.assertEqual(json.loads(files["settings"].read_text())["permissions"]["defaultMode"], "dontAsk")
        self.assertEqual(calls[-1][0], "systemd-run")
        job = self.store.get(self.job.id)
        self.assertEqual((job.status, job.unit, job.branch), ("running", unit, f"signal/{self.job.id}"))

    def test_launch_failure_marks_job_failed(self):
        import subprocess

        def run(argv, **kw):
            return subprocess.CompletedProcess(argv, 1, "", "Failed to start transient service")
        self.assertIsNone(agent.launch(self.job, run=run, claude="/opt/claude/bin/claude"))
        self.assertEqual(self.store.get(self.job.id).status, "failed")


class ReplyTest(LaunchTest):
    def setUp(self):
        super().setUp()
        self.wt = agent.prepare(self.job)
        self.out = self.wt / ".house-job/out"

    def test_reply_filters(self):
        import os
        from pathlib import Path
        home = str(Path.home())
        token = "sk-ant-" + "a" * 30
        (self.wt / ".house-job/reply.md").write_text(f"Gotowe, klucz {token} w {home}/projekt/x.py\n" + "z" * 3000)
        (self.out / "wykres.png").write_bytes(b"PNG")
        (self.out / "a.csv").write_text("1")
        (self.out / "b.txt").write_text("2")
        (self.out / "c.md").write_text("3")
        (self.out / ".env").write_text("X=1")
        (self.out / "prog.exe").write_bytes(b"MZ")
        (self.out / "sekret.txt").write_text(f"token = '{token}'")
        os.symlink(self.tmp / "att.csv", self.out / "link.csv")
        with (self.out / "duzy.csv").open("wb") as f:
            f.truncate(26 * 1024 * 1024)
        text, files, notes = agent.build_reply(self.job, self.wt)
        self.assertNotIn(token, text)
        self.assertIn("[REDACTED]", text)
        self.assertNotIn(home, text)
        self.assertIn("~/projekt", text)
        self.assertLessEqual(len(text), agent.REPLY_MAX + 40)
        names = [p.name for p in files]
        self.assertIn("wynik.md", names)
        self.assertEqual(len([n for n in names if n != "wynik.md"]), 3)
        for bad in (".env", "prog.exe", "sekret.txt", "link.csv", "duzy.csv"):
            self.assertNotIn(bad, names)
            self.assertTrue(any(bad in n for n in notes), (bad, notes))
        self.assertNotIn(token, next(p for p in files if p.name == "wynik.md").read_text())

    def test_missing_reply(self):
        text, files, _ = agent.build_reply(self.job, self.wt)
        self.assertIn("bez odpowiedzi", text)
        self.assertEqual(files, [])


class FakeAgentSig:
    """Signal stand-in for tick: sends are logged in the store like the real client."""

    def __init__(self, fail=False):
        self.account, self.sent, self.fail, self.ts = fx.OWN, [], fail, 7_000_000_000_000

    def send(self, text, group_id=None, *, attachments=(), quote=None, meta=None):
        from houselib import signalstore
        self.ts += 1
        ts = None if self.fail else self.ts
        self.sent.append({"text": text, "group": group_id, "quote": quote, "files": list(attachments),
                          "meta": meta, "ts": ts})
        signalstore.record_out(self.account, group_id or "self", text, ts, attachments=attachments, quote=quote,
                               meta=meta)
        return ts


REAL_RUN = __import__("subprocess").run


class FakeSystemd:
    def __init__(self, sandbox=True):
        self.calls, self.active, self.sandbox = [], set(), sandbox

    def __call__(self, argv, **kw):
        import subprocess
        self.calls.append(argv)
        if argv[0] == "systemd-run":
            self.active.add(argv[argv.index("--unit") + 1])
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[:3] == ["systemctl", "--user", "show"]:
            state = "active" if argv[3] in self.active else "inactive"
            return subprocess.CompletedProcess(argv, 0, f"ActiveState={state}\nSubState=x\n", "")
        if argv[:3] == ["systemctl", "--user", "stop"]:
            self.active.discard(argv[3])
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[0] == "systemctl":
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[0] == "bwrap":
            return subprocess.CompletedProcess(argv, 0 if self.sandbox else 1, "", "")
        return REAL_RUN(argv, **kw)

    def launches(self):
        return [c for c in self.calls if c[0] == "systemd-run"]


class TickTest(JobStoreCase):
    def setUp(self):
        super().setUp()
        from unittest import mock

        from houselib.signalchats import Chat
        from tests.helpers import git
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        git(self.repo, "config", "user.email", "a@b")
        git(self.repo, "config", "user.name", "a")
        git(self.repo, "commit", "-q", "--allow-empty", "-m", "base")
        self.chats = {"self": Chat("self", "self", "n", "zawsze"),
                      fx.GID: Chat(fx.GID, "techarena", "t", "signal.toml")}
        self.patches = [mock.patch("houselib.signalchats.allowed", return_value=self.chats),
                        mock.patch("houselib.HOUSE_ROOT", self.repo)]
        for p in self.patches:
            p.start()
        self.sig, self.sd = FakeAgentSig(), FakeSystemd()
        self.store.set_state(cursor=0, greeted=[fx.GID])

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        super().tearDown()

    def add(self, obj):
        from houselib import signalstore
        obj["envelope"]["serverReceivedTimestamp"] = int(datetime.now().timestamp() * 1000)  # fixtures are from 1970
        e, _ = signalparse.extract(obj, fx.OWN, {"self": "self", fx.GID: "techarena"}, now=NOW)
        signalstore.append(e)
        return e

    def tick(self):
        return agent.tick(self.sig, run=self.sd, claude="/opt/claude/bin/claude")

    def finish_unit(self, job, result="success", reply="Zrobione."):
        import json
        wt = agent.worktree_of(job)
        if reply is not None:
            (wt / ".house-job/reply.md").write_text(reply)
        (wt / "nowy.py").write_text("print(1)\n")
        if result:
            agent.job_files(job)["log"].write_text(json.dumps({"type": "result", "subtype": result,
                                                               "is_error": result != "success",
                                                               "total_cost_usd": 0.42}) + "\n")
        self.sd.active.discard(job.unit)

    def test_first_tick_skips_history(self):
        self.store.set_state(cursor=None)
        self.add(fx.self_note(1000, "@claude stare zlecenie"))
        self.tick()
        self.assertEqual(self.store.all(), [])
        self.assertEqual(self.sd.launches(), [])
        self.add(fx.self_note(2000, "@claude nowe"))
        self.tick()
        self.assertEqual(len(self.store.all()), 1)

    def test_tick_accepts_and_starts(self):
        self.add(fx.self_note(1000, "@claude dopisz test"))
        self.tick()
        (job,) = self.store.all()
        self.assertEqual(job.status, "running")
        ack = self.sig.sent[0]
        self.assertTrue(ack["text"].startswith("🤖 [house@test] przyjąłem"), ack["text"])
        self.assertEqual((ack["group"], ack["quote"]), (None, (1000, "u-own")))
        self.assertEqual(len(self.sd.launches()), 1)
        self.tick()
        self.assertEqual(len(self.sd.launches()), 1)

    def test_tick_starts_one_per_repo(self):
        self.add(fx.self_note(1000, "@claude pierwsze"))
        self.add(fx.self_note(1001, "@claude drugie"))
        self.tick()
        statuses = sorted(j.status for j in self.store.all())
        self.assertEqual(statuses, ["queued", "running"])
        self.assertTrue(any("kolejka: 1" in s["text"] for s in self.sig.sent), [s["text"] for s in self.sig.sent])

    def test_job_files_are_private(self):
        # systemd's user manager opens StandardOutput=append: files with its umask (0002 → 0664)
        import os
        old = os.umask(0o002)
        try:
            self.add(fx.self_note(1000, "@claude dopisz plik"))
            self.tick()
            (job,) = self.store.all()
            files = agent.job_files(job)
            for key in ("log", "err"):
                self.assertEqual(oct(files[key].stat().st_mode & 0o777), "0o600", key)
            self.finish_unit(job)
            self.tick()
            self.assertEqual(oct((files["dir"] / "reply.txt").stat().st_mode & 0o777), "0o600")
        finally:
            os.umask(old)

    def test_finish_commits_and_replies(self):
        from tests.helpers import git
        self.add(fx.self_note(1000, "@claude dopisz plik"))
        self.tick()
        (job,) = self.store.all()
        self.finish_unit(job)
        self.tick()
        job = self.store.get(job.id)
        self.assertEqual((job.status, job.cost), ("done", 0.42))
        self.assertIn("nowy.py", git(self.repo, "show", "--stat", f"signal/{job.id}"))
        reply = self.sig.sent[-1]
        self.assertIn("Zrobione.", reply["text"])
        self.assertIn(f"signal/{job.id}", reply["text"])
        self.assertEqual(reply["quote"], (1000, "u-own"))
        self.assertNotIn(".house-job", git(self.repo, "show", "--stat", f"signal/{job.id}"))

    def test_reconcile_lost_unit(self):
        self.add(fx.self_note(1000, "@claude x"))
        self.tick()
        (job,) = self.store.all()
        self.finish_unit(job, result=None, reply=None)
        self.tick()
        job = self.store.get(job.id)
        self.assertEqual(job.status, "failed")
        self.assertIn("przerwane", self.sig.sent[-1]["text"])

    def test_next_queued_starts_after_finish(self):
        self.add(fx.self_note(1000, "@claude a"))
        self.add(fx.self_note(1001, "@claude b"))
        self.tick()
        first = next(j for j in self.store.all() if j.status == "running")
        self.finish_unit(first)
        self.tick()
        self.assertEqual(len(self.sd.launches()), 2)

    def test_without_sandbox_job_runs_without_bash(self):
        import json
        self.sd.sandbox = False
        self.add(fx.self_note(1000, "@claude x"))
        self.tick()
        (job,) = self.store.all()
        self.assertTrue(job.no_bash)
        launch = self.sd.launches()[0]
        tools = launch[launch.index("--allowedTools") + 1:launch.index("--allowedTools") + 6]
        self.assertNotIn("Bash", tools)
        settings = json.loads(agent.job_files(job)["settings"].read_text())
        self.assertNotIn("Bash", settings["permissions"]["allow"])
        self.assertIn("Bash", settings["permissions"]["deny"])
        self.assertIn("bez uruchamiania", agent.job_files(job)["prompt"].read_text())
        self.finish_unit(job)
        self.tick()
        self.assertIn("bez uruchamiania kodu", self.sig.sent[-1]["text"])

    def test_parallel_tick_skips(self):
        with self.store.lock():
            self.assertEqual(self.tick(), ["agent: inny tick trwa"])

    def test_send_failure_retried_without_rerun(self):
        self.add(fx.self_note(1000, "@claude x"))
        self.tick()
        (job,) = self.store.all()
        self.finish_unit(job)
        self.sig.fail = True
        self.tick()
        self.assertTrue(self.store.get(job.id).reply_pending)
        self.sig.fail = False
        self.tick()
        job = self.store.get(job.id)
        self.assertFalse(job.reply_pending)
        self.assertIsNotNone(job.reply_ts)
        self.assertEqual(len(self.sd.launches()), 1)

    def test_waiting_approval_flow(self):
        self.add(fx.group_in(1000, "@claude zrób", uuid="u-nowy", name="Nowy"))
        self.tick()
        (job,) = self.store.all()
        self.assertEqual(job.status, "waiting-approval")
        question = next(s for s in self.sig.sent if s["group"] is None)
        self.assertIn("👍", question["text"])
        self.assertTrue(any("czekam na zgodę" in s["text"] for s in self.sig.sent if s["group"] == fx.GID))
        self.add(fx.reaction(2000, "👍", question["ts"], fx.OWN, gid="self"))
        self.tick()
        self.assertEqual(self.store.get(job.id).status, "running")

    def test_disabled_refuses(self):
        self.store.set_enabled(False)
        self.add(fx.self_note(1000, "@claude x"))
        self.tick()
        self.assertIn("wyłączony", self.sig.sent[0]["text"])
        self.assertEqual(self.sd.launches(), [])

    def test_breaker_alarm(self):
        for i in range(3):
            self.add(fx.self_note(1000 + i, f"@claude {i}"))
        for _ in range(3):
            self.tick()
            for job in self.store.running():
                self.finish_unit(job, result="error_during_execution")
        self.tick()
        self.assertFalse(self.store.enabled())
        self.assertTrue(any("wyłączam S7" in s["text"] for s in self.sig.sent))


class CommandTest(TickTest):
    def test_status_lists_this_chat_and_is_rate_limited(self):
        self.add(fx.self_note(1000, "@claude zadanie A"))
        self.tick()
        self.add(fx.self_note(2000, "/status"))
        self.add(fx.self_note(2001, "/status"))
        self.tick()
        answers = [s["text"] for s in self.sig.sent if "w toku" in s["text"]]
        self.assertEqual(len(answers), 1)
        self.assertIn("zadanie A", answers[0])

    def test_stop_running_job(self):
        self.add(fx.self_note(1000, "@claude długie"))
        self.tick()
        (job,) = self.store.all()
        self.add(fx.self_note(2000, "/stop"))
        self.tick()
        self.assertIn(["systemctl", "--user", "stop", job.unit], self.sd.calls)
        job = self.store.get(job.id)
        self.assertEqual(job.status, "stopped")
        self.assertTrue(any("zatrzymane" in s["text"] for s in self.sig.sent))
        self.assertTrue(agent.worktree_of(job).exists())

    def test_stop_from_other_chat_does_not_touch_job(self):
        self.add(fx.self_note(1000, "@claude długie"))
        self.tick()
        self.add(fx.group_in(2000, "/stop"))
        self.tick()
        self.assertEqual(self.store.all()[0].status, "running")

    def test_stop_quoted_queued_job(self):
        self.add(fx.self_note(1000, "@claude a"))
        self.add(fx.self_note(1001, "@claude b"))
        self.tick()
        queued = next(j for j in self.store.all() if j.status == "queued")
        self.add(fx.self_note(2000, "/stop", quote=(queued.ack_ts, fx.OWN, "🤖 przyjąłem")))
        self.tick()
        self.assertEqual(self.store.get(queued.id).status, "stopped")

    def test_agent_off_on_from_note_to_self(self):
        self.add(fx.self_note(2000, "/agent off"))
        self.tick()
        self.assertFalse(self.store.enabled())
        self.add(fx.self_note(2001, "/agent on"))
        self.tick()
        self.assertTrue(self.store.enabled())

    def test_lb_without_contest(self):
        self.add(fx.self_note(2000, "/lb"))
        self.tick()
        self.assertIn("brak konkursu", self.sig.sent[-1]["text"])

    def lb_contest(self, name, rows, spec="leaderboard:\n  cmd: x\n  team: UwUr2\n"):
        import json
        contest = self.tmp / name
        (contest / ".house").mkdir(parents=True)
        (contest / ".house/spec.yaml").write_text(spec)
        if rows is not None:
            self.lb_snap(contest, rows, "2026-11-17T12:00:00+01:00")
        return contest

    def lb_snap(self, contest, rows, at):
        import json
        with open(contest / ".house/leaderboard.jsonl", "a") as f:
            f.write(json.dumps({"at": at, "rows": [{"team": t, "score": v} for t, v in rows]}) + "\n")

    def lb_chat(self, *contests):
        from houselib.signalchats import Chat
        self.chats[fx.GID] = Chat(fx.GID, "fr", "Team", "konkurs fr", tuple(contests))

    def test_lb_first_then_sentences_quote_previous(self):
        contest = self.lb_contest("fr-2026", [("A", .54), ("UwUr2", .53)])
        self.lb_chat(contest)
        self.add(fx.group_in(2000, "/lb"))
        self.tick()
        first = self.sig.sent[-1]
        self.assertIn("1 | A | 0.54", first["text"])
        self.assertEqual(first["quote"][0], 2000)
        self.lb_snap(contest, [("B", .55), ("A", .54), ("UwUr2", .53)], "2026-11-17T13:00:00+01:00")
        self.add(fx.group_in(3000, "/lb"))
        self.tick()
        second = self.sig.sent[-1]
        self.assertIn("B wszedł z 0.55 na #1", second["text"])
        self.assertEqual(second["quote"], (first["ts"], fx.OWN))
        seen = self.store.state()["lb_seen"][f"{fx.GID}|{contest}"]
        self.assertEqual((seen["at"], seen["ts"]), ("2026-11-17T13:00:00+01:00", second["ts"]))

    def test_lb_no_config(self):
        self.lb_chat(self.lb_contest("fr-2026", None, spec=""))
        self.add(fx.group_in(2000, "/lb"))
        self.tick()
        self.assertIn("brak konfiguracji rankingu (spec.yaml: leaderboard.cmd)", self.sig.sent[-1]["text"])

    def test_lb_two_contests_separate_memory(self):
        a, b = self.lb_contest("a-2026", [("A", .5)]), self.lb_contest("b-2026", [("B", .7)])
        self.lb_chat(a, b)
        self.add(fx.group_in(2000, "/lb"))
        self.tick()
        texts = [m["text"] for m in self.sig.sent[-2:]]
        self.assertIn("1 | A | 0.5", texts[0])
        self.assertIn("1 | B | 0.7", texts[1])
        self.assertEqual(set(self.store.state()["lb_seen"]), {f"{fx.GID}|{a}", f"{fx.GID}|{b}"})


class AgentCliTest(TickTest):
    def run_cli(self, *argv, tty=False):
        import contextlib
        import io
        from unittest import mock

        from houselib import cli
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                mock.patch("houselib.signalcli._tty", return_value=tty), \
                mock.patch("houselib.signalcli._ask", return_value="t"), \
                mock.patch("houselib.signal.from_config", return_value=self.sig), \
                mock.patch("houselib.signalagent.subprocess.run", self.sd):
            code = cli.main(["signal", "agent", *argv])
        return code, out.getvalue(), err.getvalue()

    def test_on_needs_tty(self):
        self.store.set_enabled(False)
        self.assertEqual(self.run_cli("on")[0], 2)
        self.assertFalse(self.store.enabled())
        with __import__("unittest").mock.patch("houselib.setup.install_signal_timer") as timer:
            self.assertEqual(self.run_cli("on", tty=True)[0], 0)
        self.assertTrue(self.store.enabled())
        timer.assert_called_once()
        self.assertEqual(timer.call_args.kwargs.get("every"), "1min")

    def test_off_works_for_anyone_and_kill_stops_jobs(self):
        self.add(fx.self_note(1000, "@claude x"))
        self.tick()
        (job,) = self.store.all()
        with __import__("unittest").mock.patch("houselib.setup.install_signal_timer"):
            code, _, _ = self.run_cli("off", "--kill")
        self.assertEqual(code, 0)
        self.assertFalse(self.store.enabled())
        self.assertEqual(self.store.get(job.id).status, "stopped")

    def test_status(self):
        self.add(fx.self_note(1000, "@claude zadanie"))
        self.tick()
        code, out, _ = self.run_cli("status")
        self.assertEqual(code, 0)
        self.assertIn("S7: włączony", out)
        self.assertIn("running", out)

    def test_members_snapshot(self):
        self.sig.group_members = lambda gid: ["u-a", "u-b"]
        self.assertEqual(self.run_cli("members", "techarena")[0], 2)
        code, _, _ = self.run_cli("members", "techarena", tty=True)
        self.assertEqual(code, 0)
        self.assertEqual(self.store.members(fx.GID), ["u-a", "u-b"])

    def test_gc_removes_old_worktrees(self):
        self.add(fx.self_note(1000, "@claude x"))
        self.tick()
        (job,) = self.store.all()
        self.finish_unit(job)
        self.tick()
        self.store.update(job.id, ended="2020-01-01T00:00:00+00:00")
        code, _, _ = self.run_cli("gc")
        self.assertEqual(code, 0)
        self.assertFalse(agent.worktree_of(job).exists())


class SubscriptionTest(TickTest):
    def test_no_daily_dollar_alarm(self):
        for _ in range(3):
            agent._track_spend(self.sig, self.store, 40.0)
        self.assertEqual(self.sig.sent, [])
        self.assertEqual(self.store.state()["spent"][f"{datetime.now():%Y-%m-%d}"], 120.0)


class GreetingDoctorTest(TickTest):
    def test_greeting_once_per_group_when_enabled(self):
        self.store.set_state(greeted=[])
        self.tick()
        self.tick()
        hellos = [s for s in self.sig.sent if "@claude" in s["text"] and "Cześć" in s["text"]]
        self.assertEqual([h["group"] for h in hellos], [fx.GID])
        self.assertTrue(hellos[0]["text"].startswith(("🤖 [techarena@test]", "🤖 [house@test]")))
        self.add(fx.group_in(5000, "ok", quote=(hellos[0]["ts"], fx.OWN, hellos[0]["text"])))
        self.tick()
        self.assertEqual(self.store.all(), [])  # quoting the greeting does not start anything

    def test_no_greeting_when_disabled(self):
        self.store.set_state(greeted=[])
        self.store.set_enabled(False)
        self.tick()
        self.assertEqual(self.sig.sent, [])

    def test_doctor_lines(self):
        import subprocess

        def run(argv, **kw):
            rc = 0 if argv[0] in ("bwrap", "systemd-run") else 1
            return subprocess.CompletedProcess(argv, rc, "bubblewrap 0.11.1\n", "")
        lines = agent.doctor_lines(run=run, which=lambda name: f"/usr/bin/{name}")
        self.assertTrue(any(line.startswith("✓ S7: włączony") for line in lines), lines)
        self.assertTrue(any("bwrap" in line and line.startswith("✓") for line in lines), lines)

        def broken(argv, **kw):
            return subprocess.CompletedProcess(argv, 1, "", "setting up uid map: Permission denied")
        lines = agent.doctor_lines(run=broken, which=lambda name: f"/usr/bin/{name}")
        self.assertTrue(any(line.startswith("✗") and "bwrap" in line for line in lines), lines)
        self.store.set_enabled(False)
        self.assertEqual(agent.doctor_lines(run=run, which=lambda n: None), ["- S7: wyłączony"])


class HostDoctorTest(JobStoreCase):
    def lines(self, toml):
        import os
        import subprocess
        from pathlib import Path
        Path(os.environ["HOUSE_SIGNAL_CONFIG"]).write_text(toml)
        return agent.doctor_lines(run=lambda argv, **kw: subprocess.CompletedProcess(argv, 0, "", ""),
                                  which=lambda name: f"/usr/bin/{name}")

    def test_host_and_default(self):
        lines = self.lines('agent_host = "test"\n')
        self.assertIn("✓ S7: host: test · domyślny: test", lines)

    def test_missing_agent_host_warns(self):
        lines = self.lines("")
        self.assertTrue(any(line.startswith("! S7: brak agent_host") for line in lines), lines)

    def test_agent_host_never_seen_warns(self):
        lines = self.lines('agent_host = "dmo"\n')
        self.assertTrue(any(line.startswith("! S7: agent_host dmo") for line in lines), lines)
        from houselib import signalparse, signalstore
        e, _ = signalparse.extract(fx.self_note(int(datetime.now().timestamp() * 1000), "🤖 [house@dmo] hej"),
                                   fx.OWN, {"self": "self"}, now=NOW)
        signalstore.append(e)
        lines = self.lines('agent_host = "dmo"\n')
        self.assertFalse(any(line.startswith("! S7: agent_host dmo") for line in lines), lines)

    def test_bad_name_reported(self):
        lines = self.lines('agent_host = "Dom Główny"\n')
        self.assertTrue(any(line.startswith("! S7:") and "agent_host" in line and "Dom" in line for line in lines),
                        lines)


class SecurityReviewTest(TickTest):
    def start_job(self, text="@claude x"):
        self.add(fx.self_note(1000, text))
        self.tick()
        (job,) = self.store.all()
        return job, agent.worktree_of(job)

    def test_redirected_git_file_is_refused(self):
        from tests.helpers import git
        job, wt = self.start_job()
        evil = wt / ".house-job/g"
        evil.mkdir()
        (wt / ".git").write_text(f"gitdir: {self.repo / '.git'}\n")
        self.finish_unit(job)
        self.tick()
        self.assertEqual(git(self.repo, "log", "--oneline", "main").count("\n"), 1)  # main untouched
        self.assertIn(".git", self.sig.sent[-1]["text"])
        self.assertIn("nie commituję", self.sig.sent[-1]["text"])

    def test_commit_ignores_hooks_and_fsmonitor(self):
        job, _ = self.start_job()
        marker = self.tmp / "pwned"
        hooks = self.repo / ".githooks"
        hooks.mkdir()
        (hooks / "pre-commit").write_text(f"#!/bin/sh\ntouch {marker}\n")
        (hooks / "pre-commit").chmod(0o755)
        from tests.helpers import git
        git(self.repo, "config", "core.hooksPath", str(hooks))
        git(self.repo, "config", "core.fsmonitor", f"touch {marker}; true")
        self.finish_unit(job)
        self.tick()
        self.assertFalse(marker.exists())
        self.assertIn("nowy.py", git(self.repo, "show", "--stat", f"signal/{job.id}"))

    def test_house_job_never_committed(self):
        from tests.helpers import git
        job, wt = self.start_job()
        (wt / ".gitignore").write_text("!.house-job/\n")
        self.finish_unit(job)
        self.tick()
        self.assertNotIn(".house-job", git(self.repo, "show", "--stat", "--name-only", f"signal/{job.id}"))

    def test_symlinked_out_dir_sends_nothing(self):
        import os
        job, wt = self.start_job()
        secret_dir = self.tmp / "inne"
        secret_dir.mkdir()
        (secret_dir / "context.md").write_text("CUDZA ROZMOWA")
        out = wt / ".house-job/out"
        out.rmdir()
        os.symlink(secret_dir, out)
        self.finish_unit(job)
        self.tick()
        last = self.sig.sent[-1]
        self.assertEqual(last["files"], [])
        self.assertIn("pominięto", last["text"])

    def test_symlinked_house_job_dir_ignored(self):
        import os
        import shutil
        job, wt = self.start_job()
        fake = self.tmp / "fake-job"
        (fake / "out").mkdir(parents=True)
        (fake / "reply.md").write_text("PODMIENIONA")
        shutil.rmtree(wt / ".house-job")
        os.symlink(fake, wt / ".house-job")
        self.finish_unit(job, reply=None)
        self.tick()
        self.assertNotIn("PODMIENIONA", self.sig.sent[-1]["text"])

    def test_sent_files_are_house_copies(self):
        job, wt = self.start_job()
        (wt / ".house-job/out/w.csv").write_text("1,2\n")
        self.finish_unit(job)
        self.tick()
        (sent,) = self.sig.sent[-1]["files"]
        self.assertTrue(sent.startswith(str(agent.job_files(job)["dir"])), sent)

    def test_huge_reply_is_capped(self):
        job, wt = self.start_job()
        with (wt / ".house-job/reply.md").open("wb") as f:
            f.truncate(200 * 1024 * 1024)
        self.finish_unit(job, reply=None)
        self.tick()
        self.assertEqual(self.store.get(job.id).status, "done")

    def test_poison_job_does_not_wedge_others(self):
        from unittest import mock
        job, _ = self.start_job()
        self.finish_unit(job)
        with mock.patch.object(agent, "_commit", side_effect=MemoryError()):
            self.tick()
        self.assertEqual(self.store.get(job.id).status, "failed")

    def test_unknown_unit_state_keeps_job_running(self):
        job, _ = self.start_job()
        real = self.sd.__call__

        def broken(argv, **kw):
            import subprocess
            if argv[:3] == ["systemctl", "--user", "show"]:
                return subprocess.CompletedProcess(argv, 1, "", "Failed to connect to bus")
            return real(argv, **kw)
        agent.tick(self.sig, run=broken, claude="/opt/claude/bin/claude")
        self.assertEqual(self.store.get(job.id).status, "running")

    def test_unit_has_resource_limits_and_scoped_tools(self):
        job, wt = self.start_job()
        cmd = self.sd.launches()[0]
        self.assertTrue(any(c.startswith("MemoryMax=") for c in cmd))
        self.assertTrue(any(c.startswith("TasksMax=") for c in cmd))
        tools = cmd[cmd.index("--allowedTools") + 1:]
        self.assertNotIn("Bash", tools[:5])
        import json
        s = json.loads(agent.job_files(job)["settings"].read_text())
        self.assertNotIn("Bash", s["permissions"]["allow"])
        self.assertIn(f"Edit(/{wt}/**)", s["permissions"]["allow"])
        self.assertIn(str(self.repo / ".git"), s["sandbox"]["filesystem"]["allowRead"])
        hook = s["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
        import shlex
        self.assertEqual(shlex.split(hook)[-1], "pre-tool")

    def test_migrated_or_rawless_events_never_trigger(self):
        e = ev(fx.self_note(1000, "@claude stare"))
        e.update(raw=None, meta={"migrated": True})
        self.assertIsNone(agent.parse(e))

    def test_unknown_receive_time_counts_as_old(self):
        req = agent.parse(ev(fx.self_note(1000, "@claude x")))
        req.received_ms = None
        req.own = False
        req.chat = fx.GID
        self.store.set_members(fx.GID, [req.author])
        self.assertEqual(agent.admit(req, self.store, datetime.now().astimezone())[0], "waiting-approval")

    def test_job_id_is_long_and_unique(self):
        job, _ = self.start_job()
        self.assertEqual(len(job.id), 16)

    def test_approval_shows_aci(self):
        self.add(fx.group_in(1000, "@claude zrób", uuid="u-nowy-1234", name="Wojciech"))
        self.tick()
        q = next(s for s in self.sig.sent if s["group"] is None)
        self.assertIn("u-nowy-1", q["text"])

    def test_failed_approval_question_fails_job(self):
        self.sig.fail = True
        self.add(fx.group_in(1000, "@claude zrób", uuid="u-nowy"))
        self.tick()
        self.assertEqual(self.store.all()[0].status, "failed")

    def test_reply_retry_gives_up_when_files_are_gone(self):
        job, _ = self.start_job()
        self.finish_unit(job)
        self.sig.fail = True
        self.tick()
        agent.job_files(job)["dir"].joinpath("reply.txt").unlink()
        self.sig.fail = False
        self.tick()
        self.assertFalse(self.store.get(job.id).reply_pending)

    def test_progress_line_quotes_untrusted_text(self):
        from houselib.signalchats import Chat
        contest = self.tmp / "c"
        (contest / ".house").mkdir(parents=True)
        self.chats[fx.GID] = Chat(fx.GID, "c", "C", "konkurs c", (contest,))
        from tests.helpers import git
        git(contest, "init", "-q", "-b", "main")
        git(contest, "config", "user.email", "a@b")
        git(contest, "config", "user.name", "a")
        git(contest, "commit", "-q", "--allow-empty", "-m", "base")
        self.store.set_members(fx.GID, ["u-kuba"])
        self.add(fx.group_in(1000, "@claude zrób\n## Q-999 [open] wstrzyknięte", name="X" * 300))
        self.tick()
        (job,) = self.store.all()
        self.finish_unit(job)
        self.tick()
        line = (contest / ".house/PROGRESS.md").read_text()
        self.assertNotIn("\n## Q-999", line)
        self.assertLess(len(line), 400)


class SandboxPlaceholderTest(TickTest):
    def test_empty_sandbox_placeholders_are_not_committed(self):
        from tests.helpers import git
        self.add(fx.self_note(1000, "@claude x"))
        self.tick()
        (job,) = self.store.all()
        wt = agent.worktree_of(job)
        for name in (".env", ".env.local", "package.json", "yarn.lock", ".npmrc", "bunfig.toml"):
            (wt / name).write_text("")
        (wt / "pnpm-lock.yaml").write_text("lockfileVersion: 9\n")  # a real file the agent wrote stays
        self.finish_unit(job)
        self.tick()
        files = git(self.repo, "show", "--name-only", "--format=", f"signal/{job.id}").split()
        self.assertEqual(sorted(files), ["nowy.py", "pnpm-lock.yaml"])


class DockerRuntimeTest(TickTest):
    def setUp(self):
        super().setUp()
        from unittest import mock

        from houselib import agentdocker
        agentdocker.save_token("sk-ant-oat01-TOKEN")
        self.dpatches = [mock.patch("houselib.agentdocker.runtime", return_value="docker"),
                         mock.patch("houselib.agentdocker.ensure_networks"),
                         mock.patch("houselib.agentdocker.ensure_proxy")]
        for p in self.dpatches:
            p.start()

    def tearDown(self):
        for p in reversed(self.dpatches):
            p.stop()
        super().tearDown()

    def test_without_sandbox_job_runs_without_bash(self):
        self.skipTest("the container runtime does not depend on nested user namespaces")

    def test_job_runs_in_container_with_bash(self):
        import json
        self.add(fx.self_note(1000, "@claude uruchom testy"))
        self.tick()
        (job,) = self.store.all()
        self.assertEqual((job.runtime, job.no_bash), ("docker", False))
        cmd = self.sd.launches()[0]
        tail = cmd[cmd.index("--") + 1:]
        self.assertEqual(tail[:3], ["docker", "run", "--rm"])
        self.assertIn(f"ExecStopPost=docker rm -f house-agent-{job.id}", cmd)
        self.assertNotIn("TOKEN", " ".join(cmd))
        claude = tail[tail.index("claude"):]
        self.assertEqual(claude[claude.index("--settings") + 1], "/job/settings.json")
        s = json.loads(agent.job_files(job)["settings"].read_text())
        self.assertIn("Bash", s["permissions"]["allow"])
        self.assertFalse(s["sandbox"]["enabled"])
        self.assertEqual(s["hooks"]["PreToolUse"][0]["hooks"][0]["command"], "/house/bin/house-hook pre-tool")
        self.assertTrue((agent.job_files(job)["dir"] / "container.env").exists())
        self.finish_unit(job)
        self.tick()
        self.assertFalse((agent.job_files(job)["dir"] / "container.env").exists())


class HostCase(TickTest):
    """Tick with this host named `test` (HOUSE_HOST) and `agent_host` set per test."""

    def config(self, agent_host=None):
        from pathlib import Path
        import os
        Path(os.environ["HOUSE_SIGNAL_CONFIG"]).write_text(f'agent_host = "{agent_host}"\n' if agent_host else "")

    def now_ms(self):
        return int(datetime.now().timestamp() * 1000)


class HostRoutingTest(HostCase):
    """Several computers on one Signal account: each event is handled by exactly one host (this one is `test`)."""

    def test_untargeted_goes_to_agent_host_only(self):
        self.config("inny")
        self.add(fx.self_note(1000, "@claude zrób X"))
        self.tick()
        self.assertEqual((self.store.all(), self.sig.sent), ([], []))
        self.assertGreater(self.store.state()["cursor"], 0)
        self.config("test")
        self.add(fx.self_note(1001, "@claude zrób Y"))
        self.tick()
        self.assertEqual([j.text for j in self.store.all()], ["zrób Y"])

    def test_explicit_host_wins_over_default(self):
        self.config("inny")
        self.add(fx.self_note(1000, "@claude@test zrób X"))
        self.add(fx.self_note(1001, "@claude@inny zrób Y"))
        self.tick()
        self.assertEqual([j.text for j in self.store.all()], ["zrób X"])

    def test_reply_goes_to_the_quoted_host(self):
        self.config("test")
        self.add(fx.self_note(1000, "@claude review", quote=(900, fx.OWN, "🤖 [house@inny] Krok 1 skończony")))
        self.tick()
        self.assertEqual(self.store.all(), [])
        self.add(fx.self_note(1001, "@claude review", quote=(901, fx.OWN, "🤖 [house] stara wiadomość")))
        self.tick()
        self.assertEqual(len(self.store.all()), 1)
        self.config("inny")
        self.add(fx.self_note(1002, "@claude drugie", quote=(902, fx.OWN, "🤖 [house@test] Krok 2")))
        self.tick()
        self.assertEqual(len(self.store.all()), 2)

    def test_commands_follow_the_same_rules(self):
        self.config("test")
        self.add(fx.self_note(self.now_ms(), "🤖 [house@inny] jestem"))
        self.add(fx.self_note(2000, "/status", quote=(900, fx.OWN, "🤖 [house@inny] x")))
        self.add(fx.self_note(2001, "/agent@inny off"))
        self.tick()
        self.assertEqual(self.sig.sent, [])
        self.assertTrue(self.store.enabled())
        self.add(fx.self_note(2002, "/agent off"))
        self.tick()
        self.assertFalse(self.store.enabled())
        self.assertIn("S7 wyłączony na test", self.sig.sent[-1]["text"])

    def test_status_names_the_host(self):
        self.config("test")
        self.add(fx.self_note(2000, "/status"))
        self.tick()
        self.assertIn("host: test (domyślny)", self.sig.sent[-1]["text"])

    def test_no_agent_host_keeps_old_behavior(self):
        self.config(None)
        self.add(fx.self_note(1000, "@claude zrób X"))
        self.tick()
        self.assertEqual(len(self.store.all()), 1)

    def test_unknown_host_answered_by_default_host(self):
        self.add(fx.self_note(self.now_ms(), "🤖 [house@praca] coś z drugiego komputera"))
        self.config("inny")
        self.add(fx.self_note(1000, "@claude@dmo zrób X"))
        self.tick()
        self.assertEqual((self.store.all(), self.sig.sent), ([], []))
        self.config("test")
        self.add(fx.self_note(1001, "@claude@dmo zrób X"))
        self.add(fx.self_note(1002, "/status@xyz"))
        self.tick()
        self.assertEqual(self.store.all(), [])
        texts = [s["text"] for s in self.sig.sent]
        self.assertEqual(len(texts), 2, texts)
        self.assertIn("host dmo nie pisał od 30 dni (znane: praca, test)", texts[0])
        self.assertIn("host xyz nie pisał", texts[1])


class HostFollowUpTest(HostCase):
    """Review fixes: a reply to a request follows that request's host; greeting only from the default host."""

    def test_stop_reply_to_named_request_goes_to_its_host(self):
        self.add(fx.self_note(self.now_ms(), "🤖 [house@inny] jestem"))
        self.config("inny")
        self.add(fx.self_note(1000, "@claude@test długie"))
        self.tick()
        (job,) = self.store.all()
        self.add(fx.self_note(2000, "/stop", quote=(1000, fx.OWN_UUID, "@claude@test długie")))
        self.tick()
        self.assertEqual(self.store.get(job.id).status, "stopped")

    def test_stop_reply_to_implicitly_routed_request(self):
        self.config("inny")
        self.add(fx.self_note(1000, "@claude zrób", quote=(900, fx.OWN, "🤖 [house@test] Krok 1")))
        self.tick()
        (job,) = self.store.all()
        self.add(fx.self_note(2000, "/stop", quote=(1000, fx.OWN_UUID, "@claude zrób")))
        self.tick()
        self.assertEqual(self.store.get(job.id).status, "stopped")

    def test_quote_without_text_uses_stored_message(self):
        self.config("test")
        self.add(fx.self_note(900, "🤖 [house@inny] Krok 1"))
        self.add(fx.self_note(1000, "@claude zrób", quote=(900, fx.OWN, None)))
        self.tick()
        self.assertEqual(self.store.all(), [])

    def test_stop_quoting_unrelated_message_stops_nothing(self):
        self.config("test")
        self.add(fx.self_note(1000, "@claude długie"))
        self.tick()
        (job,) = self.store.all()
        self.add(fx.self_note(2000, "/stop", quote=(555, fx.OWN_UUID, "jakaś inna wiadomość")))
        self.tick()
        self.assertEqual(self.store.get(job.id).status, "running")
        self.assertIn("nic do zatrzymania", self.sig.sent[-1]["text"])

    def test_only_default_host_greets(self):
        self.config("inny")
        self.store.set_state(greeted=[])
        self.tick()
        self.assertEqual(self.sig.sent, [])
        self.config("test")
        self.tick()
        self.assertEqual(len(self.sig.sent), 1)

    def test_stored_text_wins_over_quote_text(self):
        self.add(fx.self_note(self.now_ms(), "🤖 [house@inny] jestem"))
        self.config("inny")
        self.add(fx.self_note(900, "🤖 [house@test] Krok 1"))
        self.add(fx.self_note(1000, "@claude zrób", quote=(900, fx.OWN, "🤖 [house@inny] Krok 1")))
        self.tick()
        self.assertEqual(len(self.store.all()), 1)


class HostConfigBrokenTest(HostCase):
    """A broken host config stops dispatch (fail-closed) with one alarm instead of misrouting silently."""

    def check_paused(self, toml):
        from pathlib import Path
        import os
        Path(os.environ["HOUSE_SIGNAL_CONFIG"]).write_text(toml)
        self.add(fx.self_note(1000, "@claude zrób X"))
        self.tick()
        self.tick()
        self.assertEqual(self.store.all(), [])
        alarms = [s["text"] for s in self.sig.sent if "signal.toml" in s["text"]]
        self.assertEqual(len(alarms), 1, self.sig.sent)
        self.assertIsNone(self.sig.sent[0]["group"])
        self.config("test")
        self.tick()
        self.assertEqual([j.text for j in self.store.all()], ["zrób X"])  # nothing was skipped while paused

    def test_invalid_agent_host(self):
        self.check_paused('agent_host = "Dom"\n')

    def test_invalid_host(self):
        self.check_paused('host = "Dom"\nagent_host = "test"\n')

    def test_keys_inside_group(self):
        self.check_paused('[groups.t]\nid = "x"\nagent_host = "test"\n')

    def test_unreadable_toml(self):
        self.check_paused('agent_host = \n')
