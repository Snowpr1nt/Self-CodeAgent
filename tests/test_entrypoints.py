# 来源：公众号@小林coding
# 后端八股网站：xiaolincoding.com
# Agent网站：xiaolinnote.com
# 简历模版：jianli.xiaolinnote.com
from __future__ import annotations

import asyncio
import json
from io import StringIO
from typing import Any, AsyncIterator

import pytest

from mewcode.agent import (
    Agent,
    CompactNotification,
    ErrorEvent,
    HookEvent,
    LoopComplete,
    PermissionRequest,
    PermissionResponse,
    RetryEvent,
    StreamText,
    ThinkingText,
    ToolResultEvent,
    ToolUseEvent,
    TurnComplete,
    UsageEvent,
)
from mewcode.client import LLMClient
from mewcode.conversation import ConversationManager
from mewcode.noninteractive import PromptEventWriter
from mewcode.tools import create_default_registry
from mewcode.tools.base import StreamEnd, StreamEvent, TextDelta


class RecordingClient(LLMClient):
    def __init__(self) -> None:
        self.system_prompts: list[str] = []

    async def stream(
        self,
        conversation: ConversationManager,
        system: str = "",
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        self.system_prompts.append(system)
        yield TextDelta("done")
        yield StreamEnd("end_turn", input_tokens=3, output_tokens=1)


@pytest.mark.asyncio
async def test_agent_run_system_prompt_uses_agent_work_dir(tmp_path) -> None:
    client = RecordingClient()
    agent = Agent(
        client,
        create_default_registry(),
        "anthropic",
        work_dir=str(tmp_path),
    )
    conversation = ConversationManager()
    conversation.add_user_message("inspect")

    events = [event async for event in agent.run(conversation)]

    assert any(isinstance(event, LoopComplete) for event in events)
    assert f"Working directory: {tmp_path}" in client.system_prompts[0]


@pytest.mark.asyncio
async def test_run_to_completion_system_prompt_uses_agent_work_dir(tmp_path) -> None:
    client = RecordingClient()
    agent = Agent(
        client,
        create_default_registry(),
        "anthropic",
        work_dir=str(tmp_path),
    )

    result = await agent.run_to_completion("inspect")

    assert result == "done"
    assert f"Working directory: {tmp_path}" in client.system_prompts[0]


def test_text_writer_buffers_assistant_text_until_completion() -> None:
    stdout = StringIO()
    stderr = StringIO()
    writer = PromptEventWriter(
        "text",
        stdout=stdout,
        stderr=stderr,
        monotonic=lambda: 1.0,
    )

    assert writer.handle(StreamText("你")) is False
    assert writer.handle(StreamText("好")) is False
    assert stdout.getvalue() == ""

    assert writer.handle(LoopComplete(total_turns=1)) is True
    assert stdout.getvalue() == "你好"
    assert stderr.getvalue() == ""


def test_stream_json_writer_matches_tool_results_by_id() -> None:
    ticks = iter([10.0, 10.25])
    stdout = StringIO()
    writer = PromptEventWriter(
        "stream-json",
        stdout=stdout,
        stderr=StringIO(),
        monotonic=lambda: next(ticks),
    )

    events = [
        StreamText("完成"),
        ToolUseEvent("ReadFile", "tool-1", {"file_path": "a.py"}),
        ToolUseEvent("Bash", "tool-2", {"command": "echo ok"}),
        ToolResultEvent("tool-1", "ReadFile", "missing", True, 0.0126),
        UsageEvent(input_tokens=11, output_tokens=7),
        TurnComplete(turn=1),
        LoopComplete(total_turns=2),
    ]
    completed = [writer.handle(event) for event in events]

    rows = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert completed[-1] is True
    assert [row["type"] for row in rows] == [
        "assistant",
        "tool_use",
        "tool_use",
        "tool_result",
        "usage",
        "turn_complete",
        "result",
    ]
    assert rows[3]["elapsed"] == 0.013
    assert rows[-1] == {
        "type": "result",
        "result": "完成",
        "duration_ms": 250,
        "num_turns": 2,
        "tool_calls": [
            {"name": "ReadFile", "is_error": True},
            {"name": "Bash", "is_error": False},
        ],
        "usage": {"input_tokens": 11, "output_tokens": 7},
        "stop_reason": "end_turn",
    }


def test_stream_json_writer_exposes_control_events() -> None:
    stdout = StringIO()
    writer = PromptEventWriter(
        "stream-json",
        stdout=stdout,
        stderr=StringIO(),
        monotonic=lambda: 1.0,
    )

    writer.handle(ThinkingText("reasoning"))
    writer.handle(RetryEvent("rate limit", wait=0.5))
    writer.handle(CompactNotification(2000, "compacted"))
    writer.handle(HookEvent("lint", "post_receive", "ok", True))
    writer.handle(ErrorEvent("boom"))

    rows = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert rows == [
        {"type": "thinking", "text": "reasoning"},
        {"type": "retry", "reason": "rate limit", "wait": 0.5},
        {"type": "compact", "message": "compacted", "before_tokens": 2000},
        {
            "type": "hook",
            "hook_id": "lint",
            "event": "post_receive",
            "output": "ok",
            "success": True,
        },
        {"type": "error", "message": "boom"},
    ]


def test_text_writer_routes_errors_to_stderr() -> None:
    stdout = StringIO()
    stderr = StringIO()
    writer = PromptEventWriter(
        "text",
        stdout=stdout,
        stderr=stderr,
        monotonic=lambda: 1.0,
    )

    writer.handle(ErrorEvent("boom"))

    assert stdout.getvalue() == ""
    assert stderr.getvalue() == "Error: boom\n"


@pytest.mark.asyncio
async def test_writer_auto_approves_noninteractive_permission_request() -> None:
    future: asyncio.Future[PermissionResponse] = asyncio.get_running_loop().create_future()
    writer = PromptEventWriter(
        "text",
        stdout=StringIO(),
        stderr=StringIO(),
        monotonic=lambda: 1.0,
    )

    writer.handle(PermissionRequest("Bash", "run command", future))

    assert future.result() is PermissionResponse.ALLOW
