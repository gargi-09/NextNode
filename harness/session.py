"""Shared plumbing: start the NextNode MCP server on loopback with a one-off token, connect to
it, and build the model-server clients from omnigent/providers.example.yaml."""
import secrets
from contextlib import asynccontextmanager

import httpx

from curriculum_run import provider_urls
from vllm_smoke import key_from_cluster, start_mcp_server


def model_clients(key_from_k8s: bool = False, timeout: float = 900) -> dict[str, httpx.Client]:
    key = key_from_cluster() if key_from_k8s else "local-standin"
    return {name: httpx.Client(base_url=url, timeout=timeout,
                               headers={"Authorization": f"Bearer {key if name == 'vllm-gpu' else 'ollama'}"})
            for name, url in provider_urls().items()}


@asynccontextmanager
async def mcp_session(port: int = 8767):
    """Yields (call, tools): `await call(name, arguments) -> (ok, text)` and the MCP tool list."""
    from mcp import ClientSession
    from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

    token = secrets.token_urlsafe(24)
    server = start_mcp_server(port, token)
    try:
        http = create_mcp_http_client(headers={"Authorization": f"Bearer {token}"}, timeout=httpx.Timeout(120))
        async with http, streamable_http_client(f"http://127.0.0.1:{port}/mcp", http_client=http) as (r, w, _), \
                ClientSession(r, w) as session:
            await session.initialize()

            async def call(name: str, arguments: dict) -> tuple[bool, str]:
                res = await session.call_tool(name, arguments)
                return not res.isError, "".join(c.text for c in res.content if getattr(c, "text", None))

            yield call, (await session.list_tools()).tools
    finally:
        server.terminate()
