# Day 3：Conversation、System Prompt 与入口

## 今日目标

理解 Provider 无关的 Conversation 如何与 System Prompt 配合，并为非交互 `text` / `stream-json` 输出建立无需真实 API 的回归测试。

## 完成事项

- 创建独立分支 `feature/day-03-conversation-entrypoints`。
- 审阅 Conversation 注入、Prompt 分段、Agent 事件和 CLI/TUI/Remote 分流。
- 新增 `PromptEventWriter`，将非交互输出状态机从 CLI 启动编排中分离。
- 为 NDJSON 增加 Retry 等待时间、压缩前 Token 数和 Hook 事件。
- 修复并发工具结果错误标记到最后一次工具调用的问题。
- 修复 `Agent.run()` 与 `run_to_completion()` 的 System Prompt 工作目录错误。
- 新增 7 个入口测试并完成邻接与全量回归。

## TDD 记录

### 循环一：可测试的非交互输出边界

- **RED**：测试导入 `mewcode.noninteractive.PromptEventWriter` 时触发 `ModuleNotFoundError`。
- **GREEN**：实现只负责事件到 stdout/stderr 转换的写入器，并让 `_run_prompt()` 委托给它。
- **验证**：text 缓冲、NDJSON、错误通道、控制事件和权限响应共 5 项先行通过。

### 循环二：真实工作目录进入 System Prompt

- **RED**：暂时撤去 `work_dir` 传递后，两条测试都显示提示词仍为 `Working directory: .`。
- **GREEN**：`Agent.run()` 与 `run_to_completion()` 都向 `build_system_prompt()` 传入 `self.work_dir`。
- **验证**：两条调用路径都记录到测试临时项目的绝对路径。

### 循环三：并发工具结果归属

- 静态审阅发现旧 CLI 总是更新 `tool_calls[-1]`，无法处理结果乱序。
- 回归场景先声明两个工具，再让第一个工具返回错误。
- 写入器维护 `tool_id -> index` 映射，最终汇总只标记真实失败的工具。

## 测试结果

- Day 3 新增入口测试：7 passed。
- 入口、Conversation 序列化与 Agent 邻接回归：32 passed、1 deselected。
- 全量回归：587 passed、3 failed、2 skipped、1 warning。
- 排除三条已知红色基线后的门禁：587 passed、2 skipped、3 deselected。
- 总收集数由 585 增加到 592，未新增失败项。

## 测试环境经验

第一次全量测试把 `--basetemp` 放在仓库内，并沿用不可写的沙箱用户目录，额外触发了指令向上查找和 Team 状态目录权限错误。将 pytest 临时目录与 `HOME` / `USERPROFILE` 指向仓库外的可写隔离目录后，结果恢复为原有三条失败。这说明测试隔离不仅是清理文件，也包括控制目录搜索边界和用户级状态位置。

## 今日技术理解

- Conversation 是领域模型，Provider 消息只是传输格式；不要让线上协议污染核心状态。
- System Prompt 与对话历史承担不同优先级和生命周期，不能简单合并为一段字符串。
- 入口层应只做装配和展示；事件格式化独立后，CLI 可以不访问网络完成确定性测试。
- 流式系统不能依赖“结果顺序等于请求顺序”，并发关联必须使用稳定 ID。
- NDJSON 每行都是独立 JSON，适合 shell 管道、日志采集和 CI 逐事件消费。
- 测试替身应放在外部边界：Mock LLM，保留真实 Agent、Prompt 和事件状态机。

## 在线 Smoke 状态

本日没有真实 Provider API Key，因此没有发送线上请求。CLI 参数解析、事件输出和 Mock LLM 端到端路径均已验证；真实鉴权与网络 Smoke 继续等待测试 Key。

## 下一步

Day 4 聚焦 Tool 抽象、注册表和六个核心工具，通过文件、Diff、Glob、Grep 与 Bash 的定向测试理解 Agent 如何安全地改变项目状态。
