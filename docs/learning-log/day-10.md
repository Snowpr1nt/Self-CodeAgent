# Day 10：Slash Command 与 Skill

## 今日目标

让用户能从项目 Markdown 文件定义可重复的命令和 SOP，并通过可运行的离线示例看到：文件发现 → 注册 → 参数展开 → 真实 Agent 输入 → Recovery。延续已批准的 Day 9–14 计划，不增加新的交互式规划步骤。

## 调查与方案

先读取 `MEWCODE.md`、路线图和既定实施计划，沿 loader、registry、handler、SkillExecutor、Agent 和 RecoveryState 追踪真实调用链。采用局部接通与修复，保留现有模块边界；不改写 Agent 主循环，也不扩展非交互 `-p` 入口。

发现的根因：

1. `load_user_commands()` 没有生产调用方，自定义 Command 文件能够解析却未接入 TUI。
2. inline slash 只发送用户参数，SOP 虽记录在 `active_skills`，环境信息却按设计不注入完整 SOP，因此首轮模型看不到它。
3. `skill.yaml + prompt.md` 的热更新被误交给 frontmatter 解析器，普通 prompt 正文解析失败后永远回退旧缓存。
4. 模块全局的技能注册名称集合跨多个 registry 共享，第二个应用会删除同名自定义 Command，并让第一个应用无法清理已删除 Skill。
5. `LoadSkill` 激活正文未记录 Recovery，fork 则记录了未展开的 `$ARGUMENTS` 模板。
6. fork 读取不存在的 `_conversation` 属性，实际父 Agent 使用 `_current_conversation`，因此 full/recent 丢失父历史。
7. fork 显式丢弃父权限检查器，PLAN/default 下均能直接写文件；继承检查器后还须处理无人接收的权限确认 Future。

## TDD 证据

编写测试前读取 TDD 的 `writing-good-tests.md`，为每项断言命名其捕获的真实错误。只替代外部 LLM 流，文件发现、注册、分发、Agent、Conversation、Recovery、PermissionChecker 和写文件工具使用真实组件。

首轮 RED：`tests/test_commands_skills_integration.py` 的 11 项全部失败，`11 failed in 2.73s`。失败分别指出缺少注册入口、模型只收到参数、Recovery 为空、YAML 使用旧版本、跨 registry 删除、fork 历史丢失以及实际越权文件写入。

按组最小修复后，定向 GREEN 分别为 `1 passed`、`3 passed`、`2 passed`、`5 passed`。另补充已正确行为的验收：namespace、无占位符参数追加、Command 优先于同名 Skill，以及发布示例的实际执行；没有为这些既有行为制造假 RED。

补充审阅发现：共享父 checker 会让 fork 的 Plan 初始化覆盖父 Plan 路径。先增加“运行后父路径保持原值”的断言，观察 `1 failed, 1 passed, 12 deselected in 2.50s`，再改为继承 checker 的浅副本；最终联合门禁覆盖并通过该断言。

## 实现与交付

- TUI 在全部静态、动态内置命令后注册自定义 Command，再注册 Skill；冲突保留已有命令并记录 warning。
- inline handler 将展开后的完整 SOP 作为消息发送，继续记录激活状态和 Recovery。
- 注册名称按 registry 保存，使用弱引用避免进程级交叉污染。
- YAML 技能热加载读取同一目录的元数据和正文；LoadSkill/fork 记录与实际执行一致的正文。
- fork 使用真实父历史，继承权限策略副本、模式、instructions 和 hooks；无人处理的权限请求被明确拒绝，父 Plan 路径保持不变。
- 可跟踪示例放在 `examples/commands_skills/` 的普通目录中，执行时才复制为临时 `.mewcode/`，避免源码资产被仓库忽略。
- 使用说明为 `docs/commands-skills.md`，明确当前不支持的行为：Command 需重启重载，`model` 元数据不切换 client，`LoadSkill` 只加载 SOP，`-p` 不走 TUI slash 初始化。

## 验证结果

2026-10-24 恢复执行后，先检查当前共享文件状态，确认所有 Day 10 改动仍在，再运行以下命令：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_commands.py tests/test_skills.py tests/test_commands_skills_integration.py tests/test_recovery.py tests/test_context_recovery_integration.py tests/test_session_recovery.py -q --basetemp 'C:\Users\Snowprint\.codex\visualizations\2026\10\23\01a16918-f4f0-7c81-9aa0-bbff77866d32\day10-agent-final-02'
```

结果：**116 passed in 3.27s，exit 0**。

```powershell
.\.venv\Scripts\python.exe -m examples.commands_skills.run_demo --scratch-dir 'C:\Users\Snowprint\.codex\visualizations\2026\10\23\01a16918-f4f0-7c81-9aa0-bbff77866d32'
```

结果：**exit 0**。`custom_command=review:notes`，`model_requests=3`，父激活状态仅含 inline 技能，Recovery 含 inline/fork 两个调用，`parent_history_preserved=true`。

所有用户级 Skill 发现路径指向隔离测试目录，没有读取真实配置、Memory 或 Teams。首次受限运行遇 `PermissionError: WinError 5`，按工具允许提权重跑后才记录业务 RED。最后的 Plan 状态 GREEN 命令一度因自动审批额度服务错误未执行；没有将该中断计作通过，也没有绕过审批。环境恢复后的上述联合门禁才是最终成功证据。

## 今日技术理解与边界

“已加载”“已激活”“模型实际收到正文”和“压缩后能够恢复”是四个不同状态，必须分别观察。全局名称缓存不能代表每个 UI 实例的注册所有权。fork 隔离会话并不自动继承父策略；复制策略时也需保护父策略对象上的局部可变状态。

本日没有执行 Git 写操作，Day 10 的 Diff 审阅和提交由主代理统一完成。完整 TUI 的离线启动 Smoke 与跨日全量 pytest 留在 Day 14 联合验收。
