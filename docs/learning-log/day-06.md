# Day 6：Permission、Plan 与 Sandbox

## 今日目标

建立 allow/ask/deny 的分层安全模型，用回归测试修复 Plan 文件、搜索路径和禁写规则的越权边界。

## 完成事项

- 创建独立分支 `feature/day-06-permission-sandbox`。
- 审阅危险命令、PathSandbox、RuleEngine、模式矩阵和 Agent HITL 调用链。
- 新增 5 个安全边界用例，覆盖精确 Plan 路径、伪造路径、Glob/Grep 根目录和敏感配置。
- 将规则匹配内容与沙箱访问路径拆分提取。
- 让 PathSandbox 显式接收读写意图，禁写项不再误伤读取。
- 收紧 Plan 例外为项目根语义下的完整路径相等，并再次通过写沙箱。

## TDD 记录

### 循环一：Plan 例外不能按文件名或子串授权

- **RED**：项目外同名 `day-06.md` 与包含 `.mewcode/plans/` 的伪造路径都被直接允许。
- **GREEN**：只比较相对项目根解析后的完整配置路径；匹配后仍检查写沙箱。
- **安全意义**：例外权限绑定一个对象，而不是一类看起来相似的字符串。

### 循环二：规则内容不等于访问路径

- **RED**：Glob/Grep 用 pattern 做路径检查，外部 `path` 被默认读模式放行。
- **GREEN**：规则继续匹配 pattern，PathSandbox 独立提取 `path`。
- **安全意义**：授权引擎与资源边界可以使用不同的参数视图。

### 循环三：禁写不是禁读

- **RED**：读取项目 `.mewcode/config.yaml` 也触发 ask。
- **GREEN**：`PathSandbox.check(path, write=False)` 默认只检查根目录；仅写操作应用 deny-write。
- **安全意义**：最小权限不是最大阻碍，过度拦截会诱导用户开启 bypass。

## 测试结果

- Day 6 新安全边界：5 passed。
- 权限模块完整回归：58 passed、1 skipped。
- Permission、Hook、Bash、Agent 联合绿色门禁：142 passed、1 skipped、3 deselected。
- 三条既有红线单独复核：Hook 超时与主 Agent Hook 绕过仍失败；Windows `/tmp` 用例在获批进程中本次通过，测试源码与跨平台缺口均未修改。

## 今日技术理解

- `deny`、`ask`、`allow` 不是简单枚举，而是有优先级的决策证据。
- bypass 只跳过交互确认，不应跳过不可接受的危险命令。
- 路径安全必须基于规范化后的资源位置，字符串包含判断不能承担授权。
- 读写意图属于权限上下文，同一路径对 ReadFile 和 WriteFile 可以有不同结论。
- OS Sandbox 限制进程能力，PathSandbox 限制项目资源范围，两者互补而非替代。
- 安全测试必须包含负向场景，并断言拒绝原因来自正确防线。

## 下一步

Day 7 将进入 MCP 与 ToolSearch，通过 Fake MCP 验证动态工具注册、延迟发现、错误隔离，并安排首次全量回归。
