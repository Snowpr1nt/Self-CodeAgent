"""Day 10: disk discovery through slash dispatch, Agent input, and recovery."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, AsyncIterator

import pytest

from mewcode.agent import Agent, StreamText
from mewcode.client import LLMClient
from mewcode.commands import loader as command_loader
from mewcode.commands.handlers import register_all_commands
from mewcode.commands.handlers.skill import handle_skill
from mewcode.commands.handlers.skill_register import register_skill_commands
from mewcode.commands.parser import parse_command
from mewcode.commands.registry import CommandContext, CommandRegistry
from mewcode.context.manager import build_recovery_attachment
from mewcode.conversation import ConversationManager
from mewcode.permissions import (
    DangerousCommandDetector, PathSandbox, PermissionChecker, PermissionMode, RuleEngine,
)
from mewcode.skills import loader as skill_loader_module
from mewcode.skills.executor import SkillExecutor
from mewcode.skills.loader import SkillLoader
from mewcode.tools import ToolRegistry
from mewcode.tools.base import StreamEnd, StreamEvent, TextDelta, ToolCallComplete
from mewcode.tools.load_skill import LoadSkill, LoadSkillParams
from mewcode.tools.write_file import WriteFile


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(skill_loader_module, "USER_SKILLS_DIR", str(home / ".mewcode/skills"))


class RecordingClient(LLMClient):
    def __init__(self) -> None:
        self.inputs: list[list[tuple[str, str]]] = []

    async def stream(
        self, conversation: ConversationManager, system: str = "",
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        self.inputs.append([(m.role, m.content) for m in conversation.history])
        yield TextDelta("demo complete")
        yield StreamEnd("end_turn", input_tokens=10, output_tokens=2)


class ConversationUI:
    def __init__(self, conversation: ConversationManager) -> None:
        self.conversation = conversation
        self.messages: list[str] = []
        self.completed = asyncio.Event()

    def add_system_message(self, text: str) -> None:
        self.messages.append(text)
        if "skill result]" in text or "failed:" in text:
            self.completed.set()

    def send_user_message(self, text: str) -> None:
        self.conversation.add_user_message(text)


def write_skill(root: Path, name: str, *, mode: str = "inline", context: str = "full") -> Path:
    path = root / ".mewcode/skills" / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nname: {name}\ndescription: Demo {name}\nmode: {mode}\ncontext: {context}\n"
        "---\nCheck the evidence for $ARGUMENTS.\n", encoding="utf-8",
    )
    return path


def make_context(
    root: Path, *, args: str = "", client: LLMClient | None = None,
    checker: PermissionChecker | None = None,
) -> tuple[CommandContext, SkillLoader, SkillExecutor]:
    client = client or RecordingClient()
    agent = Agent(client, ToolRegistry(), "anthropic", work_dir=str(root), permission_checker=checker)
    conversation = ConversationManager()
    ui = ConversationUI(conversation)
    loader = SkillLoader(str(root))
    loader.load_all()
    executor = SkillExecutor(agent, client, "anthropic")
    registry = CommandRegistry()
    register_all_commands(registry)
    ctx = CommandContext(
        args=args, agent=agent, conversation=conversation, session=None,
        session_manager=None, memory_manager=None, ui=ui,
        config={"registry": registry, "skill_loader": loader, "skill_executor": executor},
    )
    return ctx, loader, executor


@pytest.mark.asyncio
async def test_custom_command_project_override_alias_and_arguments_reach_agent(tmp_path: Path) -> None:
    """Dropping project precedence, aliases, registration, or expansion loses the request."""
    user_commands = Path.home() / ".mewcode/commands"
    user_commands.mkdir(parents=True)
    (user_commands / "inspect.md").write_text("User prompt", encoding="utf-8")
    commands = tmp_path / ".mewcode/commands"
    commands.mkdir(parents=True)
    (commands / "inspect.md").write_text(
        "---\ndescription: Inspect evidence\naliases: [evidence]\nargument-hint: <topic>\n"
        "---\nProject evidence for $ARGUMENTS.", encoding="utf-8",
    )
    (commands / "help.md").write_text("Do not replace builtin help.", encoding="utf-8")
    client = RecordingClient()
    ctx, _, _ = make_context(tmp_path, client=client)
    registry = ctx.config["registry"]

    command_loader.register_user_commands(registry, str(tmp_path))
    name, ctx.args, is_command = parse_command("/EVIDENCE session recovery")
    assert is_command
    await registry.find(name).handler(ctx)
    events = [event async for event in ctx.agent.run(ctx.conversation)]

    assert any(isinstance(event, StreamText) for event in events)
    assert ("user", "Project evidence for session recovery.") in client.inputs[0]
    assert registry.find("help").description != "Do not replace builtin help."
    assert registry.find("inspect").arg_prompt == "<topic>"


@pytest.mark.asyncio
async def test_inline_slash_supplies_expanded_sop_to_first_model_turn(tmp_path: Path) -> None:
    """Sending only slash arguments leaves the activated SOP invisible to the model."""
    write_skill(tmp_path, "audit")
    client = RecordingClient()
    ctx, loader, executor = make_context(tmp_path, args="session recovery", client=client)
    register_skill_commands(ctx.config["registry"], loader, executor)

    await ctx.config["registry"].find("audit").handler(ctx)
    _ = [event async for event in ctx.agent.run(ctx.conversation)]

    assert ("user", "Check the evidence for session recovery.\n") in client.inputs[0]
    assert ctx.agent.active_skills == {"audit": "Check the evidence for session recovery.\n"}
    assert "Check the evidence for session recovery." in build_recovery_attachment(ctx.agent.recovery_state, None)


@pytest.mark.asyncio
async def test_load_skill_tool_activation_survives_recovery_attachment(tmp_path: Path) -> None:
    """Skipping RecoveryState loses skills activated by the LLM after compaction."""
    write_skill(tmp_path, "audit")
    ctx, loader, _ = make_context(tmp_path)
    tool = LoadSkill()
    tool.set_loader(loader)
    tool.set_agent(ctx.agent)

    result = await tool.execute(LoadSkillParams(name="audit"))

    assert not result.is_error
    assert ctx.agent.active_skills == {"audit": "Check the evidence for $ARGUMENTS.\n"}
    assert "Check the evidence for $ARGUMENTS." in build_recovery_attachment(ctx.agent.recovery_state, None)


def test_yaml_directory_skill_hot_reload_reads_metadata_and_body(tmp_path: Path) -> None:
    """Re-parsing prompt.md as frontmatter silently serves the old YAML skill forever."""
    directory = tmp_path / ".mewcode/skills/audit"
    directory.mkdir(parents=True)
    yaml_file = directory / "skill.yaml"
    yaml_file.write_text("name: audit\ndescription: Version one\nmode: inline\n", encoding="utf-8")
    prompt = directory / "prompt.md"
    prompt.write_text("Old evidence.", encoding="utf-8")
    loader = SkillLoader(str(tmp_path))
    loader.load_all()
    yaml_file.write_text("name: audit\ndescription: Version two\nmode: fork\ncontext: none\n", encoding="utf-8")
    prompt.write_text("New evidence for $ARGUMENTS.", encoding="utf-8")

    skill = loader.get("audit")

    assert skill.description == "Version two"
    assert skill.prompt_body == "New evidence for $ARGUMENTS."
    assert skill.mode == "fork"
    assert skill.context == "none"


@pytest.mark.asyncio
async def test_skill_reload_does_not_remove_another_registrys_custom_command(tmp_path: Path) -> None:
    """A process-wide name set lets one app erase another app's command."""
    write_skill(tmp_path / "first", "audit")
    first, first_loader, executor = make_context(tmp_path / "first")
    register_skill_commands(first.config["registry"], first_loader, executor)
    command_dir = tmp_path / "second/.mewcode/commands"
    command_dir.mkdir(parents=True)
    (command_dir / "audit.md").write_text("Custom audit prompt", encoding="utf-8")
    second, second_loader, second_executor = make_context(tmp_path / "second")
    command = command_loader.load_dir(str(command_dir))[0]
    second.config["registry"].register_sync(command)

    register_skill_commands(second.config["registry"], second_loader, second_executor)

    assert second.config["registry"].find("audit") is not None
    await second.config["registry"].find("audit").handler(second)
    assert second.conversation.history[-1].content == "Custom audit prompt"


@pytest.mark.asyncio
async def test_skill_reload_removes_deleted_slash_commands_from_own_registry(tmp_path: Path) -> None:
    """Reload must still remove the first registry's old skill after a second app starts."""
    path = write_skill(tmp_path / "first", "audit")
    first, loader, executor = make_context(tmp_path / "first")
    register_skill_commands(first.config["registry"], loader, executor)
    second, second_loader, second_executor = make_context(tmp_path / "second")
    register_skill_commands(second.config["registry"], second_loader, second_executor)
    path.unlink()
    first.args = "reload"

    await handle_skill(first)

    assert first.config["registry"].find("audit") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("context", ["none", "recent", "full"])
async def test_fork_skill_uses_actual_parent_history_and_expanded_recovery(tmp_path: Path, context: str) -> None:
    """Reading the wrong Agent conversation attribute drops full/recent context."""
    write_skill(tmp_path, "audit", mode="fork", context=context)
    client = RecordingClient()
    ctx, loader, executor = make_context(tmp_path, args="session recovery", client=client)
    for i in range(8):
        ctx.conversation.add_user_message(f"Historical fact {i}")
    _ = [event async for event in ctx.agent.run(ctx.conversation)]
    client.inputs.clear()
    register_skill_commands(ctx.config["registry"], loader, executor)

    await ctx.config["registry"].find("audit").handler(ctx)
    await asyncio.wait_for(ctx.ui.completed.wait(), timeout=5)

    user_messages = [content for role, content in client.inputs[0] if role == "user"]
    assert "Check the evidence for session recovery.\n" in user_messages
    combined = "\n".join(user_messages)
    assert ("Historical fact 7" in combined) is (context != "none")
    assert ("Historical fact 0" in combined) is (context == "full")
    assert "Check the evidence for session recovery." in build_recovery_attachment(ctx.agent.recovery_state, None)
    assert ctx.agent.active_skills == {}
    assert ctx.conversation.history[-1].content == "demo complete"
    assert ctx.ui.messages[-1] == "[audit skill result]\ndemo complete"


class WriteAttemptClient(LLMClient):
    def __init__(self, path: Path) -> None:
        self.path = path
        self.calls = 0

    async def stream(
        self, conversation: ConversationManager, system: str = "",
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        self.calls += 1
        if self.calls == 1:
            yield ToolCallComplete("write-1", "WriteFile", {"file_path": str(self.path), "content": "unauthorized"})
        else:
            results = [result for message in conversation.history for result in message.tool_results]
            yield TextDelta(results[-1].content)
        yield StreamEnd("end_turn", input_tokens=10, output_tokens=2)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [PermissionMode.PLAN, PermissionMode.DEFAULT])
async def test_fork_skill_cannot_write_without_parent_permission(tmp_path: Path, mode: PermissionMode) -> None:
    """A fork must not drop the parent's checker or hang waiting on an invisible permission UI."""
    write_skill(tmp_path, "audit", mode="fork", context="none")
    target = tmp_path / "protected.txt"
    checker = PermissionChecker(DangerousCommandDetector(), PathSandbox(str(tmp_path)), RuleEngine(), mode=mode)
    checker.plan_file_path = str(tmp_path / "parent-plan.md")
    client = WriteAttemptClient(target)
    ctx, loader, executor = make_context(tmp_path, client=client, checker=checker)
    ctx.agent.registry.register(WriteFile())

    result = await asyncio.wait_for(executor.execute_fork(loader.get("audit"), "write"), timeout=5)

    assert not target.exists()
    assert "Permission denied" in result
    assert checker.plan_file_path == str(tmp_path / "parent-plan.md")


@pytest.mark.asyncio
async def test_tracked_day10_example_runs_without_real_configuration(tmp_path: Path) -> None:
    """Exercise the shipped assets and runner so a broken demo fails acceptance."""
    from examples.commands_skills.run_demo import run_demo

    result = await run_demo(str(tmp_path))

    assert result["custom_command"] == "review:notes"
    assert result["active_skills"] == ["evidence-check"]
    assert result["recovery_skills"] == ["evidence-check", "second-opinion"]
    assert result["parent_history_preserved"] is True
    assert result["model_requests"] == 3


@pytest.mark.asyncio
async def test_command_without_placeholder_appends_request_and_uses_namespace(tmp_path: Path) -> None:
    """Losing the append fallback silently drops arguments from plain Markdown commands."""
    directory = tmp_path / ".mewcode/commands/review"
    directory.mkdir(parents=True)
    (directory / "notes.md").write_text("Review source evidence.", encoding="utf-8")
    ctx, _, _ = make_context(tmp_path, args="session recovery")
    command_loader.register_user_commands(ctx.config["registry"], str(tmp_path))

    await ctx.config["registry"].find("review:notes").handler(ctx)

    assert ctx.conversation.history[-1].content == "Review source evidence.\n\n## User Request\n\nsession recovery"


@pytest.mark.asyncio
async def test_custom_command_has_priority_over_same_named_skill(tmp_path: Path) -> None:
    """Registering or reloading a skill must preserve an explicit custom command."""
    write_skill(tmp_path, "audit")
    commands = tmp_path / ".mewcode/commands"
    commands.mkdir(parents=True)
    (commands / "audit.md").write_text("Custom audit for $ARGUMENTS.", encoding="utf-8")
    ctx, loader, executor = make_context(tmp_path, args="session recovery")
    command_loader.register_user_commands(ctx.config["registry"], str(tmp_path))
    register_skill_commands(ctx.config["registry"], loader, executor)
    register_skill_commands(ctx.config["registry"], loader, executor)

    await ctx.config["registry"].find("audit").handler(ctx)

    assert ctx.conversation.history[-1].content == "Custom audit for session recovery."
    assert ctx.agent.active_skills == {}
