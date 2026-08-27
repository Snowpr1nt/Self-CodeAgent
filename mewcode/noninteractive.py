# 来源：公众号@小林coding
# 后端八股网站：xiaolincoding.com
# Agent网站：xiaolinnote.com
# 简历模版：jianli.xiaolinnote.com
from __future__ import annotations

import json
import sys
import time
from collections.abc import Callable
from typing import TextIO

from mewcode.agent import (
    AgentEvent,
    CompactNotification,
    ErrorEvent,
    HookEvent,
    LoopComplete,
    PermissionRequest,
    PermissionResponse,
    RetryEvent,
    StreamText,
    ThinkingText,
    ToolResultEvent,
    ToolUseEvent,
    TurnComplete,
    UsageEvent,
)


class PromptEventWriter:
    def __init__(
        self,
        output_format: str = "text",
        *,
        stdout: TextIO | None = None,
        stderr: TextIO | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if output_format not in {"text", "stream-json"}:
            raise ValueError(f"Unsupported output format: {output_format}")
        self.is_json = output_format == "stream-json"
        self.stdout = stdout or sys.stdout
        self.stderr = stderr or sys.stderr
        self._monotonic = monotonic
        self._started_at = monotonic()
        self._text_parts: list[str] = []
        self._tool_calls: list[dict[str, object]] = []
        self._tool_indexes: dict[str, int] = {}
        self._input_tokens = 0
        self._output_tokens = 0

    def emit_json(self, payload: dict[str, object]) -> None:
        self.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self.stdout.flush()

    def write_follow_up(self, text: str) -> None:
        if self.is_json:
            self.emit_json({"type": "assistant", "text": text})
            return
        self.stdout.write(text + "\n")
        self.stdout.flush()

    def handle(self, event: AgentEvent) -> bool:
        if isinstance(event, StreamText):
            self._text_parts.append(event.text)
            if self.is_json:
                self.emit_json({"type": "assistant", "text": event.text})

        elif isinstance(event, ThinkingText):
            if self.is_json:
                self.emit_json({"type": "thinking", "text": event.text})

        elif isinstance(event, ToolUseEvent):
            self._tool_indexes[event.tool_id] = len(self._tool_calls)
            self._tool_calls.append({"name": event.tool_name, "is_error": False})
            if self.is_json:
                self.emit_json({
                    "type": "tool_use",
                    "tool_name": event.tool_name,
                    "tool_id": event.tool_id,
                    "args": event.arguments,
                })

        elif isinstance(event, ToolResultEvent):
            index = self._tool_indexes.get(event.tool_id)
            if index is not None:
                self._tool_calls[index]["is_error"] = event.is_error
            if self.is_json:
                self.emit_json({
                    "type": "tool_result",
                    "tool_name": event.tool_name,
                    "tool_id": event.tool_id,
                    "output": event.output,
                    "is_error": event.is_error,
                    "elapsed": round(event.elapsed, 3),
                })

        elif isinstance(event, UsageEvent):
            self._input_tokens = event.input_tokens
            self._output_tokens = event.output_tokens
            if self.is_json:
                self.emit_json({
                    "type": "usage",
                    "input_tokens": event.input_tokens,
                    "output_tokens": event.output_tokens,
                })

        elif isinstance(event, TurnComplete):
            if self.is_json:
                self.emit_json({"type": "turn_complete", "turn": event.turn})

        elif isinstance(event, LoopComplete):
            text = "".join(self._text_parts)
            if self.is_json:
                self.emit_json({
                    "type": "result",
                    "result": text,
                    "duration_ms": int((self._monotonic() - self._started_at) * 1000),
                    "num_turns": event.total_turns,
                    "tool_calls": self._tool_calls,
                    "usage": {
                        "input_tokens": self._input_tokens,
                        "output_tokens": self._output_tokens,
                    },
                    "stop_reason": "end_turn",
                })
            else:
                self.stdout.write(text)
                self.stdout.flush()
            return True

        elif isinstance(event, ErrorEvent):
            if self.is_json:
                self.emit_json({"type": "error", "message": event.message})
            else:
                self.stderr.write(f"Error: {event.message}\n")
                self.stderr.flush()

        elif isinstance(event, CompactNotification):
            if self.is_json:
                self.emit_json({
                    "type": "compact",
                    "message": event.message,
                    "before_tokens": event.before_tokens,
                })

        elif isinstance(event, RetryEvent):
            if self.is_json:
                self.emit_json({
                    "type": "retry",
                    "reason": event.reason,
                    "wait": event.wait,
                })

        elif isinstance(event, HookEvent):
            if self.is_json:
                self.emit_json({
                    "type": "hook",
                    "hook_id": event.hook_id,
                    "event": event.event,
                    "output": event.output,
                    "success": event.success,
                })

        elif isinstance(event, PermissionRequest):
            if not event.future.done():
                event.future.set_result(PermissionResponse.ALLOW)

        return False
