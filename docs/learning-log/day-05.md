# Day 5：ReAct Agent Loop

## 今日目标

把 Agent 主循环理解为可测试的状态机，验证 Mock LLM 多轮工具闭环，并让 Provider 流异常通过事件边界安全终止。

## 完成事项

- 创建独立分支 `feature/day-05-agent-loop`。
- 梳理环境注入、上下文预算、流收集、工具抢跑、Conversation 拼接和停止条件。
- 新增无文件系统依赖的 Echo 两轮闭环测试。
- 新增“工具已启动后 Provider 断线”的失败测试。
- 为 `StreamingExecutor` 增加待执行任务取消与回收。
- 将 LLM stream 异常转换为 `ErrorEvent`，避免异常穿透入口层。
- 将权限放行测试改用稳定的 `git --version`，避免把仓库状态错误误判为权限错误。

## TDD 记录

### 循环一：确定性 ReAct 闭环

- Scripted LLM 第一轮调用 Echo，第二轮给出最终文本。
- 真实 Agent 产生 `ToolUse -> Usage -> ToolResult -> TurnComplete -> StreamText -> Usage -> LoopComplete`。
- 工具调用和结果使用同一个 `tool_id`，总轮数为 2。

### 循环二：流失败必须收尾

- **RED**：Provider 在发出工具调用后抛出 `NetworkError`，异常直接逃出 `Agent.run()`，Slow Tool 留在后台等待。
- **GREEN**：异常路径取消并 `gather` 所有已提交任务，然后发出单个 `ErrorEvent`。
- **边界**：不捕获 `CancelledError`，外部任务取消仍由调用方观察。

### 邻接回归：权限允许不代表命令必成功

Day 4 修正 Bash 退出码后，旧测试中的 `git status` 因执行环境不是可信仓库而正确返回错误。该测试真正要验证的是规则允许 Git 命令，因此改用环境无关的 `git --version`，没有放宽 Bash 错误语义。

## 测试结果

- Day 5 新增 Agent Loop 测试：2 passed。
- Agent 邻接测试（排除既有 Windows `/tmp` 红线）：18 passed、1 deselected。
- Agent、入口与权限扩展回归：78 passed、1 skipped、1 deselected。

## 今日技术理解

- ReAct 的“观察”不是日志文本，而是下一轮 Conversation 中结构化的 tool result。
- 流式执行优化延迟的同时引入任务生命周期问题；任何流失败都必须取消并等待后台任务。
- 事件流是 Agent 与所有入口之间的稳定协议，预期运行时错误应事件化。
- 部分流文本可以展示但不能冒充完整 assistant 历史，否则重试会污染上下文。
- 测试应把外部 Provider 变成脚本，把核心状态机保留为真实实现。
- 稳定测试只断言自己负责的边界；权限测试不应依赖当前目录恰好是可用 Git 仓库。

## 下一步

Day 6 聚焦 Permission、Plan 与 PathSandbox，建立 allow/ask/deny 安全矩阵，并修复搜索路径与计划文件的越权边界。
