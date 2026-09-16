# MCP 与 ToolSearch

## 1. MCP 在项目中的位置

MCP 把外部服务提供的工具动态接入 MewCode。核心 Agent 不理解 stdio、HTTP 或 MCP 消息格式，只看到普通 `Tool`：

```text
config.mcp_servers
  -> MCPManager
  -> MCPClient.connect / initialize / list_tools
  -> MCPToolWrapper
  -> ToolRegistry（延迟隐藏）
  -> ToolSearch（发现并加载 Schema）
  -> Agent 调用 wrapper.execute
  -> MCPClient.call_tool
```

这种适配让权限、参数校验、Agent 事件和 Conversation 复用现有工具协议。

## 2. 组件职责

| 组件 | 职责 |
|---|---|
| `MCPServerConfig` | 描述 stdio command/args/env 或 HTTP url/headers |
| `MCPClient` | 管理传输、ClientSession、初始化结果、工具枚举和关闭 |
| `MCPManager` | 逐服务器连接、部分失败隔离、工具注册和统一 shutdown |
| `MCPToolWrapper` | 将 MCP Tool 转为 MewCode Tool，转换参数与结果 |
| `ToolRegistry` | 保存工具、启停、发现状态和 Provider Schema |
| `ToolSearchTool` | 关键词或 `select:` 查找延迟工具并标记 discovered |

stdio 子进程只继承 `PATH` 和配置显式声明的环境变量，避免把宿主 API Key 无意传给第三方服务。HTTP headers 支持 `${ENV_VAR}` 展开。

## 3. 统一工具名称

MCP 工具采用：

```text
mcp__<server-name>__<tool-name>
```

双下划线不是外观偏好，而是跨模块协议。TUI/Remote 的 MCP 状态、Coordinator/SubAgent 工具过滤和命令展示都以 `mcp__` 识别 MCP 工具。Wrapper 曾生成 `mcp_server_tool`，导致工具虽然注册成功，却无法被这些模块识别；Day 7 将生产端统一到既有消费者协议。

## 4. 延迟发现

MCP 服务器可能暴露几十到上百个工具。若每轮都发送完整 Schema，会消耗上下文并降低模型选工具的稳定性。因此 Wrapper 默认 `should_defer=True`：

1. 注册后工具可执行实例已存在，但 Schema 不进入 LLM 请求。
2. System reminder 仅列出尚未发现的工具名。
3. 模型调用 `ToolSearch(query="select:mcp__docs__search")`。
4. ToolSearch 返回完整 Schema 并在 Registry 标记 discovered。
5. 下一轮 `get_all_schemas()` 才包含该工具。

关键词模式按名称和描述打分；`select:` 用于确定性加载。被显式禁用的工具必须在两种查找方式中都不可发现，否则 ToolSearch 会错误宣称能力已加载。

## 5. 错误与生命周期

- 单个服务器连接或列举工具失败，只记录到 `ConnectResult.errors`，不阻塞其他服务器。
- `initialize` 返回的 instructions 进入系统提醒，帮助模型理解服务用法。
- Wrapper 调用前若连接失活，会尝试重连。
- `call_tool` 发生传输异常时，先关闭旧 ClientSession/ExitStack，再返回错误 ToolResult；下一次调用从干净连接重建。
- 应用退出时由 Manager 关闭全部客户端并清空注册的连接引用。

只把 `_alive` 改成假会遗留旧传输栈；连接状态与资源释放必须同时发生。

## 6. Fake MCP 集成测试

Fake Client 保留真实 Manager、Wrapper、Registry、ToolSearch 和 SubAgent 过滤器，只替换网络/子进程边界。完整场景验证：

1. Manager 连接并读取 instructions 与工具列表。
2. Wrapper 以规范名称注册且初始 Schema 隐藏。
3. ToolSearch 精确发现工具。
4. Pydantic 参数模型验证输入并调用原始 MCP 工具名。
5. MCP 文本结果转换为 `ToolResult`。
6. Manager shutdown 关闭 Fake Client。

另有负向测试验证禁用工具不可发现、调用失败会关闭连接，以及自定义 SubAgent 白名单不会误删 MCP 工具。
