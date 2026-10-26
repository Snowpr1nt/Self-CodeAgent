# 来源：公众号@小林coding
# 后端八股网站：xiaolincoding.com
# Agent网站：xiaolinnote.com
# 简历模版：jianli.xiaolinnote.com
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from mewcode.agent import Agent
    from mewcode.agents.trace import TraceManager

log = logging.getLogger(__name__)


@dataclass
class ProgressInfo:
    tool_call_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    last_activity: str = ""


@dataclass
class BackgroundTask:
    id: str
    name: str
    agent: Agent
    task: str
    status: str = "running"
    result: str = ""
    start_time: float = field(default_factory=time.monotonic)
    end_time: float | None = None
    cancel: Callable[[], None] | None = None
    progress: ProgressInfo = field(default_factory=ProgressInfo)


class TaskManager:


    def __init__(self) -> None:
        self._tasks: dict[str, BackgroundTask] = {}
        self._notify_queue: asyncio.Queue[str] = asyncio.Queue()
        self._async_tasks: dict[str, asyncio.Task[None]] = {}


    def launch(
        self,
        agent: Agent,
        task: str,
        name: str = "",
        fork_conversation: Any = None,
        trace_manager: TraceManager | None = None,
    ) -> str:
        task_id = uuid.uuid4().hex[:8]
        bg = BackgroundTask(
            id=task_id,
            name=name or task_id,
            agent=agent,
            task=task,
        )
        self._tasks[task_id] = bg

        async_task = asyncio.create_task(
            self._run_background(task_id, fork_conversation)
        )
        self._async_tasks[task_id] = async_task
        async_task.add_done_callback(
            lambda runner: self._finish_task(task_id, runner, trace_manager)
        )
        bg.cancel = async_task.cancel
        return task_id


    async def _run_background(
        self, task_id: str, fork_conversation: Any = None
    ) -> None:
        bg = self._tasks.get(task_id)
        if bg is None:
            return

        try:
            if bg.agent.team_name and bg.agent._team_manager:
                from mewcode.teams.spawn_inprocess import spawn_inprocess_teammate

                team_manager = bg.agent._team_manager
                team = team_manager.get_team(bg.agent.team_name)
                member = team.get_member(bg.agent.agent_id) if team else None
                handle = spawn_inprocess_teammate(
                    bg.agent, bg.task, bg.name, conversation=fork_conversation,
                    member=member, team_name=bg.agent.team_name,
                    mailbox=team_manager.get_mailbox(bg.agent.team_name),
                )
                try:
                    team_manager.register_inprocess_handle(bg.agent.agent_id, handle)
                    result = await handle.task
                finally:
                    if not handle.done:
                        handle.cancel()
                        await asyncio.gather(handle.task, return_exceptions=True)
            elif fork_conversation is not None:
                result = await bg.agent.run_to_completion("", fork_conversation)
            else:
                result = await bg.agent.run_to_completion(bg.task)
            bg.result = result

            bg.status = "completed"

        except asyncio.CancelledError:
            bg.status = "cancelled"
            bg.result = "Task was cancelled"
        except Exception as e:
            log.error("Background task %s failed: %s", task_id, e)
            bg.status = "failed"
            bg.result = f"Error: {e}"


    def adopt_running(
        self,
        agent: Agent,
        task_description: str,
        partial_result: str = "",
        name: str = "",
        trace_manager: TraceManager | None = None,
    ) -> str:
        task_id = uuid.uuid4().hex[:8]
        bg = BackgroundTask(
            id=task_id,
            name=name or task_id,
            agent=agent,
            task=task_description,
            result=partial_result,
        )
        self._tasks[task_id] = bg

        async_task = asyncio.create_task(self._continue_background(task_id))
        self._async_tasks[task_id] = async_task
        async_task.add_done_callback(
            lambda runner: self._finish_task(task_id, runner, trace_manager)
        )
        bg.cancel = async_task.cancel
        return task_id


    async def _continue_background(self, task_id: str) -> None:
        bg = self._tasks.get(task_id)
        if bg is None:
            return

        try:
            result = await bg.agent.run_to_completion(bg.task)
            bg.result = (bg.result + "\n" + result).strip() if bg.result else result
            bg.status = "completed"
        except asyncio.CancelledError:
            bg.status = "cancelled"
        except Exception as e:
            log.error("Background task %s failed: %s", task_id, e)
            bg.status = "failed"
            bg.result = f"Error: {e}"

    def _finish_task(
        self, task_id: str, runner: asyncio.Task[None],
        trace_manager: TraceManager | None,
    ) -> None:
        # A coroutine cancelled before its first scheduling never enters its
        # try/finally. The Task callback owns finalization for every exit path.
        bg = self._tasks.get(task_id)
        if bg is None or bg.end_time is not None:
            return
        if runner.cancelled():
            bg.status = "cancelled"
            bg.result = bg.result or "Task was cancelled"
        else:
            error = runner.exception()
            if error is not None:
                bg.status = "failed"
                bg.result = f"Error: {error}"
        bg.end_time = time.monotonic()
        bg.progress.input_tokens = bg.agent.total_input_tokens
        bg.progress.output_tokens = bg.agent.total_output_tokens
        self._async_tasks.pop(task_id, None)
        bg.cancel = None
        if trace_manager is not None:
            trace_manager.update(
                bg.agent.agent_id,
                input_tokens=bg.progress.input_tokens,
                output_tokens=bg.progress.output_tokens,
            )
            trace_manager.complete(bg.agent.agent_id, bg.status)
        self._notify_queue.put_nowait(task_id)

    async def shutdown(self) -> None:
        """Cancel owned runners and await their cleanup before returning."""
        runners = list(self._async_tasks.values())
        for runner in runners:
            if not runner.done():
                runner.cancel()
        if runners:
            await asyncio.gather(*runners, return_exceptions=True)

    def get(self, task_id: str) -> BackgroundTask | None:
        return self._tasks.get(task_id)

    def list_tasks(self) -> list[BackgroundTask]:
        return list(self._tasks.values())

    def cancel(self, task_id: str) -> bool:
        bg = self._tasks.get(task_id)
        if bg is None or bg.status != "running":
            return False
        async_task = self._async_tasks.get(task_id)
        if async_task and not async_task.done():
            async_task.cancel()
            return True
        return False

    def poll_completed(self) -> list[BackgroundTask]:
        completed: list[BackgroundTask] = []
        while not self._notify_queue.empty():
            try:
                task_id = self._notify_queue.get_nowait()
                bg = self._tasks.get(task_id)
                if bg is not None:
                    completed.append(bg)
            except asyncio.QueueEmpty:
                break
        return completed
