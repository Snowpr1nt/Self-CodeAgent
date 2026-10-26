"""Offline SubAgent demonstration: python -m examples.subagents.demo."""

from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any, AsyncIterator

from mewcode.agent import Agent
from mewcode.agents import loader as loader_module
from mewcode.agents.loader import AgentLoader
from mewcode.agents.notification import inject_task_notifications
from mewcode.agents.task_manager import TaskManager
from mewcode.agents.trace import TraceManager
from mewcode.client import LLMClient, NetworkError
from mewcode.conversation import ConversationManager
from mewcode.tools import ToolRegistry
from mewcode.tools.agent_tool import AgentTool, AgentToolParams
from mewcode.tools.base import StreamEnd, StreamEvent, TextDelta, ToolCallComplete
from mewcode.tools.read_file import ReadFile


class OfflineClient(LLMClient):
    """A fake provider; Agent, registry, ReadFile, and lifecycle are real."""

    def __init__(self, fixture: Path, *, fail: bool = False) -> None:
        self.fixture = fixture
        self.fail = fail
        self.calls = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def stream(
        self, conversation: ConversationManager, system: str = "",
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        self.calls += 1
        self.started.set()
        await self.release.wait()
        if self.fail:
            raise NetworkError("offline provider failure")
        if self.calls == 1:
            yield ToolCallComplete("fixture-read", "ReadFile", {"file_path": str(self.fixture)})
            yield StreamEnd("tool_use", input_tokens=7, output_tokens=3)
            return
        result = conversation.history[-1].tool_results[0]
        assert not result.is_error and "offline fixture" in result.content
        yield TextDelta("Scope: README.txt; read offline fixture.")
        yield StreamEnd("end_turn", input_tokens=11, output_tokens=5)


async def demonstrate(runtime: Path, mode: str) -> dict[str, Any]:
    work_dir = runtime / mode
    work_dir.mkdir()
    fixture = work_dir / "README.txt"
    fixture.write_text("offline fixture\n", encoding="utf-8")
    agents_dir = work_dir / ".mewcode" / "agents"
    agents_dir.mkdir(parents=True)
    shutil.copyfile(Path(__file__).parent / "agents" / "offline-worker.md", agents_dir / "offline-worker.md")

    # Process-local loader setting: no user agents, config, API keys, or memory.
    loader_module.USER_AGENTS_DIR = str(runtime / "unused-user-agents")
    loader = AgentLoader(str(work_dir))
    loader.load_all()
    client = OfflineClient(fixture, fail=mode == "failed")
    registry = ToolRegistry()
    registry.register(ReadFile())
    parent = Agent(client, registry, "anthropic", work_dir=str(work_dir))
    parent.trace_id = mode
    manager, traces = TaskManager(), TraceManager()
    tool = AgentTool(loader, manager, traces, parent)
    background = mode != "foreground"
    if mode in {"foreground", "completed", "failed"}:
        client.release.set()
    try:
        result = await tool.execute(AgentToolParams(
            prompt="Read README.txt", description="offline example",
            subagent_type="offline-worker", run_in_background=background,
        ))
        if not background:
            assert not result.is_error and "offline fixture" in result.output
            return {"mode": mode, "result": result.output, "trace": traces.get_tree(mode)[0].status}

        bg = manager.list_tasks()[0]
        # This demonstration inspects the owned runner to verify reclamation;
        # application shutdown uses the public manager.shutdown() below.
        runner = manager._async_tasks[bg.id]
        if mode == "cancelled":
            await asyncio.wait_for(client.started.wait(), 2)
            assert manager.cancel(bg.id)
        elif mode == "cancelled-before-start":
            assert manager.cancel(bg.id)
        await asyncio.wait_for(asyncio.gather(runner, return_exceptions=True), 2)
        notes = manager.poll_completed()
        conversation = ConversationManager()
        inject_task_notifications(conversation, notes)
        assert len(conversation.history) == 1
        assert manager.poll_completed() == []
        assert not manager._async_tasks
        expected = "cancelled" if mode.startswith("cancelled") else mode
        assert bg.status == traces.get(bg.agent.agent_id).status == expected
        return {
            "mode": mode, "status": bg.status,
            "trace": traces.get(bg.agent.agent_id).status,
            "notifications": len(notes), "running_tasks": len(manager._async_tasks),
        }
    finally:
        await manager.shutdown()


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix="mewcode-subagents-") as temp_dir:
        results = [await demonstrate(Path(temp_dir), mode) for mode in (
            "foreground", "completed", "failed", "cancelled", "cancelled-before-start",
        )]
        print(json.dumps({"provider": "FAKE (offline)", "results": results}, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
