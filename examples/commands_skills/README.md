# Day 10：Command / Skill 离线示例

从仓库根目录运行（使用项目虚拟环境）：

```powershell
.\.venv\Scripts\python.exe -m examples.commands_skills.run_demo
```

如临时目录受到 Windows ACL 限制，可传入已获允许的目录：

```powershell
.\.venv\Scripts\python.exe -m examples.commands_skills.run_demo --scratch-dir <允许写入的目录>
```

脚本把本目录中的 `commands/`、`skills/` 复制到一次性项目的 `.mewcode/`，并将用户级发现路径指向同一沙箱中的空目录。它不读取真实配置、Memory、Teams，不联网，也不需要 API Key。`OfflineClient` 只记录真正送到 Agent 的输入并返回固定响应；输出不代表真实模型完成了证据分析。

运行路径：

1. `/notes session recovery`：通过 alias 找到 `review:notes`，展开 `$ARGUMENTS`，真实 Agent 收到完整 prompt。
2. `/evidence-check session recovery`：inline 技能激活，展开的 SOP 进入父会话并记录 Recovery。
3. `/second-opinion session recovery`：从 `skill.yaml + prompt.md` 加载 fork 技能；独立会话接收最近历史，结果返回 UI，父历史保持不变。
4. 输出 JSON：`model_requests` 为 `3`，`active_skills` 仅含 `evidence-check`，`recovery_skills` 包含两个技能，`parent_history_preserved` 为 `true`。

这里不用 `.mewcode/` 保存源码资产，因为仓库忽略该目录；普通 `commands/`、`skills/` 可以直接纳入版本控制。若要在真实 TUI 使用，把二者复制到目标项目的 `.mewcode/commands/`、`.mewcode/skills/`，重新启动 TUI 后执行以上命令。详见 [Command / Skill 说明](../../docs/commands-skills.md)。
