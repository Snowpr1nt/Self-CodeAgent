# 测试策略与 Day 1 基线

## 1. 标准环境

- 操作系统：Windows
- Python：3.12.6
- uv：0.12.1
- 项目版本：0.2.0
- 依赖来源：`uv.lock`
- 同步命令：`python -m uv sync --locked`

当前用户级 uv 可通过 `python -m uv` 调用。若将其 Scripts 目录加入 `PATH`，也可以直接使用 `uv`。

## 2. 测试分层

| 层级 | 目的 | 建议命令 |
|---|---|---|
| 语法检查 | 快速发现无法解析的 Python 文件 | `python -m compileall -q mewcode tests` |
| 定向测试 | 验证当前改动所属模块 | `python -m uv run pytest -q tests/test_<module>.py` |
| 回归测试 | 验证所有模块没有被破坏 | `python -m uv run pytest -q` |
| CLI Smoke | 验证安装入口与参数解析 | `.venv\Scripts\mewcode.exe --help` |
| 在线 Smoke | 验证真实 Provider 配置 | Day 2 在用户提供 API Key 后执行 |

开发功能或修复缺陷时采用 Red-Green-Refactor：先让新增测试失败，再做最小实现，最后运行定向测试和全量回归。

## 3. Day 1 全量结果

在锁定依赖环境中共执行 571 个用例：

- 566 passed
- 3 failed
- 2 skipped
- 1 warning
- 总耗时约 10.75 秒

为避免测试发现仓库根目录的 `MEWCODE.md`，pytest 临时目录放在仓库外。真实开发终端通常可以直接使用系统临时目录。

## 4. 已知失败

### 4.1 Unix 临时路径硬编码

`tests/test_agent.py::test_multi_step_autonomous` 使用 `/tmp/mewcode_test_hello.txt`。Windows 下该路径映射和权限行为不稳定，应改用 pytest 的 `tmp_path`。

### 4.2 Unix sleep 命令硬编码

`tests/test_hooks.py::TestCommandExecutor::test_timeout` 执行 `sleep 10`。Windows `cmd.exe` 没有该命令，因此测试没有进入超时分支。应改为跨平台 Python 子进程或按平台选择命令。

### 4.3 主 Agent 快速路径绕过 Hook

`tests/test_hooks.py::TestAgentHookIntegration::test_pre_tool_use_reject_skips_tool` 期望 Hook 拒绝危险 Bash，但工具被直接执行。

根因调用链：

1. `Agent.run()` 在收到无需询问的工具调用后提交 `_execute_single_tool_direct()`。
2. `_execute_single_tool_direct()` 执行权限和参数检查后直接调用工具。
3. `run_pre_tool_hooks()` 只在 `_execute_tool_noninteractive()` 中调用。

这是功能与安全缺陷，不应简单修改测试掩盖。计划在 Day 11 通过回归测试驱动修复。

## 5. 测试配置缺口

`tests/test_consolidation.py` 使用 `pytest.mark.timeout`，但项目没有声明 `pytest-timeout`，也没有注册该 Marker，因此产生 `PytestUnknownMarkWarning`。Day 1 仅记录基线，后续在测试治理任务中决定增加依赖还是移除 Marker。

## 6. 回归门禁

- 普通提交：相关测试文件必须通过。
- 安全相关提交：权限、Hook、Bash 和 Agent 测试必须同时通过。
- Day 7 与 Day 14：运行全量测试并更新本文件结果。
- 不允许用删除断言、跳过测试或扩大权限来制造“绿色”结果。

## 7. Day 2 回归结果

Day 2 新增 `tests/test_client.py`，参数化后增加 14 个用例。锁定依赖增加 `socksio`，总收集数由 571 增加到 585：

- 580 passed
- 3 failed
- 2 skipped
- 1 warning

三个失败与 Day 1 完全一致，因此 Day 2 没有引入新的回归。客户端相关定向测试 14 个全部通过；客户端及 Context Window、Serialization、Consolidation 邻接测试为 63 passed、1 skipped。

## 8. Day 3 回归结果

Day 3 新增 `tests/test_entrypoints.py`，增加 7 个用例。总收集数由 585 增加到 592：

- 587 passed
- 3 failed
- 2 skipped
- 1 warning

三个失败与 Day 1、Day 2 完全一致，没有新增红色项。排除这三条已知失败后执行绿色门禁，结果为 587 passed、2 skipped、3 deselected。

入口、Serialization 与 Agent 邻接测试为 32 passed、1 deselected。语法检查和 `mewcode --help` CLI Smoke 同时通过。

在沙箱中运行全量测试时，pytest 的 `--basetemp` 应位于仓库外，`HOME` 与 `USERPROFILE` 也应指向可写隔离目录。否则指令文件会沿父目录搜索到仓库的 `MEWCODE.md`，用户级 Team 文件也可能因真实主目录不可写而产生环境性失败。

## 9. Day 4 工具层结果

Day 4 新增 `tests/test_tools.py`，覆盖默认注册表、Provider Schema、文件状态协议、Glob/Grep 和 Bash 退出码，共增加 11 个用例。

- 工具、EditFile 与 Diff 邻接测试：18 passed。
- RED 阶段稳定复现 4 项缺陷：同时间戳内容冲突、陈旧读取、空缓存未填充、普通非零退出码未报错。
- GREEN 阶段四项全部通过，没有通过删断言或放宽错误条件制造绿色结果。

在受限 Windows 沙箱中，pytest 创建的 `--basetemp` 目录可能被 ACL 阻止再次访问；此时出现的大量 `PermissionError` 属于测试环境失败，不能当成功能回归结论。可信全量门禁必须在进程可读写的仓库外隔离目录运行。
