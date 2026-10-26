from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from mewcode.agent import Agent
from mewcode.agents.loader import AgentLoader
from mewcode.agents.parser import AgentDef
from mewcode.agents.task_manager import TaskManager
from mewcode.agents.tool_filter import build_teammate_tools, clone_registry_for_fork, resolve_agent_tools
from mewcode.agents.trace import TraceManager
from mewcode.client import LLMClient
from mewcode.sandbox import SandboxConfig
from mewcode.teams.manager import TeamManager
from mewcode.teams.registry import AgentNameRegistry
from mewcode.tools import create_default_registry
from mewcode.tools.agent_tool import AgentTool, AgentToolParams
from mewcode.tools.base import StreamEnd, TextDelta, ToolCallComplete
from mewcode.tools.write_file import Params as WriteParams


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["definition", "fork", "teammate"])
async def test_child_file_tools_have_independent_execution_directories(tmp_path, mode):
    parent_root, child_root = tmp_path / "parent", tmp_path / "child"
    parent_root.mkdir()
    child_root.mkdir()
    parent = create_default_registry()
    for tool in parent.list_tools():
        tool.work_dir = str(parent_root)
    if mode == "definition":
        child = resolve_agent_tools(parent, AgentDef("fixture", "fixture"))
    elif mode == "fork":
        child = clone_registry_for_fork(parent)
    else:
        child = build_teammate_tools(parent, None, "fixture", "worker-id", "worker", "in-process")
    for tool in child.list_tools():
        if tool.name in {"ReadFile", "WriteFile", "EditFile", "Bash", "Glob", "Grep"}:
            tool.work_dir = str(child_root)
    result = await child.get("WriteFile").execute(WriteParams(file_path="child.txt", content="child result"))
    assert not result.is_error
    assert (child_root / "child.txt").read_text(encoding="utf-8") == "child result"
    assert not (parent_root / "child.txt").exists()
    assert all(tool.work_dir == str(parent_root) for tool in parent.list_tools())


class WriteClient(LLMClient):
    def __init__(self):
        self.calls = 0
        self.finished = asyncio.Event()

    async def stream(self, conversation, system="", tools=None):
        self.calls += 1
        if self.calls == 1:
            yield ToolCallComplete("write-1", "WriteFile", {"file_path": "result.txt", "content": "isolated worker"})
            yield StreamEnd("tool_use", input_tokens=7, output_tokens=3)
        else:
            assert not conversation.history[-1].tool_results[0].is_error
            yield TextDelta("worker complete")
            yield StreamEnd("end_turn", input_tokens=10, output_tokens=2)
            self.finished.set()


@pytest.mark.asyncio
@pytest.mark.parametrize("teammate", [False, True])
async def test_agent_tool_binds_child_tools_to_its_worktree(tmp_path, monkeypatch, teammate):
    parent_root, child_root = tmp_path / "parent", tmp_path / "child"
    parent_root.mkdir()
    child_root.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    monkeypatch.setattr(AgentNameRegistry, "_instance", AgentNameRegistry())
    registry = create_default_registry()
    for tool in registry.list_tools():
        tool.work_dir = str(parent_root)
    parent_config = SandboxConfig(allow_write=[str(parent_root)], deny_write=[str(parent_root / ".git")])
    registry.get("Bash").sandbox_config = parent_config
    client = WriteClient()
    parent = Agent(client, registry, "anthropic", work_dir=str(parent_root))
    teams, tasks = TeamManager(), TaskManager()
    team = teams.create_team("isolation", parent.agent_id, teammate_mode="in-process", is_interactive=False)
    loader = AgentLoader(str(parent_root))
    loader._agents["Fixture"] = AgentDef(
        "Fixture", "fixture", isolation="" if teammate else "worktree", permission_mode="bypassPermissions")
    class Worktrees:
        async def create(self, name, ref):
            return SimpleNamespace(path=str(child_root), head_commit="fixture-head")
        async def auto_cleanup(self, name, head):
            return SimpleNamespace(kept=True, path=str(child_root), branch="fixture")
    tool = AgentTool(loader, tasks, TraceManager(), parent, team_manager=teams, worktree_manager=Worktrees())
    try:
        result = await tool.execute(AgentToolParams(
            prompt="write relative result", description="fixture", subagent_type="Fixture",
            team_name=team.name if teammate else None, name="writer"))
        assert not result.is_error
        await asyncio.wait_for(client.finished.wait(), 2)
        assert (child_root / "result.txt").read_text(encoding="utf-8") == "isolated worker"
        assert not (parent_root / "result.txt").exists()
        assert registry.get("WriteFile").work_dir == str(parent_root)
        assert parent_config.allow_write == [str(parent_root)]
        if teammate:
            child_bash = tasks.list_tasks()[0].agent.registry.get("Bash")
            assert child_bash.work_dir == str(child_root)
            assert child_bash.sandbox_config.allow_write == [str(child_root)]
            assert child_bash.sandbox_config.deny_write == [str(child_root / ".git")]
    finally:
        await tasks.shutdown()
        await teams.shutdown()
