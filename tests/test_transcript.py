"""`houselib.transcript`: the event model and the Claude Code stream-json reader (synthetic logs only)."""
import json
import tempfile
import unittest
from pathlib import Path

from houselib import transcript

SID = "11111111-2222-3333-4444-555555555555"


def usage(inp=3, read=100, w5=40, w1=10, out=7) -> dict:
    return {"input_tokens": inp, "cache_read_input_tokens": read, "cache_creation_input_tokens": w5 + w1,
            "cache_creation": {"ephemeral_5m_input_tokens": w5, "ephemeral_1h_input_tokens": w1},
            "output_tokens": out}


def assistant(block: dict, mid="msg_1", rid="req_1", u=None, **extra) -> dict:
    return {"type": "assistant", "session_id": SID, "request_id": rid, "parent_tool_use_id": None,
            "message": {"id": mid, "model": "claude-haiku-4-5", "role": "assistant", "content": [block],
                        "stop_reason": None, "usage": u or usage()}, **extra}


def user_result(tool_id: str, content="out", **block_extra) -> dict:
    return {"type": "user", "session_id": SID, "parent_tool_use_id": None,
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tool_id,
                                                      "content": content, **block_extra}]}}


def result(cost: float, inp: int) -> dict:
    return {"type": "result", "subtype": "success", "session_id": SID, "total_cost_usd": cost,
            "modelUsage": {"claude-haiku-4-5": {"inputTokens": inp, "outputTokens": 20,
                                                "cacheReadInputTokens": 111, "cacheCreationInputTokens": 50,
                                                "costUSD": cost}}}


class StreamTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "stream.jsonl"

    def parse(self, *lines) -> transcript.Parsed:
        """Write records (dicts as JSON, strings verbatim) one per line and parse the file."""
        self.path.write_text("\n".join(l if isinstance(l, str) else json.dumps(l) for l in lines))
        return transcript.claude_stream(self.path)

    @staticmethod
    def kinds(p: transcript.Parsed, kind: str) -> list:
        return [e for e in p.events if e.kind == kind]

    def test_stream_usage_once_per_call(self):
        p = self.parse(
            assistant({"type": "thinking", "thinking": "hm"}),
            assistant({"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {"command": "ls"}}),
            assistant({"type": "text", "text": "hello"}))
        us = self.kinds(p, "usage")
        self.assertEqual(len(us), 1)
        self.assertEqual(us[0].data, {"key": ("msg_1", "req_1"), "input": 3, "cache_read": 100,
                                      "cache_write_5m": 40, "cache_write_1h": 10, "output": 7, "complete": False})
        self.assertEqual((us[0].source, us[0].session_id, us[0].model),
                         ("stream", SID, "claude-haiku-4-5"))
        calls = self.kinds(p, "tool_call")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].data, {"id": "toolu_1", "name": "Bash", "input": {"command": "ls"}})
        msgs = self.kinds(p, "message")
        self.assertEqual([m.data for m in msgs], [{"role": "assistant", "length": 5}])
        self.assertEqual(p.unknown, {})
        self.assertEqual(p.broken, 0)

    def test_stream_complete_follows_the_stop_reason(self):
        done = assistant({"type": "text", "text": "x"})
        done["message"]["stop_reason"] = "end_turn"
        (u,) = self.kinds(self.parse(done), "usage")
        self.assertIs(u.data["complete"], True)

    def test_stream_partial_then_complete_record_keeps_the_complete_one(self):
        text = {"type": "text", "text": "x"}
        done = assistant(text, u=usage(out=90))
        done["message"]["stop_reason"] = "end_turn"
        p = self.parse(assistant(text, u=usage(out=1)), assistant({"type": "tool_use", "id": "t", "name": "Bash",
                                                                   "input": {}}, u=usage(out=2)), done)
        (u,) = self.kinds(p, "usage")
        self.assertEqual((u.data["output"], u.data["complete"]), (90, True))
        self.assertEqual(len(self.kinds(p, "tool_call")), 1)  # the content blocks are still their own events

    def test_stream_complete_record_is_not_replaced_by_a_later_partial_one(self):
        text = {"type": "text", "text": "x"}
        done = assistant(text, u=usage(out=90))
        done["message"]["stop_reason"] = "end_turn"
        (u,) = self.kinds(self.parse(done, assistant(text, u=usage(out=95))), "usage")
        self.assertEqual((u.data["output"], u.data["complete"]), (90, True))

    def test_stream_two_partial_records_keep_the_later_one(self):
        text = {"type": "text", "text": "x"}
        (u,) = self.kinds(self.parse(assistant(text, u=usage(out=1)), assistant(text, u=usage(out=5))), "usage")
        self.assertEqual(u.data["output"], 5)

    def test_stream_two_calls_two_usages(self):
        p = self.parse(assistant({"type": "text", "text": "a"}, mid="m1", rid="r1"),
                       assistant({"type": "text", "text": "b"}, mid="m2", rid="r2"))
        self.assertEqual([e.data["key"] for e in self.kinds(p, "usage")], [("m1", "r1"), ("m2", "r2")])

    def test_stream_cache_write_without_split(self):
        u = {"input_tokens": 1, "cache_creation_input_tokens": 9, "output_tokens": 2}
        p = self.parse(assistant({"type": "text", "text": "a"}, u=u))
        d = self.kinds(p, "usage")[0].data
        self.assertEqual((d["cache_write_5m"], d["cache_write_1h"], d["cache_read"]), (9, 0, 0))

    def test_stream_subagent_fields(self):
        rec = assistant({"type": "text", "text": "sub"}, parent_tool_use_id="toolu_agent", agent_id="a1b2")
        rec["parent_tool_use_id"] = "toolu_agent"
        p = self.parse(rec)
        for e in p.events:
            self.assertEqual((e.parent_tool_use_id, e.agent_id, e.sidechain), ("toolu_agent", "a1b2", True))
        top = self.parse(assistant({"type": "text", "text": "x"})).events[0]
        self.assertEqual((top.parent_tool_use_id, top.agent_id, top.sidechain), ("", "", False))

    def test_stream_context_from_init(self):
        init = {"type": "system", "subtype": "init", "session_id": SID, "cwd": "/w", "claude_code_version": "2.1.326"}
        e = self.parse(init, assistant({"type": "text", "text": "a"})).events[0]
        self.assertEqual((e.cwd, e.cli_version), ("/w", "2.1.326"))

    def test_stream_last_result_is_total(self):
        p = self.parse(result(0.01, 10), result(0.03, 30))
        tot = self.kinds(p, "session_total")
        self.assertEqual(len(tot), 1)
        self.assertEqual(tot[0].data, {"models": {"claude-haiku-4-5": {
            "input": 30, "cache_read": 111, "cache_write": 50, "output": 20, "cost_usd": 0.03}},
            "cost_usd": 0.03, "since_ms": None})

    def test_stream_compaction(self):
        rec = {"type": "system", "subtype": "compact_boundary", "session_id": SID,
               "compact_metadata": {"trigger": "auto", "pre_tokens": 75726, "post_tokens": 9333,
                                    "duration_ms": 4200, "preserved_segment": {"head_uuid": "x"}}}
        p = self.parse(rec)
        self.assertEqual(len(p.events), 1)
        self.assertEqual(p.events[0].kind, "compaction")
        self.assertEqual(p.events[0].data, {"trigger": "auto", "pre_tokens": 75726, "post_tokens": 9333,
                                            "duration_ms": 4200})
        self.assertEqual(p.unknown, {})

    def test_stream_tolerant(self):
        p = self.parse(
            assistant({"type": "text", "text": "ok"}),
            "warning: something on stderr",
            {"type": "x-new", "foo": 1},
            {"type": "system", "subtype": "brand_new"},
            {"type": "system", "subtype": "hook_response", "stdout": "secret"},
            {"type": "rate_limit_event"},
            "[1, 2]",
            '{"type": "assistant", "message": {"id": "msg_9", "con')  # truncated last line
        self.assertEqual(p.broken, 3)
        self.assertEqual(p.unknown, {"x-new": 1, "system/brand_new": 1})
        self.assertEqual(sorted(e.kind for e in p.events), ["message", "usage"])

    def test_stream_malformed_records_never_raise(self):
        p = self.parse(
            {"type": "assistant", "message": "nope"},
            {"type": "assistant", "message": {"id": "m", "content": "text", "usage": []}},
            {"type": "assistant", "message": {"id": "m2", "content": [1, None, {"type": "tool_use"}]}},
            {"type": "user", "message": {"content": [{"type": "tool_result"}, "x"]}},
            {"type": "system", "subtype": "compact_boundary", "compact_metadata": 5},
            {"type": "result", "modelUsage": "bad", "total_cost_usd": "x"},
            {"no": "type"})
        self.assertEqual(p.broken, 0)
        self.assertIn("?", p.unknown)

    def test_stream_tool_result_error_states(self):
        p = self.parse(user_result("t1", is_error=True), user_result("t2", is_error=False), user_result("t3"))
        got = [(e.data["id"], e.data["is_error"]) for e in self.kinds(p, "tool_result")]
        self.assertEqual(got, [("t1", True), ("t2", False), ("t3", None)])

    def test_stream_tool_result_error_from_record_level_result(self):
        def rec(tid, tur=None, **block):
            r = user_result(tid, **block)
            if tur != "absent":
                r["tool_use_result"] = tur
            return r
        p = self.parse(rec("d", {"stdout": "x"}), rec("l", [{"type": "text", "text": "x"}]), rec("s", "Error: boom"),
                       rec("m", "absent"), rec("n", None), rec("i", 5),
                       rec("w1", "Error: boom", is_error=False), rec("w2", {"a": 1}, is_error=True))
        self.assertEqual([(e.data["id"], e.data["is_error"]) for e in self.kinds(p, "tool_result")],
                         [("d", False), ("l", False), ("s", True), ("m", None), ("n", None), ("i", None),
                          ("w1", False), ("w2", True)])

    def test_stream_tool_result_size(self):
        listed = [{"type": "text", "text": "abc"}, {"type": "image", "source": {}}, {"type": "text", "text": "de"}]
        p = self.parse(user_result("t1", "hello"), user_result("t2", listed), user_result("t3", None))
        self.assertEqual([e.data["size"] for e in self.kinds(p, "tool_result")], [5, 5, 0])

    def test_deep_nesting_counts_as_broken(self):
        p = self.parse("[" * 100_000, assistant({"type": "text", "text": "ok"}))
        self.assertEqual(p.broken, 1)
        self.assertEqual(sorted(e.kind for e in p.events), ["message", "usage"])

    def test_raw_is_source_record(self):
        rec = assistant({"type": "text", "text": "a"})
        self.assertEqual(self.parse(rec).events[0].raw, rec)

    def test_empty_file(self):
        p = self.parse()
        self.assertEqual((p.events, p.unknown, p.broken), ([], {}, 0))

    def test_missing_file_is_empty(self):
        p = transcript.claude_stream(self.path.parent / "absent.jsonl")
        self.assertEqual((p.events, p.unknown, p.broken), ([], {}, 0))


def codex_cmd(cmd="ls", out="a\nb", code=0, iid="item_1") -> list[dict]:
    started = {"id": iid, "type": "command_execution", "command": cmd, "aggregated_output": "", "exit_code": None,
               "status": "in_progress"}
    done = {**started, "aggregated_output": out, "exit_code": code, "status": "completed" if code == 0 else "failed"}
    return [{"type": "item.started", "item": started}, {"type": "item.completed", "item": done}]


def codex_turn(inp=28659, cached=26368, write=0, out=100) -> dict:
    return {"type": "turn.completed",
            "usage": {"input_tokens": inp, "cached_input_tokens": cached, "cache_write_input_tokens": write,
                      "output_tokens": out, "reasoning_output_tokens": 5}}


class CodexTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "codex.jsonl"

    def parse(self, *lines) -> transcript.Parsed:
        self.path.write_text("\n".join(l if isinstance(l, str) else json.dumps(l) for l in lines))
        return transcript.codex_stream(self.path)

    @staticmethod
    def kinds(p: transcript.Parsed, kind: str) -> list:
        return [e for e in p.events if e.kind == kind]

    def test_codex_normalised_tokens(self):
        u = codex_turn()["usage"]
        self.assertLessEqual(u["cached_input_tokens"], u["input_tokens"])
        p = self.parse({"type": "thread.started", "thread_id": "th1"}, {"type": "turn.started"}, codex_turn())
        us = self.kinds(p, "usage")
        self.assertEqual(len(us), 1)
        self.assertEqual(us[0].data, {"key": ("th1", 0), "input": 2291, "cache_read": 26368, "cache_write_5m": 0,
                                      "cache_write_1h": 0, "output": 100, "complete": True})
        self.assertEqual((us[0].source, us[0].session_id), ("codex", "th1"))
        # cache writes are carved out of input too, and input never goes below 0
        w = self.parse(codex_turn(inp=1000, cached=200, write=300), codex_turn(inp=10, cached=8, write=8))
        self.assertEqual([(e.data["input"], e.data["cache_write_5m"]) for e in self.kinds(w, "usage")],
                         [(500, 300), (0, 8)])

    def test_codex_exit_codes(self):
        missing = codex_cmd(iid="c")
        del missing[1]["item"]["exit_code"]
        p = self.parse({"type": "thread.started", "thread_id": "th1"},
                       *codex_cmd("true", code=0, iid="a"), *codex_cmd("false", out="x", code=1, iid="b"), *missing)
        self.assertEqual([(e.data["id"], e.data["name"], e.data["input"]) for e in self.kinds(p, "tool_call")],
                         [("a", "Bash", {"command": "true"}), ("b", "Bash", {"command": "false"}),
                          ("c", "Bash", {"command": "ls"})])
        self.assertEqual([(e.data["id"], e.data["is_error"], e.data["size"]) for e in self.kinds(p, "tool_result")],
                         [("a", False, 3), ("b", True, 1), ("c", None, 3)])

    def test_codex_two_turns_total(self):
        p = self.parse({"type": "thread.started", "thread_id": "th1"}, {"type": "turn.started"},
                       {"type": "item.completed", "item": {"id": "m", "type": "agent_message", "text": "hello"}},
                       codex_turn(inp=1000, cached=400, out=10), {"type": "turn.started"},
                       codex_turn(inp=2000, cached=1000, write=100, out=20))
        self.assertEqual([e.data["key"] for e in self.kinds(p, "usage")], [("th1", 0), ("th1", 1)])
        self.assertEqual(self.kinds(p, "message")[0].data, {"role": "assistant", "length": 5})
        tot = self.kinds(p, "session_total")
        self.assertEqual(len(tot), 1)
        self.assertIs(p.events[-1], tot[0])
        self.assertEqual(tot[0].data, {"models": {"unknown": {"input": 600 + 900, "cache_read": 1400,
                                                              "cache_write": 100, "output": 30, "cost_usd": None}},
                                       "cost_usd": None, "since_ms": None})
        self.assertEqual(tot[0].source, "codex")

    def test_codex_deep_nesting_counts_as_broken(self):
        p = self.parse("[" * 100_000, {"type": "thread.started", "thread_id": "th1"})
        self.assertEqual(p.broken, 1)

    def test_codex_failed_status_without_exit_code_is_error(self):
        failed, ok, unknown = codex_cmd(iid="f"), codex_cmd(iid="o"), codex_cmd(iid="u")
        for c in (failed, ok, unknown):
            del c[1]["item"]["exit_code"]
        failed[1]["item"]["status"] = "failed"
        ok[1]["item"]["status"] = "completed"
        code = codex_cmd(iid="c", code=0)
        code[1]["item"]["status"] = "failed"  # a real exit code wins over the status
        p = self.parse(*failed, *ok, *unknown, *code)
        self.assertEqual([(e.data["id"], e.data["is_error"]) for e in self.kinds(p, "tool_result")],
                         [("f", True), ("o", None), ("u", None), ("c", False)])

    def test_codex_truncated_last_line_and_unknown(self):
        p = self.parse({"type": "thread.started", "thread_id": "th1"}, {"type": "mystery.event"},
                       {"type": "item.completed", "item": {"type": "web_search"}}, {"type": "turn.started"},
                       '{"type": "turn.completed", "usage": {"input_tok')
        self.assertEqual(p.broken, 1)
        self.assertEqual(p.unknown, {"mystery.event": 1, "item.completed/web_search": 1})
        self.assertEqual(self.kinds(p, "session_total"), [])

    def test_codex_file_change_becomes_edit_calls(self):
        done = {"id": "item_3", "type": "file_change", "status": "completed",
                "changes": [{"path": "/w/a.py", "kind": "update"}, {"path": "/w/tests/test_a.py", "kind": "add"}]}
        failed = {"id": "item_4", "type": "file_change", "status": "failed", "changes": [{"path": "/w/b.py"}]}
        p = self.parse({"type": "thread.started", "thread_id": "th1"},
                       {"type": "item.completed", "item": done}, {"type": "item.completed", "item": failed})
        calls, results = self.kinds(p, "tool_call"), self.kinds(p, "tool_result")
        self.assertEqual([(e.data["name"], e.data["input"]) for e in calls],
                         [("Edit", {"file_path": "/w/a.py"}), ("Edit", {"file_path": "/w/tests/test_a.py"}),
                          ("Edit", {"file_path": "/w/b.py"})])
        self.assertEqual([c.data["id"] for c in calls], [r.data["id"] for r in results])
        self.assertEqual(len({c.data["id"] for c in calls}), 3)
        self.assertEqual([r.data["is_error"] for r in results], [False, False, True])
        self.assertEqual(p.unknown, {})

    def test_codex_file_change_odd_shapes(self):
        items = [{"type": "file_change"}, {"type": "file_change", "changes": "x", "status": "completed"},
                 {"type": "file_change", "changes": [5, {"path": 3}, {"kind": "add"}, None], "status": "completed"}]
        p = self.parse(*({"type": "item.completed", "item": i} for i in items))
        self.assertEqual((p.events, p.unknown, p.broken), ([], {}, 0))

    def test_codex_odd_shapes_and_missing_file(self):
        p = self.parse({"type": "turn.completed", "usage": "bad"}, {"type": "item.completed", "item": 5}, [1])
        self.assertEqual(p.broken, 1)
        self.assertEqual((p.events, p.unknown), ([], {"item.completed": 1}))
        m = transcript.codex_stream(self.path.parent / "absent.jsonl")
        self.assertEqual((m.events, m.unknown, m.broken), ([], {}, 0))


def s_assistant(block: dict, mid="msg_1", rid="req_1", stop=None, model="claude-haiku-4-5", u=None, **extra) -> dict:
    """An interactive-session `assistant` record (camelCase `requestId`, top-level context)."""
    return {"type": "assistant", "sessionId": SID, "requestId": rid, "timestamp": "2026-11-17T10:00:00Z",
            "cwd": "/work/contest", "gitBranch": "main", "version": "2.1.326", "isSidechain": False,
            "message": {"id": mid, "model": model, "role": "assistant", "content": [block], "stop_reason": stop,
                        "usage": u or usage()}, **extra}


def s_user(content, **extra) -> dict:
    return {"type": "user", "sessionId": SID, "cwd": "/work/contest", "isSidechain": False,
            "message": {"role": "user", "content": content}, **extra}


def cost_state(total: float, inp: int, extra_models: dict | None = None) -> dict:
    return {"type": "cost-state", "sessionId": SID, "totalCostUSD": total,
            "modelUsage": {"claude-opus-4-7": {"inputTokens": inp, "outputTokens": 20, "cacheReadInputTokens": 111,
                                               "cacheCreationInputTokens": 50, "costUSD": total}, **(extra_models or {})}}


def write_jsonl(path: Path, *lines) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(l if isinstance(l, str) else json.dumps(l) for l in lines))
    return path


class SessionTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.main = self.root / f"{SID}.jsonl"

    def parse(self, *lines) -> transcript.Parsed:
        write_jsonl(self.main, *lines)
        return transcript.claude_session(self.main)

    @staticmethod
    def kinds(p: transcript.Parsed, kind: str) -> list:
        return [e for e in p.events if e.kind == kind]

    def test_session_with_subagent(self):
        write_jsonl(self.main, s_assistant({"type": "text", "text": "main"}, mid="m_main", rid="r_main"))
        sub = self.root / SID / "subagents"
        write_jsonl(sub / "agent-a1.jsonl",
                    s_assistant({"type": "tool_use", "id": "toolu_s", "name": "Read", "input": {}}, mid="m_sub",
                                rid="r_sub", stop="tool_use", isSidechain=True, u=usage(inp=5, read=0, w5=0, w1=0, out=9)),
                    s_user([{"type": "tool_result", "tool_use_id": "toolu_s", "content": "xyz"}], isSidechain=True))
        write_jsonl(sub / "agent-a1.meta.json", {"toolUseId": "toolu_agent", "agentType": "Explore", "spawnDepth": 1})
        p = transcript.claude_session(self.main)
        self.assertEqual(p.broken, 0)
        self.assertEqual(p.unknown, {})
        us = self.kinds(p, "usage")
        self.assertEqual([e.data["key"] for e in us], [("m_main", "r_main"), ("m_sub", "r_sub")])
        self.assertEqual(sum(e.data["output"] for e in us), 16)  # totals include the sub-agent
        main_ev = [e for e in p.events if e.agent_id == ""]
        self.assertTrue(main_ev)
        self.assertFalse(any(e.sidechain or e.parent_tool_use_id for e in main_ev))
        sub_ev = [e for e in p.events if e.agent_id == "a1"]
        self.assertEqual({e.kind for e in sub_ev}, {"usage", "tool_call", "tool_result"})
        for e in sub_ev:
            self.assertEqual((e.parent_tool_use_id, e.agent_type, e.sidechain), ("toolu_agent", "Explore", True))
        self.assertEqual(us[0].data["complete"], False)
        self.assertEqual(us[1].data["complete"], True)
        self.assertEqual((us[0].session_id, us[0].cwd, us[0].git_branch, us[0].cli_version, us[0].source),
                         (SID, "/work/contest", "main", "2.1.326", "session"))

    def test_meta_missing(self):
        write_jsonl(self.main, s_user("hi"))
        sub = self.root / SID / "subagents"
        write_jsonl(sub / "agent-b2.jsonl", s_assistant({"type": "text", "text": "x"}, isSidechain=True))
        p = transcript.claude_session(self.main)
        ev = [e for e in p.events if e.agent_id == "b2"]
        self.assertTrue(ev)
        self.assertEqual({(e.parent_tool_use_id, e.agent_type, e.sidechain) for e in ev}, {("", "", True)})

    def test_meta_malformed(self):
        write_jsonl(self.main, s_user("hi"))
        sub = self.root / SID / "subagents"
        write_jsonl(sub / "agent-c3.jsonl", s_assistant({"type": "text", "text": "x"}, isSidechain=True))
        (sub / "agent-c3.meta.json").write_text("{not json")
        write_jsonl(sub / "agent-d4.jsonl", s_assistant({"type": "text", "text": "x"}, isSidechain=True))
        write_jsonl(sub / "agent-d4.meta.json", [1, 2])
        write_jsonl(sub / "agent-e5.jsonl", s_assistant({"type": "text", "text": "x"}, isSidechain=True))
        write_jsonl(sub / "agent-e5.meta.json", {"toolUseId": 7, "agentType": None})
        p = transcript.claude_session(self.main)
        self.assertEqual({e.agent_id for e in p.events if e.agent_id}, {"c3", "d4", "e5"})
        self.assertEqual({e.parent_tool_use_id for e in p.events}, {""})

    def test_cost_state_total(self):
        p = self.parse(s_user("hi"), cost_state(1.0, 100),
                       cost_state(2.5, 300, {"claude-haiku-4-5": {"inputTokens": 7, "outputTokens": 1,
                                                                  "cacheReadInputTokens": 2,
                                                                  "cacheCreationInputTokens": 3, "costUSD": 0.01}}))
        tot = self.kinds(p, "session_total")
        self.assertEqual(len(tot), 1)
        self.assertEqual(tot[0].data["cost_usd"], 2.5)
        self.assertEqual(tot[0].data["models"], {
            "claude-opus-4-7": {"input": 300, "cache_read": 111, "cache_write": 50, "output": 20, "cost_usd": 2.5},
            "claude-haiku-4-5": {"input": 7, "cache_read": 2, "cache_write": 3, "output": 1, "cost_usd": 0.01}})
        self.assertEqual(p.unknown, {})

    def test_cost_state_since_ms_between_usage_records(self):
        start = 1_763_373_000_000  # 2025-11-17T10:30:00Z
        before = s_assistant({"type": "text", "text": "a"}, mid="m1", rid="r1", timestamp="2025-11-17T10:00:00Z")
        after = s_assistant({"type": "text", "text": "b"}, mid="m2", rid="r2", timestamp="2025-11-17T11:00:00Z")
        p = self.parse(before, {**cost_state(2.5, 300), "startTime": start}, after)
        tot = self.kinds(p, "session_total")
        self.assertEqual(tot[0].data["since_ms"], start)
        self.assertEqual(transcript.view(tot[0])["data"]["since_ms"], start)
        self.assertEqual([e.ts for e in self.kinds(p, "usage")], ["2025-11-17T10:00:00Z", "2025-11-17T11:00:00Z"])

    def test_cost_state_since_ms_uses_last_record_and_only_ints(self):
        for bad in (None, "1763373000000", 1.5, True, [1]):
            p = self.parse(s_user("hi"), {**cost_state(1.0, 1), "startTime": 5}, {**cost_state(2.0, 2), "startTime": bad})
            self.assertIsNone(self.kinds(p, "session_total")[0].data["since_ms"], bad)
        p = self.parse(s_user("hi"), cost_state(1.0, 1))
        self.assertIsNone(self.kinds(p, "session_total")[0].data["since_ms"])

    def test_no_cost_state_no_total(self):
        p = self.parse(s_user("hi"), s_assistant({"type": "text", "text": "x"}))
        self.assertEqual(self.kinds(p, "session_total"), [])

    def test_compaction_camel_case(self):
        p = self.parse({"type": "system", "subtype": "compact_boundary", "sessionId": SID, "timestamp": "t",
                        "compactMetadata": {"trigger": "auto", "preTokens": 75726, "postTokens": 9333,
                                            "durationMs": 4100}})
        (c,) = self.kinds(p, "compaction")
        self.assertEqual(c.data, {"trigger": "auto", "pre_tokens": 75726, "post_tokens": 9333, "duration_ms": 4100})
        self.assertEqual(p.unknown, {})

    def test_content_records_skipped_silently(self):
        skipped = ["file-history-snapshot", "file-history-delta", "api-request-blob", "api-request", "api-request-shape",
                   "queue-operation", "session-owner", "atis-latch", "tools-used", "mode", "permission-mode",
                   "ai-title", "last-prompt", "attachment", "summary", "bridge-session", "pr-link", "agent-setting",
                   "agent-name", "continued-in", "fork-context-ref"]
        quiet = ["turn_duration", "away_summary", "local_command", "informational", "scheduled_task_fire",
                 "bridge_status", "agents_killed"]
        p = self.parse(*[{"type": t, "content": "SECRET", "sessionId": SID} for t in skipped],
                       *[{"type": "system", "subtype": t, "content": "SECRET", "summary": "SECRET", "durationMs": 5,
                          "cwd": "/x"} for t in quiet],
                       {"type": "brand-new"}, {"type": "system", "subtype": "weird"}, {"type": "system"}, {"type": 5})
        self.assertEqual(p.events, [])
        self.assertEqual(p.unknown, {"brand-new": 1, "system/weird": 1, "system": 1, "?": 1})

    def test_usage_dedup_within_file(self):
        t = {"type": "text", "text": "x"}
        p = self.parse(
            s_assistant(t, mid="a", rid="1", u=usage(out=1)),                    # incomplete
            s_assistant(t, mid="a", rid="1", stop="end_turn", u=usage(out=9)),   # complete wins
            s_assistant(t, mid="a", rid="1", u=usage(out=2)),                    # later incomplete loses to complete
            s_assistant(t, mid="b", rid="1", u=usage(out=3)),
            s_assistant(t, mid="b", rid="1", u=usage(out=4)),                    # later beats earlier (both incomplete)
            s_assistant(t, mid="c", rid="1", stop="end_turn", u=usage(out=5)),
            s_assistant(t, mid="c", rid="1", stop="end_turn", u=usage(out=6)),   # later beats earlier (both complete)
            s_assistant(t, mid="a", rid="2", u=usage(out=7)))                    # other request id: other call
        got = {e.data["key"]: (e.data["output"], e.data["complete"]) for e in self.kinds(p, "usage")}
        self.assertEqual(got, {("a", "1"): (9, True), ("b", "1"): (4, False), ("c", "1"): (6, True),
                               ("a", "2"): (7, False)})
        self.assertEqual(len(self.kinds(p, "usage")), 4)
        self.assertEqual(len(self.kinds(p, "message")), 8)  # content blocks are all kept

    def test_usage_dedup_within_file_prefers_non_sidechain(self):
        t = {"type": "text", "text": "x"}
        orig = s_assistant(t, mid="m", rid="r", stop="end_turn", u=usage(out=10))
        side = s_assistant(t, mid="m", rid="r", stop="end_turn", u=usage(out=1), isSidechain=True)
        for order in ((orig, side), (side, orig)):
            with self.subTest(first=order[0].get("isSidechain")):
                us = self.kinds(self.parse(*order), "usage")
                self.assertEqual([(e.data["output"], e.sidechain) for e in us], [(10, False)])

    def test_synthetic_model_has_no_usage(self):
        p = self.parse(s_assistant({"type": "text", "text": "x"}, mid="s", model="<synthetic>"))
        self.assertEqual(self.kinds(p, "usage"), [])
        self.assertEqual(len(self.kinds(p, "message")), 1)

    def test_user_records(self):
        p = self.parse(s_user("hello"),
                       s_user([{"type": "tool_result", "tool_use_id": "t1", "content": "abc", "is_error": True},
                               {"type": "tool_result", "tool_use_id": "t2", "content": [{"type": "text", "text": "xy"}]},
                               {"type": "text", "text": "plain"}]),
                       s_user(5), {"type": "user", "message": "bad"})
        self.assertEqual([(e.data["role"], e.data["length"]) for e in self.kinds(p, "message")],
                         [("user", 5), ("user", 5)])
        self.assertEqual([e.data for e in self.kinds(p, "tool_result")],
                         [{"id": "t1", "is_error": True, "size": 3}, {"id": "t2", "is_error": None, "size": 2}])

    def test_session_tool_result_error_from_record_level_result(self):
        def rec(tid, tur="absent", **block):
            extra = {} if tur == "absent" else {"toolUseResult": tur}
            return s_user([{"type": "tool_result", "tool_use_id": tid, "content": "x", **block}], **extra)
        p = self.parse(rec("d", {"filePath": "/x"}), rec("l", [{"type": "text", "text": "x"}]), rec("s", "boom"),
                       rec("m"), rec("n", None), rec("i", 1.5), rec("w1", "boom", is_error=False),
                       rec("w2", {"a": 1}, is_error=True))
        self.assertEqual([(e.data["id"], e.data["is_error"]) for e in self.kinds(p, "tool_result")],
                         [("d", False), ("l", False), ("s", True), ("m", None), ("n", None), ("i", None),
                          ("w1", False), ("w2", True)])

    def test_session_deep_nesting_counts_as_broken(self):
        write_jsonl(self.main, "[" * 100_000, s_assistant({"type": "text", "text": "ok"}))
        sub = self.root / SID / "subagents"
        write_jsonl(sub / "agent-a1.jsonl", "[" * 100_000, s_assistant({"type": "text", "text": "sub"}, mid="s1"))
        (sub / "agent-a1.meta.json").write_text("[" * 100_000)
        p = transcript.claude_session(self.main)
        self.assertEqual(p.broken, 2)
        self.assertEqual([(e.agent_id, e.agent_type) for e in self.kinds(p, "message")], [("", ""), ("a1", "")])

    def test_tolerant_and_tool_results_dir_never_read(self):
        write_jsonl(self.main, "not json", [1], s_assistant({"type": "tool_use", "id": "u", "name": "Bash", "input": {}}))
        write_jsonl(self.root / SID / "tool-results" / "agent-x.jsonl", s_assistant({"type": "text", "text": "leak"}))
        write_jsonl(self.root / SID / "subagents" / "notes.txt", "x")
        p = transcript.claude_session(self.main)
        self.assertEqual(p.broken, 2)
        self.assertEqual(sorted(e.kind for e in p.events), ["tool_call", "usage"])
        m = transcript.claude_session(self.root / "absent.jsonl")
        self.assertEqual((m.events, m.unknown, m.broken), ([], {}, 0))


class ContestTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name).resolve()
        self.real = base / "real" / "contest"
        self.real.mkdir(parents=True)
        self.link = base / "link"
        self.link.symlink_to(self.real)
        self.projects = base / "projects"
        self.projects.mkdir()

    def pdir(self, path: Path) -> Path:
        d = self.projects / transcript.encode_path(path)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def test_deeply_nested_first_line_never_raises(self):
        d = self.pdir(self.real)
        write_jsonl(d / "deep.jsonl", "[" * 100_000, s_user("hi", cwd=str(self.real)))
        self.assertEqual(transcript.contest_sessions(self.real, self.projects), [d / "deep.jsonl"])

    def test_encode_path(self):
        self.assertEqual(transcript.encode_path(Path("/home/a.b/c_d/.worktrees/x")), "-home-a-b-c-d--worktrees-x")

    def test_contest_assignment_symlink_and_worktree(self):
        main = write_jsonl(self.pdir(self.real) / "s1.jsonl", {"type": "mode"}, s_user("hi", cwd=str(self.real)))
        wt = write_jsonl(self.pdir(self.real / ".worktrees" / "T001-x") / "s2.jsonl",
                         s_user("hi", cwd=str(self.real / ".worktrees" / "T001-x")))
        via_link = write_jsonl(self.pdir(self.link) / "s3.jsonl", s_user("hi", cwd=str(self.link / "sub")))
        write_jsonl(self.pdir(self.real.parent / "contest-old") / "s4.jsonl",
                    s_user("hi", cwd=str(self.real.parent / "contest-old")))
        write_jsonl(self.pdir(self.real) / "s5.jsonl", s_user("hi", cwd=str(self.real.parent / "contest-old")))
        write_jsonl(self.pdir(self.real) / "s6.jsonl", {"type": "mode"})                       # no cwd at all
        write_jsonl(self.pdir(self.real) / "s1" / "subagents" / "agent-a.jsonl", s_user("hi", cwd=str(self.real)))
        (self.pdir(self.real) / "notes.txt").write_text("x")
        self.assertEqual(set(transcript.contest_sessions(self.link, self.projects)), {main, wt, via_link})
        # given by its real path, a dir named after the symlink path is not reachable by name
        self.assertEqual(set(transcript.contest_sessions(self.real, self.projects)), {main, wt})
        names = {d.name for d in transcript.project_dirs(self.link, self.projects)}
        self.assertEqual(names, {transcript.encode_path(p) for p in (
            self.real, self.link, self.real / ".worktrees" / "T001-x", self.real.parent / "contest-old")})

    def test_contest_assignment_missing_projects_and_broken_files(self):
        self.assertEqual(transcript.project_dirs(self.real, self.projects / "absent"), [])
        self.assertEqual(transcript.contest_sessions(self.real, self.projects / "absent"), [])
        d = self.pdir(self.real)
        (d / "bad.jsonl").write_text("not json\n[1]\n" + json.dumps({"cwd": 5}) + "\n")
        ok = write_jsonl(d / "ok.jsonl", "garbage", s_user("x", cwd=str(self.real)))
        self.assertEqual(transcript.contest_sessions(self.real, self.projects), [ok])


class LoadTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def session(self, name: str, *lines) -> tuple[str, Path]:
        return "session", write_jsonl(self.root / f"{name}.jsonl", *lines)

    def stream(self, name: str, *lines) -> tuple[str, Path]:
        return "stream", write_jsonl(self.root / f"{name}.jsonl", *lines)

    @staticmethod
    def usages(p: transcript.Parsed) -> list:
        return [e for e in p.events if e.kind == "usage"]

    def test_dedup_same_call_two_sessions(self):
        t = {"type": "text", "text": "x"}
        a = self.session("sess-a", s_assistant(t, mid="m", rid="r", stop="end_turn", u=usage(out=5)))
        b = self.session("sess-b", s_assistant(t, mid="m", rid="r", stop="end_turn", u=usage(out=5)),
                         s_assistant(t, mid="m2", rid="r2", u=usage(out=1)))
        p = transcript.load([a, b])
        self.assertEqual(sorted(e.data["key"] for e in self.usages(p)), [("m", "r"), ("m2", "r2")])
        self.assertEqual(len([e for e in p.events if e.kind == "message"]), 3)  # only usage is de-duplicated

    def test_dedup_later_ts_wins_and_tie_takes_higher_output(self):
        t = {"type": "text", "text": "x"}
        a = self.session("sess-a", s_assistant(t, stop="end_turn", u=usage(out=5), timestamp="2026-11-17T10:00:00Z"))
        b = self.session("sess-b", s_assistant(t, stop="end_turn", u=usage(out=3), timestamp="2026-11-17T11:00:00Z"))
        self.assertEqual([e.data["output"] for e in self.usages(transcript.load([a, b]))], [3])
        self.assertEqual([e.data["output"] for e in self.usages(transcript.load([b, a]))], [3])
        c = self.session("sess-c", s_assistant(t, stop="end_turn", u=usage(out=9), timestamp="2026-11-17T11:00:00Z"))
        self.assertEqual([e.data["output"] for e in self.usages(transcript.load([b, c]))], [9])
        self.assertEqual([e.data["output"] for e in self.usages(transcript.load([c, b]))], [9])

    def test_dedup_without_ts_later_input_wins(self):
        t = {"type": "text", "text": "x"}
        a = self.session("sess-a", s_assistant(t, stop="end_turn", u=usage(out=5), timestamp=None))
        b = self.session("sess-b", s_assistant(t, stop="end_turn", u=usage(out=3), timestamp=None))
        self.assertEqual([e.data["output"] for e in self.usages(transcript.load([a, b]))], [3])
        self.assertEqual([e.data["output"] for e in self.usages(transcript.load([b, a]))], [5])

    def test_dedup_sidechain_copy_loses(self):
        t = {"type": "text", "text": "x"}
        side = self.session("sess-a", s_assistant(t, stop="end_turn", u=usage(out=99), isSidechain=True,
                                                  timestamp="2026-11-17T12:00:00Z"))
        main = self.session("sess-b", s_assistant(t, u=usage(out=1), timestamp="2026-11-17T09:00:00Z"))
        for order in ([side, main], [main, side]):
            us = self.usages(transcript.load(order))
            self.assertEqual([(e.data["output"], e.sidechain) for e in us], [(1, False)])

    def test_dedup_incomplete_then_complete(self):
        t = {"type": "text", "text": "x"}
        inc = self.session("sess-a", s_assistant(t, u=usage(out=1), timestamp="2026-11-17T12:00:00Z"))
        comp = self.session("sess-b", s_assistant(t, stop="end_turn", u=usage(out=8), timestamp="2026-11-17T09:00:00Z"))
        for order in ([inc, comp], [comp, inc]):
            us = self.usages(transcript.load(order))
            self.assertEqual([(e.data["output"], e.data["complete"]) for e in us], [(8, True)])

    def test_collect_keeps_every_copy_and_dedupe_collapses_them(self):
        t = {"type": "text", "text": "x"}
        a = self.session("sess-a", s_assistant(t, stop="end_turn", u=usage(out=5), timestamp="2026-11-17T10:00:00Z"))
        b = self.session("sess-b", s_assistant(t, stop="end_turn", u=usage(out=3), timestamp="2026-11-17T11:00:00Z"))
        got = transcript.collect([a, b])
        self.assertEqual([e.data["output"] for e in self.usages(got)], [5, 3])
        self.assertEqual([e.data["output"] for e in transcript.dedupe(got.events) if e.kind == "usage"], [3])

    def test_survivors_keep_original_order(self):
        t = {"type": "text", "text": "x"}
        a = self.session("sess-a", s_assistant(t, mid="a", rid="1", u=usage(out=1)),
                         s_assistant(t, mid="b", rid="1", u=usage(out=2)))
        b = self.session("sess-b", s_assistant(t, mid="a", rid="1", stop="end_turn", u=usage(out=3)))
        p = transcript.load([a, b])
        self.assertEqual([(e.data["key"], e.data["output"]) for e in self.usages(p)], [(("b", "1"), 2), (("a", "1"), 3)])

    def test_synthetic_dropped(self):
        t = {"type": "text", "text": "x"}
        src = self.session("sess-a", s_assistant(t, mid="s", model="<synthetic>"), s_assistant(t, mid="ok"))
        p = transcript.load([src])
        self.assertEqual([e.data["key"] for e in self.usages(p)], [("ok", "req_1")])

    def test_stream_and_session_same_id_counted_once(self):
        t = {"type": "text", "text": "x"}
        st = self.stream("stream", assistant(t, mid="m1", rid="r1", u=usage(out=4)))
        # the session file of the same run: same id, one extra record that must not leak in, and counts
        ses = ("session", write_jsonl(self.root / f"{SID}.jsonl", s_assistant(t, mid="m1", rid="r1", u=usage(out=4)),
                                      s_assistant(t, mid="m2", rid="r2"), {"type": "weird-type"}, "not json"))
        other = self.session("other-id", s_assistant(t, mid="m3", rid="r3"))
        p = transcript.load([st, ses, other])
        self.assertEqual(sorted(e.data["key"] for e in self.usages(p)), [("m1", "r1"), ("m3", "r3")])
        self.assertEqual({e.source for e in p.events if e.data.get("key") == ("m1", "r1")}, {"stream"})
        self.assertEqual((p.unknown, p.broken), ({}, 0))

    def test_sums_counts_and_unknown_source(self):
        a = self.session("sess-a", {"type": "weird-type"}, "not json")
        b = self.session("sess-b", {"type": "weird-type"}, "oops")
        c = ("codex", write_jsonl(self.root / "c.jsonl", {"type": "thread.started", "thread_id": "th"}, "x"))
        p = transcript.load([a, b, c, ("bogus", self.root / "none.jsonl"), ("bogus", self.root / "none2.jsonl")])
        self.assertEqual(p.unknown, {"weird-type": 2, "source/bogus": 2})
        self.assertEqual(p.broken, 3)

    def test_codex_source_loaded(self):
        c = ("codex", write_jsonl(self.root / "c.jsonl", {"type": "thread.started", "thread_id": "th1"},
                                  codex_turn()))
        p = transcript.load([c])
        self.assertEqual([(e.source, e.data["key"]) for e in self.usages(p)], [("codex", ("th1", 0))])

    def test_idless_usage_never_collapses(self):
        t = {"type": "text", "text": "x"}
        lines = [s_assistant(t, mid="", rid="", stop="end_turn", u=usage(out=n)) for n in (1, 2, 3)]
        direct = transcript.claude_session(write_jsonl(self.root / "direct.jsonl", *lines))
        self.assertEqual(len(self.usages(direct)), 3)
        p = transcript.load([self.session("sess-a", *lines)])
        self.assertEqual(len(self.usages(p)), 3)
        two = transcript.load([self.session("sess-b", *lines[:2]), self.session("sess-c", lines[2])])
        self.assertEqual(len(self.usages(two)), 3)

    def test_codex_without_thread_id_both_kept(self):
        a = ("codex", write_jsonl(self.root / "a.jsonl", codex_turn(out=1)))
        b = ("codex", write_jsonl(self.root / "b.jsonl", codex_turn(out=2)))
        p = transcript.load([a, b])
        self.assertEqual(sorted(e.data["output"] for e in self.usages(p)), [1, 2])

    def test_load_empty(self):
        p = transcript.load([])
        self.assertEqual((p.events, p.unknown, p.broken), ([], {}, 0))


class ViewTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def events(self, *lines) -> list:
        return transcript.claude_session(write_jsonl(self.root / f"{SID}.jsonl", *lines)).events

    @staticmethod
    def call(name: str, inp) -> transcript.Event:
        return transcript.Event(kind="tool_call", ts=None, session_id="s", source="session",
                                data={"id": "t", "name": name, "input": inp}, raw={"secret": "CANARY-RAW"})

    def test_canaries_never_in_view(self):
        def tu(i, name, inp):
            return s_assistant({"type": "tool_use", "id": i, "name": name, "input": inp}, mid=f"m{i}", rid=f"r{i}")
        cmd = 'curl -H "Authorization: Bearer sk-abcdefghijklmnopqrstuvwxyz123456" x'
        evs = self.events(
            tu("t1", "Write", {"file_path": "/a/b.py", "content": "CANARY-WRITE"}),
            tu("t2", "Agent", {"subagent_type": "Explore", "description": "look", "prompt": "CANARY-PROMPT"}),
            s_user([{"type": "tool_result", "tool_use_id": "t1", "content": "CANARY-RESULT"}]),
            s_assistant({"type": "text", "text": "CANARY-TEXT"}, mid="mt", rid="rt"),
            tu("t3", "Bash", {"command": cmd, "description": "CANARY-DESC"}),
            cost_state(1.5, 10))
        text = json.dumps([transcript.view(e) for e in evs])
        for bad in ("CANARY-WRITE", "CANARY-PROMPT", "CANARY-RESULT", "CANARY-TEXT", "CANARY-DESC", "sk-abc"):
            self.assertNotIn(bad, text)
        self.assertIn("[REDACTED]", text)
        self.assertIn("curl", text)
        self.assertIn("/a/b.py", text)
        self.assertIn("Explore", text)

    def test_view_fields(self):
        e = transcript.Event(kind="message", ts="2026-11-17T10:00:00Z", session_id="s", source="stream", model="m",
                             agent_id="a1", parent_tool_use_id="tu", data={"role": "user", "length": 4, "text": "x"},
                             raw={"x": 1}, cwd="/secret/cwd", git_branch="b", agent_type="Explore",
                             cli_version="2.1.326")
        v = transcript.view(e)
        self.assertEqual(v, {"kind": "message", "ts": "2026-11-17T10:00:00Z", "session_id": "s", "source": "stream",
                             "model": "m", "agent_id": "a1", "parent_tool_use_id": "tu", "agent_type": "Explore",
                             "cli_version": "2.1.326", "data": {"role": "user", "length": 4}})
        bare = transcript.view(transcript.Event(kind="message", ts=None, session_id="s", source="x"))
        self.assertEqual((bare["agent_type"], bare["cli_version"]), ("", None))
        sk = "sk-abcdefghijklmnopqrstuvwxyz123456"
        leaky = transcript.view(transcript.Event(kind="message", ts=None, session_id="s", source="x",
                                                 agent_type=sk, cli_version=sk))
        self.assertEqual((leaky["agent_type"], leaky["cli_version"]), ("[REDACTED]", "[REDACTED]"))
        odd = transcript.view(transcript.Event(kind="message", ts=None, session_id="s", source="x",
                                               agent_type=5, cli_version=[1]))
        self.assertEqual((odd["agent_type"], odd["cli_version"]), ("", None))

    def test_view_tool_whitelists(self):
        view = lambda name, inp: transcript.view(self.call(name, inp))["data"]
        self.assertEqual(view("Bash", {"command": "ls", "timeout": 5}), {"id": "t", "name": "Bash", "input": {"command": "ls"}})
        for name in ("Edit", "Write", "MultiEdit", "Read"):
            self.assertEqual(view(name, {"file_path": "/f", "content": "c", "old_string": "o"})["input"], {"file_path": "/f"})
        self.assertEqual(view("Read", {"file_path": "/f", "offset": 9000, "limit": 120, "allow_large": True,
                                       "pages": "1-3"})["input"],
                         {"file_path": "/f", "offset": 9000, "limit": 120, "allow_large": True})
        self.assertEqual(view("NotebookEdit", {"notebook_path": "/n.ipynb", "new_source": "c"})["input"],
                         {"file_path": "/n.ipynb"})
        self.assertEqual(view("Grep", {"pattern": "p", "path": "/x", "glob": "g"})["input"], {"pattern": "p", "path": "/x"})
        self.assertEqual(view("Glob", {"pattern": "p"})["input"], {"pattern": "p"})
        for name in ("Agent", "Task"):
            self.assertEqual(view(name, {"subagent_type": "t", "description": "d", "prompt": "P"})["input"],
                             {"subagent_type": "t", "description": "d", "prompt_chars": 1})
        self.assertEqual(view("Skill", {"skill": "tdd", "args": "a"})["input"], {"skill": "tdd"})
        self.assertEqual(view("Skill", {"name": "tdd"})["input"], {"skill": "tdd"})
        self.assertEqual(view("WebFetch", {"url": "https://x"})["input"], {})
        self.assertEqual(view("Read", {"file_path": 5})["input"], {})  # non-string values are dropped
        self.assertEqual(view("Bash", {"command": ["ls"]})["input"], {})
        self.assertEqual(view("Bash", "not a dict")["input"], {})
        self.assertEqual(view("Grep", {"pattern": "p", "path": None})["input"], {"pattern": "p"})

    def test_view_read_range_fields_are_numbers_and_flags_only(self):
        view = lambda inp: transcript.view(self.call("Read", inp))["data"]["input"]
        self.assertEqual(view({"file_path": "/f"}), {"file_path": "/f"})  # absent stays absent (no limit = whole file)
        self.assertEqual(view({"file_path": "/f", "limit": 0, "allow_large": False}),
                         {"file_path": "/f", "limit": 0, "allow_large": False})
        odd = view({"file_path": "/f", "offset": "9000", "limit": True, "allow_large": "yes"})
        self.assertEqual(odd, {"file_path": "/f"})  # strings and bools are not numbers, a string is not a flag

    def test_view_agent_prompt_chars_is_a_size_never_text(self):
        view = lambda inp: transcript.view(self.call("Agent", inp))["data"]["input"]
        secret = "sk-" + "abcdefghij" * 4
        got = view({"subagent_type": "researcher", "description": "d", "prompt": "BRIEF " + secret + "x" * 30000})
        self.assertEqual(got["prompt_chars"], len("BRIEF " + secret) + 30000)
        self.assertIsInstance(got["prompt_chars"], int)
        self.assertEqual(set(got), {"subagent_type", "description", "prompt_chars"})
        dumped = json.dumps(got)
        for leak in ("BRIEF", "abcdefghij", "xxxxx"):
            self.assertNotIn(leak, dumped)
        self.assertEqual(view({"subagent_type": "t", "prompt": 5}), {"subagent_type": "t"})  # not a string: no size
        self.assertEqual(view({"subagent_type": "t"}), {"subagent_type": "t"})

    def test_view_bash_limit_none_keeps_the_whole_redacted_command(self):
        secret = "sk-" + "abcdefghij" * 4
        cmd = "x" * 500 + " export API_KEY=" + secret + " tail"
        got = transcript.view(self.call("Bash", {"command": cmd}), bash_limit=None)["data"]["input"]["command"]
        self.assertGreater(len(got), 500)
        self.assertTrue(got.endswith("tail") or "REDACTED" in got)
        self.assertNotIn("abcdefghij", got)
        self.assertEqual(len(transcript.view(self.call("Bash", {"command": cmd}))["data"]["input"]["command"]), 300)

    def test_view_bash_cut_never_exposes_partial_secret(self):
        secret = "sk-" + "abcdefghij" * 4
        for pad in range(270, 300):
            cmd = "x" * pad + " Authorization: Bearer " + secret + " tail"
            got = transcript.view(self.call("Bash", {"command": cmd}))["data"]["input"]["command"]
            self.assertLessEqual(len(got), 300)
            self.assertNotIn("sk-", got, pad)
            self.assertNotIn("abcdefghij", got, pad)
        # the redacted text is cut, not the original: a secret straddling the limit leaves no part of itself
        cmd = "x" * 290 + " sk-" + "q" * 30
        got = transcript.view(self.call("Bash", {"command": cmd}))["data"]["input"]["command"]
        self.assertNotIn("qqq", got)
        self.assertNotIn("sk-", got)

    def test_view_strips_extra_keys_per_kind(self):
        def ev(kind, data):
            return transcript.Event(kind=kind, ts=None, session_id="s", source="session", data=data)
        out = transcript.view(ev("usage", {"key": ("m", "r"), "input": 1, "cache_read": 2, "cache_write_5m": 3,
                                           "cache_write_1h": 4, "output": 5, "complete": True, "summary": "CANARY"}))
        self.assertEqual(out["data"], {"key": ["m", "r"], "input": 1, "cache_read": 2, "cache_write_5m": 3,
                                       "cache_write_1h": 4, "output": 5, "complete": True})
        out = transcript.view(ev("compaction", {"trigger": "auto", "pre_tokens": 9, "post_tokens": 3,
                                                "duration_ms": 7, "summary": "CANARY"}))
        self.assertEqual(out["data"], {"trigger": "auto", "pre_tokens": 9, "post_tokens": 3, "duration_ms": 7})
        out = transcript.view(ev("session_total", {
            "models": {"m": {"input": 1, "cache_read": 2, "cache_write": 3, "output": 4, "cost_usd": 0.5, "x": "CANARY"}},
            "cost_usd": 0.5, "since_ms": 1763373000000, "summary": "CANARY"}))
        self.assertEqual(out["data"], {"models": {"m": {"input": 1, "cache_read": 2, "cache_write": 3, "output": 4,
                                                        "cost_usd": 0.5}}, "cost_usd": 0.5,
                                       "since_ms": 1763373000000})
        self.assertNotIn("CANARY", json.dumps(out))

    def test_view_redacts_every_string(self):
        sk = "sk-abcdefghijklmnopqrstuvwxyz123456"
        call = lambda name, inp: transcript.Event(
            kind="tool_call", ts=None, session_id=f"s-{sk}", source="session", model=f"m-{sk}", agent_id=f"a-{sk}",
            parent_tool_use_id=f"p-{sk}", data={"id": f"t-{sk}", "name": name, "input": inp})
        evs = [call("Grep", {"pattern": sk, "path": f"/x/{sk}"}),
               call("Agent", {"subagent_type": f"t-{sk}", "description": f"do {sk}"}),
               call("Read", {"file_path": f"/f/{sk}"}), call("Skill", {"skill": sk}), call(f"Tool-{sk}", {}),
               transcript.Event(kind="message", ts=f"t-{sk}", session_id="s", source="s", data={"role": sk, "length": 1}),
               transcript.Event(kind="compaction", ts=None, session_id="s", source="s", data={"trigger": sk}),
               transcript.Event(kind="usage", ts=None, session_id="s", source="s", data={"key": (sk, 0)}),
               transcript.Event(kind="session_total", ts=None, session_id="s", source="s",
                                data={"models": {sk: {"input": 1}}})]
        text = json.dumps([transcript.view(e) for e in evs])
        self.assertNotIn("sk-abc", text)
        self.assertIn("[REDACTED]", text)
        v = transcript.view(evs[0])["data"]["input"]
        self.assertEqual(v["pattern"], "[REDACTED]")
        self.assertEqual(transcript.view(evs[1])["data"]["input"]["description"], "do [REDACTED]")

    def test_view_is_type_safe_for_hand_built_events(self):
        bad = object()
        evs = [transcript.Event(kind="tool_call", ts=5, session_id=None, source=bad, model=3, agent_id=1,
                                parent_tool_use_id=[], data={"id": 5, "name": bad, "input": bad}),
               transcript.Event(kind="tool_result", ts=None, session_id="s", source="s",
                                data={"id": bad, "is_error": "yes", "size": "3"}),
               transcript.Event(kind="message", ts=None, session_id="s", source="s",
                                data={"role": 5, "length": True}),
               transcript.Event(kind="usage", ts=None, session_id="s", source="s",
                                data={"key": (bad, 1), "input": "x", "output": [1], "complete": 1}),
               transcript.Event(kind="compaction", ts=None, session_id="s", source="s",
                                data={"trigger": 1, "pre_tokens": "9"}),
               transcript.Event(kind="session_total", ts=None, session_id="s", source="s",
                                data={"models": {"m": "oops", 5: {}}, "cost_usd": "1", "since_ms": True}),
               transcript.Event(kind="session_total", ts=None, session_id="s", source="s",
                                data={"models": {}, "since_ms": 1.5}),
               transcript.Event(kind="message", ts=None, session_id="s", source="s", data="oops")]
        out = [transcript.view(e) for e in evs]
        json.dumps(out)
        self.assertEqual(out[1]["data"], {"id": "", "is_error": None, "size": None})
        self.assertEqual(out[2]["data"], {"role": "", "length": None})
        self.assertIsNone(out[3]["data"]["key"])
        self.assertIsNone(out[3]["data"]["input"])
        self.assertIsNone(out[3]["data"]["complete"])
        self.assertEqual(out[5]["data"]["models"], {"m": {"input": None, "cache_read": None, "cache_write": None,
                                                         "output": None, "cost_usd": None}})
        self.assertIsNone(out[5]["data"]["since_ms"])
        self.assertIsNone(out[6]["data"]["since_ms"])
        self.assertEqual(out[7]["data"], {"role": "", "length": None})

    def test_view_other_kinds(self):
        def ev(kind, data):
            return transcript.Event(kind=kind, ts=None, session_id="s", source="codex", data=data, raw={"r": "CANARY"})
        v = transcript.view(ev("tool_result", {"id": "t", "is_error": None, "size": 3, "content": "CANARY"}))
        self.assertEqual(v["data"], {"id": "t", "is_error": None, "size": 3})
        u = {"key": ("m", "r"), "input": 1, "cache_read": 2, "cache_write_5m": 3, "cache_write_1h": 4, "output": 5,
             "complete": True}
        self.assertEqual(transcript.view(ev("usage", u))["data"], {**u, "key": ["m", "r"]})
        c = {"trigger": "auto", "pre_tokens": 9, "post_tokens": 3, "duration_ms": 7}
        self.assertEqual(transcript.view(ev("compaction", c))["data"], c)
        t = {"models": {"m": {"input": 1, "cache_read": 2, "cache_write": 3, "output": 4, "cost_usd": 0.5}}, "cost_usd": 0.5,
             "since_ms": None}
        self.assertEqual(transcript.view(ev("session_total", t))["data"], t)
        self.assertEqual(transcript.view(ev("mystery", {"x": "CANARY"}))["data"], {})
        text = json.dumps(transcript.view(ev("tool_result", {"id": "t", "size": 1})))
        self.assertNotIn("CANARY", text)


class RepresentationTest(unittest.TestCase):
    def test_repr_never_leaks_content(self):
        e = transcript.Event(kind="tool_call", ts=None, session_id="s", source="session",
                             data={"id": "t", "name": "Write", "input": {"content": "CANARY-INPUT"}},
                             raw={"message": {"content": "CANARY-RAW"}})
        text = repr(e) + str(e)
        self.assertNotIn("CANARY", text)
        self.assertIn("tool_call", text)


class ResultTextTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def results(self, loader, name, *lines) -> list:
        p = loader(write_jsonl(self.root / name, *lines))
        return [e for e in p.events if e.kind == "tool_result"]

    def test_stream_text_and_list(self):
        listed = [{"type": "text", "text": "ab"}, {"type": "image", "source": {}}, {"type": "text", "text": "cd"}]
        evs = self.results(transcript.claude_stream, "s.jsonl", user_result("t1", "hello"),
                           user_result("t2", listed), user_result("t3", None), user_result("t4", 5))
        self.assertEqual([transcript.result_text(e) for e in evs], ["hello", "abcd", "", ""])

    def test_session_text_and_list(self):
        evs = self.results(transcript.claude_session, f"{SID}.jsonl",
                           s_user([{"type": "tool_result", "tool_use_id": "t1", "content": "out"}]),
                           s_user([{"type": "tool_result", "tool_use_id": "t2",
                                    "content": [{"type": "text", "text": "x"}, {"type": "text", "text": "y"}]}]))
        self.assertEqual([transcript.result_text(e) for e in evs], ["out", "xy"])

    def test_codex_aggregated_output(self):
        evs = self.results(transcript.codex_stream, "c.jsonl", *codex_cmd("ls", out="a\nb"))
        self.assertEqual([transcript.result_text(e) for e in evs], ["a\nb"])

    def test_other_events_and_odd_raw_give_empty(self):
        call = transcript.claude_stream(write_jsonl(self.root / "x.jsonl", assistant(
            {"type": "tool_use", "id": "u", "name": "Bash", "input": {"command": "ls"}}))).events
        self.assertEqual([transcript.result_text(e) for e in call], ["", ""])
        odd = [transcript.Event(kind="tool_result", ts=None, session_id="s", source=src, data={"id": "t"}, raw=raw)
               for src in ("stream", "session", "codex") for raw in (None, {}, {"message": 5}, {"item": []})]
        self.assertEqual([transcript.result_text(e) for e in odd], [""] * 12)

    def test_matches_block_by_tool_use_id(self):
        rec = s_user([{"type": "tool_result", "tool_use_id": "a", "content": "first"},
                      {"type": "tool_result", "tool_use_id": "b", "content": "second"}])
        e = transcript.Event(kind="tool_result", ts=None, session_id="s", source="session", data={"id": "b"}, raw=rec)
        self.assertEqual(transcript.result_text(e), "second")

    def test_docstring_warns_about_unredacted_content(self):
        self.assertIn("unredacted", transcript.result_text.__doc__)


if __name__ == "__main__":
    unittest.main()
