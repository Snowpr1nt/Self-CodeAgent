# Day 7：MCP 与 ToolSearch

## 今日目标

理解外部 MCP 服务如何成为 Agent Tool，用 Fake MCP 完成注册、发现、调用和关闭闭环，并执行第一次全量回归审计。

## 完成事项

- 创建独立分支 `feature/day-07-mcp-tool-search`。
- 审阅 stdio/HTTP Client、Manager、Wrapper、ToolRegistry、ToolSearch 和入口生命周期。
- 新增 Fake MCP 端到端测试，覆盖 instructions、Schema 隐藏、发现、调用与 shutdown。
- 将 MCP 工具名统一为 `mcp__server__tool`。
- 修复 `select:` 可发现已禁用延迟工具的问题。
- 修复 MCP 调用异常只改存活标记、未关闭旧传输栈的问题。
- 执行 614 项全量收集和第一次整包回归，区分功能失败与 Windows 沙箱 ACL 失败。

## TDD 记录

### 循环一：跨模块名称必须是协议

- **RED**：Wrapper 生成 `mcp_docs_search`；TUI、Remote 和 SubAgent 过滤器只识别 `mcp__docs__search`。
- **表现**：Fake MCP 注册后按规范名取不到工具，自定义 SubAgent 白名单会误删 MCP 工具。
- **GREEN**：Wrapper 使用双下划线规范，既有消费者无需分叉适配。

### 循环二：禁用状态优先于发现

- **RED**：关键词搜索会跳过 disabled，`select:` 却仍返回 Schema 并标记 discovered。
- **GREEN**：`find_deferred_by_names()` 与关键词路径都跳过禁用工具。
- **价值**：能力禁用不能通过另一种搜索语法绕过。

### 循环三：失败连接必须释放

- **RED**：`call_tool` 抛出传输错误后只写 `_alive=False`，Fake Client 的 close 计数仍为 0。
- **GREEN**：异常路径等待 `close()`，保留原始错误文本并让后续重连从干净状态开始。

## 测试结果

- RED：28 passed、7 failed，七项都对应上述三个缺陷。
- MCP、ToolSearch、SubAgent、Teams 邻接回归：144 passed。
- 最终新增集成与 ToolSearch 快速门禁：15 passed。
- 全量收集：614 tests、1 个既有未知 marker warning。
- 首次整包绿色门禁排除三条已登记红线后，得到 565 passed、2 skipped、3 deselected；另有 44 failed，均为受限 Windows 沙箱对 `0700` 临时目录拒绝访问导致的 `PermissionError`，不是断言回归。

尝试在非沙箱环境重跑整包和 ACL 受影响分组时，执行审批被拒绝，因此没有把这 44 项记为通过。相关 Day 7 模块均已在获批隔离环境中通过，完整绿色整包仍需可正常访问临时目录的终端。

## 今日技术理解

- MCP 是动态 Tool Provider；Manager 负责连接容错，Wrapper 负责协议适配，Registry 负责能力状态。
- 工具名属于跨模块 API，生产者和所有消费者必须共享同一命名约定。
- 延迟加载优化的是每一轮 Prompt 成本，不是程序启动时是否创建工具实例。
- disabled 与 discovered 是正交状态：禁用优先，发现不能恢复权限。
- 连接失败后的“状态重置”和“资源释放”必须原子地发生。
- 全量测试要区分产品断言、测试代码缺陷和执行环境故障；不能把 `PermissionError` 当回归，也不能把 aborted 当通过。

## 下一步

Day 8 聚焦 Context 与 Recovery：工具结果预算、落盘预览、压缩边界、断路器和恢复附件。
