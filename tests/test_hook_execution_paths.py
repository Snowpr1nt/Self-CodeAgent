from __future__ import annotations

import asyncio
import os
from pathlib import Path
import shlex
import subprocess
import sys
from types import SimpleNamespace

import pytest

from mewcode.agent import Agent, PermissionRequest, PermissionResponse
from mewcode.hooks import Action, Hook, HookContext, HookEngine
from mewcode.hooks.executors import execute_command
from mewcode.permissions import PermissionMode
from mewcode.tools import ToolRegistry
from mewcode.tools.base import ToolCallComplete
from mewcode.tools.write_file import WriteFile
from mewcode.tools.bash import Bash, Params as BashParams


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["direct", "interactive", "background"])
@pytest.mark.parametrize("reject", [False, True])
async def test_all_execution_paths_obey_hooks(tmp_path: Path, path: str, reject: bool) -> None:
    registry = ToolRegistry()
    registry.register(WriteFile())
    engine = HookEngine([
        Hook("before", "pre_tool_use", Action("prompt", message="pre"), reject=reject),
        Hook("after", "post_tool_use", Action("prompt", message="post")),
    ])
    checker = SimpleNamespace(mode=PermissionMode.DEFAULT,
                              check=lambda *_: SimpleNamespace(effect="ask", reason="approval"))
    agent = Agent(None, registry, "anthropic", work_dir=str(tmp_path), hook_engine=engine,
                  permission_checker=checker if path == "interactive" else None)
    target = tmp_path / "output.txt"
    call = ToolCallComplete("t1", "WriteFile", {"file_path": str(target), "content": "approved"})
    if path == "direct":
        result = (await agent._execute_single_tool_direct(call)).result
    elif path == "background":
        result = await agent._execute_tool_noninteractive(call)
    else:
        async for event in agent._execute_tool(call):
            if isinstance(event, PermissionRequest):
                event.future.set_result(PermissionResponse.ALLOW)
            else:
                result = event[0]
    assert result.is_error is reject
    assert target.exists() is not reject
    if reject:
        assert "Hook rejected" in result.output
    else:
        assert target.read_text(encoding="utf-8") == "approved"
    assert [note.output for note in engine.drain_notifications()] == (
        ["pre"] if reject else ["pre", "post"]
    )


def _command(arguments: list[str]) -> str:
    return subprocess.list2cmdline(arguments) if os.name == "nt" else shlex.join(arguments)


@pytest.mark.asyncio
async def test_hook_timeout_stops_child_before_late_write(tmp_path: Path) -> None:
    marker = tmp_path / "late.txt"
    script = tmp_path / "late.py"
    script.write_text(
        "import time\nfrom pathlib import Path\ntime.sleep(1)\n"
        f"Path({str(marker)!r}).write_text('orphan')\n", encoding="utf-8",
    )
    action = Action("command", command=_command([sys.executable, str(script)]), timeout=0.1)
    result = await asyncio.wait_for(execute_command(action, HookContext()), 3)
    assert not result.success
    assert "timed out" in result.output
    await asyncio.sleep(1.2)
    assert not marker.exists()


@pytest.mark.asyncio
async def test_hook_cancellation_stops_child_before_late_write(tmp_path: Path) -> None:
    marker = tmp_path / "late.txt"
    started = tmp_path / "started.txt"
    script = tmp_path / "late.py"
    script.write_text(
        "import time\nfrom pathlib import Path\n"
        f"Path({str(started)!r}).write_text('started')\ntime.sleep(1)\n"
        f"Path({str(marker)!r}).write_text('orphan')\n", encoding="utf-8",
    )
    task = asyncio.create_task(execute_command(
        Action("command", command=_command([sys.executable, str(script)])), HookContext(),
    ))
    async with asyncio.timeout(3):
        while not started.exists():
            await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(1.2)
    assert not marker.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["hook", "bash"])
@pytest.mark.parametrize("termination", ["timeout", "cancel"])
async def test_exited_leader_does_not_leave_live_descendants(
    tmp_path: Path, entry: str, termination: str,
) -> None:
    """Keeping a job/group alive must outlast the shell and its direct Python child."""
    marker = tmp_path / "late.txt"
    started = tmp_path / "started.txt"
    parent_exited = tmp_path / "parent-exited.txt"
    child = tmp_path / "child.py"
    child.write_text(
        "import time\nfrom pathlib import Path\n"
        f"Path({str(started)!r}).write_text('started')\n"
        "time.sleep(1.5)\n"
        f"Path({str(marker)!r}).write_text('orphan')\n", encoding="utf-8",
    )
    parent = tmp_path / "parent.py"
    parent.write_text(
        "import atexit, subprocess, sys\nfrom pathlib import Path\n"
        f"atexit.register(lambda: Path({str(parent_exited)!r}).write_text('exiting'))\n"
        f"subprocess.Popen([sys.executable, {str(child)!r}])\n", encoding="utf-8",
    )
    command = _command([sys.executable, str(parent)])
    if entry == "hook":
        operation = execute_command(Action("command", command=command, timeout=1), HookContext())
    else:
        tool = Bash()
        tool.work_dir = str(tmp_path)
        operation = tool.execute(BashParams(command=command, timeout=1))
    task = asyncio.create_task(operation)
    async with asyncio.timeout(3):
        while not started.exists() or not parent_exited.exists():
            await asyncio.sleep(0.01)
    # The child sleeps with stdout inherited. Give the shell time to exit before cleanup.
    await asyncio.sleep(0.1)
    if termination == "cancel":
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 3)
    else:
        result = await asyncio.wait_for(task, 3)
        assert (not result.success) if entry == "hook" else result.is_error
        assert "timed out" in result.output
    await asyncio.sleep(1.6)
    assert not marker.exists()
