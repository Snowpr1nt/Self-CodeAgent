# SubAgent 与后台任务

Day12 的用户价值：让一个明确范围的子任务在前台返回结果或后台运行，并保证成功、失败、取消都能结束追踪和释放异步任务。离线演示验证真实 Agent 循环及工具调用，Provider 使用 Fake，不代表在线模型验收。

## 调用链

`AgentTool.execute` 选择 AgentDef 或 fork → 选择 Client → 过滤工具 → 建立权限检查器与子 Agent → 建立 TraceNode。

- 指定 `subagent_type`：新 Conversation；普通任务前台执行，`run_in_background` 或定义中的 `background` 可切到后台。
- 不指定类型且启用 fork：深拷贝父 Conversation，补齐尾部未完成 tool_use 的 interrupted 结果，附加 fork 指令；fork 始终后台执行。父 Conversation 不被修改。
- 普通子 Agent 移除 Agent、AskUserQuestion、Plan 等工具；后台再应用允许列表。MCP 工具按现有设计单独放行。
- fork 保持父工具定义，浅拷贝 AgentTool 并标记 query_source；再次 fork 会明确拒绝。
- 所有注册表复制保留父表 disabled 与 discovered 状态，子表后续状态变更不反向修改父表。fork 的 deferred 工具 schema 因而保持一致。
- 六个文件/Bash 执行工具为子 Agent 拥有独立实例、读前编辑缓存和工作目录；FileHistory 及 Bash 沙箱根绑定到子工作目录。

前台 `run_to_completion` 返回 ToolResult，异常返回错误结果，取消继续传播 `CancelledError`。后台通过 `TaskManager.launch` 创建 asyncio.Task 并立即返回 Task ID。

## 生命周期与资源归属

`TaskManager` 同时保存任务历史 `_tasks` 和正在运行的句柄 `_async_tasks`。历史供 `/tasks info` 展示，完成后不自动删除；运行句柄、cancel 回调在收尾后释放。二者不是同一个生命周期。

后台 runner 执行实际任务，记录 completed/failed/cancelled 状态。统一 done callback 完成最终收尾：记录结束时间与 Token、移除运行句柄、清空 cancel、同步 Trace，再入通知队列。收尾只执行一次。

选择 Task done callback 的原因：`create_task` 后同轮取消时，协程可能还没有开始，协程自身的 `try/finally` 根本不会执行。仅靠协程 finally 会留下永远 running 的历史、未回收句柄和缺失通知。launch 与 adopt_running 都必须覆盖这个边界。

`cancel(task_id)` 表示已请求取消，不是同步收尾完成。`shutdown()` 取消并 await 当前归属的所有 runner；返回后这些任务的清理和通知已完成，可重复调用。宿主应停止新的任务提交后调用它。它不会强制终止吞掉取消的第三方协程。

团队子任务初始回答完成后还可能等待邮箱消息，因此后台状态保持 running，直到整个 runner 退出，等待期间仍可取消。队友活跃/空闲属于 TeamManager 的另一层状态。

## Trace 与通知

普通后台、fork 与进程内 teammate 的 launch 显式传入 TraceManager。最终状态与输入/输出 Token 在同一个 callback 同步。前台取消更新 cancelled 并继续传播取消，避免 Trace 永远 running。

`poll_completed()` 消费队列中的最终任务，同一 Task ID 只由收尾入队一次，第二次 poll 为空。调用方将返回列表交给 `inject_task_notifications()` 写入 Conversation。该函数本身不存持久化去重状态；重复手动注入同一列表会重复写入。结果文本按现有规则截断到 5000 字符。

失败与取消同样需要通知，因为父 Agent 必须知道委派任务没有成功。通知只在 runner 真正结束后产生，不在用户提出取消请求时提前生成。

## 验证与演示

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_subagent_lifecycle.py tests/test_subagent.py tests/test_mcp_integration.py tests/test_agent_loop.py -q --basetemp <新的专用临时目录>
.\.venv\Scripts\python.exe -m examples.subagents.demo
```

新增测试用 Event 控制 Fake Client 的开始与释放，通过真实 Agent/Conversation 验证成功、失败、启动前取消、运行中取消、Trace/Token、通知消费、adopt、shutdown、team 空闲取消与工具状态复制。不使用真实网络或固定时长等待；`sleep(0)` 仅用于让已注册的 asyncio done callback 得到调度。

范围限制：此处验证 Python asyncio 任务回收；演示不启动系统子进程，不声称覆盖第三方工具创建的任意 OS 进程树。真实 Provider、外部 MCP 和终端 pane 后端需要各自的在线/环境验收。Worktree 的创建与保留策略由 Day13 验收，完整双 Agent 协作由 Day14 验收。
