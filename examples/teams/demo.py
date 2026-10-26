"""Two real Agent loops with a deterministic fake Provider, entirely offline."""
from __future__ import annotations

import asyncio
import json
import re
import tempfile
from pathlib import Path
from unittest.mock import patch

from mewcode.agent import Agent
from mewcode.agents.tool_filter import build_teammate_tools
from mewcode.client import LLMClient
from mewcode.teams.manager import TeamManager
from mewcode.teams.models import TeammateInfo
from mewcode.teams.registry import AgentNameRegistry
from mewcode.teams.shared_task import SharedTaskStore
from mewcode.teams.spawn_inprocess import spawn_inprocess_teammate
from mewcode.tools import ToolRegistry
from mewcode.tools.base import StreamEnd, TextDelta, ToolCallComplete
from mewcode.tools.send_message import SendMessageParams, SendMessageTool
from mewcode.tools.task_create import TaskCreateParams, TaskCreateTool


class OfflineWorker(LLMClient):
    """Only the model is fake; all emitted coordination tools run for real."""

    def __init__(self, name: str, task_id: str, peer_sent: asyncio.Event) -> None:
        self.name = name
        self.task_id = task_id
        self.peer_sent = peer_sent
        self.calls = 0
        self.received_peer_message = False

    async def stream(self, conversation, system="", tools=None):
        self.calls += 1
        if self.calls == 1:
            if self.name == "bob":
                await self.peer_sent.wait()
            yield ToolCallComplete(f"{self.name}-get", "TaskGet", {"task_id": self.task_id})
        elif self.calls == 2:
            if self.name == "bob":
                # The Event coordinates timing only. The content must arrive
                # through SendMessage -> persisted Mailbox -> Agent injection.
                self.received_peer_message = any("alice-partial=20" in m.content for m in conversation.history)
                assert self.received_peer_message
            yield ToolCallComplete(f"{self.name}-start", "TaskUpdate", {
                "task_id": self.task_id, "status": "in_progress", "assignee": self.name,
            })
            if self.name == "alice":
                yield ToolCallComplete("alice-peer", "SendMessage", {
                    "to": "bob", "message": "alice-partial=20", "summary": "first partial result for peer",
                })
            value = 20 if self.name == "alice" else 22
            yield ToolCallComplete(f"{self.name}-result", "SendMessage", {
                "to": "lead", "message": f"result {self.name}={value}", "summary": "completed partial result for lead",
            })
        elif self.calls == 3:
            # Every previous tool result must be a real successful execution.
            assert all(not result.is_error for result in conversation.history[-1].tool_results)
            if self.name == "alice":
                self.peer_sent.set()
            yield ToolCallComplete(f"{self.name}-done", "TaskUpdate", {
                "task_id": self.task_id, "status": "completed",
            })
        else:
            yield TextDelta(f"{self.name}: task {self.task_id} completed")
        yield StreamEnd("end_turn", input_tokens=7, output_tokens=3)


async def run_demo(runtime: Path) -> dict:
    """Return evidence from real task files, mailbox messages, and owned tasks."""
    runtime.mkdir(parents=True, exist_ok=True)
    home = runtime / "isolated-home"
    home.mkdir(exist_ok=True)
    with patch.object(Path, "home", return_value=home), patch.object(
        AgentNameRegistry, "_instance", AgentNameRegistry(),
    ):
        manager = TeamManager()
        team = manager.create_team("offline-pair", "lead-id", teammate_mode="in-process", is_interactive=False)
        mailbox = manager.get_mailbox(team.name)
        creator = TaskCreateTool(manager, team.name, "lead")
        for name in ("alice", "bob"):
            result = await creator.execute(TaskCreateParams(title=f"analyse {name} partition", assignee=name))
            assert not result.is_error
        board = manager.get_task_store(team.name)
        assigned = {task.assignee: task.id for task in board.list_tasks()}
        peer_sent = asyncio.Event()
        handles, clients = [], {}
        try:
            for name in ("alice", "bob"):
                client = OfflineWorker(name, assigned[name], peer_sent)
                clients[name] = client
                work_dir = runtime / name
                work_dir.mkdir()
                agent_id = f"{name}-id"
                registry = build_teammate_tools(ToolRegistry(), manager, team.name, agent_id, name, "in-process")
                agent = Agent(client, registry, "anthropic", work_dir=str(work_dir))
                agent.agent_id, agent.team_name, agent._team_manager = agent_id, team.name, manager
                member = TeammateInfo(name, agent_id, "offline-worker", "FAKE", "", "in-process", True)
                manager.register_member(team.name, member)
                handle = spawn_inprocess_teammate(
                    agent, f"Complete assigned shared task {assigned[name]}", name,
                    member=member, team_name=team.name, mailbox=mailbox,
                )
                manager.register_inprocess_handle(agent_id, handle)
                handles.append(handle)

            async def wait_for_idle():
                while not all(handle.progress.status == "idle" for handle in handles):
                    if any(handle.done for handle in handles):
                        raise RuntimeError("a worker exited before reporting idle")
                    await asyncio.sleep(0)
            await asyncio.wait_for(wait_for_idle(), 3)
            notes = manager.drain_lead_mailbox()
            assert manager.drain_lead_mailbox() == []
            combined = "\n".join(notes)
            partials = {name: int(value) for name, value in re.findall(r"result (alice|bob)=(\d+)", combined)}
            assert partials == {"alice": 20, "bob": 22}
            assert clients["bob"].received_peer_message
            # Reopen task storage to prove assignments and statuses are on disk.
            saved = SharedTaskStore(Path(team.config_path).parent / "tasks.json").list_tasks()
            assert [(t.assignee, t.status) for t in saved] == [("alice", "completed"), ("bob", "completed")]
            lead = SendMessageTool(manager, team.name, "lead-id", "lead")
            for name in ("alice", "bob"):
                result = await lead.execute(SendMessageParams(
                    to=name, message="All results merged; finish.", message_type="shutdown_request",
                ))
                assert not result.is_error
            await asyncio.wait_for(asyncio.gather(*(h.task for h in handles)), 3)
            assert not manager._inprocess_handles
            assert all(h.done and h.progress.status == "completed" for h in handles)
            manager.delete_team(team.name)
            assert not Path(team.config_path).parent.exists()
            assert not AgentNameRegistry.instance().list_all()
            return {
                "provider": "FAKE (offline)", "workers": 2,
                "tasks": [{"assignee": t.assignee, "status": t.status} for t in saved],
                "peer_message_received": True, "partial_results": list(partials.values()),
                "merged_result": sum(partials.values()),
                "worker_statuses": [h.progress.status for h in handles],
                "running_handles": len(manager._inprocess_handles), "team_removed": True,
            }
        finally:
            await manager.shutdown()


async def main() -> None:
    with tempfile.TemporaryDirectory(prefix="mewcode-teams-") as tmp:
        print(json.dumps(await run_demo(Path(tmp)), indent=2))


if __name__ == "__main__":
    asyncio.run(main())
