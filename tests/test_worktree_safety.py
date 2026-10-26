"""Day 13 acceptance: real Git repositories, return codes, and disk effects."""
from __future__ import annotations

import os
import json
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from mewcode.worktree.changes import count_worktree_changes, has_unpushed_commits
from mewcode.worktree.cleanup import cleanup_stale_worktrees
from mewcode.worktree.manager import WorktreeError, WorktreeManager
from mewcode.worktree.session import load_worktree_session


@pytest.fixture(autouse=True)
def scoped_git_trust(monkeypatch, tmp_path):
    """Keep native Git real, with command-local trust only inside this test."""
    run = subprocess.run

    def trusted_run(args, *positional, **kwargs):
        if args and args[0] == "git":
            cwd = Path(kwargs["cwd"]).resolve()
            assert cwd.is_relative_to(tmp_path.resolve()), cwd
            args = ["git", "-c", f"safe.directory={cwd.as_posix()}", *args[1:]]
        return run(args, *positional, **kwargs)

    monkeypatch.setattr(subprocess, "run", trusted_run)


def git(repo: Path | str, *args: str, check: bool = True):
    result = subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True, timeout=30,
    )
    if check:
        assert result.returncode == 0, result.stderr
    return result


@pytest.fixture
def repo(tmp_path):
    path = tmp_path / "repo"
    path.mkdir()
    git(path, "init", "-b", "main")
    git(path, "config", "user.name", "Worktree Test")
    git(path, "config", "user.email", "worktree-test@localhost")
    (path / ".gitignore").write_text(".mewcode/\n", encoding="utf-8")
    (path / "README.md").write_text("base\n", encoding="utf-8")
    git(path, "add", ".")
    git(path, "commit", "-m", "test baseline")
    return path


@pytest.fixture
def manager(repo):
    return WorktreeManager(str(repo), symlink_directories=[])


def commit_file(path: str | Path, filename: str = "work.txt") -> str:
    root = Path(path)
    (root / filename).write_text("local work\n", encoding="utf-8")
    git(root, "add", filename)
    git(root, "commit", "-m", "preserve local work")
    return git(root, "rev-parse", "HEAD").stdout.strip()


def publish_baseline(repo, tmp_path):
    remote = tmp_path / "remote.git"
    remote.mkdir()
    git(remote, "init", "--bare")
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "origin", "main")


def make_stale(path):
    old = time.time() - 48 * 3600
    os.utime(path, (old, old))


@pytest.mark.asyncio
async def test_failed_remove_preserves_session_branch_and_disk(manager, repo):
    wt = await manager.create("locked")
    await manager.enter(wt.name)
    git(repo, "worktree", "lock", wt.path)

    with pytest.raises(WorktreeError, match="remove"):
        await manager.exit(wt.name, action="remove", discard_changes=True)

    assert Path(wt.path).is_dir()
    assert manager.active[wt.name] == wt
    assert manager.current_session.worktree_name == wt.name
    assert load_worktree_session(repo / ".mewcode").worktree_name == wt.name
    assert git(repo, "rev-parse", wt.branch).stdout.strip() == wt.head_commit


@pytest.mark.asyncio
async def test_clean_remove_deletes_directory_branch_and_session(manager, repo):
    wt = await manager.create("clean")
    await manager.enter(wt.name)
    await manager.exit(wt.name, action="remove")
    assert not Path(wt.path).exists()
    assert git(repo, "show-ref", "--verify", f"refs/heads/{wt.branch}", check=False).returncode != 0
    assert not manager.active
    assert manager.current_session is None
    assert load_worktree_session(repo / ".mewcode") is None


@pytest.mark.asyncio
async def test_restore_keeps_creation_baseline_and_protects_commits(manager, repo):
    wt = await manager.create("restore")
    await manager.enter(wt.name)
    committed = commit_file(wt.path)

    restored = WorktreeManager(str(repo))
    assert restored.restore_session() is not None
    assert restored.active[wt.name].head_commit == wt.head_commit
    with pytest.raises(WorktreeError, match="changes"):
        await restored.exit(wt.name, action="remove")
    assert Path(wt.path).is_dir()
    assert git(repo, "rev-parse", wt.branch).stdout.strip() == committed


@pytest.mark.asyncio
async def test_fast_recovery_protects_existing_commits(manager, repo):
    wt = await manager.create("recover")
    committed = commit_file(wt.path)
    recovered = WorktreeManager(str(repo))
    reused = await recovered.create(wt.name)
    assert reused.path == wt.path
    with pytest.raises(WorktreeError, match="changes"):
        await recovered.exit(wt.name, action="remove")
    assert git(repo, "rev-parse", wt.branch).stdout.strip() == committed


@pytest.mark.asyncio
async def test_create_does_not_reset_existing_branch(manager, repo):
    wt = await manager.create("existing")
    committed = commit_file(wt.path)
    git(repo, "worktree", "remove", wt.path)
    manager.active.clear()

    with pytest.raises(WorktreeError):
        await manager.create(wt.name)

    assert git(repo, "rev-parse", wt.branch).stdout.strip() == committed
    assert not Path(wt.path).exists()


@pytest.mark.asyncio
async def test_changed_checkout_does_not_delete_original_branch_commits(manager, repo):
    wt = await manager.create("switched")
    committed = commit_file(wt.path)
    git(wt.path, "switch", "-c", "alternate", wt.head_commit)
    with pytest.raises(WorktreeError, match="branch"):
        await manager.exit(wt.name, action="remove")
    assert Path(wt.path).is_dir()
    assert git(repo, "rev-parse", wt.branch).stdout.strip() == committed
    assert manager.active[wt.name] == wt


@pytest.mark.asyncio
async def test_exit_tool_uses_custom_base_commit(manager, repo):
    from mewcode.tools.exit_worktree import ExitWorktreeParams, ExitWorktreeTool
    git(repo, "switch", "-c", "custom-base")
    base = commit_file(repo, "base-only.txt")
    git(repo, "switch", "main")
    wt = await manager.create("custom", "custom-base")
    await manager.enter(wt.name)
    result = await ExitWorktreeTool(manager).execute(ExitWorktreeParams(action="remove"))
    assert not result.is_error, result.output
    assert not Path(wt.path).exists()
    assert git(repo, "rev-parse", "custom-base").stdout.strip() == base


def test_non_repository_is_not_reported_clean(tmp_path):
    changes = count_worktree_changes(str(tmp_path), "HEAD")
    assert changes.uncommitted > 0
    assert changes.new_commits > 0


@pytest.mark.parametrize("baseline", ["missing-baseline", ""])
def test_invalid_baseline_is_not_reported_clean(repo, baseline):
    changes = count_worktree_changes(str(repo), baseline)
    assert changes.uncommitted == 0
    assert changes.new_commits > 0


@pytest.mark.asyncio
@pytest.mark.parametrize("tracked", [False, True])
async def test_change_counts_include_tracked_and_untracked_files(manager, tracked):
    wt = await manager.create("dirty")
    filename = "README.md" if tracked else "untracked.txt"
    (Path(wt.path) / filename).write_text("changed\n", encoding="utf-8")
    changes = count_worktree_changes(wt.path, wt.head_commit)
    assert changes.uncommitted == 1
    assert changes.new_commits == 0
    result = await manager.auto_cleanup(wt.name, wt.head_commit)
    assert result.kept
    assert Path(wt.path).is_dir()


@pytest.mark.asyncio
async def test_ignored_user_data_blocks_automatic_and_default_removal(manager, repo, tmp_path):
    (repo / ".gitignore").write_text(".mewcode/\n.env\n", encoding="utf-8")
    git(repo, "add", ".gitignore")
    git(repo, "commit", "-m", "ignore local environment")
    publish_baseline(repo, tmp_path)
    wt = await manager.create("agent-a1234567")
    local = Path(wt.path) / ".env"
    local.write_text("USER_DATA=preserve\n", encoding="utf-8")
    make_stale(wt.path)
    assert await cleanup_stale_worktrees(manager, cutoff_hours=24) == 0
    assert (await manager.auto_cleanup(wt.name, wt.head_commit)).kept
    with pytest.raises(WorktreeError, match="changes"):
        await manager.exit(wt.name, action="remove")
    assert local.read_text(encoding="utf-8") == "USER_DATA=preserve\n"


@pytest.mark.asyncio
async def test_branch_delete_failure_leaves_agent_on_existing_root(manager, repo):
    agent = make_agent(repo)
    manager.bind_agent(agent)
    wt = await manager.create("branch-locked")
    await manager.enter(wt.name)
    lock = repo / ".git" / "refs" / "heads" / f"{wt.branch}.lock"
    lock.write_text("test lock", encoding="utf-8")
    with pytest.raises(WorktreeError, match="branch"):
        await manager.exit(wt.name, action="remove")
    assert not Path(wt.path).exists()
    assert git(repo, "rev-parse", wt.branch).stdout.strip() == wt.head_commit
    assert agent.work_dir == str(repo)
    assert manager.current_session is None
    assert wt.name not in manager.active


@pytest.mark.asyncio
async def test_unpushed_detection_with_remote_and_no_upstream(manager, repo, tmp_path):
    publish_baseline(repo, tmp_path)
    wt = await manager.create("no-upstream")
    assert git(wt.path, "rev-parse", "--verify", "@{upstream}", check=False).returncode != 0
    assert not has_unpushed_commits(wt.path)
    commit_file(wt.path)
    assert has_unpushed_commits(wt.path)
    result = await manager.auto_cleanup(wt.name, wt.head_commit)
    assert result.kept
    assert Path(wt.path).is_dir()


@pytest.mark.asyncio
async def test_unpushed_detection_without_remotes_keeps_stale_worktree(manager):
    wt = await manager.create("agent-a1234567")
    commit_file(wt.path)
    make_stale(wt.path)
    assert has_unpushed_commits(wt.path)
    assert await cleanup_stale_worktrees(manager, cutoff_hours=24) == 0
    assert Path(wt.path).is_dir()


@pytest.mark.asyncio
@pytest.mark.parametrize("registered", [False, True])
async def test_locked_stale_worktree_is_not_counted_as_removed(manager, repo, tmp_path, registered):
    publish_baseline(repo, tmp_path)
    wt = await manager.create("agent-a1234567")
    git(repo, "worktree", "lock", wt.path)
    make_stale(wt.path)
    if not registered:
        manager.active.clear()

    assert await cleanup_stale_worktrees(manager, cutoff_hours=24) == 0
    assert Path(wt.path).is_dir()
    assert git(repo, "rev-parse", wt.branch).stdout.strip() == wt.head_commit


@pytest.mark.asyncio
@pytest.mark.parametrize("registered", [False, True])
async def test_stale_cleanup_removes_actual_directory_and_branch(manager, repo, tmp_path, registered):
    publish_baseline(repo, tmp_path)
    wt = await manager.create("agent-a1234567")
    make_stale(wt.path)
    if not registered:
        manager.active.clear()
    assert await cleanup_stale_worktrees(manager, cutoff_hours=24) == 1
    assert not Path(wt.path).exists()
    assert git(repo, "show-ref", "--verify", f"refs/heads/{wt.branch}", check=False).returncode != 0


@pytest.mark.asyncio
async def test_stale_cleanup_keeps_active_session(manager, repo, tmp_path):
    publish_baseline(repo, tmp_path)
    wt = await manager.create("agent-a1234567")
    await manager.enter(wt.name)
    make_stale(wt.path)
    assert await cleanup_stale_worktrees(manager, cutoff_hours=24) == 0
    assert Path(wt.path).is_dir()


@pytest.mark.asyncio
async def test_auto_cleanup_keeps_current_session(manager):
    wt = await manager.create("current")
    await manager.enter(wt.name)
    result = await manager.auto_cleanup(wt.name, wt.head_commit)
    assert result.kept
    assert Path(wt.path).is_dir()
    assert manager.current_session.worktree_name == wt.name


@pytest.mark.asyncio
async def test_nested_enter_does_not_lose_original_session(manager):
    await manager.create("first")
    await manager.create("second")
    original = await manager.enter("first")
    with pytest.raises(WorktreeError, match="already"):
        await manager.enter("second")
    assert manager.current_session == original


@pytest.mark.asyncio
async def test_exit_other_worktree_does_not_clear_current_session(manager):
    await manager.create("current")
    await manager.create("other")
    await manager.enter("current")
    await manager.exit("other", action="keep")
    assert manager.current_session.worktree_name == "current"


@pytest.mark.asyncio
async def test_legacy_session_keeps_original_head_protection(manager, repo):
    wt = await manager.create("legacy")
    await manager.enter(wt.name)
    path = repo / ".mewcode" / "worktree_session.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data.pop("worktree_base_commit")
    path.write_text(json.dumps(data), encoding="utf-8")
    commit_file(wt.path)
    restarted = WorktreeManager(str(repo))
    assert restarted.restore_session() is not None
    with pytest.raises(WorktreeError, match="changes"):
        await restarted.exit(wt.name, action="remove")
    assert Path(wt.path).is_dir()


def make_agent(repo):
    from mewcode.agent import Agent
    from mewcode.filehistory.history import FileHistory
    from mewcode.permissions import (
        DangerousCommandDetector, PathSandbox, PermissionChecker, PermissionMode, RuleEngine,
    )
    from mewcode.sandbox import SandboxConfig
    from mewcode.tools import create_default_registry

    checker = PermissionChecker(
        DangerousCommandDetector(), PathSandbox(str(repo)), RuleEngine(),
        mode=PermissionMode.ACCEPT_EDITS,
    )
    # Test the project boundary independently of the optional OS temp allowance.
    checker.sandbox._allowed_roots = [Path(repo).resolve()]
    registry = create_default_registry()
    agent = Agent(None, registry, "anthropic", str(repo), permission_checker=checker)
    agent.start_session("worktree-session")
    agent.file_history = FileHistory(str(repo), agent.session_id)
    for tool in registry.list_tools():
        if hasattr(tool, "file_history"):
            tool.file_history = agent.file_history
    registry.get("Bash").sandbox_config = SandboxConfig(
        allow_write=[str(repo), "/tmp"],
        deny_write=[str(repo / ".mewcode" / "config.yaml")],
    )
    return agent


async def execute(agent, name, **arguments):
    from mewcode.tools.base import ToolCallComplete
    return await agent._execute_tool_noninteractive(ToolCallComplete("id", name, arguments))


@pytest.mark.asyncio
async def test_command_entry_routes_relative_files_to_worktree(manager, repo, monkeypatch):
    from mewcode.commands.handlers.worktree import create_worktree_command
    monkeypatch.chdir(repo)
    agent = make_agent(repo)
    storage = agent.session_dir
    history = agent.file_history
    ctx = SimpleNamespace(args="create command-wt", agent=agent,
                          ui=SimpleNamespace(add_system_message=lambda text: None))
    command = create_worktree_command(manager)
    await command.handler(ctx)
    wt = manager.active["command-wt"]
    assert agent.work_dir == wt.path

    result = await execute(agent, "WriteFile", file_path="created.txt", content="worktree only")
    assert not result.is_error, result.output
    assert (Path(wt.path) / "created.txt").read_text(encoding="utf-8") == "worktree only"
    assert not (repo / "created.txt").exists()
    assert agent.session_dir == storage
    assert agent.file_history is history
    assert str(Path(wt.path) / "created.txt") in history._tracked

    # Read/edit/search use the same root; absolute paths retain their meaning.
    assert "worktree only" in (await execute(agent, "ReadFile", file_path="created.txt")).output
    result = await execute(agent, "EditFile", file_path="created.txt", old_string="only", new_string="changed")
    assert not result.is_error, result.output
    assert (Path(wt.path) / "created.txt").read_text(encoding="utf-8") == "worktree changed"
    assert "created.txt" in (await execute(agent, "Glob", pattern="*.txt")).output
    assert "worktree changed" in (await execute(agent, "Grep", pattern="changed")).output
    assert "base" in (await execute(agent, "ReadFile", file_path=str(Path(wt.path) / "README.md"))).output

    ctx.args = "exit"
    await command.handler(ctx)
    assert agent.work_dir == str(repo)
    assert manager.current_session is None
    result = await execute(agent, "WriteFile", file_path="back.txt", content="main only")
    assert not result.is_error
    assert (repo / "back.txt").is_file()
    assert not (Path(wt.path) / "back.txt").exists()


@pytest.mark.asyncio
async def test_tool_entry_rebases_permissions_and_bash_and_exit_restores(manager, repo, monkeypatch):
    from mewcode.tools.enter_worktree import EnterWorktreeParams, EnterWorktreeTool
    from mewcode.tools.exit_worktree import ExitWorktreeParams, ExitWorktreeTool
    monkeypatch.chdir(repo)
    agent = make_agent(repo)
    original_sandbox = agent.permission_checker.sandbox
    tool = EnterWorktreeTool(manager, agent=agent)
    result = await tool.execute(EnterWorktreeParams(name="tool-wt"))
    assert not result.is_error, result.output
    wt = manager.active["tool-wt"]
    assert agent.work_dir == wt.path
    assert agent.permission_checker.sandbox.project_root == Path(wt.path)
    assert agent.registry.get("Bash").work_dir == wt.path
    config = agent.registry.get("Bash").sandbox_config
    assert str(repo) not in config.allow_write
    assert wt.path in config.allow_write
    assert str(Path(wt.path) / ".mewcode" / "config.yaml") in config.deny_write

    # Parent paths and worktree-sensitive configuration cannot be silently written.
    rejected = await execute(agent, "WriteFile", file_path=str(repo / "parent.txt"), content="blocked")
    assert rejected.is_error
    assert not (repo / "parent.txt").exists()
    rejected = await execute(agent, "WriteFile", file_path=".mewcode/config.yaml", content="blocked")
    assert rejected.is_error
    assert not (Path(wt.path) / ".mewcode" / "config.yaml").exists()
    bash = agent.registry.get("Bash")
    result = await bash.execute(bash.params_model(command="echo worktree>from-bash.txt"))
    assert not result.is_error, result.output
    assert (Path(wt.path) / "from-bash.txt").is_file()
    assert not (repo / "from-bash.txt").exists()

    result = await ExitWorktreeTool(manager, agent=agent).execute(ExitWorktreeParams(action="keep"))
    assert not result.is_error
    assert agent.work_dir == str(repo)
    assert agent.permission_checker.sandbox is original_sandbox
    assert agent.registry.get("Bash").work_dir == str(repo)


@pytest.mark.asyncio
async def test_worktree_bash_sandbox_allows_git_metadata(manager, repo):
    agent = make_agent(repo)
    manager.bind_agent(agent)
    wt = await manager.create("git-metadata")
    await manager.enter(wt.name)
    gitdir = Path(git(wt.path, "rev-parse", "--absolute-git-dir").stdout.strip())
    common = Path(git(wt.path, "rev-parse", "--git-common-dir").stdout.strip())
    if not common.is_absolute():
        common = Path(wt.path) / common
    # Git add writes the private index; commit writes shared objects and refs.
    targets = [gitdir / "index", common / "objects" / "test-object",
               common / "refs" / "heads" / wt.branch]
    config = agent.registry.get("Bash").sandbox_config
    assert all(any(target.resolve().is_relative_to(Path(allowed).resolve())
                   for allowed in config.allow_write) for target in targets)
    assert str(repo) not in config.allow_write


@pytest.mark.asyncio
async def test_restoring_bound_session_routes_writes_into_worktree(manager, repo, monkeypatch):
    monkeypatch.chdir(repo)
    wt = await manager.create("restore-bound")
    await manager.enter(wt.name)
    restarted_agent = make_agent(repo)
    restarted = WorktreeManager(str(repo), agent=restarted_agent)
    assert restarted.restore_session() is not None
    result = await execute(restarted_agent, "WriteFile", file_path="after-restart.txt", content="isolated")
    assert not result.is_error, result.output
    assert (Path(wt.path) / "after-restart.txt").is_file()
    assert not (repo / "after-restart.txt").exists()


@pytest.mark.asyncio
async def test_worktree_switch_keeps_transcript_on_original_session_root(manager, repo):
    agent = make_agent(repo)
    path = repo / ".mewcode" / "sessions" / f"{agent.session_id}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("original transcript", encoding="utf-8")
    manager.bind_agent(agent)
    wt = await manager.create("transcript")
    await manager.enter(wt.name)
    assert Path(agent._transcript_path).read_text(encoding="utf-8") == "original transcript"
    agent.start_session("next-session")
    assert agent.session_dir == repo / ".mewcode" / "session" / "tool-results" / "next-session"


@pytest.mark.asyncio
async def test_worktree_read_recovery_contains_worktree_content(manager, repo, monkeypatch):
    from mewcode.context.manager import build_recovery_attachment
    from mewcode.tools.base import ToolCallComplete
    monkeypatch.chdir(repo)
    agent = make_agent(repo)
    manager.bind_agent(agent)
    wt = await manager.create("recovery")
    (Path(wt.path) / "README.md").write_text("WORKTREE RECOVERY", encoding="utf-8")
    await manager.enter(wt.name)
    result = await execute(agent, "ReadFile", file_path="README.md")
    assert not result.is_error
    agent._snapshot_for_recovery(
        ToolCallComplete("read-id", "ReadFile", {"file_path": "README.md"}), result,
    )
    assert "WORKTREE RECOVERY" in build_recovery_attachment(agent.recovery_state, None)
