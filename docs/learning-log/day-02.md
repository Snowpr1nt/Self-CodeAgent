# Day 2：配置与 LLM 流式适配器

## 今日目标

理解 Provider 配置如何变成模型请求，并通过 Mock Provider 测试保证 Anthropic、OpenAI Responses 和 OpenAI Compatible 都输出统一事件。

## 完成事项

- 创建独立分支 `feature/day-02-llm-client`。
- 审阅 `ProviderConfig`、配置分层合并、协议工厂和三类流式客户端。
- 新增 14 个客户端测试，覆盖 API Key 优先级、缺失 Key、SOCKS 初始化和三协议事件转换。
- 修复 OpenAI Responses 忽略 `max_output_tokens` 的问题。
- 修复 OpenAI Compatible 在没有 usage chunk 时缺少 `StreamEnd` 的问题。
- 为 Anthropic/OpenAI SDK 增加 SOCKS 代理运行时依赖。
- 完成邻接回归和全量回归。

## TDD 记录

### 循环一：OpenAI 输出上限

- **RED**：请求记录中不存在 `max_output_tokens`，测试触发 `KeyError`。
- **GREEN**：在 Responses 请求参数中加入 `self.max_output_tokens`。
- **验证**：工具调用增量、缓存 Token 扣除和结束事件序列同时通过。

### 循环二：兼容流结束契约

- **RED**：只返回文本结束 chunk 的兼容 Provider 仅产生 `TextDelta`。
- **GREEN**：记录是否收到 usage 结束块；流耗尽时补 `StreamEnd(end_turn)`。
- **验证**：有 usage 时仍使用真实 Token，没有 usage 时只补一次零用量结束事件。
- **补充边界**：将 Chat Completions 的 `length` 映射为内部 `max_tokens`，保留 Agent 的截断恢复语义。

### 循环三：SOCKS Provider 初始化

- **RED**：三个客户端在 `ALL_PROXY=socks5://...` 环境中都因缺少 `socksio` 构造失败。
- **GREEN**：将依赖改为 `httpx[socks]` 并更新 `uv.lock`。
- **验证**：三个客户端均可构造并正常关闭，不需要忽略系统代理。

## 测试结果

- Day 2 定向测试：14 passed。
- 客户端、Context Window、Serialization、Consolidation 邻接回归：62 passed、1 skipped。
- 全量回归：580 passed、3 failed、2 skipped。
- 3 个失败与 Day 1 记录完全相同，没有新增红色项。

## 今日技术理解

- 适配器模式的价值不只是统一调用函数，更重要的是统一事件和错误语义。
- 流式协议必须保证明确的结束事件；第三方兼容接口不能假设完全实现官方可选字段。
- 配置字段只有真正进入 SDK 请求才算生效，单纯保存在对象属性上无法提供功能。
- Mock 应放在网络边界，真实执行序列化与流式状态机，才能捕获适配层缺陷。
- 依赖锁文件也是运行时契约的一部分；代理支持缺失会在请求发出前就导致应用启动失败。

## 在线 Smoke 状态

本日没有使用真实 Provider API Key，因此不发送线上请求。Mock Provider 已覆盖请求字段和事件转换；真实鉴权、网络和模型响应留待用户提供测试 Key 后单独执行，避免把凭据写入仓库。

## 下一步

Day 3 聚焦 Conversation、System Prompt 与 CLI/TUI/Remote 入口，重点理解同一内部消息如何转换为不同 Provider 的请求格式。
