"""Deterministic SubAgent lifecycle checks; no network or fixed sleeps."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any, AsyncIterator

import pytest
from pydantic import BaseModel

from mewcode.agent import Agent
from mewcode.agents.loader import AgentLoader
from mewcode.agents.notification import inject_task_notifications
from mewcode.agents.parser import AgentDef
from mewcode.agents.task_manager import TaskManager
from mewcode.agents.tool_filter import (
    apply_coordinator_filter, build_teammate_tools, clone_registry_for_fork,
    resolve_agent_tools,
)
from mewcode.agents.trace import TraceManager
from mewcode.client import LLMClient, NetworkError
from mewcode.conversation import ConversationManager, ToolUseBlock
from mewcode.tools import ToolRegistry
from mewcode.tools.agent_tool import AgentTool, AgentToolParams
from mewcode.tools.base import StreamEnd, StreamEvent, TextDelta, Tool, ToolResult
from mewcode.teams.mailbox import Mailbox
from mewcode.teams.models import AgentTeam, BackendType
from mewcode.teams.manager import TeamManager
from mewcode.teams.registry import AgentNameRegistry


class ControlledClient(LLMClient):
    """Keep the real Agent loop; control only the external provider boundary."""

    def __init__(self, *, error: bool = False, blocked: bool = False) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.closed = asyncio.Event()
        self.error = error
        self.calls: list[tuple[ConversationManager, list[dict[str, Any]]]] = []
        if not blocked:
            self.release.set()

    async def stream(
        self, conversation: ConversationManager, system: str = "",
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        self.calls.append((conversation, tools or []))
        self.started.set()
        try:
            await self.release.wait()
            if self.error:
                raise NetworkError("controlled provider failure")
            yield TextDelta("Scope: inspected fixture; complete.")
            yield StreamEnd("end_turn", input_tokens=11, output_tokens=5)
        finally:
            self.closed.set()


def make_tool(tmp_path: Path, client: ControlledClient):
    loader = AgentLoader(str(tmp_path))
    loader._agents["Fixture"] = AgentDef(
        agent_type="Fixture", when_to_use="Offline lifecycle fixture",
        system_prompt="Return the fixture result.",
    )
    manager = TaskManager()
    traces = TraceManager()
    parent = Agent(client, ToolRegistry(), "anthropic", work_dir=str(tmp_path))
    parent.trace_id = "fixture-trace"
    parent._current_conversation = ConversationManager()
    parent._current_conversation.add_user_message("Parent context")
    tool = AgentTool(loader, manager, traces, parent, enable_fork=True)
    parent.registry.register(tool)
    return tool, manager, traces, parent


async def settle(manager: TaskManager, task_id: str) -> None:
    # Capture the owned runner before it can be removed. Await its actual end,
    # then a loop turn for asyncio's done callbacks; no timing assumptions.
    runner = manager._async_tasks[task_id]
    await asyncio.wait_for(asyncio.gather(runner, return_exceptions=True), 2)
    await asyncio.sleep(0)


@pytest.mark.asyncio
@pytest.mark.parametrize("adopt", [False, True])
async def test_cancel_before_first_scheduling_finalizes_once(tmp_path: Path, adopt: bool):
    client = ControlledClient(blocked=True)
    agent = Agent(client, ToolRegistry(), "anthropic", work_dir=str(tmp_path))
    manager = TaskManager()
    if adopt:
        task_id = manager.adopt_running(agent, "fixture", partial_result="partial")
    else:
        task_id = manager.launch(agent, "fixture")
    assert manager.cancel(task_id)
    await settle(manager, task_id)

    bg = manager.get(task_id)
    assert bg.status == "cancelled"
    assert bg.end_time is not None
    assert not manager._async_tasks
    assert [task.id for task in manager.poll_completed()] == [task_id]
    assert manager.poll_completed() == []
    assert not manager.cancel(task_id)
    assert not client.started.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [False, True])
async def test_background_result_trace_and_notification_finish_together(tmp_path: Path, error: bool):
    client = ControlledClient(error=error, blocked=True)
    tool, manager, traces, parent = make_tool(tmp_path, client)
    result = await tool.execute(AgentToolParams(
        prompt="fixture", description="fixture", subagent_type="Fixture",
        run_in_background=True,
    ))
    assert not result.is_error
    bg = manager.list_tasks()[0]
    await asyncio.wait_for(client.started.wait(), 2)
    assert bg.status == "running"
    assert not manager.poll_completed()
    client.release.set()
    await settle(manager, bg.id)

    status = "failed" if error else "completed"
    assert bg.status == status
    assert ("controlled provider failure" if error else "inspected fixture") in bg.result
    node = traces.get(bg.agent.agent_id)
    assert node.status == status
    assert node.end_time is not None
    assert node.parent_id == parent.agent_id
    assert node.trace_id == "fixture-trace"
    assert (node.input_tokens, node.output_tokens) == ((0, 0) if error else (11, 5))
    assert not manager._async_tasks
    notes = manager.poll_completed()
    conversation = ConversationManager()
    inject_task_notifications(conversation, notes)
    inject_task_notifications(conversation, manager.poll_completed())
    assert len(conversation.history) == 1
    assert f"Status: {status}" in conversation.history[0].content


@pytest.mark.asyncio
async def test_cancel_running_background_closes_stream_and_trace(tmp_path: Path):
    client = ControlledClient(blocked=True)
    tool, manager, traces, _ = make_tool(tmp_path, client)
    await tool.execute(AgentToolParams(
        prompt="fixture", description="fixture", subagent_type="Fixture",
        run_in_background=True,
    ))
    bg = manager.list_tasks()[0]
    await asyncio.wait_for(client.started.wait(), 2)
    assert manager.cancel(bg.id)
    await settle(manager, bg.id)
    assert client.closed.is_set()
    assert bg.status == "cancelled"
    assert traces.get(bg.agent.agent_id).status == "cancelled"
    assert not manager._async_tasks
    assert manager.poll_completed() == [bg]
    assert manager.poll_completed() == []


@pytest.mark.asyncio
async def test_cancel_foreground_preserves_cancellation_and_finalizes_trace(tmp_path: Path):
    client = ControlledClient(blocked=True)
    tool, manager, traces, _ = make_tool(tmp_path, client)
    runner = asyncio.create_task(tool.execute(AgentToolParams(
        prompt="fixture", description="fixture", subagent_type="Fixture",
    )))
    await asyncio.wait_for(client.started.wait(), 2)
    runner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await runner
    node = traces.get_tree("fixture-trace")[0]
    assert node.status == "cancelled"
    assert node.end_time is not None
    assert client.closed.is_set()
    assert manager.list_tasks() == []


@pytest.mark.asyncio
async def test_idle_teammate_remains_cancellable_until_runner_finishes(tmp_path: Path, monkeypatch):
    client = ControlledClient()
    agent = Agent(client, ToolRegistry(), "anthropic", work_dir=str(tmp_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    teams = TeamManager()
    team = teams.create_team("fixture-team", "lead", teammate_mode="in-process", is_interactive=False)
    mailbox = teams.get_mailbox(team.name)
    agent.team_name = team.name
    agent._team_manager = teams
    manager = TaskManager()
    task_id = manager.launch(agent, "fixture")
    runner = manager._async_tasks[task_id]
    await asyncio.wait_for(client.closed.wait(), 2)
    bg = manager.get(task_id)
    try:
        assert "[idle]" in mailbox.consume("lead")[0].content
        assert bg.status == "running"
        assert manager.cancel(task_id)
    finally:
        runner.cancel()
        await asyncio.gather(runner, return_exceptions=True)
        await asyncio.sleep(0)
    assert bg.status == "cancelled"
    assert not manager._async_tasks
    assert manager.poll_completed() == [bg]


@pytest.mark.asyncio
async def test_shutdown_awaits_running_streams_and_unscheduled_tasks(tmp_path: Path):
    manager = TaskManager()
    client = ControlledClient(blocked=True)
    agent = Agent(client, ToolRegistry(), "anthropic", work_dir=str(tmp_path))
    running_id = manager.launch(agent, "running")
    await asyncio.wait_for(client.started.wait(), 2)
    early_id = manager.launch(agent, "not yet scheduled")
    await manager.shutdown()
    assert client.closed.is_set()
    assert not manager._async_tasks
    assert {bg.id for bg in manager.poll_completed()} == {running_id, early_id}
    assert all(bg.status == "cancelled" for bg in manager.list_tasks())
    await manager.shutdown()
    assert manager.poll_completed() == []


@pytest.mark.asyncio
async def test_tui_exit_drains_owned_background_tasks(tmp_path: Path):
    from mewcode.app import MewCodeApp

    manager = TaskManager()
    client = ControlledClient(blocked=True)
    agent = Agent(client, ToolRegistry(), "anthropic", work_dir=str(tmp_path))
    task_id = manager.launch(agent, "running")
    await asyncio.wait_for(client.started.wait(), 2)

    async def shutdown_mcp():
        return None

    app = SimpleNamespace(
        _streaming=False, _exit_requested=False, _stale_cleanup_task=None,
        agent=None, hook_engine=None, task_manager=manager,
        session=None, _shutdown_mcp=shutdown_mcp,
        exit=lambda: None,
    )
    await MewCodeApp.action_handle_ctrl_c(app)

    assert client.closed.is_set()
    assert manager.get(task_id).status == "cancelled"
    assert not manager._async_tasks


class EmptyParams(BaseModel):
    pass


class NamedTool(Tool):
    description = "Registry fixture"
    params_model = EmptyParams

    def __init__(self, name: str, *, deferred: bool = False) -> None:
        self.name = name
        self.should_defer = deferred

    async def execute(self, params: EmptyParams) -> ToolResult:
        return ToolResult(output=self.name)


@pytest.mark.parametrize("mode", ["definition", "fork", "teammate", "coordinator"])
def test_registry_copy_preserves_disabled_and_discovered_state(mode: str):
    parent = ToolRegistry()
    parent.register(NamedTool("ReadFile"))
    parent.register(NamedTool("Bash"))
    parent.register(NamedTool("mcp__fixture__lookup", deferred=True))
    parent.disable("Bash")
    parent.mark_discovered("mcp__fixture__lookup")
    if mode == "fork":
        child = clone_registry_for_fork(parent)
    elif mode == "teammate":
        child = build_teammate_tools(parent, None, "fixture", "worker", "worker", "in-process")
        # In-process teammates have a separate fixed tool allowlist that does
        # not include MCP tools; state still applies to retained tools.
    elif mode == "coordinator":
        child = apply_coordinator_filter(parent)
    else:
        child = resolve_agent_tools(parent, AgentDef("Fixture", "fixture"), True)
    assert not child.is_enabled("Bash")
    schemas = {schema["name"] for schema in child.get_all_schemas()}
    assert "ReadFile" in schemas
    assert "Bash" not in schemas
    if mode != "teammate":
        assert schemas == {"ReadFile", "mcp__fixture__lookup"}
    child.disable("ReadFile")
    assert parent.is_enabled("ReadFile")


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [False, True])
async def test_foreground_returns_result_or_provider_failure(tmp_path: Path, error: bool):
    client = ControlledClient(error=error)
    tool, manager, traces, _ = make_tool(tmp_path, client)
    result = await tool.execute(AgentToolParams(
        prompt="fixture", description="fixture", subagent_type="Fixture",
    ))
    assert result.is_error is error
    assert ("controlled provider failure" if error else "inspected fixture") in result.output
    assert traces.get_tree("fixture-trace")[0].status == ("failed" if error else "completed")
    assert manager.poll_completed() == []


@pytest.mark.asyncio
async def test_fork_closes_pending_calls_without_mutating_parent(tmp_path: Path):
    client = ControlledClient(blocked=True)
    tool, manager, traces, parent = make_tool(tmp_path, client)
    parent._current_conversation.add_assistant_message("checking", [
        ToolUseBlock("pending-read", "ReadFile", {"file_path": "fixture.txt"}),
    ])
    result = await tool.execute(AgentToolParams(prompt="fork fixture", description="fixture"))
    assert not result.is_error
    bg = manager.list_tasks()[0]
    await asyncio.wait_for(client.started.wait(), 2)
    child_conversation, schemas = client.calls[0]
    assert child_conversation.history[-2].tool_results[0].tool_use_id == "pending-read"
    assert child_conversation.history[-2].tool_results[0].content == "interrupted"
    assert len(parent._current_conversation.history) == 2
    nested_tool = bg.agent.registry.get("Agent")
    nested = await nested_tool.execute(AgentToolParams(prompt="nested", description="fixture"))
    assert nested.is_error
    assert "cannot fork" in nested.output
    assert schemas == parent.registry.get_all_schemas()
    client.release.set()
    await settle(manager, bg.id)
    assert traces.get(bg.agent.agent_id).status == "completed"


@pytest.mark.asyncio
async def test_teammate_launch_connects_background_trace(tmp_path: Path, monkeypatch):
    client = ControlledClient(blocked=True)
    tool, manager, traces, parent = make_tool(tmp_path, client)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    teams = TeamManager()
    teams.create_team("fixture-team", parent.agent_id, teammate_mode="in-process", is_interactive=False)

    class WorktreeFixture:
        async def create(self, name, ref):
            return SimpleNamespace(path=str(tmp_path))

    tool._team_manager = teams
    tool._worktree_manager = WorktreeFixture()
    try:
        result = await tool.execute(AgentToolParams(
            prompt="fixture", description="fixture", subagent_type="Fixture",
            team_name="fixture-team", name="lifecycle-worker",
        ))
        assert not result.is_error
        bg = manager.list_tasks()[0]
        await asyncio.wait_for(client.started.wait(), 2)
        assert manager.cancel(bg.id)
        await settle(manager, bg.id)
        assert traces.get(bg.agent.agent_id).status == "cancelled"
        assert not manager._async_tasks
    finally:
        await manager.shutdown()
        AgentNameRegistry.instance().unregister("lifecycle-worker")
