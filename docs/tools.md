# Tool 抽象与核心工具

## 1. 调用链

`ToolRegistry` 是模型能力与本地实现之间的边界：它保存工具实例、控制启停与延迟发现，并按 Provider 输出工具 Schema。Agent 收到结构化工具调用后，通过名称找到 `Tool`，用其 Pydantic 参数模型校验输入，再执行异步 `execute()`。

```text
LLM tool call
  -> ToolRegistry.get(name)
  -> params_model.model_validate(arguments)
  -> Permission / Hook
  -> Tool.execute(params)
  -> ToolResult(output, is_error)
  -> Conversation tool_result
```

## 2. 六个核心工具

| 工具 | 类别 | 并发安全 | 主要职责 |
|---|---|---:|---|
| `ReadFile` | read | 是 | UTF-8 读取、行号与分页、建立文件状态快照 |
| `WriteFile` | write | 否 | 新建或覆盖文件；覆盖前要求已读且未被外部修改 |
| `EditFile` | write | 否 | 唯一精确替换并返回可着色 Diff |
| `Bash` | command | 否 | 执行命令、合并输出、处理超时与退出码语义 |
| `Glob` | read | 是 | 按模式查找文件并跳过依赖/缓存目录 |
| `Grep` | read | 是 | 正则搜索内容，可限定文件 Glob |

`Diff` 不是模型直接调用的工具，而是 `EditFile` 的结果表达层。它把真实修改转换为统一 diff、增删行统计和截断后的展示文本。

## 3. 文件一致性协议

默认注册表让三个文件工具共享一个 `FileStateCache`：

1. `ReadFile` 记录绝对路径、完整内容与纳秒级修改时间。
2. `WriteFile` 覆盖现有文件或 `EditFile` 修改文件前检查快照。
3. 修改时间或内容任一变化，都视为外部修改并要求重新读取。
4. 成功写入后刷新状态快照，并使内容缓存失效。

同时比较内容与修改时间可防止“内容已变但时间戳被恢复”的静默覆盖。可选依赖必须使用 `is not None` 判断；`FileCache` 定义了 `__len__`，空缓存的布尔值为假，直接写 `if cache` 会导致缓存永远无法首次填充。

## 4. Bash 退出码

普通命令遵循 `0` 成功、非零失败。部分命令的退出码 1 是业务结果而非执行错误，例如 `grep` 表示无匹配、`diff` 表示文件不同、`test` 表示条件为假。`Bash` 保留退出码提示，并通过命令语义决定 `ToolResult.is_error`，让 Agent 能区分“没有结果”和“命令失败”。

## 5. 测试方法

- 注册表契约：工具集合、类别、并发属性与 Provider Schema。
- 文件协议：未读禁止覆盖、读取后可写、缓存刷新、同时间戳内容冲突。
- 搜索工具：Include 过滤、依赖目录排除与非法正则。
- Bash：用假进程隔离操作系统 Shell，分别验证普通错误和语义性非零退出码。

测试重点是可观察契约，而不是把实现细节逐行复制到断言中。外部边界使用 Fake，核心注册表、缓存和工具实现保持真实。
