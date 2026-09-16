from __future__ import annotations

from pathlib import Path

import pytest

from mewcode.permissions import (
    DangerousCommandDetector,
    PathSandbox,
    PermissionChecker,
    PermissionMode,
    RuleEngine,
)
from mewcode.tools.glob import Glob
from mewcode.tools.grep import Grep
from mewcode.tools.read_file import ReadFile
from mewcode.tools.write_file import WriteFile


def _checker(
    project_root: Path,
    mode: PermissionMode = PermissionMode.DEFAULT,
) -> PermissionChecker:
    return PermissionChecker(
        detector=DangerousCommandDetector(),
        sandbox=PathSandbox(str(project_root)),
        rule_engine=RuleEngine(),
        mode=mode,
    )


def _outside_path(tmp_path: Path, *parts: str) -> Path:
    return Path(tmp_path.anchor).joinpath("mewcode-permission-boundary", *parts)


def test_plan_mode_allows_only_exact_configured_plan(tmp_path: Path) -> None:
    project = tmp_path / "project"
    plan = project / ".mewcode" / "plans" / "day-06.md"
    plan.parent.mkdir(parents=True)
    checker = _checker(project, PermissionMode.PLAN)
    checker.plan_file_path = str(plan)
    tool = WriteFile()

    exact = checker.check(tool, {"file_path": str(plan), "content": "# Plan"})
    same_name_elsewhere = checker.check(
        tool,
        {
            "file_path": str(_outside_path(tmp_path, plan.name)),
            "content": "not the configured plan",
        },
    )

    assert exact.effect == "allow"
    assert same_name_elsewhere.effect == "ask"
    assert "沙箱" in same_name_elsewhere.reason


def test_plan_directory_substring_does_not_bypass_sandbox(tmp_path: Path) -> None:
    project = tmp_path / "project"
    configured_plan = project / ".mewcode" / "plans" / "configured.md"
    configured_plan.parent.mkdir(parents=True)
    checker = _checker(project, PermissionMode.PLAN)
    checker.plan_file_path = str(configured_plan)
    misleading = _outside_path(tmp_path, ".mewcode", "plans", "other.md")

    decision = checker.check(
        WriteFile(),
        {
            "file_path": misleading.as_posix(),
            "content": "outside project",
        },
    )

    assert decision.effect == "ask"
    assert "沙箱" in decision.reason


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        (Glob(), {"pattern": "**/*.py"}),
        (Grep(), {"pattern": "TODO"}),
    ],
)
def test_search_tools_check_search_root_not_pattern(
    tmp_path: Path,
    tool: Glob | Grep,
    arguments: dict[str, str],
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    outside = _outside_path(tmp_path, "search-root")
    checker = _checker(project)

    decision = checker.check(tool, {**arguments, "path": str(outside)})

    assert decision.effect == "ask"
    assert "沙箱" in decision.reason


def test_sensitive_project_config_is_readable_but_not_writable(tmp_path: Path) -> None:
    project = tmp_path / "project"
    config = project / ".mewcode" / "config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text("provider: test\n", encoding="utf-8")
    checker = _checker(project, PermissionMode.ACCEPT_EDITS)

    read = checker.check(ReadFile(), {"file_path": str(config)})
    write = checker.check(
        WriteFile(),
        {"file_path": str(config), "content": "provider: changed\n"},
    )

    assert read.effect == "allow"
    assert write.effect == "ask"
    assert "禁写" in write.reason
