from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from mcp import types as mcp_types

from mewcode.agents.parser import AgentDef
from mewcode.agents.tool_filter import resolve_agent_tools
from mewcode.config import MCPServerConfig
from mewcode.mcp.client import MCPClient
from mewcode.mcp.manager import MCPManager
from mewcode.mcp.tool_wrapper import MCPToolWrapper
from mewcode.tools import ToolRegistry
from mewcode.tools.impl.tool_search import ToolSearchParams, ToolSearchTool
from mewcode.tools.read_file import ReadFile


class FakeMCPClient(MCPClient):
    def __init__(self, config: MCPServerConfig) -> None:
        super().__init__(config)
        self.connect_count = 0
        self.close_count = 0
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.fail_next_call = False

    @property
    def is_alive(self) -> bool:
        return self._alive

    @property
    def instructions(self) -> str:
        return "Search the fake documentation server before answering."

    async def connect(self) -> None:
        self.connect_count += 1
        self._alive = True

    async def list_tools(self) -> list[mcp_types.Tool]:
        return [
            mcp_types.Tool(
                name="search",
                description="Search documentation",
                inputSchema={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            )
        ]

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> mcp_types.CallToolResult:
        self.calls.append((name, arguments))
        if self.fail_next_call:
            self.fail_next_call = False
            raise RuntimeError("transport closed")
        return mcp_types.CallToolResult(
            content=[mcp_types.TextContent(type="text", text="result: agent loop")],
            isError=False,
        )

    async def close(self) -> None:
        self.close_count += 1
        self._alive = False


@pytest.mark.asyncio
async def test_fake_mcp_register_discover_call_and_shutdown() -> None:
    config = MCPServerConfig(name="docs", command="fake-mcp")
    fake_client = FakeMCPClient(config)
    manager = MCPManager()
    manager.load_configs([config])
    registry = ToolRegistry()
    search = ToolSearchTool(registry, protocol="anthropic")
    registry.register(search)

    with patch("mewcode.mcp.manager.MCPClient", return_value=fake_client):
        connected = await manager.register_all_tools(registry)

    tool_name = "mcp__docs__search"
    wrapper = registry.get(tool_name)
    assert connected.errors == []
    assert connected.servers[0].instructions.startswith("Search the fake")
    assert wrapper is not None
    assert tool_name not in {schema["name"] for schema in registry.get_all_schemas()}

    discovery = await search.execute(ToolSearchParams(query=f"select:{tool_name}"))

    assert not discovery.is_error
    assert registry.is_discovered(tool_name)
    assert tool_name in {schema["name"] for schema in registry.get_all_schemas()}

    params = wrapper.params_model.model_validate({"query": "agent loop"})
    result = await wrapper.execute(params)

    assert not result.is_error
    assert result.output == "result: agent loop"
    assert fake_client.calls == [("search", {"query": "agent loop"})]

    await manager.shutdown()
    assert fake_client.close_count == 1
    assert not fake_client.is_alive


def test_mcp_tool_name_is_recognized_by_subagent_filter() -> None:
    config = MCPServerConfig(name="docs", command="fake-mcp")
    client = FakeMCPClient(config)
    tool_def = mcp_types.Tool(
        name="search",
        description="Search documentation",
        inputSchema={"type": "object", "properties": {}},
    )
    wrapper = MCPToolWrapper("docs", tool_def, client)
    registry = ToolRegistry()
    registry.register(ReadFile())
    registry.register(wrapper)
    definition = AgentDef(
        agent_type="reader",
        when_to_use="Read documentation",
        source="project",
        tools=["ReadFile"],
    )

    filtered = resolve_agent_tools(registry, definition)

    assert {tool.name for tool in filtered.list_tools()} == {
        "ReadFile",
        "mcp__docs__search",
    }


@pytest.mark.asyncio
async def test_tool_search_cannot_select_disabled_deferred_tool() -> None:
    config = MCPServerConfig(name="docs", command="fake-mcp")
    client = FakeMCPClient(config)
    tool_def = mcp_types.Tool(
        name="search",
        description="Search documentation",
        inputSchema={"type": "object", "properties": {}},
    )
    wrapper = MCPToolWrapper("docs", tool_def, client)
    registry = ToolRegistry()
    registry.register(wrapper)
    registry.disable(wrapper.name)
    search = ToolSearchTool(registry)

    result = await search.execute(
        ToolSearchParams(query=f"select:{wrapper.name}")
    )

    assert "No matching deferred tools" in result.output
    assert not registry.is_discovered(wrapper.name)


@pytest.mark.asyncio
async def test_failed_mcp_call_closes_transport_before_reconnect() -> None:
    config = MCPServerConfig(name="docs", command="fake-mcp")
    client = FakeMCPClient(config)
    await client.connect()
    client.fail_next_call = True
    tool_def = (await client.list_tools())[0]
    wrapper = MCPToolWrapper("docs", tool_def, client)
    params = wrapper.params_model.model_validate({"query": "agent loop"})

    result = await wrapper.execute(params)

    assert result.is_error
    assert "transport closed" in result.output
    assert client.close_count == 1
    assert not client.is_alive
