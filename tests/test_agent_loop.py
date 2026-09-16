from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, AsyncIterator

import pytest
from pydantic import BaseModel

from mewcode.agent import (
    Agent,
    ErrorEvent,
    LoopComplete,
    StreamText,
    ToolResultEvent,
    ToolUseEvent,
    TurnComplete,
    UsageEvent,
)
from mewcode.client import LLMClient, NetworkError
from mewcode.conversation import ConversationManager
from mewcode.tools import ToolRegistry
from mewcode.tools.base import StreamEnd, StreamEvent, TextDelta, Tool, ToolCallComplete, ToolResult


class EchoParams(BaseModel):
    value: str


class EchoTool(Tool):
    name = "Echo"
    description = "Return the supplied value."
    params_model = EchoParams
    category = "read"
    is_concurrency_safe = True

    async def execute(self, params: EchoParams) -> ToolResult:
        return ToolResult(output=f"echo:{params.value}")


class ScriptedClient(LLMClient):
    def __init__(self) -> None:
        self.calls = 0

    async def stream(
        self,
        conversation: ConversationManager,
        system: str = "",
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        self.calls += 1
        if self.calls == 1:
            yield ToolCallComplete("echo-1", "Echo", {"value": "hello"})
            yield StreamEnd("end_turn", input_tokens=10, output_tokens=4)
            return

        yield TextDelta("The tool returned echo:hello.")
        yield StreamEnd("end_turn", input_tokens=18, output_tokens=7)


@pytest.mark.asyncio
async def test_react_loop_closes_after_tool_feedback(tmp_path: Path) -> None:
    registry = ToolRegistry()
    registry.register(EchoTool())
    client = ScriptedClient()
    agent = Agent(client, registry, "anthropic", work_dir=str(tmp_path))
    conversation = ConversationManager()
    conversation.add_user_message("Echo hello, then report the result.")

    events = [event async for event in agent.run(conversation)]

    assert client.calls == 2
    assert [type(event) for event in events] == [
        ToolUseEvent,
        UsageEvent,
        ToolResultEvent,
        TurnComplete,
        StreamText,
        UsageEvent,
        LoopComplete,
    ]
    result = next(event for event in events if isinstance(event, ToolResultEvent))
    assert result.tool_id == "echo-1"
    assert result.output == "echo:hello"
    assert not result.is_error
    assert events[-1] == LoopComplete(total_turns=2)


class SlowParams(BaseModel):
    pass


class SlowTool(Tool):
    name = "Slow"
    description = "Wait until cancelled."
    params_model = SlowParams
    category = "read"
    is_concurrency_safe = True

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def execute(self, params: SlowParams) -> ToolResult:
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        return ToolResult(output="unreachable")


class InterruptedClient(LLMClient):
    def __init__(self, tool: SlowTool) -> None:
        self.tool = tool

    async def stream(
        self,
        conversation: ConversationManager,
        system: str = "",
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        yield ToolCallComplete("slow-1", "Slow", {})
        await self.tool.started.wait()
        raise NetworkError("provider connection lost")


@pytest.mark.asyncio
async def test_stream_failure_is_event_and_cancels_started_tools(tmp_path: Path) -> None:
    tool = SlowTool()
    registry = ToolRegistry()
    registry.register(tool)
    agent = Agent(
        InterruptedClient(tool),
        registry,
        "anthropic",
        work_dir=str(tmp_path),
    )
    conversation = ConversationManager()
    conversation.add_user_message("Run the slow tool.")

    events = [event async for event in agent.run(conversation)]

    assert any(isinstance(event, ToolUseEvent) for event in events)
    errors = [event for event in events if isinstance(event, ErrorEvent)]
    assert errors == [ErrorEvent(message="LLM stream failed: provider connection lost")]
    assert tool.cancelled.is_set()
    assert not any(isinstance(event, LoopComplete) for event in events)
