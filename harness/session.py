"""Shared plumbing: start the NextNode MCP server on loopback with a one-off token, connect to
it, and build the model-server clients from omnigent/providers.example.yaml."""
import asyncio
import secrets
from contextlib import asynccontextmanager

import httpx

from curriculum_run import provider_urls
from harness.tools import coded
from vllm_smoke import key_from_cluster, start_mcp_server


def model_clients(key_from_k8s: bool = False, timeout: float = 900) -> dict[str, httpx.Client]:
    key = key_from_cluster() if key_from_k8s else "local-standin"
    return {name: httpx.Client(base_url=url, timeout=timeout,
                               headers={"Authorization": f"Bearer {key if name == 'vllm-gpu' else 'ollama'}"})
            for name, url in provider_urls().items()}


class McpConnection:
    """An MCP client session that heals itself.

    The mcp library runs each request in its transport's task group; a request that outlives its
    HTTP timeout kills that task group, and with it the session and whatever task opened it. So
    the session lives in its own owner task: a call that times out (the harness cancels it) or
    fails drops the session, and the next call opens a fresh one to the same server. A lost
    session surfaces as a transient INTERNAL error, which the harness retries.
    """

    def __init__(self, url: str, token: str, http_timeout_s: float = 120):
        self.url, self.token, self.http_timeout_s = url, token, http_timeout_s
        self._session = self._owner = self._stop = None
        self._dropped: list[asyncio.Task] = []
        self.opens = 0

    @property
    def reconnects(self) -> int:
        return max(0, self.opens - 1)

    async def _open(self):
        from mcp import ClientSession
        from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

        loop = asyncio.get_running_loop()
        ready, stop = loop.create_future(), asyncio.Event()

        async def owner():
            try:
                http = create_mcp_http_client(headers={"Authorization": f"Bearer {self.token}"},
                                              timeout=httpx.Timeout(self.http_timeout_s))
                async with http, streamable_http_client(self.url, http_client=http) as (r, w, _), \
                        ClientSession(r, w) as session:
                    await session.initialize()
                    ready.set_result(session)
                    await stop.wait()
            except BaseException as e:      # the transport's ExceptionGroup, or our own cancel
                if not ready.done():
                    ready.set_exception(ConnectionError(f"MCP connect failed: {e!r}"))
            finally:
                if self._owner is task:
                    self._session = self._owner = None

        task = loop.create_task(owner())
        self._owner, self._stop = task, stop
        self.opens += 1
        self._session = await ready

    async def session(self):
        if self._session is None:
            if self._owner is not None:
                self.drop()
            await self._open()
        return self._session

    def drop(self):
        """Abandon the current session; its transport is cancelled, not waited on."""
        if self._owner is not None:
            self._stop.set()
            self._owner.cancel()
            self._dropped.append(self._owner)
        self._session = self._owner = None

    async def call(self, name: str, arguments: dict) -> tuple[bool, str]:
        try:
            session = await self.session()
        except Exception as e:
            return False, coded("INTERNAL", str(e))
        try:
            res = await session.call_tool(name, arguments)
        except asyncio.CancelledError:      # timed out by the harness: this transport is poisoned
            self.drop()
            raise
        except Exception as e:
            self.drop()
            return False, coded("INTERNAL", f"MCP session lost during {name}: {e.__class__.__name__}")
        return not res.isError, "".join(c.text for c in res.content if getattr(c, "text", None))

    async def close(self):
        self.drop()
        await asyncio.gather(*self._dropped, return_exceptions=True)


@asynccontextmanager
async def mcp_session(port: int = 8767):
    """Yields (call, tools): `await call(name, arguments) -> (ok, text)` and the MCP tool list."""
    token = secrets.token_urlsafe(24)
    server = start_mcp_server(port, token)
    conn = McpConnection(f"http://127.0.0.1:{port}/mcp", token)
    try:
        tools = (await (await conn.session()).list_tools()).tools
        yield conn.call, tools
    finally:
        await conn.close()
        server.terminate()
