from __future__ import annotations

from pathlib import Path
from typing import Any, AsyncIterator

import pytest

from mewcode.agent import Agent
from mewcode.client import LLMClient
from mewcode.context import (
    append_replacement_records,
    apply_tool_result_budget,
    ensure_session_dir,
)
from mewcode.context.manager import ContentReplacementRecord, SINGLE_RESULT_CHAR_LIMIT
from mewcode.conversation import ConversationManager, Message, ToolResultBlock, ToolUseBlock
from mewcode.memory.auto_memory import MemoryManager
from mewcode.memory.session import SessionManager
from mewcode.tools import ToolRegistry
from mewcode.tools.base import StreamEnd, StreamEvent, TextDelta


class _ReplyClient(LLMClient):
    async def stream(
        self,
        conversation: ConversationManager,
        system: str = "",
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        yield TextDelta("continued")
        yield StreamEnd("end_turn", input_tokens=20, output_tokens=3)


def test_session_restarts_with_isolated_replacement_state(tmp_path: Path) -> None:
    sessions = SessionManager(str(tmp_path))
    first_session = sessions.create()
    first_agent = Agent(_ReplyClient(), ToolRegistry(), "anthropic", work_dir=str(tmp_path))
    first_agent.start_session(first_session.session_id)
    conversation = ConversationManager()
    conversation.history.extend([
        Message(role="assistant", content="reading", tool_uses=[
            ToolUseBlock("large-1", "ReadFile", {"file_path": "sample.txt"})
        ]),
        Message(role="user", content="", tool_results=[
            ToolResultBlock("large-1", "x" * (SINGLE_RESULT_CHAR_LIMIT + 1))
        ]),
    ])
    records = apply_tool_result_budget(
        conversation, first_agent.session_dir, first_agent.replacement_state
    )
    append_replacement_records(first_agent.session_dir, records)
    for message in conversation.history:
        first_session.append(message)
    first_session.close()

    resumed = sessions.resume(first_session.session_id)
    assert resumed is not None
    restarted_agent = Agent(_ReplyClient(), ToolRegistry(), "anthropic", work_dir=str(tmp_path))
    restarted_agent.restore_session(first_session.session_id, resumed.messages)

    assert restarted_agent.replacement_state.replacements["large-1"] == records[0].replacement
    assert (restarted_agent.session_dir / "large-1.txt").exists()

    second_session = sessions.create()
    restarted_agent.start_session(second_session.session_id)
    assert restarted_agent.replacement_state.replacements == {}
    assert restarted_agent.session_dir != first_agent.session_dir
    assert (first_agent.session_dir / "large-1.txt").exists()
    resumed.session.close()
    second_session.close()


def _large_conversation() -> ConversationManager:
    conversation = ConversationManager()
    for _ in range(20):
        conversation.add_user_message("old request " * 400)
        conversation.add_assistant_message("old answer " * 400)
    conversation.record_usage_anchor(200_000)
    return conversation


@pytest.mark.asyncio
async def test_legacy_compaction_preserves_scoped_session_files(tmp_path: Path) -> None:
    from mewcode.context import auto_compact
    from mewcode.context.manager import CompactEvent

    agent = Agent(_ReplyClient(), ToolRegistry(), "anthropic", work_dir=str(tmp_path))
    agent.restore_session("legacy", [])
    scoped = Agent(_ReplyClient(), ToolRegistry(), "anthropic", work_dir=str(tmp_path))
    scoped.start_session("new-session")
    protected = scoped.session_dir / "result.txt"
    protected.write_text("owned by new session", encoding="utf-8")
    stale = agent.session_dir / "stale.txt"
    stale.write_text("old output", encoding="utf-8")

    result = await auto_compact(
        _large_conversation(), agent.client, 200_000, agent.session_dir
    )

    assert isinstance(result, CompactEvent)
    assert protected.read_text(encoding="utf-8") == "owned by new session"
    assert not stale.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("supplied", [False, True])
async def test_background_injects_memory_without_instructions(
    tmp_path: Path, supplied: bool,
) -> None:
    class CaptureClient(_ReplyClient):
        contents: list[str]

        async def stream(self, conversation, system="", tools=None):
            self.contents = [m.content for m in conversation.history]
            async for event in super().stream(conversation, system, tools):
                yield event

    memory = MemoryManager(str(tmp_path))
    memory._user_mem_dir = ""
    memory.project_path.parent.mkdir(parents=True, exist_ok=True)
    memory.project_path.write_text("BACKGROUND PREFERENCE", encoding="utf-8")
    client = CaptureClient()
    agent = Agent(client, ToolRegistry(), "anthropic", work_dir=str(tmp_path), memory_manager=memory)
    agent._consolidator = None
    conversation = ConversationManager() if supplied else None

    assert await agent.run_to_completion("continue", conversation) == "continued"
    assert sum("BACKGROUND PREFERENCE" in c for c in client.contents) == 1


@pytest.mark.asyncio
async def test_background_supplied_conversation_survives_compaction(tmp_path: Path) -> None:
    agent = Agent(_ReplyClient(), ToolRegistry(), "anthropic", work_dir=str(tmp_path))
    conversation = _large_conversation()
    assert await agent.run_to_completion("continue", conversation) == "continued"
    assert conversation.env_injected


@pytest.mark.asyncio
async def test_recovery_attachment_survives_disk_restart(tmp_path: Path) -> None:
    from types import SimpleNamespace
    from mewcode.app import MewCodeApp
    from mewcode.agent import CompactNotification
    from mewcode.context import auto_compact
    from mewcode.context.manager import CompactEvent

    sessions = SessionManager(str(tmp_path))
    session = sessions.create()
    agent = Agent(_ReplyClient(), ToolRegistry(), "anthropic", work_dir=str(tmp_path))
    agent.start_session(session.session_id)
    agent.recovery_state.record_file_read("example.py", "RECOVERY FILE CONTENT")
    result = await auto_compact(
        _large_conversation(), agent.client, 200_000, agent.session_dir,
        recovery=agent.recovery_state,
    )
    assert isinstance(result, CompactEvent)
    MewCodeApp._persist_compact_boundary(
        SimpleNamespace(session=session),
        CompactNotification(result.before_tokens, "compacted", result.boundary),
    )
    session.close()
    resumed = sessions.resume(session.session_id)
    assert resumed is not None
    try:
        assert "RECOVERY FILE CONTENT" in resumed.messages[0].content
    finally:
        resumed.session.close()


@pytest.mark.asyncio
async def test_compaction_event_cursor_keeps_recent_messages_once_after_restart(tmp_path: Path) -> None:
    """Head insertion after notification must not move the app's append cursor."""
    from types import SimpleNamespace

    from mewcode.agent import CompactNotification, LoopComplete, TurnComplete
    from mewcode.app import MewCodeApp

    sessions = SessionManager(str(tmp_path))
    session = sessions.create()
    agent = Agent(
        _ReplyClient(), ToolRegistry(), "anthropic", work_dir=str(tmp_path),
        instructions_content="Fixture project instructions",
    )
    agent.start_session(session.session_id)
    conversation = _large_conversation()
    conversation.add_user_message("recent request before compaction")
    conversation.add_assistant_message("recent answer before compaction")
    for message in conversation.history:
        session.append(message)

    history_cursor = len(conversation.history)
    compacted = False
    async for event in agent.run(conversation):
        if isinstance(event, CompactNotification):
            compacted = True
            MewCodeApp._persist_compact_boundary(SimpleNamespace(session=session), event)
            # This is the real app event-consumer contract: the event means
            # replacement history is ready, so only later messages are appended.
            history_cursor = len(conversation.history)
        elif isinstance(event, (TurnComplete, LoopComplete)):
            for message in conversation.history[history_cursor:]:
                session.append(message)
            history_cursor = len(conversation.history)
    session.close()
    assert compacted

    resumed = sessions.resume(session.session_id)
    assert resumed is not None
    try:
        contents = [message.content for message in resumed.messages]
        assert contents.count("recent request before compaction") == 1
        assert contents.count("recent answer before compaction") == 1
        assert contents[-1] == "continued"
    finally:
        resumed.session.close()


@pytest.mark.asyncio
async def test_session_commands_switch_file_history_and_agent_state(tmp_path: Path) -> None:
    from types import SimpleNamespace
    from mewcode.app import MewCodeApp
    from mewcode.commands.handlers.session import handle_session
    from mewcode.commands.registry import CommandContext
    from mewcode.filehistory.history import FileHistory

    sessions = SessionManager(str(tmp_path))
    saved = sessions.create()
    saved.append(Message(role="user", content="saved request"))
    saved_id = saved.session_id
    saved.close()
    current = sessions.create()
    agent = Agent(_ReplyClient(), ToolRegistry(), "anthropic", work_dir=str(tmp_path))
    agent.start_session(current.session_id)
    agent.active_skills["old"] = "old SOP"
    state = SimpleNamespace(
        agent=agent, session=current, file_history=FileHistory(str(tmp_path), current.session_id),
    )
    conversations = []
    rendered = []

    async def render(messages):
        rendered.extend(messages)

    config = {
        "set_session": lambda session: MewCodeApp._set_session(state, session),
        "set_conversation": conversations.append,
        "clear_chat": lambda: None,
        "render_restored": render,
    }
    ctx = CommandContext("new", agent, None, current, sessions, None,
                         SimpleNamespace(add_system_message=lambda text: None), config)
    await handle_session(ctx)
    assert agent.active_skills == {}
    assert state.file_history._session_dir.name == state.session.session_id
    assert agent.file_history is state.file_history
    assert conversations[-1].history == []
    ctx.session = state.session
    ctx.args = f"resume {saved_id}"
    await handle_session(ctx)
    try:
        assert agent.session_id == saved_id
        assert state.file_history._session_dir.name == saved_id
        assert rendered[0].content == "saved request"
        assert conversations[-1].history[0].content == "saved request"
    finally:
        state.session.close()


@pytest.mark.parametrize("session_id", ["../escape", "absolute", ""])
def test_agent_session_paths_reject_invalid_ids(tmp_path: Path, session_id: str) -> None:
    if session_id == "absolute":
        session_id = str(tmp_path.parent / "outside")
    agent = Agent(_ReplyClient(), ToolRegistry(), "anthropic", work_dir=str(tmp_path))
    with pytest.raises(ValueError):
        agent.start_session(session_id)
    with pytest.raises(ValueError):
        agent.restore_session(session_id, [])


def test_legacy_replacement_log_survives_repeated_resume(tmp_path: Path) -> None:
    legacy_dir = ensure_session_dir(str(tmp_path))
    append_replacement_records(
        legacy_dir,
        [ContentReplacementRecord(tool_use_id="old-1", replacement="old preview")],
    )
    messages = [Message(role="user", content="", tool_results=[
        ToolResultBlock("old-1", "original")
    ])]

    for _ in range(2):
        agent = Agent(_ReplyClient(), ToolRegistry(), "anthropic", work_dir=str(tmp_path))
        agent.restore_session("old-session", messages)
        assert agent.replacement_state.replacements["old-1"] == "old preview"
        assert agent.session_dir == legacy_dir


def test_session_manager_rejects_path_escape(tmp_path: Path) -> None:
    sessions = SessionManager(str(tmp_path))
    sibling = tmp_path / ".mewcode" / "outside.jsonl"
    sibling.write_text("protected", encoding="utf-8")

    assert sessions.resume("../outside") is None
    assert not sessions.delete("../outside")
    assert sibling.read_text(encoding="utf-8") == "protected"


@pytest.mark.asyncio
async def test_resumed_conversation_injects_current_memory_once(tmp_path: Path) -> None:
    sessions = SessionManager(str(tmp_path))
    saved = sessions.create()
    saved.append(Message(role="user", content="previous request"))
    saved.append(Message(role="assistant", content="previous answer"))
    session_id = saved.session_id
    saved.close()
    resumed = sessions.resume(session_id)
    assert resumed is not None
    memory = MemoryManager(str(tmp_path))
    memory._user_mem_dir = ""
    memory.project_path.parent.mkdir(parents=True, exist_ok=True)
    memory.project_path.write_text("remember the new preference", encoding="utf-8")

    agent = Agent(
        _ReplyClient(), ToolRegistry(), "anthropic", work_dir=str(tmp_path),
        memory_manager=memory,
    )
    agent.restore_session(session_id, resumed.messages)
    agent._consolidator = None
    conversation = ConversationManager(history=list(resumed.messages))
    _ = [event async for event in agent.run(conversation)]

    contents = [message.content for message in conversation.history]
    assert contents.count("previous request") == 1
    assert sum("remember the new preference" in content for content in contents) == 1
    assert contents[-1] == "continued"
    resumed.session.close()
