"""`houselib.cost`: one row of numbers per contest session (synthetic sessions, streams and registers only)."""
import json
import os
import re
import tempfile
import time
import unittest
from contextlib import contextmanager
from dataclasses import asdict
from datetime import date, datetime, timezone
from pathlib import Path
from unittest import mock

from houselib import attempts, cost, transcript

CANARY = "SECRET-CANARY-do-not-leak"
SID = "aaaaaaaa-0000-0000-0000-000000000001"
OPUS = "claude-opus-4-7"
HAIKU = "claude-haiku-4-5"


def usage(inp=3, read=100, w5=40, w1=10, out=7) -> dict:
    return {"input_tokens": inp, "cache_read_input_tokens": read, "cache_creation_input_tokens": w5 + w1,
            "cache_creation": {"ephemeral_5m_input_tokens": w5, "ephemeral_1h_input_tokens": w1},
            "output_tokens": out}


def asst(mid: str, u: dict, cwd: str, sid: str = SID, rid: str | None = None, model: str = OPUS,
         ts: str | None = "2026-11-17T10:00:00.000Z", stop: str | None = "end_turn", side: bool = False) -> dict:
    """An interactive-session `assistant` record."""
    rec = {"type": "assistant", "sessionId": sid, "requestId": rid or "r-" + mid, "cwd": cwd, "isSidechain": side,
           "message": {"id": mid, "model": model, "role": "assistant", "stop_reason": stop, "usage": u,
                       "content": [{"type": "text", "text": CANARY}]}}
    if ts:
        rec["timestamp"] = ts
    return rec


def user(cwd: str, sid: str = SID) -> dict:
    return {"type": "user", "sessionId": sid, "cwd": cwd, "isSidechain": False,
            "message": {"role": "user", "content": CANARY}}


def compaction(pre: int, cwd: str, sid: str = SID) -> dict:
    return {"type": "system", "subtype": "compact_boundary", "sessionId": sid, "cwd": cwd,
            "timestamp": "2026-11-17T10:30:00.000Z",
            "compactMetadata": {"trigger": "auto", "preTokens": pre, "postTokens": 9000, "durationMs": 5}}


def cost_state(models: dict, start_ms: int | None = None, sid: str = SID) -> dict:
    rec = {"type": "cost-state", "sessionId": sid, "totalCostUSD": 1.5,
           "modelUsage": {m: {"inputTokens": a, "cacheReadInputTokens": b, "cacheCreationInputTokens": c,
                              "outputTokens": d, "costUSD": 0.5} for m, (a, b, c, d) in models.items()}}
    if start_ms is not None:
        rec["startTime"] = start_ms
    return rec


def s_asst(mid: str, u: dict, sid: str = SID, model: str = OPUS, side_parent: str = "") -> dict:
    """A stream-json `assistant` record (snake_case ids, no timestamp)."""
    return {"type": "assistant", "session_id": sid, "request_id": "r-" + mid, "parent_tool_use_id": side_parent or None,
            "message": {"id": mid, "model": model, "role": "assistant", "stop_reason": "end_turn", "usage": u,
                        "content": [{"type": "text", "text": CANARY}]}}


def s_result(models: dict, sid: str = SID) -> dict:
    return {"type": "result", "subtype": "success", "session_id": sid, "total_cost_usd": 2.0,
            "modelUsage": {m: {"inputTokens": a, "cacheReadInputTokens": b, "cacheCreationInputTokens": c,
                               "outputTokens": d, "costUSD": 1.0} for m, (a, b, c, d) in models.items()}}


def write_jsonl(path: Path, *lines) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(l if isinstance(l, str) else json.dumps(l) for l in lines) + "\n")
    return path


@contextmanager
def local_zone(name: str):
    """Run with the process time zone set to `name` (`TZ`), restoring it afterwards."""
    old = os.environ.get("TZ")
    os.environ["TZ"] = name
    time.tzset()
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old
        time.tzset()


def ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso).replace(tzinfo=timezone.utc).timestamp() * 1000)


class CostBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.contest = self.root / "work" / "contest"
        (self.contest / ".house").mkdir(parents=True)
        self.projects = self.root / "projects"
        self.signal = self.root / "data" / "signal" / "agent"  # absent unless a test creates it

    def pdir(self, cwd: Path) -> Path:
        return self.projects / transcript.encode_path(cwd)

    def session(self, name: str, *lines, cwd: Path | None = None) -> Path:
        return write_jsonl(self.pdir(cwd or self.contest) / f"{name}.jsonl", *lines)

    def rows(self, contest: Path | None = None, **kw) -> list[cost.Row]:
        kw.setdefault("signal_dir", self.signal)
        return cost.rows(contest or self.contest, projects=self.projects, **kw)

    def register(self, task="T001", n=1, tool="claude", log="", session="", start="2026-11-16T22:00:00+01:00"):
        attempts.record(self.contest / ".house", attempts.Row(task=task, n=n, source="night", agent="coder", tool=tool,
                                                              session=session, log=log, start=start))


class SessionRowTest(CostBase):
    def three_calls(self) -> list:
        c = str(self.contest)
        return [user(c),
                asst("m1", usage(10, 0, 5000, 0, 100), c, ts="2026-11-17T10:00:00.000Z"),
                asst("m2", usage(2, 5000, 300, 200, 50), c, ts="2026-11-17T10:01:00.000Z"),
                asst("m3", usage(1, 5500, 100, 0, 400), c, ts="2026-11-17T10:02:00.000Z")]

    def test_fixture_numbers_equal_hand_computed_values(self):
        self.session(SID, *self.three_calls())
        (r,) = self.rows()
        self.assertEqual((r.kind, r.session, r.date, r.model), ("interactive", SID, "2026-11-17", OPUS))
        self.assertEqual(r.calls, 3)
        # context = input + cache_read + both cache writes of each call: 5010, 5502, 5601
        self.assertEqual((r.context_first, r.context_median, r.context_peak), (5010, 5502, 5601))
        self.assertEqual((r.input, r.cache_read, r.cache_write_5m, r.cache_write_1h, r.output),
                         (13, 10500, 5400, 200, 550))
        self.assertEqual((r.compactions, r.max_pre_tokens, r.outside, r.note), (0, None, None, ""))
        # 13*1 + 10500*0.1 + 5400*1.25 + 200*2 + 550*5
        self.assertAlmostEqual(r.weighted, 10963.0)
        self.assertAlmostEqual(cost.weighted(r, cost.DEFAULT_WEIGHTS), 10963.0)

    def test_median_of_an_even_number_of_calls_is_the_mean_of_the_middle_two(self):
        c = str(self.contest)
        self.session(SID, asst("m1", usage(100, 0, 0, 0, 1), c), asst("m2", usage(300, 0, 0, 0, 1), c))
        (r,) = self.rows()
        self.assertEqual((r.context_first, r.context_median, r.context_peak), (100, 200, 300))

    def test_weighted_uses_given_weights(self):
        self.session(SID, *self.three_calls())
        (r,) = self.rows()
        w = {"input": 0, "cache_read": 0, "cache_write_5m": 0, "cache_write_1h": 0, "output": 2}
        self.assertEqual(cost.weighted(r, w), 1100)
        self.assertEqual(cost.DEFAULT_WEIGHTS, {"input": 1, "cache_read": 0.1, "cache_write_5m": 1.25,
                                                "cache_write_1h": 2, "output": 5})

    def test_repeated_record_counted_once(self):
        c = str(self.contest)
        u = usage(10, 0, 5000, 0, 100)
        # the same call logged on three content-block records (the first without a stop reason), plus one real call
        self.session(SID, asst("m1", u, c, stop=None), asst("m1", u, c), asst("m1", u, c),
                     asst("m2", usage(1, 5000, 0, 0, 5), c))
        (r,) = self.rows()
        self.assertEqual((r.calls, r.input, r.output), (2, 11, 105))

    def test_same_call_in_two_session_files_counted_once(self):
        c = str(self.contest)
        shared = asst("m1", usage(10, 0, 5000, 0, 100), c)
        self.session(SID, shared)
        self.session("bbbbbbbb-0000-0000-0000-000000000002", {**shared, "sessionId": "bbbbbbbb-0000-0000-0000-000000000002"})
        self.assertEqual(sum(r.calls for r in self.rows()), 1)

    def test_compaction_count_and_largest_pre_tokens(self):
        c = str(self.contest)
        self.session(SID, *self.three_calls(), compaction(180_000, c), compaction(150_000, c))
        (r,) = self.rows()
        self.assertEqual((r.compactions, r.max_pre_tokens), (2, 180_000))

    def test_subagent_summed_into_the_parent_tree(self):
        c = str(self.contest)
        main = self.session(SID, asst("m1", usage(10, 0, 5000, 0, 100), c))
        sub = main.with_suffix("") / "subagents"
        write_jsonl(sub / "agent-a1.jsonl",
                    asst("s1", usage(4, 0, 900, 0, 10), c, side=True), asst("s2", usage(6, 900, 0, 0, 20), c, side=True))
        write_jsonl(sub / "agent-a1.meta.json", {"toolUseId": "toolu_x", "agentType": "Explore"})
        (r,) = self.rows()
        self.assertEqual((r.calls, r.input, r.cache_read, r.cache_write_5m, r.output), (3, 20, 900, 5900, 130))
        # context is the main thread only: one call of 5010
        self.assertEqual((r.context_first, r.context_median, r.context_peak), (5010, 5010, 5010))
        self.assertEqual(r.note, "")

    def test_subagent_only_tree_is_its_own_row(self):
        c = str(self.contest)
        sub = self.pdir(self.contest) / SID / "subagents"  # no <SID>.jsonl next to it
        write_jsonl(sub / "agent-a1.jsonl", asst("s1", usage(4, 0, 900, 0, 10), c, side=True),
                    asst("s2", usage(6, 900, 0, 0, 20), c, side=True))
        (r,) = self.rows()
        self.assertEqual((r.kind, r.session, r.calls, r.input, r.output), ("interactive", SID, 2, 10, 30))
        self.assertEqual((r.context_first, r.context_median, r.context_peak, r.note), (None, None, None,
                                                                                       "sub-agents only"))

    def test_subagent_files_of_another_contest_are_not_picked_up(self):
        other = self.root / "work" / "other"
        sub = self.pdir(other) / SID / "subagents"
        write_jsonl(sub / "agent-a1.jsonl", asst("s1", usage(), str(other), side=True))
        self.assertEqual(self.rows(), [])

    def test_no_usage_is_no_data(self):
        self.session(SID, user(str(self.contest)))
        (r,) = self.rows()
        self.assertEqual((r.calls, r.note, r.weighted, r.outside, r.context_first), (0, "no data", None, None, None))
        self.assertEqual((r.input, r.cache_read, r.cache_write_5m, r.cache_write_1h, r.output), (0,) * 5)

    def test_file_without_any_event_gives_no_row(self):
        self.session(SID, {"type": "mode"}, "not json")
        self.assertEqual(self.rows(), [])

    def test_no_contest_sessions_and_missing_projects(self):
        self.assertEqual(cost.rows(self.contest, projects=self.root / "absent", signal_dir=self.signal), [])

    def test_symlinked_contest_and_worktree_sessions_are_assigned(self):
        real = self.contest
        link = self.root / "link"
        link.symlink_to(real)
        wt = real / ".worktrees" / "T001-x"
        self.session("s-real", asst("m1", usage(1, 0, 0, 0, 1), str(real), sid="s-real"), cwd=real)
        self.session("s-wt", asst("m2", usage(2, 0, 0, 0, 1), str(wt), sid="s-wt"), cwd=wt)
        self.session("s-link", asst("m3", usage(4, 0, 0, 0, 1), str(link / "sub"), sid="s-link"), cwd=link)
        self.session("s-old", asst("m4", usage(8, 0, 0, 0, 1), str(real) + "-old", sid="s-old"), cwd=Path(str(real) + "-old"))
        got = {r.session: r.input for r in self.rows(contest=link)}
        self.assertEqual(got, {"s-real": 1, "s-wt": 2, "s-link": 4})

    def test_row_holds_numbers_not_text(self):
        c = str(self.contest)
        main = self.session(SID, *self.three_calls(), compaction(1, c))
        write_jsonl(main.with_suffix("") / "subagents" / "agent-a.jsonl", asst("s1", usage(), c, side=True))
        text = json.dumps([asdict(r) for r in self.rows()])
        self.assertNotIn(CANARY, text)
        for r in self.rows():
            self.assertNotIn(CANARY, repr(r))


class OutsideTest(CostBase):
    def test_outside_with_an_extra_haiku_model(self):
        c = str(self.contest)
        # Σ usage of opus: 10+0+500+0+20 = 530 and 5+500+100+0+30 = 635 -> 1165 tokens
        self.session(SID, asst("m1", usage(10, 0, 500, 0, 20), c), asst("m2", usage(5, 500, 100, 0, 30), c),
                     cost_state({OPUS: (22, 500, 600, 50), HAIKU: (40, 0, 0, 10)}, start_ms=ms("2026-11-17T00:00:00")))
        (r,) = self.rows()
        # opus total 22+500+600+50 = 1172, so 7 outside; haiku was never called in the record: all 50
        self.assertEqual(r.outside, {OPUS: 7, HAIKU: 50})

    def test_resumed_session_compares_only_since_the_last_process_run(self):
        c = str(self.contest)
        old = [asst("o1", usage(500, 0, 0, 0, 500), c, ts="2026-11-17T09:00:00.000Z"),
               asst("o2", usage(500, 0, 0, 0, 500), c, ts="2026-11-17T09:05:00.000Z")]
        new = asst("n1", usage(400, 100, 200, 100, 200), c, ts="2026-11-17T12:00:00.000Z")  # 1000 tokens
        # the last process run (since 11:00) used 1100 tokens: the new call plus 100 of side calls
        self.session(SID, *old, new, cost_state({OPUS: (500, 100, 300, 200)}, start_ms=ms("2026-11-17T11:00:00")))
        (r,) = self.rows()
        self.assertEqual(r.calls, 3)
        self.assertEqual(r.outside, {OPUS: 100})  # over the whole file it would be 1100 - 3000 < 0

    def test_outside_is_never_negative(self):
        c = str(self.contest)
        self.session(SID, asst("m1", usage(1000, 0, 0, 0, 0), c),
                     cost_state({OPUS: (10, 0, 0, 0)}, start_ms=ms("2026-11-17T00:00:00")))
        (r,) = self.rows()
        self.assertEqual(r.outside, {OPUS: 0})

    def test_session_total_without_start_time_covers_the_whole_log(self):
        c = str(self.contest)
        self.session(SID, asst("m1", usage(10, 0, 0, 0, 0), c, ts="2000-01-01T00:00:00.000Z"),
                     cost_state({OPUS: (25, 0, 0, 0)}))
        (r,) = self.rows()
        self.assertEqual(r.outside, {OPUS: 15})


    def test_fork_that_wins_the_dedup_does_not_inflate_the_original_outside(self):
        c = str(self.contest)
        own = asst("m0", usage(1, 0, 0, 0, 1), c, ts="2026-11-17T09:59:00.000Z")  # 2 tokens only this session has
        m1 = asst("m1", usage(10, 0, 500, 0, 20), c, ts="2026-11-17T10:00:00.000Z")
        m2 = asst("m2", usage(5, 500, 100, 0, 30), c, ts="2026-11-17T10:01:00.000Z")
        # opus used 2 + 530 + 635 = 1167 of a total of 23+500+600+51 = 1174: 7 tokens outside
        self.session(SID, own, m1, m2, cost_state({OPUS: (23, 500, 600, 51)}, start_ms=ms("2026-11-17T00:00:00")))
        fork = "ffffffff-0000-0000-0000-00000000000f"
        # a fork that copied both calls and is newer, so its copies win the global de-duplication
        self.session(fork, *({**m, "sessionId": fork, "timestamp": "2026-11-17T12:00:00.000Z"} for m in (m1, m2)))
        got = {r.session: r for r in self.rows()}
        self.assertEqual(got[SID].outside, {OPUS: 7})
        self.assertEqual((got[SID].calls, got[fork].calls), (1, 2))  # each call is still counted once overall

    def test_outside_without_a_fork_is_unchanged_by_local_selection(self):
        c = str(self.contest)
        u = usage(10, 0, 500, 0, 20)
        # a call repeated inside one file: counted once, so 530 used of 540
        self.session(SID, asst("m1", u, c, stop=None), asst("m1", u, c),
                     cost_state({OPUS: (10, 0, 500, 30)}, start_ms=ms("2026-11-17T00:00:00")))
        (r,) = self.rows()
        self.assertEqual(r.outside, {OPUS: 10})


class NightTest(CostBase):
    def night_stream(self, name="T001-1", sid=SID, extra=()) -> str:
        log = f".house/logs/{name}.jsonl"
        write_jsonl(self.contest / log, s_asst("m1", usage(10, 0, 5000, 0, 100), sid=sid),
                    s_asst("m1", usage(10, 0, 5000, 0, 100), sid=sid),  # repeated content-block record
                    s_asst("m2", usage(2, 5000, 300, 200, 50), sid=sid),
                    s_result({OPUS: (13, 5000, 5500, 160), HAIKU: (7, 0, 0, 3)}, sid=sid), *extra)
        return log

    def test_night_session_counted_once_despite_stream_and_session_file(self):
        wt = self.contest / ".worktrees" / "T001"
        self.register(log=self.night_stream(), session=SID)
        # the same run as an interactive-style session file, with one more call that must not leak in
        self.session(SID, asst("m1", usage(10, 0, 5000, 0, 100), str(wt)), asst("m2", usage(2, 5000, 300, 200, 50), str(wt)),
                     asst("m9", usage(999, 0, 0, 0, 999), str(wt)), cwd=wt)
        (r,) = self.rows()
        self.assertEqual((r.kind, r.session, r.date, r.calls), ("night", SID, "2026-11-16", 2))
        # buckets from the `result` (both models; opus's 5500 writes split 5300:200 like its calls)
        self.assertEqual((r.input, r.cache_read, r.cache_write_5m, r.cache_write_1h, r.output),
                         (20, 5000, 5300, 200, 163))
        self.assertEqual(r.note, "totals from result; TTL split from calls")
        # context: 5010 and 5502 -> median 5256
        self.assertEqual((r.context_first, r.context_median, r.context_peak), (5010, 5256, 5502))
        # outside from the `result` line (whole log): opus 13+5000+5500+160 = 10673 - 10662 = 11; haiku 10
        self.assertEqual(r.outside, {OPUS: 11, HAIKU: 10})

    def test_sub_agent_events_of_a_stream_count_but_do_not_set_the_context(self):
        log = ".house/logs/T001-1.jsonl"
        write_jsonl(self.contest / log, s_asst("m1", usage(10, 0, 500, 0, 1)),
                    s_asst("s1", usage(1, 0, 90000, 0, 1), side_parent="toolu_1"))
        self.register(log=log)
        (r,) = self.rows()
        self.assertEqual((r.calls, r.context_peak, r.cache_write_5m), (2, 510, 90500))

    def test_night_stream_with_a_partial_then_a_complete_record_counts_the_complete_one(self):
        log = ".house/logs/T001-1.jsonl"
        partial = s_asst("m1", usage(10, 0, 500, 0, 1))
        partial["message"]["stop_reason"] = None
        write_jsonl(self.contest / log, partial, s_asst("m1", usage(10, 0, 500, 0, 80)))
        self.register(log=log)
        (r,) = self.rows()
        self.assertEqual((r.calls, r.output), (1, 80))

    def partial_stream(self, name="T001-1", models=None, complete=False) -> str:
        """A night stream whose per-call output is a partial value (no stop_reason) plus a `result` total."""
        log = f".house/logs/{name}.jsonl"
        calls = [s_asst("m1", usage(10, 0, 5000, 0, 3)), s_asst("m2", usage(2, 5000, 300, 200, 4))]
        if not complete:
            for c in calls:
                c["message"]["stop_reason"] = None
        write_jsonl(self.contest / log, *calls, s_result(models or {OPUS: (12, 5000, 5300 + 200, 900)}))
        return log

    def test_night_row_takes_output_from_the_total_when_usage_is_partial(self):
        self.register(log=self.partial_stream())
        (r,) = self.rows()
        self.assertEqual((r.calls, r.output), (2, 900))
        self.assertEqual((r.input, r.cache_read, r.cache_write_5m, r.cache_write_1h), (12, 5000, 5300, 200))
        self.assertEqual(r.note, "totals from result; TTL split from calls")

    def test_weighted_cost_uses_the_corrected_output(self):
        self.register(log=self.partial_stream())
        (r,) = self.rows()
        self.assertEqual(r.weighted, 12 * 1 + 5000 * 0.1 + 5300 * 1.25 + 200 * 2 + 900 * 5)

    def test_outside_output_is_zero_after_the_correction(self):
        self.register(log=self.partial_stream(models={OPUS: (12, 5000, 5500, 900), HAIKU: (7, 0, 0, 3)}))
        (r,) = self.rows()
        self.assertEqual(r.output, 903)  # the model without a per-call record counts too (totals from result)
        self.assertEqual(r.outside, {OPUS: 0, HAIKU: 10})

    def test_every_model_takes_its_output_from_the_result(self):
        log = ".house/logs/T001-1.jsonl"
        partial = s_asst("m1", usage(10, 0, 5000, 0, 3))
        partial["message"]["stop_reason"] = None
        write_jsonl(self.contest / log, partial, s_asst("h1", usage(1, 0, 0, 0, 50), model=HAIKU),
                    s_result({OPUS: (10, 0, 5000, 400), HAIKU: (1, 0, 0, 999)}))
        self.register(log=log)
        (r,) = self.rows()
        self.assertEqual(r.output, 400 + 999)  # the result is the true total, also for HAIKU's complete call

    def test_stream_with_complete_usage_still_takes_the_result_totals(self):
        self.register(log=self.partial_stream(complete=True))
        (r,) = self.rows()
        self.assertEqual((r.output, r.note), (900, "totals from result; TTL split from calls"))
        self.assertEqual(r.outside, {OPUS: 900 - 7})

    def test_session_file_source_is_unchanged(self):
        wt = self.contest / ".worktrees" / "T001"
        partial = asst("m1", usage(10, 0, 500, 0, 3), str(wt), stop=None)
        self.session(SID, partial, cost_state({OPUS: (10, 0, 500, 900)}), cwd=wt)
        (r,) = self.rows()
        self.assertEqual((r.output, r.note), (3, ""))

    def test_stream_log_replaces_the_session_tree_and_still_counts_its_sub_agents(self):
        wt = self.contest / ".worktrees" / "T001"
        log = ".house/logs/T001-1.jsonl"
        write_jsonl(self.contest / log, s_asst("m1", usage(10, 0, 500, 0, 5), sid=SID),
                    s_asst("s1", usage(4, 0, 900, 0, 10), sid=SID, side_parent="toolu_1"),
                    s_asst("s2", usage(6, 0, 100, 0, 20), sid=SID, side_parent="toolu_1"))
        self.register(log=log, session=SID)
        main = self.session(SID, asst("m1", usage(10, 0, 500, 0, 5), str(wt)), cwd=wt)
        write_jsonl(main.with_suffix("") / "subagents" / "agent-a1.jsonl",
                    asst("x9", usage(999, 0, 0, 0, 999), str(wt), side=True))  # only the file tree has it
        (r,) = self.rows()
        # the stream carries the sub-agent events: 3 calls, the session tree is dropped (x9 never counts)
        self.assertEqual((r.kind, r.calls, r.input, r.output), ("night", 3, 20, 35))

    def test_codex_row_has_no_context(self):
        log = ".house/logs/T002-1.jsonl"
        write_jsonl(self.contest / log, {"type": "thread.started", "thread_id": "thr-1"},
                    {"type": "item.completed", "item": {"type": "agent_message", "id": "i1", "text": CANARY}},
                    {"type": "turn.completed", "usage": {"input_tokens": 1000, "cached_input_tokens": 600,
                                                         "output_tokens": 50}},
                    {"type": "turn.completed", "usage": {"input_tokens": 2000, "cached_input_tokens": 1500,
                                                         "output_tokens": 70}})
        self.register(task="T002", tool="codex", log=log)
        (r,) = self.rows()
        self.assertEqual((r.kind, r.session, r.date, r.calls), ("night", "thr-1", "2026-11-16", 2))
        self.assertEqual((r.context_first, r.context_median, r.context_peak), (None, None, None))
        # normalised: input 1000-600 = 400 and 2000-1500 = 500
        self.assertEqual((r.input, r.cache_read, r.cache_write_5m, r.output), (900, 2100, 0, 120))
        self.assertAlmostEqual(r.weighted, 900 + 210 + 600)
        self.assertIsNone(r.outside)  # its total is the sum of the same turns: nothing can be outside
        self.assertNotIn(CANARY, repr(r))

    def test_codex_log_recognised_by_its_first_record_when_the_register_has_no_tool(self):
        log = ".house/logs/T002-1.jsonl"
        write_jsonl(self.contest / log, {"type": "thread.started", "thread_id": "thr-2"},
                    {"type": "turn.completed", "usage": {"input_tokens": 10, "cached_input_tokens": 0,
                                                         "output_tokens": 5}})
        self.register(task="T002", tool="", log=log)
        (r,) = self.rows()
        self.assertEqual((r.session, r.calls, r.context_first), ("thr-2", 1, None))

    def test_register_rows_without_a_usable_log_are_skipped(self):
        self.register(task="T001", log="")                              # merge-like row, no log
        self.register(task="T002", log=".house/logs/missing.jsonl")     # log never written
        self.register(task="T003", log="../outside.jsonl")              # points out of the contest
        write_jsonl(self.root / "work" / "outside.jsonl", s_asst("m1", usage()))
        self.assertEqual(self.rows(), [])

    def test_two_attempts_sharing_one_log_count_once(self):
        log = self.night_stream()
        self.register(n=1, log=log)
        self.register(n=2, log=log)
        self.assertEqual([r.calls for r in self.rows()], [2])

    def test_since_keeps_sessions_from_that_date_and_unknown_dates(self):
        c = str(self.contest)
        self.session("s-old", asst("m1", usage(), c, sid="s-old", ts="2026-11-10T10:00:00.000Z"))
        self.session("s-new", asst("m2", usage(), c, sid="s-new", ts="2026-11-17T10:00:00.000Z"))
        self.session("s-edge", asst("m3", usage(), c, sid="s-edge", ts="2026-11-15T00:00:00.000Z"))
        self.assertEqual([r.session for r in self.rows(since="2026-11-15")], ["s-edge", "s-new"])
        self.assertEqual([r.session for r in self.rows(since=date(2026, 11, 16))], ["s-new"])
        self.assertEqual(self.rows(since="2027-01-01"), [])
        # a stream without timestamps and a register row without a start has no date: it cannot be proven old
        log = self.night_stream(sid="s-nodate")
        self.register(task="T009", log=log, start="")
        got = self.rows(since="2027-01-01")
        self.assertEqual([(r.session, r.date) for r in got], [("s-nodate", None)])

    def test_rows_sorted_by_date_then_session(self):
        c = str(self.contest)
        self.session("s-b", asst("m1", usage(), c, sid="s-b", ts="2026-11-17T10:00:00.000Z"))
        self.session("s-a", asst("m2", usage(), c, sid="s-a", ts="2026-11-17T09:00:00.000Z"))
        self.session("s-c", asst("m3", usage(), c, sid="s-c", ts="2026-11-16T09:00:00.000Z"))
        self.assertEqual([r.session for r in self.rows()], ["s-c", "s-a", "s-b"])


class ResultTotalsTest(unittest.TestCase):
    """C1: a stream log with a `result` takes its buckets from the last `result.modelUsage` (compaction summary calls
    have no `assistant` event, so per-call sums undercount)."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    @staticmethod
    def result(models: dict, split: bool = True) -> dict:
        """A `result` record; `models` maps a model to (input, cache_read, w5, w1, output)."""
        out = {}
        for m, (a, b, w5, w1, d) in models.items():
            out[m] = {"inputTokens": a, "cacheReadInputTokens": b, "cacheCreationInputTokens": w5 + w1,
                      "outputTokens": d, "costUSD": 1.0}
            if split:
                out[m].update({"cacheCreationEphemeral5mInputTokens": w5, "cacheCreationEphemeral1hInputTokens": w1})
        return {"type": "result", "subtype": "success", "session_id": SID, "modelUsage": out}

    def row(self, *lines) -> cost.Row:
        return cost.log_row(write_jsonl(self.dir / "run.jsonl", *lines))

    def test_buckets_come_from_the_result_and_include_calls_without_an_assistant_event(self):
        # per call: input 12, cache_read 5000, w5 5300, w1 200, output 150; the result also holds a compaction summary
        r = self.row(s_asst("m1", usage(10, 0, 5000, 0, 100)), s_asst("m2", usage(2, 5000, 300, 200, 50)),
                     self.result({OPUS: (243, 860000, 1968, 400825, 11824)}))
        self.assertEqual((r.input, r.cache_read, r.cache_write_5m, r.cache_write_1h, r.output),
                         (243, 860000, 1968, 400825, 11824))
        self.assertEqual(r.note, "totals from result")
        self.assertEqual(r.weighted, 243 + 86000 + 1968 * 1.25 + 400825 * 2 + 11824 * 5)
        # calls and contexts stay per call
        self.assertEqual((r.calls, r.context_first, r.context_peak), (2, 5010, 5502))

    def test_models_are_summed(self):
        r = self.row(s_asst("m1", usage(10, 0, 5000, 0, 100)),
                     self.result({OPUS: (10, 100, 5000, 0, 100), HAIKU: (5, 7, 3, 2, 9)}))
        self.assertEqual((r.input, r.cache_read, r.cache_write_5m, r.cache_write_1h, r.output),
                         (15, 107, 5003, 2, 109))

    def test_only_the_last_result_counts_since_model_usage_is_cumulative(self):
        r = self.row(s_asst("m1", usage(1, 0, 10, 0, 1)), self.result({OPUS: (2, 11617, 10, 0, 4)}),
                     s_asst("m2", usage(1, 11617, 10, 0, 1)), self.result({OPUS: (4, 26595, 20, 0, 7)}),
                     s_asst("m3", usage(1, 26595, 10, 0, 1)), self.result({OPUS: (6, 51465, 30, 0, 13)}))
        self.assertEqual((r.input, r.cache_read, r.cache_write_5m, r.output), (6, 51465, 30, 13))
        self.assertEqual(r.calls, 3)

    def test_without_a_ttl_split_the_per_call_proportions_are_used(self):
        # per call: w5 300, w1 100 -> 3:1; the total writes 4000
        r = self.row(s_asst("m1", usage(1, 0, 300, 100, 1)), self.result({OPUS: (5, 0, 4000, 0, 9)}, split=False))
        self.assertEqual((r.cache_write_5m, r.cache_write_1h), (3000, 1000))
        self.assertEqual(r.note, "totals from result; TTL split from calls")

    def test_without_a_ttl_split_or_per_call_writes_the_writes_are_5_minute(self):
        r = self.row(s_asst("m1", usage(1, 0, 0, 0, 1)), self.result({OPUS: (5, 0, 4000, 0, 9)}, split=False))
        self.assertEqual((r.cache_write_5m, r.cache_write_1h), (4000, 0))

    def test_stream_without_a_result_keeps_the_per_call_sums(self):
        r = self.row(s_asst("m1", usage(10, 0, 5000, 0, 100)), s_asst("m2", usage(2, 5000, 300, 200, 50)))
        self.assertEqual((r.input, r.cache_read, r.cache_write_5m, r.cache_write_1h, r.output),
                         (12, 5000, 5300, 200, 150))
        self.assertEqual(r.note, "no result totals")


class DateAndLabelTest(CostBase):
    def test_row_date_is_the_local_date_of_the_earliest_event(self):
        c = str(self.contest)
        self.session(SID, asst("m1", usage(), c, ts="2026-11-17T20:00:00.000Z"))
        with local_zone("Pacific/Auckland"):  # +13: already the 18th
            self.assertEqual([r.date for r in self.rows()], ["2026-11-18"])
            self.assertEqual([r.session for r in self.rows(since="2026-11-18")], [SID])
        with local_zone("America/Los_Angeles"):  # -8: still the 17th
            self.assertEqual([r.date for r in self.rows()], ["2026-11-17"])
            self.assertEqual(self.rows(since="2026-11-18"), [])

    def test_earliest_event_is_found_by_epoch_not_by_string_order(self):
        c = str(self.contest)
        # as text the second is smaller; as an instant the first (2026-11-17T22:30Z) is earlier
        self.session(SID, asst("m1", usage(), c, ts="2026-11-18T00:30:00.000+02:00"),
                     asst("m2", usage(), c, ts="2026-11-18T00:00:00.000Z"))
        with local_zone("UTC"):
            self.assertEqual([r.date for r in self.rows()], ["2026-11-17"])
            self.assertEqual([r.session for r in self.rows(since="2026-11-17")], [SID])

    def test_attempt_row_with_a_missing_log_still_labels_the_session_night(self):
        wt = self.contest / ".worktrees" / "T001"
        self.register(log=".house/logs/gone.jsonl", session=SID)
        self.session(SID, asst("m1", usage(), str(wt)), cwd=wt)
        (r,) = self.rows()
        self.assertEqual(r.kind, "night")

    def test_worktree_session_without_a_register_row_is_a_worker_guess(self):
        wt = self.contest / ".worktrees" / "T001-x"
        self.session("s-wt", asst("m1", usage(), str(wt), sid="s-wt"), cwd=wt)
        self.session("s-main", asst("m2", usage(), str(self.contest / "src"), sid="s-main"))
        self.session("s-late", user(str(self.contest), sid="s-late"), asst("m3", usage(), str(wt), sid="s-late"))
        got = {r.session: r.kind for r in self.rows()}
        self.assertEqual(got, {"s-wt": "worker?", "s-main": "interactive", "s-late": "interactive"})

    def test_worktree_session_with_a_register_row_stays_night(self):
        wt = self.contest / ".worktrees" / "T001"
        self.register(log=".house/logs/gone.jsonl", session="s-wt")
        self.session("s-wt", asst("m1", usage(), str(wt), sid="s-wt"), cwd=wt)
        (r,) = self.rows()
        self.assertEqual(r.kind, "night")


class SinceTest(CostBase):
    def setUp(self):
        super().setUp()
        c = str(self.contest)
        self.session("s-old", asst("m1", usage(), c, sid="s-old", ts="2026-11-10T10:00:00.000Z"))
        self.session("s-new", asst("m2", usage(), c, sid="s-new", ts="2026-11-17T10:00:00.000Z"))

    def test_every_input_form_is_normalised_to_a_date(self):
        for since in ("2026-11-17", date(2026, 11, 17), datetime(2026, 11, 17, 23, 59), datetime(2026, 11, 17, 0, 0,
                                                                                               tzinfo=timezone.utc)):
            self.assertEqual([r.session for r in self.rows(since=since)], ["s-new"], since)
        self.assertEqual([r.session for r in self.rows(since=datetime(2026, 11, 10, 23, 0))], ["s-old", "s-new"])

    def test_parse_since_accepts_only_iso_dates(self):
        self.assertEqual(cost.parse_since("2026-11-17"), "2026-11-17")
        self.assertIsNone(cost.parse_since(None))
        for bad in ("2026-13-01", "2026-11-31", "nov 5", "2026-1-5", "20261117", "2026-11-17T10:00", "", "2026-11-17 "):
            with self.assertRaises(ValueError, msg=bad):
                cost.parse_since(bad)
            with self.assertRaises(ValueError, msg=bad):
                self.rows(since=bad)


class FirstCwdTest(unittest.TestCase):
    def test_first_cwd_is_public_in_transcript(self):
        with tempfile.TemporaryDirectory() as d:
            p = write_jsonl(Path(d) / "s.jsonl", "junk", {"type": "mode"}, {"cwd": "/a/b"}, {"cwd": "/c"})
            self.assertEqual(transcript.first_cwd(p), "/a/b")
            self.assertIsNone(transcript.first_cwd(Path(d) / "absent.jsonl"))


class SignalTest(CostBase):
    def job(self, job_id: str, repo: Path, log: list | None) -> None:
        jobs = json.loads((self.signal / "jobs.json").read_text()) if (self.signal / "jobs.json").exists() else {}
        jobs[job_id] = {"id": job_id, "repo": str(repo), "created": "2026-11-17T08:00:00+01:00", "text": CANARY,
                        "status": "done"}
        write_jsonl(self.signal / "jobs.json", json.dumps(jobs))
        if log is not None:
            write_jsonl(self.signal / job_id / "log.jsonl", *log)

    def test_signal_job_of_the_contest_is_a_row_and_other_repos_are_not(self):
        sid = "sig-session-1"
        self.job("j1", self.contest, [s_asst("m1", usage(10, 0, 500, 0, 5), sid=sid),
                                      s_result({OPUS: (10, 0, 500, 5)}, sid=sid)])
        self.job("j2", self.root / "work" / "other", [s_asst("m9", usage(77), sid="sig-other")])
        self.job("j3", self.contest, None)  # no log yet
        (r,) = self.rows()
        self.assertEqual((r.kind, r.session, r.date, r.calls, r.input, r.cache_write_5m), ("signal", sid, "2026-11-17", 1,
                                                                                         10, 500))
        self.assertEqual(r.outside, {OPUS: 0})
        self.assertNotIn(CANARY, json.dumps(asdict(r)))

    def test_repo_is_matched_through_a_symlink(self):
        link = self.root / "link"
        link.symlink_to(self.contest)
        self.job("j1", link, [s_asst("m1", usage(), sid="sig-1")])
        self.assertEqual([r.session for r in self.rows()], ["sig-1"])

    def test_signal_log_without_per_call_usage_is_total_only(self):
        sid = "sig-session-2"
        self.job("j1", self.contest, [s_result({OPUS: (10, 20, 30, 40)}, sid=sid)])
        (r,) = self.rows()
        self.assertEqual(r.note, "total only")
        self.assertEqual((r.calls, r.input, r.cache_read, r.cache_write_5m, r.cache_write_1h, r.output),
                         (0, 10, 20, 30, 0, 40))
        self.assertEqual((r.context_first, r.outside), (None, None))
        self.assertEqual(r.model, OPUS)
        # the totals do not split cache writes by TTL: all of it is priced as a 5 minute write
        self.assertAlmostEqual(r.weighted, 10 + 2 + 37.5 + 200)

    def test_total_only_row_takes_the_model_with_most_tokens_in_the_total(self):
        sid = "sig-session-5"
        self.job("j1", self.contest, [s_result({HAIKU: (1, 1, 1, 1), OPUS: (10, 20, 30, 40), "m-small": (1, 0, 0, 0)},
                                               sid=sid)])
        (r,) = self.rows()
        self.assertEqual((r.note, r.model), ("total only", OPUS))

    def test_signal_job_also_present_as_session_file_counts_once(self):
        sid = "sig-session-3"
        wt = self.contest / ".worktrees" / "signal-j1"
        self.job("j1", self.contest, [s_asst("m1", usage(10, 0, 500, 0, 5), sid=sid)])
        self.session(sid, asst("m1", usage(10, 0, 500, 0, 5), str(wt), sid=sid), cwd=wt)
        (r,) = self.rows()
        self.assertEqual((r.kind, r.calls), ("signal", 1))

    def test_reading_jobs_creates_nothing(self):
        missing = self.root / "fresh-data"
        with mock.patch.dict(os.environ, {"HOUSE_DATA_DIR": str(missing)}):
            self.assertEqual(cost.rows(self.contest, projects=self.projects), [])
        self.assertFalse(missing.exists())
        self.assertFalse(self.signal.exists())
        self.assertEqual(self.rows(), [])
        self.assertFalse(self.signal.exists())

    def test_default_signal_dir_follows_house_data_dir(self):
        data = self.root / "data"
        self.signal = data / "signal" / "agent"
        self.job("j1", self.contest, [s_asst("m1", usage(), sid="sig-4")])
        with mock.patch.dict(os.environ, {"HOUSE_DATA_DIR": str(data)}):
            self.assertEqual([r.session for r in cost.rows(self.contest, projects=self.projects)], ["sig-4"])

    def test_broken_job_files_never_crash(self):
        write_jsonl(self.signal / "jobs.json", "[1, 2")
        self.assertEqual(self.rows(), [])
        write_jsonl(self.signal / "jobs.json", json.dumps({"x": 5, "../evil": {"id": "../evil", "repo": str(self.contest)},
                                                           "j": {"id": "j", "repo": 7}}))
        self.assertEqual(self.rows(), [])


def table_row(**kw) -> cost.Row:
    base = dict(session="s", kind="interactive", date="2026-11-17", model=OPUS, calls=3, context_first=42_000,
                context_median=60_000, context_peak=150_000, input=13, cache_read=10_500_000, cache_write_5m=5400,
                cache_write_1h=200, output=500, compactions=2, max_pre_tokens=180_000,
                outside={"a": 7000, "b": 3000}, weighted=0.0, note="")
    return cost.Row(**{**base, **kw})


def cells(line: str) -> list[str]:
    return re.split(r"  +", line.strip())


FOOTER = "estimate of API cost (relative prices), not a bill and not subscription usage"
WEIGHTS_LINE = "weights: input 1, cache_read 0.1, cache_write_5m 1.25, cache_write_1h 2, output 5"
TITLES = ["date", "kind", "model", "calls", "ctx_first", "ctx_med", "ctx_peak", "input", "cache_r", "cache_w5",
          "cache_w1h", "output", "compact", "outside", "weighted", "note"]


class RenderTest(unittest.TestCase):
    def setUp(self):
        self.old = table_row()
        self.night = table_row(kind="night", date="2026-11-16", model=None, calls=0, context_first=None,
                               context_median=None, context_peak=None, input=0, cache_read=0, cache_write_5m=0,
                               cache_write_1h=0, output=0, compactions=0, max_pre_tokens=None, outside=None, weighted=None,
                               note="no data")

    def lines(self, rows=None, weights=None) -> list[str]:
        return cost.render(rows if rows is not None else [self.old, self.night], weights or cost.DEFAULT_WEIGHTS
                           ).splitlines()

    def test_columns_rows_totals_and_footer(self):
        lines = self.lines()
        self.assertEqual(cells(lines[0]), TITLES)
        # sorted by date, so the night row (11-16) comes first
        self.assertEqual(cells(lines[1])[:15], ["2026-11-16", "night", "—", "0", "—", "—", "—", "0", "0", "0", "0",
                                                 "0", "0", "—", "—"])
        self.assertEqual(cells(lines[1])[15], "no data")
        # 13 tokens < 0.05K; 10.5M = 10500K; weighted 13 + 1050000 + 6750 + 400 + 2500 = 1059663
        self.assertEqual(cells(lines[2]), ["2026-11-17", "interactive", OPUS, "3", "42K", "60K", "150K", "<0.1K",
                                           "10500K", "5.4K", "0.2K", "0.5K", "2 (180K)", "10K", "1059663"])
        self.assertEqual(cells(lines[3]), ["total", "2 sessions", "—", "3", "—", "—", "—", "<0.1K", "10500K", "5.4K",
                                           "0.2K", "0.5K", "2 (180K)", "10K", "1059663"])
        self.assertEqual(lines[-2], WEIGHTS_LINE)
        self.assertEqual(lines[-1], FOOTER)

    def test_columns_are_aligned(self):
        lines = self.lines()

        def spans(line):
            return [m.span() for m in re.finditer(r"\S+(?: \S+)*", line)]
        head = spans(lines[0])
        for line in lines[1:4]:
            row = spans(line)
            for i in range(3):  # date, kind, model: left aligned
                self.assertEqual(row[i][0], head[i][0], (i, line))
            for i in range(3, 15):  # numbers: right aligned
                self.assertEqual(row[i][1], head[i][1], (i, line))

    def test_weights_override_the_default(self):
        w = {"input": 0, "cache_read": 0, "cache_write_5m": 0, "cache_write_1h": 0, "output": 2}
        lines = self.lines([self.old], w)
        self.assertEqual(cells(lines[1])[14], "1000")
        self.assertEqual(lines[-2], "weights: input 0, cache_read 0, cache_write_5m 0, cache_write_1h 0, output 2")

    def test_rows_without_a_weighted_cost_add_nothing_to_the_total(self):
        lines = self.lines([self.night])
        self.assertEqual(cells(lines[2])[:2], ["total", "1 session"])
        self.assertEqual(cells(lines[2])[14], "—")

    def test_no_rows(self):
        self.assertEqual(cost.render([], cost.DEFAULT_WEIGHTS), "no sessions")

    def test_model_text_is_sanitised(self):
        lines = self.lines([table_row(model="x\ny z" + "q" * 80)])
        self.assertEqual(len(lines[1].splitlines()), 1)
        self.assertTrue(cells(lines[1])[2].startswith("x?y?zqqq"))
        self.assertLessEqual(len(cells(lines[1])[2]), 28)

    def test_model_names_that_look_like_paths_are_shown_as_unknown(self):
        for model in ("/home/user/models/x", "~/models/x", "./x", "../x", "C:\\models\\x", "C:/models/x"):
            lines = self.lines([table_row(model=model)])
            self.assertEqual(cells(lines[1])[2], "?", model)
            self.assertNotIn("models", "\n".join(lines))
        self.assertEqual(cells(self.lines([table_row(model="anthropic/claude-x")])[1])[2], "anthropic/claude-x")

    def test_compaction_column_without_pre_tokens(self):
        lines = self.lines([table_row(compactions=1, max_pre_tokens=None)])
        self.assertEqual(cells(lines[1])[12], "1")

    def test_k_formatting(self):
        k = cost.format_k
        self.assertEqual([k(n) for n in (None, 0, 49, 50, 949, 5400, 9949, 9950, 42_499, 42_500, 1_234_567)],
                         ["—", "0", "<0.1K", "0.1K", "0.9K", "5.4K", "9.9K", "10K", "42K", "43K", "1235K"])


class WeightsFromSpecTest(unittest.TestCase):
    def test_defaults_without_a_cost_section(self):
        self.assertEqual(cost.weights_from_spec({}), (cost.DEFAULT_WEIGHTS, None))
        self.assertEqual(cost.weights_from_spec({"cost": {"weights": None}}), (cost.DEFAULT_WEIGHTS, None))
        self.assertEqual(cost.weights_from_spec({"cost": {"other": 1}}), (cost.DEFAULT_WEIGHTS, None))

    def test_overrides_numbers_and_numeric_strings_and_ignores_unknown_keys(self):
        w, warning = cost.weights_from_spec({"cost": {"weights": {"output": 3, "cache_read": "0.5", "bogus": "x",
                                                                    "input": 0}}})
        self.assertEqual(w, {"input": 0, "cache_read": 0.5, "cache_write_5m": 1.25, "cache_write_1h": 2, "output": 3})
        self.assertIsNone(warning)

    def test_bad_values_fall_back_with_one_warning_line(self):
        w, warning = cost.weights_from_spec({"cost": {"weights": {"output": "abc", "input": -1, "cache_read": True,
                                                                    "cache_write_5m": "nan", "cache_write_1h": 4}}})
        self.assertEqual(w, {"input": 1, "cache_read": 0.1, "cache_write_5m": 1.25, "cache_write_1h": 4, "output": 5})
        self.assertEqual(len(warning.splitlines()), 1)
        for key in ("output", "input", "cache_read", "cache_write_5m"):
            self.assertIn(key, warning)
        self.assertNotIn("abc", warning)  # values are never echoed

    def test_weights_that_are_not_a_mapping_fall_back_with_a_warning(self):
        w, warning = cost.weights_from_spec({"cost": {"weights": ["1", "2"]}})
        self.assertEqual(w, cost.DEFAULT_WEIGHTS)
        self.assertIn("cost.weights", warning)
        w, warning = cost.weights_from_spec({"cost": "x"})
        self.assertEqual((w, warning is None), (cost.DEFAULT_WEIGHTS, True))

    def test_result_is_a_copy(self):
        w, _ = cost.weights_from_spec({})
        w["output"] = 99
        self.assertEqual(cost.DEFAULT_WEIGHTS["output"], 5)


if __name__ == "__main__":
    unittest.main()
