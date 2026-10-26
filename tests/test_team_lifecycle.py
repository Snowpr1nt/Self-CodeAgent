"""Offline, real Team/Agent/mailbox lifecycle acceptance."""
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from mewcode.agent import Agent
from mewcode.agents.tool_filter import build_teammate_tools
from mewcode.client import LLMClient, NetworkError
from mewcode.teams.manager import TeamError, TeamManager
from mewcode.teams.models import TeammateInfo
from mewcode.teams.registry import AgentNameRegistry
from mewcode.teams.spawn_inprocess import spawn_inprocess_teammate
from mewcode.tools import ToolRegistry
from mewcode.tools.base import StreamEnd, TextDelta, ToolCallComplete
from mewcode.tools.send_message import SendMessageParams, SendMessageTool


class ReplyClient(LLMClient):
    def __init__(self, *, blocked=False, fail=False):
        self.calls = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.closed = asyncio.Event()
        self.fail = fail
        if not blocked:
            self.release.set()

    async def stream(self, conversation, system="", tools=None):
        self.calls += 1
        self.started.set()
        try:
            await self.release.wait()
            if self.fail:
                raise NetworkError("offline teammate failure")
            yield TextDelta("offline reply")
            yield StreamEnd("end_turn", input_tokens=7, output_tokens=3)
        finally:
            self.closed.set()


@pytest.fixture
def team_runtime(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(AgentNameRegistry, "_instance", AgentNameRegistry())
    monkeypatch.setattr("mewcode.teams.spawn_inprocess.IDLE_POLL_INTERVAL", 0)
    manager = TeamManager()
    team = manager.create_team("lifecycle", "lead-id", teammate_mode="in-process", is_interactive=False)
    return manager, team


def worker(tmp_path, runtime, client):
    manager, team = runtime
    registry = build_teammate_tools(ToolRegistry(), manager, team.name, "worker-id", "worker", "in-process")
    agent = Agent(client, registry, "anthropic", work_dir=str(tmp_path))
    agent.agent_id = "worker-id"
    agent.team_name = team.name
    agent._team_manager = manager
    member = TeammateInfo("worker", agent.agent_id, "fixture", "fake", "", "in-process", True)
    manager.register_member(team.name, member)
    handle = spawn_inprocess_teammate(agent, "initial task", "worker", member=member, team_name=team.name,
                                      mailbox=manager.get_mailbox(team.name))
    manager.register_inprocess_handle(agent.agent_id, handle)
    return handle


async def until(predicate):
    async def poll():
        while not predicate():
            await asyncio.sleep(0)
    await asyncio.wait_for(poll(), 2)


@pytest.mark.asyncio
async def test_send_to_lead_uses_current_team_without_global_alias(team_runtime):
    manager, team = team_runtime
    sender = SendMessageTool(manager, team.name, "worker-id", "worker")
    result = await sender.execute(SendMessageParams(to="lead", message="result=42", summary="worker result ready"))
    assert not result.is_error
    notes = manager.drain_lead_mailbox()
    assert len(notes) == 1 and "result=42" in notes[0]
    assert manager.drain_lead_mailbox() == []


@pytest.mark.asyncio
async def test_send_rejects_globally_registered_recipient_outside_team(team_runtime):
    manager, team = team_runtime
    AgentNameRegistry.instance().register("outsider", "other-team-worker")
    result = await SendMessageTool(manager, team.name, "lead-id", "lead").execute(
        SendMessageParams(to="outsider", message="private task", summary="task assignment"))
    assert result.is_error
    assert manager.get_mailbox(team.name).read("other-team-worker") == []


@pytest.mark.asyncio
async def test_idle_notification_and_followup_use_agent_ids(tmp_path, team_runtime):
    client = ReplyClient()
    manager, team = team_runtime
    handle = worker(tmp_path, team_runtime, client)
    try:
        await until(lambda: handle.progress.status == "idle")
        notes = manager.drain_lead_mailbox()
        assert len(notes) == 1 and "[idle] worker" in notes[0]
        result = await SendMessageTool(manager, team.name, "lead-id", "lead").execute(
            SendMessageParams(to="worker", message="follow-up task", summary="continue the worker task"))
        assert not result.is_error
        await until(lambda: client.calls == 2)
        assert handle.progress.status == "idle"
    finally:
        handle.cancel()
        await asyncio.gather(handle.task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("before_start", [False, True])
async def test_structured_shutdown_exits_without_another_provider_call(tmp_path, team_runtime, before_start):
    client = ReplyClient()
    manager, team = team_runtime
    handle = worker(tmp_path, team_runtime, client)
    try:
        if not before_start:
            await until(lambda: handle.progress.status == "idle")
        result = await SendMessageTool(manager, team.name, "lead-id", "lead").execute(
            SendMessageParams(to="worker", message="please stop", message_type="shutdown_request"))
        assert not result.is_error
        await asyncio.wait_for(asyncio.shield(handle.task), 1)
        assert handle.progress.status == "completed"
        assert client.calls == (0 if before_start else 1)
    finally:
        handle.cancel()
        await asyncio.gather(handle.task, return_exceptions=True)


@pytest.mark.asyncio
async def test_cancel_before_start_finalizes_progress(tmp_path, team_runtime):
    handle = worker(tmp_path, team_runtime, ReplyClient(blocked=True))
    handle.cancel()
    await asyncio.gather(handle.task, return_exceptions=True)
    assert handle.done
    assert handle.progress.status == "stopped"


@pytest.mark.asyncio
async def test_team_shutdown_waits_for_stream_cleanup(tmp_path, team_runtime):
    manager, team = team_runtime
    client = ReplyClient(blocked=True)
    handle = worker(tmp_path, team_runtime, client)
    await client.started.wait()
    try:
        await manager.shutdown()
        assert handle.done and client.closed.is_set()
        assert handle.progress.status == "stopped"
        assert not manager._inprocess_handles
    finally:
        handle.cancel()
        await asyncio.gather(handle.task, return_exceptions=True)


@pytest.mark.asyncio
async def test_tui_exit_awaits_team_owned_runner(tmp_path, team_runtime):
    from mewcode.app import MewCodeApp

    manager, team = team_runtime
    client = ReplyClient(blocked=True)
    handle = worker(tmp_path, team_runtime, client)
    await client.started.wait()

    async def shutdown_mcp():
        return None

    app = SimpleNamespace(
        _streaming=False, _exit_requested=False, _stale_cleanup_task=None,
        agent=None, hook_engine=None, team_manager=manager,
        session=None, _shutdown_mcp=shutdown_mcp, exit=lambda: None,
    )
    try:
        await MewCodeApp.action_handle_ctrl_c(app)
        assert handle.done and client.closed.is_set()
        assert not manager._inprocess_handles
        assert manager.get_team(team.name) is None
    finally:
        handle.cancel()
        await asyncio.gather(handle.task, return_exceptions=True)


@pytest.mark.asyncio
async def test_shutdown_during_tool_turn_survives_agent_mailbox_consumption(tmp_path, team_runtime):
    class ToolClient(ReplyClient):
        async def stream(self, conversation, system="", tools=None):
            self.calls += 1
            self.started.set()
            await self.release.wait()
            if self.calls == 1:
                yield ToolCallComplete("list-1", "TaskList", {})
                yield StreamEnd("tool_use", input_tokens=7, output_tokens=3)
            else:
                yield TextDelta("turn complete")
                yield StreamEnd("end_turn", input_tokens=9, output_tokens=2)
    manager, team = team_runtime
    client = ToolClient(blocked=True)
    handle = worker(tmp_path, team_runtime, client)
    try:
        await client.started.wait()
        result = await SendMessageTool(manager, team.name, "lead-id", "lead").execute(
            SendMessageParams(to="worker", message="stop after current turn", message_type="shutdown_request"))
        assert not result.is_error
        client.release.set()
        await asyncio.wait_for(asyncio.shield(handle.task), 1)
        assert handle.progress.status == "completed"
        assert client.calls == 2
    finally:
        handle.cancel()
        await asyncio.gather(handle.task, return_exceptions=True)


@pytest.mark.asyncio
async def test_agent_tool_teammate_uses_persistent_inprocess_backend(tmp_path, team_runtime):
    from mewcode.agents.loader import AgentLoader
    from mewcode.agents.parser import AgentDef
    from mewcode.agents.task_manager import TaskManager
    from mewcode.agents.trace import TraceManager
    from mewcode.tools.agent_tool import AgentTool, AgentToolParams
    manager, team = team_runtime
    tasks = TaskManager()
    client = ReplyClient()
    parent = Agent(client, ToolRegistry(), "anthropic", work_dir=str(tmp_path))
    parent.agent_id = "lead-id"
    loader = AgentLoader(str(tmp_path))
    loader._agents["Fixture"] = AgentDef("Fixture", "offline fixture")
    class Worktrees:
        async def create(self, name, ref):
            return SimpleNamespace(path=str(tmp_path))
    tool = AgentTool(loader, tasks, TraceManager(), parent, team_manager=manager, worktree_manager=Worktrees())
    try:
        result = await tool.execute(AgentToolParams(
            prompt="fixture", description="fixture", subagent_type="Fixture", name="product-worker", team_name=team.name))
        assert not result.is_error
        await client.closed.wait()
        await asyncio.sleep(0)
        assert manager._inprocess_handles
        assert "[idle] product-worker" in manager.drain_lead_mailbox()[0]
    finally:
        await tasks.shutdown()
        await manager.shutdown()


@pytest.mark.asyncio
async def test_provider_failure_is_reported_once_and_team_runner_is_reclaimed(tmp_path, team_runtime):
    from mewcode.agents.task_manager import TaskManager
    manager, team = team_runtime
    client = ReplyClient(fail=True)
    registry = build_teammate_tools(ToolRegistry(), manager, team.name, "failure-id", "failure", "in-process")
    agent = Agent(client, registry, "anthropic", work_dir=str(tmp_path))
    agent.agent_id, agent.team_name, agent._team_manager = "failure-id", team.name, manager
    manager.register_member(team.name, TeammateInfo("failure", "failure-id", "fixture", "FAKE", "", "in-process", True))
    tasks = TaskManager()
    task_id = tasks.launch(agent, "fail offline", name="failure")
    runner = tasks._async_tasks[task_id]
    await asyncio.wait_for(asyncio.gather(runner, return_exceptions=True), 2)
    bg = tasks.get(task_id)
    assert bg.status == "failed" and "offline teammate failure" in bg.result
    assert tasks.poll_completed() == [bg]
    assert tasks.poll_completed() == []
    assert not tasks._async_tasks and not manager._inprocess_handles
    assert not team.get_member("failure").is_active


def git(path, *args):
    result = subprocess.run(["git", "-c", f"safe.directory={path.as_posix()}", *args],
                            cwd=path, capture_output=True, text=True, check=True)
    return result.stdout.strip()


@pytest.fixture
def git_worktree(tmp_path, team_runtime):
    from mewcode.worktree.manager import WorktreeManager
    from mewcode.worktree.models import Worktree
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.email", "fixture@localhost")
    git(repo, "config", "user.name", "Offline fixture")
    (repo / "tracked.txt").write_text("baseline", encoding="utf-8")
    git(repo, "add", "tracked.txt")
    git(repo, "commit", "-m", "fixture baseline")
    baseline = git(repo, "rev-parse", "HEAD")
    # A local remote-tracking ref proves the baseline is already published;
    # no network or external repository is involved.
    git(repo, "update-ref", "refs/remotes/fixture/main", baseline)
    target = tmp_path / "worker-tree"
    git(repo, "worktree", "add", "-b", "fixture-worker", str(target))
    worktrees = WorktreeManager(str(repo))
    worktrees.active["worker"] = Worktree("worker", str(target), "fixture-worker", "HEAD", baseline)
    manager, team = team_runtime
    manager._worktree_manager = worktrees
    manager.register_member(team.name, TeammateInfo("worker", "worker-id", "fixture", "fake", str(target), "in-process", False))
    return manager, team, repo, target


@pytest.mark.parametrize("state", ["dirty", "unpushed", "locked"])
def test_team_delete_preserves_worktree_data_and_metadata(git_worktree, state):
    manager, team, repo, target = git_worktree
    if state == "dirty":
        (target / "user-note.txt").write_text("preserve my draft", encoding="utf-8")
    elif state == "unpushed":
        (target / "tracked.txt").write_text("new local commit", encoding="utf-8")
        git(target, "add", "tracked.txt")
        git(target, "commit", "-m", "unpublished result")
    else:
        git(repo, "worktree", "lock", str(target))
    with pytest.raises(TeamError):
        manager.delete_team(team.name)
    assert target.exists()
    assert manager.get_team(team.name) is not None
    assert Path(team.config_path).exists()
    assert AgentNameRegistry.instance().resolve("worker") == "worker-id"


def test_team_delete_removes_clean_published_worktree(git_worktree):
    manager, team, repo, target = git_worktree
    manager.delete_team(team.name)
    assert not target.exists()
    assert team.name not in manager.list_teams()


def test_team_cleanup_failure_keeps_target_and_team_metadata(git_worktree, monkeypatch):
    manager, team, repo, target = git_worktree
    original = manager._worktree_manager._run_git
    def fail_remove(args, cwd=None):
        if args[:2] == ["worktree", "remove"]:
            raise subprocess.TimeoutExpired(args, 1)
        return original(args, cwd)
    monkeypatch.setattr(manager._worktree_manager, "_run_git", fail_remove)
    with pytest.raises(TeamError, match="Git removal failed"):
        manager.delete_team(team.name)
    assert (target / "tracked.txt").read_text(encoding="utf-8") == "baseline"
    assert Path(team.config_path).exists()
    assert manager._worktree_manager.active["worker"].path == str(target)


@pytest.mark.asyncio
async def test_two_real_agents_exchange_tasks_and_cleanup(tmp_path):
    from examples.teams.demo import run_demo
    result = await run_demo(tmp_path / "two-agents")
    assert result["tasks"] == [
        {"assignee": "alice", "status": "completed"},
        {"assignee": "bob", "status": "completed"},
    ]
    assert result["peer_message_received"]
    assert result["merged_result"] == 42
    assert result["worker_statuses"] == ["completed", "completed"]
    assert result["running_handles"] == 0
    assert result["team_removed"]
