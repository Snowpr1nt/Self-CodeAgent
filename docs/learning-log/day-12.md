# Day12：SubAgent 与后台任务

日期：2026-10-23。交付范围：前台结果、后台成功/失败/取消、Trace、通知消费、任务回收和可重复离线演示。用户已授权连续推进，未修改用户配置、凭据或用户级记忆。

## 从调用链得到的问题

阅读 AgentTool、fork、tool_filter、TraceManager、TaskManager、notification 后，发现四个可以直接复现的边界：

1. launch/adopt 后同轮 cancel，asyncio 协程还没开始，原 try/finally 没机会执行，任务一直 running，运行句柄与通知丢失。
2. 后台 launch 没有连接 Trace 的结束事件；任务成功、失败和取消后 Trace 仍 running。前台 CancelledError 也没有结束 Trace。
3. 新建工具注册表只复制 Tool 对象，丢失 disabled/discovered；子 Agent 会重新启用父 Agent 已禁用的工具，fork 已加载的 deferred schema 也改变。
4. team 初次回答结束就标记 completed，但 runner 还在等待邮箱；cancel 拒绝这个仍在运行的任务。

## RED → GREEN

测试保持真实 Agent/Conversation/TaskManager/Trace，仅将外部 Provider 换成 Event 控制的 Fake Client。额外 team 测试使用真实 Mailbox，隔离外部 Worktree 与 Team 注册边界。

| 阶段 | 实际结果 |
|---|---|
| 初次沙箱 pytest | Windows 临时目录 WinError 5，退出 1；属于环境错误，未计作产品 RED |
| 原命令以 require_escalated 重跑 | **9 failed, 2 passed in 2.55s**，立即取消、Trace 与工具状态断言失败 |
| team 空闲与 shutdown 补充 RED | **2 failed, 11 deselected in 2.54s** |
| 首次实现后定向及邻接 | **76 passed in 3.34s** |
| teammate/coordinator 补充 RED | **2 failed, 14 deselected in 2.50s**，两个遗漏的连接点已复现 |
| 主代理最终四文件联合门禁 | **82 passed in 4.20s**，无 failed/skipped/warnings |
| 离线演示 | **exit 0**，前台读到 fixture；四个后台场景各 1 通知、0 运行任务，状态与 Trace 一致 |
| compileall 与定向 diff --check | **exit 0**；Git 仅有既有 LF/CRLF 提示 |

最终测试命令：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_subagent_lifecycle.py tests/test_subagent.py tests/test_mcp_integration.py tests/test_agent_loop.py -q --basetemp 'C:\Users\Snowprint\.codex\visualizations\2026\10\23\01a16918-f4f0-7c81-9aa0-bbff77866d32\day12-agent-green02'
.\.venv\Scripts\python.exe -m examples.subagents.demo
.\.venv\Scripts\python.exe -m compileall -q mewcode/agents mewcode/tools/agent_tool.py tests/test_subagent_lifecycle.py examples/subagents
```

先前沙箱因 Windows 临时目录 ACL 失败；主代理在可写隔离目录以当前宿主权限重跑，取得上面的新门禁输出。再次复现测试时换用新的专用 basetemp，不将源码或用户目录作为 pytest 清理目标。

## 最小修复与学习要点

统一 done callback 持有最终收尾责任，覆盖尚未进入协程的取消；runner 处理工作状态，callback 处理资源、Token、Trace 和一次性通知。`shutdown()` 取消并 await 所有归属 runner，历史结果保留给 `/tasks info`。

取消请求与取消完成是两件事。测试需要等待真实 runner 结束，确认 Provider 流已经关闭，再检查资源计数。Event 表达状态依赖；固定秒数 sleep 只会让竞态变得偶尔不出现。

能力复制还包含状态。父 ToolRegistry 中“有这个工具”与“允许执行这个工具”不同，deferred 工具是否已发现也影响实际 schema。复制时必须保持这些约束，且子表不能修改父表。

独立审阅进一步发现子 Agent 的文件/Bash 工具仍共享父工具实例，真实相对写入会落错工作目录。六个执行工具现在按子 Agent 克隆，缓存、FileHistory 和沙箱根随其工作目录绑定。该隔离在 Day14 的双 Agent 邻接测试中以真实文件写入验证。

通知去重由单次收尾和队列消费保证，inject 函数本身不是持久化去重存储。测试对第二次 poll 断言为空，避免误把同一列表重复注入的行为当作支持范围。

## 交付与限制

- 新增 `tests/test_subagent_lifecycle.py`：16 个用例。
- 新增 `examples/subagents/`：Fake Provider，真实 ReadFile 闭环，可直接运行。
- 技术说明：`docs/subagents-background.md`。
- 本工作单元按协作分工未操作 Git；提交由主代理在独立审阅与门禁后完成。
- 宿主退出调用 `TaskManager.shutdown()` 由主代理接入；共同维护的 roadmap/testing 文档由主代理更新。
- 不声称真实模型、MCP、终端 pane 或任意 OS 子进程树已经验证。此次 Windows 实测证明 asyncio runner/流取消回收；Worktree 与完整双 Agent 团队由 Day13/14验收。
