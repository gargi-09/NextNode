"""The harness's MCP plumbing survives what crashed the first overnight eval (2026-10-27).

The MCP server's log was piped and never read, so after ~50 calls the pipe filled, the server
froze, and the mcp client's transport died with httpx.ReadTimeout, taking the whole eval with it.
These tests pin both fixes: the server log goes to a file, and a slow or dead session is dropped
and reopened, so the harness sees a retryable error instead of a crash.
"""
import asyncio
import json
import socket
import subprocess
import threading
import time

import httpx
import pytest

import harness  # noqa: F401  (puts scripts/ on sys.path)
import vllm_smoke
from eval import run_eval
from harness.replay import Fixture
from harness.session import McpConnection
from harness.tools import ToolRunner


async def no_sleep(_):
    return None


def test_the_mcp_server_log_goes_to_a_file_not_an_unread_pipe(monkeypatch, tmp_path):
    seen = {}

    class FakeProc:
        def poll(self):
            return None

    def fake_popen(cmd, **kwargs):
        seen.update(kwargs)
        return FakeProc()

    monkeypatch.setattr(vllm_smoke, "ROOT", tmp_path)
    monkeypatch.setattr(vllm_smoke.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(vllm_smoke.time, "sleep", lambda _: None)
    monkeypatch.setattr(vllm_smoke.httpx, "get", lambda *a, **k: httpx.Response(200))
    vllm_smoke.start_mcp_server(8799, "x" * 24)
    assert seen["stderr"] is not subprocess.PIPE and seen["stdout"] is not subprocess.PIPE
    assert (tmp_path / ".omnigent-logs" / "mcp-server-8799.log").exists()


# ───────── a real MCP server in this process, with one tool that is slow on its first call ─────────

@pytest.fixture
def slow_server():
    mcp = pytest.importorskip("mcp.server.fastmcp")
    uvicorn = pytest.importorskip("uvicorn")
    from mcp.server.transport_security import TransportSecuritySettings

    server = mcp.FastMCP("slow", stateless_http=True, json_response=True, streamable_http_path="/mcp",
                         transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))
    state = {"calls": 0, "first_delay": 1.5}

    @server.tool()
    async def lookup(skill: str) -> dict:
        state["calls"] += 1
        if state["calls"] == 1:
            await asyncio.sleep(state["first_delay"])
        return {"skill": skill}

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = uvicorn.Server(uvicorn.Config(server.streamable_http_app(), host="127.0.0.1", port=port,
                                        log_level="warning"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}/mcp", state
    srv.should_exit = True
    thread.join(5)


def test_a_transport_killed_by_an_http_timeout_is_reopened_and_the_call_retried(slow_server):
    url, state = slow_server

    async def main():
        conn = McpConnection(url, "token-not-checked", http_timeout_s=0.5)   # shorter than the slow call
        tools = ToolRunner(conn.call, timeout_s=5, sleep=no_sleep)
        try:
            ok, text = await tools.call("lookup", {"skill": "Python"})
            return ok, text, tools.stats, conn.reconnects
        finally:
            await conn.close()

    ok, text, stats, reconnects = asyncio.run(main())
    assert ok and json.loads(text) == {"skill": "Python"}
    assert stats.recovered == 1 and reconnects >= 1


def test_a_call_timed_out_by_the_harness_drops_the_session_and_the_retry_succeeds(slow_server):
    url, state = slow_server

    async def main():
        state["first_delay"] = 3                                          # well past the 1 s harness timeout
        conn = McpConnection(url, "token-not-checked", http_timeout_s=30)
        tools = ToolRunner(conn.call, timeout_s=1.0, sleep=no_sleep)
        try:
            ok, _ = await tools.call("lookup", {"skill": "SQL"})
            ok2, _ = await tools.call("lookup", {"skill": "Docker"})        # the next call still works
            return ok, ok2, tools.stats, conn.reconnects
        finally:
            await conn.close()

    ok, ok2, stats, reconnects = asyncio.run(main())
    assert ok and ok2 and stats.by_code == {"TIMEOUT": 1} and reconnects == 1


def test_an_unreachable_server_is_a_retryable_error_not_a_crash():
    async def main():
        conn = McpConnection("http://127.0.0.1:9/mcp", "t", http_timeout_s=1)
        try:
            return await conn.call("lookup", {"skill": "Python"})
        finally:
            await conn.close()

    ok, text = asyncio.run(main())
    assert not ok and json.loads(text)["code"] == "INTERNAL"


# ───────── eval: a crashing task does not end the run; reruns skip recorded tasks ─────────

def test_crashed_tasks_become_failed_rows_and_are_recorded_again(tmp_path):
    result = run_eval.crashed_result("crashed: ExceptionGroup: boom", 3.2)
    assert result["status"] == "failed" and result["failure_category"] == "execution"
    assert result["tools"]["calls"] == 0 and result["tokens"]["total"] == 0
    assert not run_eval.resumable({"cause": "execution"})
    assert run_eval.resumable({"cause": None}) and run_eval.resumable({"cause": "retrieval"})


def test_rerunning_record_mode_skips_tasks_already_recorded(tmp_path):
    fixture = Fixture(tmp_path / "recorded.json")
    fixture.turns("eval-01", "reasoner").append({"hash": "h", "response": {}})
    progress = tmp_path / "progress.json"
    progress.write_text(json.dumps({"eval-01": {"cause": None}, "eval-02": {"cause": None}}), encoding="utf-8")
    assert set(run_eval.load_progress(fixture, path=progress)) == {"eval-01"}   # eval-02 has no answers
    assert run_eval.load_progress(fixture, fresh={"eval-01"}, path=progress) == {}
    fixture.turns("eval-02", "reasoner").append({"hash": "h", "response": {}})
    # --fresh --only eval-01 must keep eval-02's progress
    assert set(run_eval.load_progress(fixture, fresh={"eval-01"}, path=progress)) == {"eval-02"}
