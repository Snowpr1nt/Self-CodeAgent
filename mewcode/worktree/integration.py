# 来源：公众号@小林coding
# 后端八股网站：xiaolincoding.com
# Agent网站：xiaolinnote.com
# 简历模版：jianli.xiaolinnote.com

from __future__ import annotations

import secrets
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mewcode.agent import Agent
    from mewcode.worktree.manager import WorktreeManager


WORKTREE_NOTICE_TEMPLATE = """\
[WORKTREE CONTEXT]
You have inherited the parent agent's conversation context.
You are currently working in an isolated Git Worktree: {wt_path}
The parent agent's working directory is: {parent_cwd}

IMPORTANT:
- File paths mentioned in the parent conversation refer to the PARENT directory.
- You must translate them to your local worktree path before reading or editing.
- Always re-read files before editing — your copy may differ from the parent's version.
[/WORKTREE CONTEXT]
"""


def generate_worktree_name() -> str:
    """生成 worktree 名称，格式为 agent-a + 7 位十六进制，对齐 Go 的 generateAgentSlug。

    Go 使用 "agent-a" + hex.EncodeToString(b)[:7]，其中 b 是 4 字节随机数。
    4 字节 = 8 位十六进制，截取前 7 位。匹配 Go 的 cleanup 正则 ^agent-a[0-9a-f]{7}$。
    """
    return f"agent-a{secrets.token_hex(4)[:7]}"


def build_worktree_notice(parent_cwd: str, wt_path: str) -> str:
    return WORKTREE_NOTICE_TEMPLATE.format(
        parent_cwd=parent_cwd,
        wt_path=wt_path,
    )


class WorktreeAgentBinding:
    """Switch execution paths while retaining the conversation's storage root."""

    def __init__(self, agent: Agent) -> None:
        self.agent = agent
        self.original_root = Path(agent.work_dir).resolve()
        checker = agent.permission_checker
        self.original_sandbox = checker.sandbox if checker else None
        bash = agent.registry.get("Bash")
        self.original_bash_config = bash.sandbox_config if bash else None
        self.switch(str(self.original_root))

    def _rebase(self, path: str | Path, root: Path) -> str:
        value = Path(path)
        try:
            return str(root / value.relative_to(self.original_root))
        except ValueError:
            return str(path)

    def _git_metadata_write_paths(self, root: Path) -> list[str]:
        """Allow only Git's private index and shared commit/ref storage."""
        git_file = root / ".git"
        if not git_file.is_file():
            return []
        try:
            marker = git_file.read_text(encoding="utf-8").strip()
            if not marker.startswith("gitdir:"):
                return []
            gitdir = Path(marker.split(":", 1)[1].strip())
            if not gitdir.is_absolute():
                gitdir = root / gitdir
            gitdir = gitdir.resolve()
            trusted = (self.original_root / ".git").resolve()
            if not gitdir.is_relative_to(trusted / "worktrees"):
                return []
            common = (gitdir / (gitdir / "commondir").read_text(encoding="utf-8").strip()).resolve()
            if common != trusted:
                return []
            head = (gitdir / "HEAD").read_text(encoding="utf-8").strip()
            if not head.startswith("ref: refs/heads/"):
                return []
            paths = [gitdir, common / "objects", common / "refs" / "heads",
                     common / "logs" / "refs" / "heads"]
            return [str(path) for path in paths if path.is_dir()]
        except OSError:
            return []

    def switch(self, work_dir: str) -> None:
        from mewcode.permissions import PathSandbox

        root = Path(work_dir).resolve()
        self.agent.work_dir = str(root)
        for tool in self.agent.registry.list_tools():
            if tool.name in {"ReadFile", "WriteFile", "EditFile", "Glob", "Grep", "Bash"}:
                tool.work_dir = str(root)

        checker = self.agent.permission_checker
        if checker and self.original_sandbox:
            if root == self.original_root:
                checker.sandbox = self.original_sandbox
            else:
                checker.sandbox = PathSandbox(
                    str(root),
                    deny_write=[self._rebase(p, root) for p in self.original_sandbox._deny_write],
                )
                checker.sandbox._allowed_roots = [root, *self.original_sandbox._allowed_roots[1:]]
            checker.plan_file_path = ""
        self.agent._plan_path_cache = None

        bash = self.agent.registry.get("Bash")
        if bash and self.original_bash_config is not None:
            if root == self.original_root:
                bash.sandbox_config = self.original_bash_config
            else:
                bash.sandbox_config = replace(
                    self.original_bash_config,
                    allow_write=[
                        *[self._rebase(p, root) for p in self.original_bash_config.allow_write],
                        *self._git_metadata_write_paths(root),
                    ],
                    deny_write=[self._rebase(p, root) for p in self.original_bash_config.deny_write],
                )

