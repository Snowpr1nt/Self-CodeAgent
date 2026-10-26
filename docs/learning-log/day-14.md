# Day14：双 Agent 协作与团队生命周期

日期：2026-10-25 至 26。任务：用真实 Teams/邮箱/共享任务验证分配、消息交换、结果合并、继续执行、结束、取消和回收；修复已复现的数据保留边界。Provider 始终为离线 Fake，不使用用户账号数据或网络。

## 先调查，再 RED

读 TeamManager、Mailbox、SharedTaskStore、AgentNameRegistry、Coordinator、spawn_inprocess 和 AgentTool/TaskManager 接线，定位到以下问题：

1. SendMessage 按 ID 投递，进程内 runner 却按名称等待；idle 通知写给字面 lead，而接收端按 lead_agent_id 消费。
2. `shutdown_request` 只检查文本前缀；初始等待与工具循环又可能先把它作为普通消息消费。
3. 产品入口没有使用已有的进程内后端，运行一套单独的 60 秒轮询，生命周期契约不一致。
4. 启动前取消没有更新进度；TeamManager 没有统一的 cancel-and-await 退出方法。
5. TeamDelete 使用 force 且忽略 Git 返回码，异常还尝试递归删除；正确仓库目录、数据安全与删除失败都没有受到完整验证。
6. 子注册表复用父执行工具，真实相对文件写落入父根；浅拷贝后修改目录还会改变父工具，Bash 沙箱允许根也需要重定位。

## 验证证据

| 阶段 | 实际输出 |
|---|---|
| Teams 首轮 RED | **11 failed in 5.51s**，覆盖地址/关闭/回收与真实 Git 清理边界 |
| 首轮修复 | **11 passed in 3.05s** |
| 产品后端与运行中关闭补充 RED | **2 failed, 11 deselected in 3.39s** |
| 统一后端后的新旧生命周期 | **30 passed in 4.01s** |
| 子工具隔离 RED（主代理宿主工具） | **5 failed in 2.49s**，三个父目录被改、两个真实写入位置错误 |
| 子工具修复及 Teams 邻接（主代理宿主工具） | **146 passed in 5.20s** |
| TUI 退出直管 Team runner 补充 RED → GREEN | **1 failed**，修复后与后台退出用例合并 **2 passed** |
| 最终全量回归 | **731 passed、2 skipped in 44.10s**；无失败 |
| 双 Agent 演示与 CLI Smoke | 演示 exit 0，20 + 22 = 42、0 运行 handle、团队已删除；隔离目录 `mewcode --help` exit 0 |
| compileall、定向 diff --check | **exit 0**；Git 有 LF/CRLF 提示 |

第一次全量测试把隔离 USERPROFILE 放到 TEMP 的允许根之下，令 `test_home_ssh` 的“主目录不在允许临时目录”前提失效，得到 729 passed、1 failed、2 skipped。将两个目录设为仓库外同级隔离目录后全量通过；没有改产品权限或断言。两项跳过已单独核对：Windows 没有 `/etc` 候选文件，另一个缺少真实 LLM 测试 Key。

联合验证命令（每次使用新 basetemp）：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_team_lifecycle.py tests/test_subagent_execution_isolation.py tests/test_subagent_lifecycle.py tests/test_subagent.py tests/test_teams.py tests/test_coordinator_multi_team.py -q --basetemp <新的专用临时目录>
.\.venv\Scripts\python.exe -m examples.teams.demo
.\.venv\Scripts\python.exe -m compileall -q examples/teams mewcode/teams mewcode/agents mewcode/tools/agent_tool.py mewcode/tools/send_message.py tests/test_team_lifecycle.py tests/test_subagent_execution_isolation.py
```

## 修复与设计边界

消息先在当前团队解析目标，所有内部投递统一用 agent_id；保留兼容旧名称邮箱读取。关闭请求同时保存到 handle 标志，普通 Agent 读过消息也不会丢掉退出意图。TaskManager 委派给已有后端，失败/取消和未开始就取消均通过最终 callback 收尾。

TUI 退出原来只等待 TaskManager，再直接尝试 TeamDelete。若 runner 仅由 TeamManager 持有，删除会因成员仍在运行被拒绝且退出过程吞掉异常，留下活任务。真实 TUI 方法调用测试先复现该状态；现在先 await `TeamManager.shutdown()` 再清理团队。

子工具独立实例、缓存、工作目录与 FileHistory；Bash 沙箱根随工作树重定位，父 Agent 的配置保持原样。工具定义保持一致，权限状态仍来自父注册表。

工作树清理由保守检测与 Git 的实际结果共同决定；失败时保留用户成果及团队元信息，移除 force 和递归删除兜底。未知状态需要保留，不能用失败的查询得出“没有改动”。

## 学到的开发方法

“模块已经写好”不能代替产品调用链验证。单独测试 spawn 函数会漏掉 AgentTool 实际启动另一套 runner 的情况；必须至少有一条从产品入口到最终生命周期的验证。

消息有三个不同身份：显示名称、团队成员 ID、邮箱地址。跨模块如果没有明确同一个地址契约，初次执行成功也不能证明后续任务能送达。测试需要包含 idle 后再次发消息。

安全清理既要验证拒绝，也要验证实际成功。只断言抛错会漏掉已丢失元信息，或者声称删除成功而工作树仍在磁盘的情况。

## 交付与限制

- `examples/teams/demo.py`、README：真实两 Agent、真实任务与消息；Fake Provider。
- `tests/test_team_lifecycle.py`、`tests/test_subagent_execution_isolation.py`。
- `docs/teams-collaboration.md`。
- 主代理额外修改 `app.py` 退出回收，并更新 roadmap/testing、讲解稿及总结；最终提交由主代理完成。
- 不将离线结果当作在线模型、终端 pane、外部 MCP 或跨进程共享文件事务验收。
