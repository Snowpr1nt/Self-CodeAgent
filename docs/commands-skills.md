# Slash Command 与 Skill

Slash Command 把重复的用户请求变成一个简短入口；Skill 把可重复执行的步骤保存在独立 SOP 中。Day 10 的验收关注磁盘发现、注册、命令分发、真实 Agent 收到的输入和 Recovery，全部可用离线模型替身复现。

## 运行示例

从仓库根目录执行：

```powershell
.\.venv\Scripts\python.exe -m examples.commands_skills.run_demo
```

示例源码在 [examples/commands_skills](../examples/commands_skills/README.md)。它在临时项目内创建 `.mewcode/`，隔离用户发现目录，通过真实 loader、registry、handler、SkillExecutor 和 Agent 执行三次请求。固定响应由 `OfflineClient` 产生，不需要 API Key，也不读取个人配置、Memory 或 Teams。

预期 JSON 中 `custom_command` 是 `review:notes`，`model_requests` 是 `3`，`active_skills` 仅含 `evidence-check`，`recovery_skills` 同时包含 `evidence-check` 和 `second-opinion`，`parent_history_preserved` 是 `true`。Windows ACL 限制默认临时目录时，可追加 `--scratch-dir <允许写入的现有目录>`。

## 自定义 Command

搜索路径按以下顺序合并：

| 优先级 | 目录 | 覆盖规则 |
|---|---|---|
| 高 | `<work_dir>/.mewcode/commands/` | 覆盖用户级同名命令的完整定义，包括 aliases |
| 低 | `~/.mewcode/commands/` | 提供跨项目复用命令 |

递归扫描 `.md`，文件路径决定命令名：`review/notes.md` 对应 `/review:notes`，路径中的字母转小写，空格转连字符。YAML frontmatter 可选：

```markdown
---
description: Review notes with source evidence
aliases: [notes]
argument-hint: <topic>
---
Review the notes for $ARGUMENTS.
```

`/notes session recovery` 和 `/review:notes session recovery` 发出同一段完整 prompt。命令名解析不区分大小写；aliases 请使用小写字符串。`argument-hint` 显示在帮助中，TUI 中不带参数执行时会显示该提示并停止本次分发。没有 description 时，使用正文第一行非标题文本。

正文中所有 `$ARGUMENTS` 按字面替换为参数。没有占位符但有参数时，追加 `## User Request` 段落；没有参数时保留原正文。占位符只做文本替换，不执行 shell，不读取额外文件。

TUI 选择 provider 后完成注册。内置命令先注册，自定义 Command 次之，Skill 最后注册。自定义命令的 name 或任一 alias 与已有命令冲突时，整条命令跳过并记录 warning；同名 Skill 不会覆盖 Command。动态内置 `/worktree`、`/tasks`、`/trace` 也先于自定义命令注册。

Command 在启动时加载；编辑 Command 文件后重新启动 TUI。`/skill reload` 只管理技能。当前 `mewcode -p` 是普通非交互 Agent 提示入口，不经过 TUI 的 slash 分发，也没有自动初始化本节的 Command/Skill 系统。

## Skill 定义与加载

项目目录 `<work_dir>/.mewcode/skills/` 优先于 `~/.mewcode/skills/`，同名以项目定义为准；当前没有内置技能。目录下支持三种形态：

```text
.mewcode/skills/
  audit.md                    # YAML frontmatter + SOP
  evidence-check/SKILL.md      # YAML frontmatter + SOP
  second-opinion/skill.yaml    # YAML 元数据
  second-opinion/prompt.md     # 独立 SOP
```

同一技能目录中，`skill.yaml + prompt.md` 优先于 `SKILL.md`。Skill 只扫描技能根目录下的 `.md` 文件和直接子目录；不会像 Command 那样任意递归。

`SKILL.md` / 单文件技能示例：

```markdown
---
name: evidence-check
description: Check a claim against source evidence
mode: inline
context: full
---
For $ARGUMENTS, separate the observed evidence from your inference.
```

`name` 必须以小写字母起始，后续只含小写字母、数字和连字符。`description` 必填；`mode` 为 `inline` 或 `fork`，默认 inline；`context` 为 `full`、`recent` 或 `none`，默认 full。解析错误会记录 warning 并跳过该文件。`skill.yaml` 格式沿用较宽松的兼容解析：可从目录名推断 name、从正文推断 description，非法 mode 回退 inline；建议始终显式填写上述字段。`model` 元数据可被解析和显示，但当前执行器复用已选择的 client，不据此切换模型。

已有 Skill 在调用 `loader.get()` 时重读正文和元数据；这也覆盖 `skill.yaml + prompt.md`。无法重新解析时保留最近一次有效缓存。新增、删除、改名后使用 `/skill reload` 重新扫描、更新目录与 slash 注册；TUI 每次发送用户消息前还会检测技能根目录的修改时间并按需重载。目录深处新增文件不保证改变根目录修改时间，显式 reload 更可靠。

管理命令：

```text
/skill list
/skill info evidence-check
/skill reload
```

## inline、fork 与 Recovery

| 入口 | Agent / 会话行为 | Recovery 行为 |
|---|---|---|
| `/evidence-check <args>`，mode=inline | 展开 SOP，更新父 `active_skills`，完整正文进入父会话并送到下一轮模型 | 保存同一份已展开正文 |
| `/second-opinion <args>`，mode=fork | 后台运行独立 Agent / Conversation，向 UI 显示结果，父会话不加入 fork 历史 | 父与 fork 的 Recovery 都保存已展开 SOP；父 `active_skills` 不激活 fork |
| 模型调用 `LoadSkill(name=...)` | 返回完整 SOP 工具结果并激活父技能；此工具只加载 SOP，不执行 fork，也不替换参数 | 保存原始 SOP，与本次实际激活正文一致 |

inline SOP 作为普通消息进入历史一次，环境提示只含技能目录；不会在每轮环境信息中重复注入完整 SOP。fork 的 `context` 选择如下：

- `none`：只加入本次已展开 SOP。
- `recent`：取父会话最近 5 条有正文且没有工具结果的消息。
- `full`：为父会话每条符合条件的消息取前 200 字符，拼成一条摘要消息。它是简短上下文摘要，并非完整逐字复制。

fork 使用父 client、工具 registry、工作目录、权限策略副本、权限模式、项目 instructions 和 hooks。权限检查器的局部状态独立，因此 fork 的 Plan 路径不会覆盖父 Agent 的 Plan 路径。当前 fork 结果收集器没有交互确认 UI；需要确认的工具请求被明确拒绝，已获允许的操作照常执行。这既保留父策略，也避免后台等待一个无人处理的权限 Future。

Recovery 记录本会话调用过的技能 SOP，供 Context 压缩重建附件；它不是跨会话重新执行命令的队列。附件受技能条数与 token 预算约束。会话持久化/恢复细节见 [Context 与 Recovery](context-recovery.md) 和 [Session 与 Memory](session-memory.md)。

## 验收与调用链

```text
磁盘 .md / skill.yaml
  -> CommandLoader / SkillLoader
  -> CommandRegistry（内置 > 自定义 Command > Skill）
  -> parse_command -> handler
  -> 完整 prompt / SkillExecutor
  -> 真实 Agent 输入、工具权限、UI 结果
  -> RecoveryState -> build_recovery_attachment
```

`tests/test_commands_skills_integration.py` 以临时项目和隔离 Home 验证：项目覆盖、alias、namespace、参数替换、无占位符追加、Command/Skill 优先级、inline 模型可见性、LoadSkill 恢复、YAML 热更新、多 registry 热重载隔离、fork 的三个上下文模式、父权限与示例可运行性。仅外部模型使用确定性的离线 client；loader、registry、handler、Agent、Conversation、Recovery 与文件权限检查均使用真实组件。

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_commands.py tests/test_skills.py tests/test_commands_skills_integration.py tests/test_recovery.py -q --basetemp <专用临时目录>
```
