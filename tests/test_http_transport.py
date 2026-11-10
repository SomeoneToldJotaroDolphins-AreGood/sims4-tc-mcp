"""streamable-http transport + bearer-token middleware tests."""

import asyncio
import hashlib
import hmac

import httpx2
import pytest
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

from sims4_mcp import config, server

TOKEN = "test-secret-123"


def _free_port() -> int:
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def http_server():
    """Run the authenticated streamable-http app in a background task."""
    import uvicorn

    app = server._bearer_middleware(
        server.mcp.streamable_http_app(host="127.0.0.1"), TOKEN)
    port = _free_port()
    uconfig = uvicorn.Config(app, host="127.0.0.1", port=port,
                             log_level="warning")
    uv = uvicorn.Server(uconfig)
    loop_thread = asyncio.new_event_loop()

    import threading
    t = threading.Thread(target=loop_thread.run_until_complete,
                         args=(uv.serve(),), daemon=True)
    t.start()
    import time
    for _ in range(100):
        if uv.started:
            break
        time.sleep(0.05)
    assert uv.started, "uvicorn failed to start"
    yield f"http://127.0.0.1:{port}/mcp"
    uv.should_exit = True
    t.join(timeout=5)


def _call(url, headers):
    async def run():
        client = httpx2.AsyncClient(headers=headers, timeout=10)
        async with client:
            async with streamable_http_client(url,
                                              http_client=client) as streams:
                async with ClientSession(streams[0], streams[1]) as s:
                    await s.initialize()
                    tools = await s.list_tools()
                    res = await s.call_tool(
                        "hash_fnv",
                        {"text": "Sims4", "bits": 32, "high_bit": True})
                    sc = res.structured_content
                    data = sc.get("result", sc) if sc else None
                    if not data or "hex" not in data:
                        import json as _json
                        data = _json.loads(res.content[0].text)
                    return len(tools.tools), data
    return asyncio.run(run())


def test_bearer_token_grants_access(http_server):
    count, result = _call(http_server,
                          {"Authorization": f"Bearer {TOKEN}"})
    assert count >= 15
    assert int(result["hex"], 16) >> 31 == 1


def test_missing_token_rejected(http_server):
    with pytest.raises(Exception):
        _call(http_server, {})


def test_wrong_token_rejected(http_server):
    with pytest.raises(Exception):
        _call(http_server, {"Authorization": "Bearer nope"})


def test_middleware_compares_hashes():
    app = server._bearer_middleware(None, TOKEN)  # construction must not fail
    digest = hashlib.sha256(TOKEN.encode()).digest()
    assert hmac.compare_digest(digest,
                               hashlib.sha256(TOKEN.encode()).digest())


def test_lan_bind_requires_token(monkeypatch):
    monkeypatch.delenv("SIMS4_MCP_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("SIMS4_MCP_ALLOW_UNAUTHENTICATED", raising=False)
    with pytest.raises(RuntimeError):
        config.requires_auth("0.0.0.0")
    # loopback without token: allowed to start, unauthenticated
    assert config.requires_auth("127.0.0.1") is False
    monkeypatch.setenv("SIMS4_MCP_AUTH_TOKEN", "x")
    assert config.requires_auth("0.0.0.0") is True
    assert config.requires_auth("127.0.0.1") is True


def test_transport_defaults(monkeypatch):
    monkeypatch.delenv("SIMS4_MCP_TRANSPORT", raising=False)
    assert config.transport() == "stdio"
    monkeypatch.setenv("SIMS4_MCP_TRANSPORT", "streamable-http")
    assert config.transport() == "streamable-http"
    monkeypatch.setenv("SIMS4_MCP_TRANSPORT", "bogus")
    assert config.transport() == "stdio"
    monkeypatch.setenv("SIMS4_MCP_PORT", "not-a-number")
    assert config.http_port() == 7348
