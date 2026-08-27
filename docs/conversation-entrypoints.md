# Conversation、System Prompt 与入口技术概要

## 1. 三层职责

MewCode 把一次用户交互拆成三个彼此独立的层次：

| 层次 | 主要模块 | 核心职责 |
|---|---|---|
| 对话状态 | `mewcode/conversation.py` | 保存 Provider 无关的消息、思考块、工具调用和工具结果 |
| 行为提示 | `mewcode/prompts.py` | 组合身份、安全规则、工具规则、环境、项目指令、Skill 与 Memory |
| 交互入口 | `mewcode/__main__.py`、`mewcode/app.py`、`mewcode/remote.py` | 把 CLI、TUI 或 WebSocket 输入转换为 Agent 调用，再把 Agent 事件转换为用户可消费的输出 |

这种拆分让 Conversation 不依赖 Anthropic 或 OpenAI 的线上格式，也让同一个 Agent Loop 可以被三种入口复用。

## 2. Conversation 数据模型

`ConversationManager` 保存 `Message` 列表。每条消息包含以下 Provider 无关字段：

- `role`：内部只使用 `user` 和 `assistant`。
- `content`：普通文本。
- `tool_uses`：工具 ID、名称和结构化参数。
- `tool_results`：工具 ID、文本结果和错误标记。
- `thinking_blocks`：思考内容与 Provider 返回的签名。

发送请求前，`mewcode/serialization.py` 再将这套统一结构转换为不同协议的 wire format。Anthropic 使用内容块，OpenAI Responses 使用 `function_call`，OpenAI Compatible 使用 Chat Completions 的 `tool_calls`。

### 上下文注入顺序

`Agent.run()` 启动时按以下顺序补充上下文：

1. `inject_environment()` 将运行环境插入历史首位。
2. `inject_long_term_memory()` 将项目指令和长期记忆插入环境之后。
3. 原始用户消息和后续对话保持原顺序。

两个注入方法都带有幂等标记，防止每次 ReAct 迭代重复插入。自动压缩替换历史后，标记与 Token 锚点会被重置，Agent 再向摘要后的历史补回必要上下文。

## 3. System Prompt 组合

`PromptBuilder` 按优先级从低到高组合段落：

1. Identity
2. System
3. Doing Tasks
4. Executing Actions
5. Using Tools
6. Tone and Style
7. Text Output
8. Environment
9. Project Instructions
10. Skills
11. Memory
12. Hook Injected Context

System Prompt 与 Conversation 分开传给 LLM Client。前者定义高优先级行为规则，后者保存可增长、可压缩、可序列化的会话状态。

Day 3 修复前，`Agent.run()` 和 `run_to_completion()` 调用 `build_system_prompt()` 时没有传入 Agent 的 `work_dir`，导致提示词始终显示 `Working directory: .`。现在两条路径都传递 `self.work_dir`，模型看到的工作目录与工具沙箱使用的目录保持一致。

## 4. 三种入口

`mewcode.__main__:main` 只负责参数解析、公共配置加载和入口分流：

| 启动方式 | 入口 | 输出通道 | 典型用途 |
|---|---|---|---|
| `mewcode -p "..."` | 非交互 CLI | stdout / stderr | 脚本、CI、一次性任务 |
| `mewcode` | Textual TUI | 终端组件 | 人工对话和权限确认 |
| `mewcode --remote` | WebSocket 服务 | 浏览器事件 | 远程 UI |

三种入口最终都创建 `Agent` 与 `ConversationManager`。差异主要位于事件展示、权限交互和生命周期管理，不应复制 Agent 的业务规则。

## 5. 非交互输出契约

Day 3 将 `-p` 的事件转换从大型启动函数中提取为 `PromptEventWriter`，因此无需真实 API 就能测试输出边界。

### text

- 累积所有 `StreamText`，收到 `LoopComplete` 后一次写入 stdout。
- `ErrorEvent` 写入 stderr，避免污染可管道处理的正常结果。
- 非交互模式收到 `PermissionRequest` 时自动返回 `ALLOW`。这是当前行为，不等同于绕过预先得到的 `deny` 决策；其安全策略将在 Day 6 专门审阅。

### stream-json

每个事件输出一行独立 JSON，即 NDJSON：

| `type` | 主要字段 | 含义 |
|---|---|---|
| `assistant` | `text` | 文本增量 |
| `thinking` | `text` | 思考增量 |
| `tool_use` | `tool_name`、`tool_id`、`args` | 工具调用 |
| `tool_result` | `tool_id`、`output`、`is_error`、`elapsed` | 工具执行结果 |
| `usage` | `input_tokens`、`output_tokens` | 累计 Token 用量 |
| `turn_complete` | `turn` | 一轮工具闭环结束 |
| `retry` | `reason`、`wait` | 重试状态 |
| `compact` | `message`、`before_tokens` | 上下文压缩通知 |
| `hook` | `hook_id`、`event`、`output`、`success` | Hook 执行通知 |
| `error` | `message` | 错误事件 |
| `result` | 最终文本、耗时、轮数、工具摘要、用量 | 本次 Agent Loop 汇总 |

工具结果可能因并发执行而与工具声明顺序不同。写入器使用 `tool_id` 查找对应调用，不再把错误状态误写到最后一个工具上。

## 6. 测试方法

`tests/test_entrypoints.py` 使用两类替身：

- `RecordingClient` 只模拟 LLM 网络边界，记录 Agent 实际发送的 System Prompt。
- `StringIO` 捕获 stdout 和 stderr，向写入器直接输入 Agent 事件。

测试覆盖真实的事件状态转换、Unicode NDJSON、并发工具 ID、控制事件、错误通道、权限响应，以及 `Agent.run()` 和 `run_to_completion()` 两条提示词路径。真实 Provider Smoke 仍需用户提供测试 Key 后执行。
