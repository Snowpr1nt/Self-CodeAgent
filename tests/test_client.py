from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest

from mewcode.client import (
    AnthropicClient,
    AuthenticationError,
    OpenAIClient,
    OpenAICompatClient,
)
from mewcode.config import ProviderConfig
from mewcode.conversation import ConversationManager
from mewcode.tools.base import (
    StreamEnd,
    TextDelta,
    ToolCallComplete,
    ToolCallDelta,
    ToolCallStart,
)


def _provider(protocol: str, **overrides: Any) -> ProviderConfig:
    values: dict[str, Any] = {
        "name": f"test-{protocol}",
        "protocol": protocol,
        "base_url": "https://provider.invalid/v1",
        "model": "test-model",
        "api_key": "test-key",
    }
    values.update(overrides)
    return ProviderConfig(**values)


def _conversation() -> ConversationManager:
    conversation = ConversationManager()
    conversation.add_user_message("Read the project configuration")
    return conversation


def _openai_client(responses: FakeResponses, max_output_tokens: int) -> OpenAIClient:
    client = object.__new__(OpenAIClient)
    client.model = "test-model"
    client.max_output_tokens = max_output_tokens
    client._client = SimpleNamespace(responses=responses)
    return client


def _openai_compat_client(completions: FakeChatCompletions) -> OpenAICompatClient:
    client = object.__new__(OpenAICompatClient)
    client.model = "test-model"
    client.max_output_tokens = 8192
    client._client = SimpleNamespace(
        chat=SimpleNamespace(completions=completions)
    )
    return client


def _anthropic_client(messages: FakeAnthropicMessages) -> AnthropicClient:
    client = object.__new__(AnthropicClient)
    client.model = "test-model"
    client.thinking = False
    client.max_output_tokens = 2048
    client._client = SimpleNamespace(messages=messages)
    return client


class AsyncEventStream:
    def __init__(self, events: list[Any]) -> None:
        self._events = events

    def __aiter__(self):
        async def iterate():
            for event in self._events:
                yield event

        return iterate()


class FakeResponses:
    def __init__(self, events: list[Any]) -> None:
        self.events = events
        self.request: dict[str, Any] | None = None

    async def create(self, **kwargs: Any) -> AsyncEventStream:
        self.request = kwargs
        return AsyncEventStream(self.events)


class FakeChatCompletions:
    def __init__(self, chunks: list[Any]) -> None:
        self.chunks = chunks
        self.request: dict[str, Any] | None = None

    async def create(self, **kwargs: Any) -> AsyncEventStream:
        self.request = kwargs
        return AsyncEventStream(self.chunks)


class FakeAnthropicStream(AsyncEventStream):
    def __init__(self, events: list[Any], final_message: Any) -> None:
        super().__init__(events)
        self.final_message = final_message

    async def __aenter__(self) -> FakeAnthropicStream:
        return self

    async def __aexit__(self, *_args: Any) -> None:
        return None

    async def get_final_message(self) -> Any:
        return self.final_message


@dataclass
class FakeAnthropicMessages:
    stream_result: FakeAnthropicStream
    request: dict[str, Any] | None = None

    def stream(self, **kwargs: Any) -> FakeAnthropicStream:
        self.request = kwargs
        return self.stream_result


@pytest.mark.parametrize(
    ("protocol", "env_name"),
    [
        ("anthropic", "ANTHROPIC_API_KEY"),
        ("openai", "OPENAI_API_KEY"),
        ("openai-compat", "OPENAI_API_KEY"),
    ],
)
def test_provider_config_resolves_protocol_api_key_from_environment(
    protocol: str,
    env_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(env_name, "environment-key")

    assert _provider(protocol, api_key="").resolve_api_key() == "environment-key"


def test_provider_config_prefers_explicit_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "environment-key")

    assert _provider("openai", api_key="config-key").resolve_api_key() == "config-key"


@pytest.mark.parametrize(
    ("client_type", "protocol"),
    [
        (AnthropicClient, "anthropic"),
        (OpenAIClient, "openai"),
        (OpenAICompatClient, "openai-compat"),
    ],
)
@pytest.mark.asyncio
async def test_provider_clients_can_initialize_with_socks_proxy(
    client_type: type[AnthropicClient | OpenAIClient | OpenAICompatClient],
    protocol: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALL_PROXY", "socks5://127.0.0.1:7897")
    client = client_type(_provider(protocol))

    await client._client.close()


@pytest.mark.parametrize(
    ("client_type", "protocol", "env_name"),
    [
        (AnthropicClient, "anthropic", "ANTHROPIC_API_KEY"),
        (OpenAIClient, "openai", "OPENAI_API_KEY"),
        (OpenAICompatClient, "openai-compat", "OPENAI_API_KEY"),
    ],
)
def test_provider_clients_reject_missing_api_key(
    client_type: type[AnthropicClient | OpenAIClient | OpenAICompatClient],
    protocol: str,
    env_name: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(env_name, raising=False)

    with pytest.raises(AuthenticationError):
        client_type(_provider(protocol, api_key=""))


@pytest.mark.asyncio
async def test_openai_responses_stream_honors_output_limit_and_maps_tool_call() -> None:
    responses = FakeResponses(
        [
            SimpleNamespace(
                type="response.output_item.added",
                item=SimpleNamespace(
                    type="function_call",
                    name="ReadFile",
                    call_id="call-1",
                ),
            ),
            SimpleNamespace(
                type="response.function_call_arguments.delta",
                delta='{"file_path":',
            ),
            SimpleNamespace(
                type="response.function_call_arguments.delta",
                delta='"pyproject.toml"}',
            ),
            SimpleNamespace(type="response.function_call_arguments.done"),
            SimpleNamespace(
                type="response.completed",
                response=SimpleNamespace(
                    usage=SimpleNamespace(
                        input_tokens=12,
                        output_tokens=4,
                        input_tokens_details=SimpleNamespace(cached_tokens=2),
                    )
                ),
            ),
        ]
    )
    client = _openai_client(responses, max_output_tokens=1234)

    events = [event async for event in client.stream(_conversation())]

    assert responses.request is not None
    assert responses.request["max_output_tokens"] == 1234
    assert events == [
        ToolCallStart(tool_name="ReadFile", tool_id="call-1"),
        ToolCallDelta(text='{"file_path":'),
        ToolCallDelta(text='"pyproject.toml"}'),
        ToolCallComplete(
            tool_id="call-1",
            tool_name="ReadFile",
            arguments={"file_path": "pyproject.toml"},
        ),
        StreamEnd(
            stop_reason="end_turn",
            input_tokens=10,
            output_tokens=4,
            cache_read=2,
            cache_creation=0,
        ),
    ]


@pytest.mark.asyncio
async def test_openai_compat_stream_emits_end_without_usage_chunk() -> None:
    completions = FakeChatCompletions(
        [
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(content="Done", tool_calls=None),
                        finish_reason="stop",
                    )
                ],
                usage=None,
            )
        ]
    )
    client = _openai_compat_client(completions)

    events = [event async for event in client.stream(_conversation())]

    assert events == [
        TextDelta(text="Done"),
        StreamEnd(stop_reason="end_turn"),
    ]


@pytest.mark.asyncio
async def test_openai_compat_stream_maps_length_to_max_tokens() -> None:
    completions = FakeChatCompletions(
        [
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(content=None, tool_calls=None),
                        finish_reason="length",
                    )
                ],
                usage=None,
            )
        ]
    )
    client = _openai_compat_client(completions)

    events = [event async for event in client.stream(_conversation())]

    assert events == [StreamEnd(stop_reason="max_tokens")]


@pytest.mark.asyncio
async def test_anthropic_stream_maps_tool_call_and_usage() -> None:
    stream = FakeAnthropicStream(
        events=[
            SimpleNamespace(
                type="content_block_start",
                content_block=SimpleNamespace(
                    type="tool_use",
                    name="ReadFile",
                    id="tool-1",
                ),
            ),
            SimpleNamespace(
                type="content_block_delta",
                delta=SimpleNamespace(
                    type="input_json_delta",
                    partial_json='{"file_path":"MEWCODE.md"}',
                ),
            ),
            SimpleNamespace(type="content_block_stop"),
        ],
        final_message=SimpleNamespace(
            stop_reason="tool_use",
            usage=SimpleNamespace(
                input_tokens=20,
                output_tokens=6,
                cache_read_input_tokens=3,
                cache_creation_input_tokens=5,
            ),
        ),
    )
    messages = FakeAnthropicMessages(stream)
    client = _anthropic_client(messages)

    events = [event async for event in client.stream(_conversation())]

    assert messages.request is not None
    assert messages.request["max_tokens"] == 2048
    assert events == [
        ToolCallStart(tool_name="ReadFile", tool_id="tool-1"),
        ToolCallDelta(text='{"file_path":"MEWCODE.md"}'),
        ToolCallComplete(
            tool_id="tool-1",
            tool_name="ReadFile",
            arguments={"file_path": "MEWCODE.md"},
        ),
        StreamEnd(
            stop_reason="tool_use",
            input_tokens=20,
            output_tokens=6,
            cache_read=3,
            cache_creation=5,
        ),
    ]
