from __future__ import annotations

from pathlib import Path

import pytest

from mewcode.cache import FileCache
from mewcode.tools import create_default_registry
from mewcode.tools.bash import Bash, Params as BashParams
from mewcode.tools.file_state_cache import FileStateCache
from mewcode.tools.glob import Glob, Params as GlobParams
from mewcode.tools.grep import Grep, Params as GrepParams
from mewcode.tools.read_file import Params as ReadParams
from mewcode.tools.read_file import ReadFile
from mewcode.tools.write_file import Params as WriteParams
from mewcode.tools.write_file import WriteFile


def test_default_registry_exposes_six_core_tools() -> None:
    registry = create_default_registry()
    tools = {tool.name: tool for tool in registry.list_tools()}

    assert set(tools) == {"ReadFile", "WriteFile", "EditFile", "Bash", "Glob", "Grep"}
    assert {name: tool.category for name, tool in tools.items()} == {
        "ReadFile": "read",
        "WriteFile": "write",
        "EditFile": "write",
        "Bash": "command",
        "Glob": "read",
        "Grep": "read",
    }
    assert {name for name, tool in tools.items() if tool.is_concurrency_safe} == {
        "ReadFile",
        "Glob",
        "Grep",
    }


def test_default_registry_emits_provider_specific_schemas() -> None:
    registry = create_default_registry()

    anthropic = registry.get_all_schemas("anthropic")
    openai = registry.get_all_schemas("openai")

    assert len(anthropic) == len(openai) == 6
    assert set(anthropic[0]) == {"name", "description", "input_schema"}
    assert set(openai[0]) == {"type", "name", "description", "parameters"}
    assert openai[0]["type"] == "function"


@pytest.mark.asyncio
async def test_write_requires_read_before_overwrite(tmp_path: Path) -> None:
    path = tmp_path / "example.txt"
    path.write_text("before", encoding="utf-8")
    registry = create_default_registry()
    write = registry.get("WriteFile")

    assert isinstance(write, WriteFile)
    result = await write.execute(WriteParams(file_path=str(path), content="after"))

    assert result.is_error
    assert "read" in result.output.lower()
    assert path.read_text(encoding="utf-8") == "before"


@pytest.mark.asyncio
async def test_read_then_write_uses_shared_file_state(tmp_path: Path) -> None:
    path = tmp_path / "example.txt"
    path.write_text("before", encoding="utf-8")
    registry = create_default_registry()
    read = registry.get("ReadFile")
    write = registry.get("WriteFile")

    assert isinstance(read, ReadFile)
    assert isinstance(write, WriteFile)
    await read.execute(ReadParams(file_path=str(path)))
    result = await write.execute(WriteParams(file_path=str(path), content="after"))

    assert not result.is_error
    assert path.read_text(encoding="utf-8") == "after"


def test_state_cache_detects_content_change_with_unchanged_mtime(tmp_path: Path) -> None:
    path = tmp_path / "example.txt"
    path.write_text("before", encoding="utf-8")
    resolved = str(path.resolve())
    state = FileStateCache()
    state.record(resolved, "before", path.stat().st_mtime_ns)

    path.write_text("after!", encoding="utf-8")
    state._cache[resolved] = ("before", path.stat().st_mtime_ns)

    ok, message = state.check(resolved)

    assert not ok
    assert "modified" in message


@pytest.mark.asyncio
async def test_read_file_refreshes_stale_content_cache(tmp_path: Path) -> None:
    path = tmp_path / "example.txt"
    path.write_text("before", encoding="utf-8")
    file_cache = FileCache()
    state_cache = FileStateCache()
    read = ReadFile(file_cache=file_cache, file_state_cache=state_cache)

    first = await read.execute(ReadParams(file_path=str(path)))
    file_cache.put(str(path.resolve()), "before")
    path.write_text("after", encoding="utf-8")
    second = await read.execute(ReadParams(file_path=str(path)))

    assert first.output == "1\tbefore"
    assert second.output == "1\tafter"


@pytest.mark.asyncio
async def test_read_file_populates_empty_content_cache(tmp_path: Path) -> None:
    path = tmp_path / "example.txt"
    path.write_text("content", encoding="utf-8")
    file_cache = FileCache()
    read = ReadFile(file_cache=file_cache, file_state_cache=FileStateCache())

    result = await read.execute(ReadParams(file_path=str(path)))

    assert not result.is_error
    assert file_cache.get(str(path.resolve())) == "content"


@pytest.mark.asyncio
async def test_glob_and_grep_skip_dependency_directories(tmp_path: Path) -> None:
    source = tmp_path / "src"
    dependency = tmp_path / "node_modules" / "pkg"
    source.mkdir()
    dependency.mkdir(parents=True)
    (source / "main.py").write_text("needle = True\n", encoding="utf-8")
    (source / "notes.txt").write_text("needle\n", encoding="utf-8")
    (dependency / "hidden.py").write_text("needle = True\n", encoding="utf-8")

    glob_result = await Glob().execute(GlobParams(pattern="**/*.py", path=str(tmp_path)))
    grep_result = await Grep().execute(
        GrepParams(pattern="needle", path=str(tmp_path), include="*.py")
    )

    assert not glob_result.is_error
    assert glob_result.output == str(Path("src") / "main.py")
    assert not grep_result.is_error
    assert str(Path("src") / "main.py") in grep_result.output
    assert "hidden.py" not in grep_result.output
    assert "notes.txt" not in grep_result.output


@pytest.mark.asyncio
async def test_grep_reports_invalid_regex(tmp_path: Path) -> None:
    result = await Grep().execute(GrepParams(pattern="[", path=str(tmp_path)))

    assert result.is_error
    assert "invalid regex" in result.output


class _FakeProcess:
    def __init__(self, returncode: int, output: bytes = b"") -> None:
        self.returncode = returncode
        self._output = output

    async def communicate(self) -> tuple[bytes, None]:
        return self._output, None


@pytest.mark.asyncio
async def test_bash_marks_regular_nonzero_exit_as_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def create_process(*args: object, **kwargs: object) -> _FakeProcess:
        return _FakeProcess(7, b"failed")

    monkeypatch.setattr(
        "mewcode.tools.bash.asyncio.create_subprocess_shell",
        create_process,
    )

    result = await Bash().execute(BashParams(command="custom-command"))

    assert result.is_error
    assert result.output == "failed\n\nExit code 7"


@pytest.mark.asyncio
async def test_bash_preserves_semantic_nonzero_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    async def create_process(*args: object, **kwargs: object) -> _FakeProcess:
        return _FakeProcess(1)

    monkeypatch.setattr(
        "mewcode.tools.bash.asyncio.create_subprocess_shell",
        create_process,
    )

    result = await Bash().execute(BashParams(command="grep needle missing.txt"))

    assert not result.is_error
    assert result.output == "Exit code 1 (no matches found)"
