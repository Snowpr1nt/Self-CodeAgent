# Permission、Plan 与 Sandbox

## 1. 为什么需要多层防线

Tool 能读文件、改代码和执行 Shell。模型生成参数不等于用户授权，因此执行前必须把“工具想做什么”转换为可审计的 `allow`、`ask` 或 `deny` 决策。MewCode 将确定性安全规则、项目路径边界、用户规则和运行模式叠加，而不是依赖单个 Prompt。

## 2. 决策顺序

`PermissionChecker.check()` 按优先级短路：

1. **Plan 例外**：只允许计划期系统工具，以及当前会话精确配置的计划文件。
2. **安全只读命令**：已知无副作用的命令直接允许。
3. **危险命令**：命中硬黑名单立即拒绝，即使处于 bypass 模式。
4. **OS Sandbox 命令**：启用内核隔离时可自动允许，但显式 deny/ask 规则仍优先。
5. **PathSandbox**：文件和搜索工具必须在允许根目录内；禁写路径只约束写操作。
6. **RuleEngine**：用户、项目、本地三级规则按层级和“同层最后一条”匹配。
7. **会话放行**：用户选择本次会话不再询问后，加入内存规则。
8. **模式矩阵**：没有更具体规则时按模式与工具类别决定。
9. **HITL**：最终的 `ask` 通过 `PermissionRequest.future` 等待用户回答。

越具体、越安全关键的判断越早执行。危险黑名单不能被 bypass，禁写配置不能被 accept-edits，显式 ask/deny 不能被 OS Sandbox 自动放行覆盖。

## 3. 模式矩阵

| 模式 | read | write | command | 典型用途 |
|---|---|---|---|---|
| `default` | allow | ask | ask | 日常开发，副作用逐次确认 |
| `acceptEdits` | allow | allow | ask | 连续编辑，Shell 仍确认 |
| `plan` | allow | ask | ask | 只探索与写精确计划文件 |
| `bypassPermissions` | allow | allow | allow | 外部隔离已充分时使用；危险黑名单仍生效 |

矩阵只是兜底，不会覆盖前面更高优先级的安全判断。

## 4. 路径与规则是两个维度

`RuleEngine` 需要匹配用户意图内容：Bash 匹配 `command`，Glob/Grep 匹配 `pattern`。`PathSandbox` 则必须检查真实访问位置：文件工具检查 `file_path`，Glob/Grep 检查 `path`。

如果复用同一个提取字段，`Grep(pattern="TODO", path="C:/outside")` 会把 `TODO` 当路径检查，从而漏掉越界搜索。实现因此保留两套提取逻辑：规则内容不变，沙箱路径单独提取。

## 5. PathSandbox

PathSandbox 会：

- 将相对路径绑定到项目根目录，而不是进程当前目录。
- 解析已存在路径的真实位置，阻止符号链接逃逸。
- 对尚不存在的路径解析最近存在祖先，再恢复剩余路径。
- 允许项目根、系统临时目录和显式额外根。
- 仅在 `write=True` 时应用 `.mewcode/config.yaml`、本地权限文件和 Skills 目录等禁写项。

读取配置是 Agent 理解项目所必需的；禁止的是未经确认地修改敏感配置，而不是查看它。

## 6. Plan 文件例外

Plan 模式的写权限必须绑定当前会话生成的精确路径。仅比较文件名或搜索 `.mewcode/plans/` 字符串会造成越权：攻击者可在项目外构造同名路径或伪造目录片段。

正确判定流程是：

1. 目标路径与配置路径都相对项目根解析。
2. 比较解析后的完整路径。
3. 即使完全相同，仍通过写路径沙箱。
4. 其余写操作继续进入普通 ask/deny 流程。

## 7. 测试安全矩阵

- 精确计划路径允许；同名外部文件与伪造目录片段询问。
- Glob/Grep 的外部搜索根询问，而不是检查 pattern。
- 敏感配置可读；即使 accept-edits 也不能自动写。
- 危险命令在 bypass 中仍拒绝。
- 显式 allow/ask/deny 规则与模式矩阵组合验证。
- 端到端测试通过 `PermissionRequest` 模拟用户允许或拒绝，并确认 Agent 循环继续。
