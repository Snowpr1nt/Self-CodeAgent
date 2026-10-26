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

## 10. Day 5 Agent Loop 结果

Day 5 新增 `tests/test_agent_loop.py` 的 2 个确定性场景：两轮工具闭环，以及 Provider 在工具启动后断线的异常收尾。

- 新测试：2 passed。
- Agent 邻接：18 passed、1 deselected。
- Agent、入口与权限扩展回归：78 passed、1 skipped、1 deselected。

扩展回归排除了既有 `tests/test_agent.py::test_multi_step_autonomous` Windows `/tmp` 红线。Day 4 修正 Bash 非零退出码后，权限测试改用 `git --version` 验证规则放行，避免 `git status` 的仓库信任状态污染权限断言。

## 11. Day 6 安全矩阵结果

Day 6 新增 `tests/test_permission_boundaries.py`，共 5 个用例，验证精确 Plan 文件、外部伪造路径、Glob/Grep 搜索根和敏感配置读写差异。

- 新安全边界：5 passed。
- 权限模块：58 passed、1 skipped。
- Permission、Hook、Bash、Agent 联合门禁：142 passed、1 skipped、3 deselected。

联合门禁只排除文档已登记的三项：Unix `/tmp`、Unix `sleep` 和主 Agent Hook 绕过。三项单独复核时，后两项稳定失败；`/tmp` 用例在获批的非沙箱进程中可写而通过，说明它是环境相关的跨平台缺口，并不代表测试已修复。

尝试运行不透明的整包测试与大分组测试时，执行审批被拒绝；未将这些 `aborted` 结果当作通过。Day 7 的首次全量回归仍按路线图保留，届时应在明确隔离的测试环境中执行。

## 12. Day 7 MCP 与首次全量回归

Day 7 新增 `tests/test_mcp_integration.py` 的 4 个场景，覆盖 Fake MCP 注册/发现/调用/关闭、SubAgent 识别、禁用工具发现和失败连接释放。

- RED：28 passed、7 failed。
- MCP、ToolSearch、SubAgent、Teams 邻接回归：144 passed。
- 新集成与 ToolSearch 最终快速门禁：15 passed。
- 全量收集：614 tests。

第一次整包门禁明确排除三条已登记红线，执行结果为 565 passed、44 failed、2 skipped、3 deselected、1 warning。44 个失败全部是受限 Windows 沙箱无法访问 Python/pytest 以 `0700` 创建的临时目录，堆栈统一为 `PermissionError`；没有新增产品断言失败。

一次性测试启动器验证 pytest `tmp_path` 放宽目录模式后可继续运行，但标准库 `tempfile.TemporaryDirectory` 仍受同一 ACL 限制。非沙箱整包与受影响分组的执行审批随后被拒绝，因此保留环境失败记录，不声明全量绿色。Day 7 功能相关的 144 项邻接测试已在可写隔离目录通过。

## 13. Day 8 Context 与 Recovery

Day 8 新增 `tests/test_context_recovery_integration.py` 的 6 个集成场景、8 个测试用例，覆盖落盘路径约束、跨平台文件名、真实目录边界、截断 JSONL 恢复、摘要流中断重试和压缩后落盘文件保留。

- RED：第一轮 5 failed；代码审阅补充的跨平台循环 3 failed。
- 新增测试 GREEN：8 passed。
- Context、Recovery、Replacement、Context Window、Session/Memory 邻接回归：159 passed。
- Command、Entrypoint、Skill 邻接回归：82 passed。
- Agent 主循环：16 passed、1 deselected；只排除已登记红色基线 `tests/test_agent.py::test_multi_step_autonomous`。
- 最终合并门禁：257 passed、1 deselected。

首次基线在受限沙箱中得到 95 passed、56 errors；56 项全部停在 pytest `tmp_path` 建立阶段，原因为 Windows 临时目录 ACL 的 `PermissionError: WinError 5`，不是产品断言失败。同一幂等命令自动改用获批隔离临时目录重跑后得到 151 passed，记录了完整的失败原因和恢复结果。

## 14. Day 9 Session 与 Memory

`tests/test_session_recovery.py` 使用真实 Session 文件和两个 Agent 实例覆盖重启恢复、独立工具结果目录、旧版共用目录兼容、会话 ID 路径边界与磁盘 `MEMORY.md` 注入。

- RED：首轮 2 failed；兼容和路径边界补充循环 2 failed。
- 新测试：4 passed。
- Session、Memory、Context、Agent Loop、Command 邻接回归：160 passed。

2026-10-23 交接补充：上述 160 passed 发生在长期记忆测试改为真实磁盘索引之前；调整后新测试单独运行得到 4 passed。最后一次提交前合并验证返回 `aborted`，没有可确认结果，工具也未提供中断原因。Day 9 尚未提交，完整门禁待接任模型复核。

### Day 9 最终收尾（2026-10-23）

在本轮仓库外专用 basetemp，以获批宿主权限重跑 Windows ACL 失败的幂等测试。新增 RED 首轮 5 failed/4 passed，命令切换与 ID 边界补充 4 failed/9 passed；独立审阅补充压缩通知游标测试 RED 为 1 failed。最终 `test_session_recovery.py test_memory.py test_context.py test_replacement_state.py test_agent_loop.py test_commands.py`：**170 passed in 4.07s**，没有排除用例。Recovery 使用实际 TUI 持久化方法和磁盘 Session 恢复，后台入口覆盖已有/新建 Conversation 与真实压缩。先前 aborted 的未知原因记录不变，现已取得新的完整门禁输出。

### Day 10 Slash Command 与 Skill

真实目录发现、Command 注册/别名、参数展开、Skill inline/fork、Recovery 记录及 fork 权限测试先有 11 failed 的 RED。最终 `test_commands.py test_skills.py test_commands_skills_integration.py test_recovery.py test_context_recovery_integration.py test_session_recovery.py`：**116 passed in 3.27s**。`python -m examples.commands_skills.run_demo --scratch-dir <本轮仓库外根目录>` exit 0，Fake Provider 收到 3 次请求；这不是在线模型验收。首次 Windows 沙箱 `WinError 5` 后在宿主权限下重跑；额度服务拒绝过一次 GREEN 命令，最终成功证据来自恢复后新的完整门禁。

### Day 11 Hook 与跨平台测试

三条历史红线均解除，三个工具执行入口的 pre/post Hook 行为以 6 个组合场景验证；真实子进程分别覆盖 Hook/Bash 的超时和取消，以及 shell 先退出的后代进程。首次相关门禁在 `test_lock_reclaims_dead_pid` 触发 Windows `KeyboardInterrupt`，即使隔离临时目录也复现；增加进程探测 RED 测试后改用 Win32 只读存活查询。最终 `test_consolidation.py test_hooks.py test_hook_execution_paths.py test_permissions.py test_permission_boundaries.py test_agent.py test_agent_loop.py test_tools.py`：**157 passed、2 skipped in 16.42s**，没有排除用例。`pytest.mark.timeout` 的未知 marker 已移除，真实 LLM 测试改用 `asyncio.wait_for(120)`，缺少 `MEWCODE_TEST_API_KEY` 时仍跳过。测试的 `--basetemp` 均在仓库外可写目录。

### Day 12 SubAgent 与后台任务

先以 Event 控制的 Fake Provider 复现启动前取消、后台 Trace 未结束、禁用工具状态丢失等缺口，再集中修复。主代理最终 `test_subagent_lifecycle.py test_subagent.py test_mcp_integration.py test_agent_loop.py`：**82 passed in 4.20s**，无跳过。`python -m examples.subagents.demo` exit 0；前台读到离线 fixture，后台成功、失败、运行中取消、启动前取消均有 1 条通知和 0 个运行任务。双 Agent/子工具目录隔离的邻接测试另得 **146 passed in 5.20s**，归 Day14 集成门禁；均不代表在线 Provider 验收。

### Day 13 Git Worktree

真实临时 Git 仓库先验证工作目录切换、基线持久化、忽略文件与未推送提交保护。独立审阅补充的三项 RED 为 **3 failed、28 deselected**：切换检出分支后误删原分支、ExitWorktree 对自定义基线误报新增提交、Bash OS 沙箱缺少 Git 元数据写入路径。修复后对应 **3 passed、28 deselected**；最终 `test_worktree.py test_worktree_safety.py test_tools.py test_edit_file.py test_permission_boundaries.py test_session_recovery.py test_commands.py`：**155 passed in 20.85s**。Git 操作仅发生在专用临时测试仓库；本轮 Windows 测试覆盖沙箱路径配置，未在 macOS/Linux 内核沙箱执行 Git 提交。

### Day 14 Teams 与最终回归

Team 消息、持久 runner、关闭及清理以 `test_team_lifecycle.py` 和真实临时 Git 仓库验证；子工具执行目录以 `test_subagent_execution_isolation.py` 验证。新增 TUI 退出测试先得到 **1 failed**（Team runner 未等待），修复后与后台任务退出测试合并为 **2 passed**。`python -m examples.teams.demo` exit 0：Alice/Bob 完成真实共享任务，分别交付 20/22，lead 合并 42；Provider 为 Fake。

第一次全量测试把隔离 USERPROFILE 放在允许的 TEMP 根目录下，导致 `test_home_ssh` 前提不成立，得到 **729 passed、1 failed、2 skipped**。把 USERPROFILE 与 TEMP 改为仓库外同级隔离目录后，最终 `python -m pytest tests -q`：**731 passed、2 skipped in 44.10s**，无失败。跳过项分别是 Windows 没有 `/etc` 候选文件的 symlink 测试，以及未提供 `MEWCODE_TEST_API_KEY` 的真实 LLM 整理测试。`compileall`、`git diff --check`、隔离目录的 `python -m mewcode --help` 均 exit 0。自动测试覆盖 TUI 退出生命周期，未进行人工交互式 UI Smoke；真实 Provider/MCP 在线 Smoke 未做。
