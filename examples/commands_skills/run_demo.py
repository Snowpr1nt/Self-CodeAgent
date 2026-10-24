"""Run the Day 10 disk -> slash -> Agent -> recovery example without an API key."""
from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any, AsyncIterator
from unittest.mock import patch

from mewcode.agent import Agent
from mewcode.client import LLMClient
from mewcode.commands.handlers import register_all_commands
from mewcode.commands.handlers.skill_register import register_skill_commands
from mewcode.commands.loader import register_user_commands
from mewcode.commands.parser import parse_command
from mewcode.commands.registry import CommandContext, CommandRegistry
from mewcode.context.manager import build_recovery_attachment
from mewcode.conversation import ConversationManager
from mewcode.skills.executor import SkillExecutor
from mewcode.skills.loader import SkillLoader
from mewcode.tools import ToolRegistry
from mewcode.tools.base import StreamEnd, StreamEvent, TextDelta


class OfflineClient(LLMClient):
    """Record actual Agent inputs; the response is deliberately deterministic."""

    def __init__(self) -> None:
        self.requests: list[list[str]] = []

    async def stream(
        self, conversation: ConversationManager, system: str = "",
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[StreamEvent]:
        prompts = [message.content for message in conversation.history if message.role == "user"]
        self.requests.append(prompts)
        yield TextDelta("Offline response: the request reached the Agent.")
        yield StreamEnd("end_turn", input_tokens=10, output_tokens=2)


class DemoUI:
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


async def run_demo(scratch_dir: str | None = None) -> dict[str, Any]:
    assets = Path(__file__).parent
    with tempfile.TemporaryDirectory(prefix="mewcode-day10-", dir=scratch_dir) as temporary:
        root = Path(temporary)
        project = root / "project"
        home = root / "home"
        home.mkdir()
        shutil.copytree(assets / "commands", project / ".mewcode/commands")
        shutil.copytree(assets / "skills", project / ".mewcode/skills")

        # Isolate both discovery paths so this demonstration never reads personal skills.
        with patch.object(Path, "home", return_value=home), patch(
            "mewcode.skills.loader.USER_SKILLS_DIR", str(home / ".mewcode/skills")
        ):
            client = OfflineClient()
            agent = Agent(client, ToolRegistry(), "anthropic", work_dir=str(project))
            conversation = ConversationManager()
            ui = DemoUI(conversation)
            loader = SkillLoader(str(project))
            loader.load_all()
            executor = SkillExecutor(agent, client, "anthropic")
            registry = CommandRegistry()
            register_all_commands(registry)
            register_user_commands(registry, str(project))
            register_skill_commands(registry, loader, executor)
            ctx = CommandContext(
                args="", agent=agent, conversation=conversation, session=None,
                session_manager=None, memory_manager=None, ui=ui,
                config={"registry": registry, "skill_loader": loader, "skill_executor": executor},
            )

            async def dispatch(text: str) -> None:
                name, ctx.args, is_command = parse_command(text)
                command = registry.find(name)
                if not is_command or command is None:
                    raise ValueError(f"Unknown demo command: {text}")
                await command.handler(ctx)

            await dispatch("/notes session recovery")
            custom_prompt = conversation.history[-1].content
            _ = [event async for event in agent.run(conversation)]
            await dispatch("/evidence-check session recovery")
            inline_prompt = conversation.history[-1].content
            _ = [event async for event in agent.run(conversation)]
            parent_history_size = len(conversation.history)
            await dispatch("/second-opinion session recovery")
            await asyncio.wait_for(ui.completed.wait(), timeout=5)

            assert custom_prompt in client.requests[0]
            assert inline_prompt in client.requests[1]
            assert len(conversation.history) == parent_history_size
            assert any("second opinion on session recovery" in prompt for prompt in client.requests[2])
            attachment = build_recovery_attachment(agent.recovery_state, None)
            assert "$ARGUMENTS" not in attachment
            return {
                "custom_command": registry.find("notes").name,
                "custom_prompt": custom_prompt,
                "inline_prompt": inline_prompt,
                "active_skills": sorted(agent.active_skills),
                "fork_result": ui.messages[-1],
                "parent_history_preserved": len(conversation.history) == parent_history_size,
                "recovery_skills": sorted(record.name for record in agent.recovery_state.snapshot_skills()),
                "model_requests": len(client.requests),
            }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scratch-dir", help="Existing directory for an automatically cleaned sandbox")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run_demo(args.scratch_dir)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
