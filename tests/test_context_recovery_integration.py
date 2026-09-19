from __future__ import annotations

from pathlib import Path

import pytest

from mewcode.context.manager import (
    PERSISTED_TAG,
    SINGLE_RESULT_CHAR_LIMIT,
    CompactCircuitBreaker,
    CompactEvent,
    ContentReplacementRecord,
    append_replacement_records,
    apply_tool_result_budget,
    auto_compact,
    create_replacement_state,
    load_replacement_records,
    persist_tool_result,
)
from mewcode.conversation import (
    _CHARS_PER_TOKEN,
    ConversationManager,
    Message,
    ToolResultBlock,
    ToolUseBlock,
)
from mewcode.tools.base import StreamEnd, TextDelta


def _message(role: str, label: str, tokens: int = 3_000) -> Message:
    return Message(
        role=role,
        content=f"{label}_" + "x" * int(tokens * _CHARS_PER_TOKEN),
    )


def _long_conversation() -> ConversationManager:
    conversation = ConversationManager()
    for index in range(8):
        conversation.history.append(_message("user", f"OLD_USER_{index}"))
        conversation.history.append(_message("assistant", f"OLD_ASSISTANT_{index}"))
    for index in range(6):
        conversation.history.append(_message("user", f"RECENT_{index}"))
    conversation.record_usage_anchor(input_tokens=200_000)
    return conversation


class _SummaryClient:
    async def stream(self, conversation, system="", tools=None):
        yield TextDelta(text="<summary>RECOVERED SUMMARY</summary>")
        yield StreamEnd(stop_reason="end_turn", input_tokens=10, output_tokens=10)


class _InterruptedSummaryClient:
    def __init__(self) -> None:
        self.calls = 0

    async def stream(self, conversation, system="", tools=None):
        self.calls += 1
        if self.calls == 1:
            yield TextDelta(text="<summary>PARTIAL")
            return
        yield TextDelta(text="<summary>RECOVERED SUMMARY</summary>")
        yield StreamEnd(stop_reason="end_turn", input_tokens=10, output_tokens=10)


def test_persist_tool_result_confines_untrusted_id(tmp_path: Path) -> None:
    escaped_path = tmp_path.parent / "escaped.txt"
    try:
        result_path = persist_tool_result("../escaped", "secret", tmp_path)

        assert result_path.resolve().is_relative_to(tmp_path.resolve())
        assert result_path.read_text(encoding="utf-8") == "secret"
        assert not escaped_path.exists()
    finally:
        escaped_path.unlink(missing_ok=True)


@pytest.mark.parametrize("tool_use_id", ["CON", "aux.log", "工具调用"])
def test_persist_tool_result_hashes_unportable_id(
    tmp_path: Path,
    tool_use_id: str,
) -> None:
    result_path = persist_tool_result(tool_use_id, "content", tmp_path)

    assert result_path.name.startswith("tool-")
    assert result_path.suffix == ".txt"
    assert result_path.read_text(encoding="utf-8") == "content"


def test_similar_prefix_read_is_not_treated_as_spill_readback(
    tmp_path: Path,
) -> None:
    session_dir = tmp_path / "spill"
    session_dir.mkdir()
    sibling_path = tmp_path / "spill-copy" / "large.txt"
    conversation = ConversationManager()
    conversation.history.extend(
        [
            Message(
                role="assistant",
                content="reading",
                tool_uses=[
                    ToolUseBlock(
                        tool_use_id="read-1",
                        tool_name="ReadFile",
                        arguments={"file_path": str(sibling_path)},
                    )
                ],
            ),
            Message(
                role="user",
                content="",
                tool_results=[
                    ToolResultBlock(
                        tool_use_id="read-1",
                        content="x" * (SINGLE_RESULT_CHAR_LIMIT + 1),
                    )
                ],
            ),
        ]
    )

    apply_tool_result_budget(
        conversation,
        session_dir,
        create_replacement_state(),
    )

    result = conversation.history[-1].tool_results[0]
    assert result.content.startswith(PERSISTED_TAG)
    assert (session_dir / "read-1.txt").exists()


def test_replacement_log_skips_interrupted_trailing_record(
    tmp_path: Path,
) -> None:
    append_replacement_records(
        tmp_path,
        [ContentReplacementRecord(tool_use_id="good", replacement="preview")],
    )
    records_path = tmp_path / "replacement_records.jsonl"
    with records_path.open("a", encoding="utf-8") as handle:
        handle.write('{"kind":"tool-result","tool_use_id":"partial"')

    records = load_replacement_records(tmp_path)

    assert [(record.tool_use_id, record.replacement) for record in records] == [
        ("good", "preview")
    ]


@pytest.mark.asyncio
async def test_auto_compact_retries_incomplete_summary_stream(
    tmp_path: Path,
) -> None:
    conversation = _long_conversation()
    client = _InterruptedSummaryClient()
    breaker = CompactCircuitBreaker()

    result = await auto_compact(
        conversation,
        client,
        context_window=200_000,
        session_dir=tmp_path,
        breaker=breaker,
    )

    assert isinstance(result, CompactEvent)
    assert client.calls == 2
    assert result.boundary is not None
    assert result.boundary.summary == "RECOVERED SUMMARY"
    assert breaker.consecutive_failures == 0


@pytest.mark.asyncio
async def test_auto_compact_keeps_spill_referenced_by_recent_tail(
    tmp_path: Path,
) -> None:
    conversation = ConversationManager()
    for index in range(8):
        conversation.history.append(_message("user", f"OLD_USER_{index}"))
        conversation.history.append(_message("assistant", f"OLD_ASSISTANT_{index}"))
    conversation.history.extend(
        [
            Message(
                role="assistant",
                content="reading",
                tool_uses=[
                    ToolUseBlock(
                        tool_use_id="recent-spill",
                        tool_name="Bash",
                        arguments={"command": "generate-large-output"},
                    )
                ],
            ),
            Message(
                role="user",
                content="",
                tool_results=[
                    ToolResultBlock(
                        tool_use_id="recent-spill",
                        content="x" * (SINGLE_RESULT_CHAR_LIMIT + 1),
                    )
                ],
            ),
        ]
    )
    apply_tool_result_budget(
        conversation,
        tmp_path,
        create_replacement_state(),
    )
    spill_path = tmp_path / "recent-spill.txt"
    assert spill_path.exists()
    conversation.record_usage_anchor(input_tokens=200_000)

    result = await auto_compact(
        conversation,
        _SummaryClient(),
        context_window=200_000,
        session_dir=tmp_path,
    )

    assert isinstance(result, CompactEvent)
    assert result.boundary is not None
    assert any(
        tool_result.tool_use_id == "recent-spill"
        for message in result.boundary.keep
        for tool_result in message.tool_results
    )
    assert spill_path.exists()
    assert spill_path.read_text(encoding="utf-8").startswith("x")
