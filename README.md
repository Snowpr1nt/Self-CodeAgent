# MewCode

MewCode 是一个 Python 终端 Code Agent 学习项目。它用模型客户端、持续对话和工具循环完成编码任务，并在执行前应用 Hook、权限与路径约束。项目还包括 Session/Memory、Slash Command/Skill、MCP、SubAgent、Git Worktree 和 Agent Teams。完整的 14 天开发路线已完成本地实现与离线验收。

## 从这里开始

- [项目讲解稿](docs/project-walkthrough.md)：从一条请求读懂入口、Agent Loop、工具、安全与协作模块。
- [14 天路线图](docs/roadmap.md)与[学习总结](docs/learning-summary.md)：查看每天的目标和关键判断。
- [测试账本](docs/testing.md)：查看 RED/GREEN、全量回归和验收边界。
- [架构图](docs/architecture.md)：快速定位源码模块。

当前 Windows 工作区可直接用 `./.venv/Scripts/python.exe -m mewcode --help` 查看 CLI。新环境若已安装 `uv`，可先运行 `uv sync --frozen` 建立锁定依赖。启动交互式程序前需按 [LLM Client 文档](docs/llm-client.md)配置获准使用的 Provider；不要把凭据提交到 Git。

无需 API Key 的本地演示：

```powershell
./.venv/Scripts/python.exe -m examples.subagents.demo
./.venv/Scripts/python.exe -m examples.teams.demo
```

Teams 演示使用两个真实 Agent 实例、共享任务与邮箱，模型响应由 Fake Provider 提供；Alice/Bob 的 20 和 22 合并为 42。Command/Skill 示例见 [examples/commands_skills](examples/commands_skills/README.md)。

## 验证状态

2026-10-26 在 Windows/Python 3.12.6 上，仓库外隔离临时目录中的全量 pytest 为 **731 passed、2 skipped**。跳过项是 Windows 没有 `/etc` 候选文件的符号链接测试，以及缺少 `MEWCODE_TEST_API_KEY` 的真实 LLM 整理测试。`compileall`、CLI `--help` 与双 Agent 离线演示通过。运行方法与历史结果见[测试账本](docs/testing.md)。

真实 Provider/MCP 在线 Smoke、macOS/Linux 内核沙箱、终端 pane 后端及人工交互式 TUI 验收仍需相应环境。此仓库目前没有配置 Git remote；本地提交尚未推送或合并到 `main`。
