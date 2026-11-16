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
        if received is None:
            obj["envelope"].pop("serverReceivedTimestamp", None)
        else:
            obj["envelope"]["serverReceivedTimestamp"] = received
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
        self.assertEqual(pairs["--max-budget-usd"], str(agent.BUDGET_USD))
        self.assertEqual(pairs["--output-format"], "stream-json")
        self.assertIn("mcp__*", claude)
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
        self.assertEqual(calls[0][0], "systemd-run")
        job = self.store.get(self.job.id)
        self.assertEqual((job.status, job.unit, job.branch), ("running", unit, f"signal/{self.job.id}"))

    def test_launch_failure_marks_job_failed(self):
        import subprocess

        def run(argv, **kw):
            return subprocess.CompletedProcess(argv, 1, "", "Failed to start transient service")
        self.assertIsNone(agent.launch(self.job, run=run, claude="/opt/claude/bin/claude"))
        self.assertEqual(self.store.get(self.job.id).status, "failed")
