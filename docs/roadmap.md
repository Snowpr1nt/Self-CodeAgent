# MewCode Python 14 天开发路线图

默认每天约 4 小时：45 分钟阅读与建模、120 分钟 TDD 开发、45 分钟验证、30 分钟 Diff 审阅与学习记录。

| 状态 | 天数 | 主题 | 验收产物 |
|---|---:|---|---|
| ✅ | 1 | Git、uv、架构与测试基线 | 原始源码标签、锁定环境、架构图、测试报告、学习日志 |
| ✅ | 2 | 配置与 LLM Client | Mock Provider 流式测试；真实 API Smoke 等待测试 Key |
| ✅ | 3 | Conversation、Prompt 与入口 | 非交互 text/stream-json 场景 |
| ✅ | 4 | Tool 抽象与六个核心工具 | 文件、Diff、Glob、Grep、Bash 定向测试 |
| ✅ | 5 | ReAct Agent Loop | Mock LLM 多轮工具闭环 |
| ✅ | 6 | Permission、Plan 与 Sandbox | allow/ask/deny 安全矩阵 |
| ✅ | 7 | MCP 与 ToolSearch | Fake MCP 集成和首次全量回归 |
| ✅ | 8 | Context 与 Recovery | 压缩边界、落盘和恢复测试 |
| ✅ | 9 | Session 与 Memory | 重启、后台记忆、Recovery 附件、会话清理隔离；170 项门禁 |
| ✅ | 10 | Slash Command 与 Skill | 自定义 Command/Skill 端到端示例；116 项邻接门禁 |
| ✅ | 11 | Hook 与跨平台测试 | 三个工具入口、进程树收尾与 Windows 锁探测；157 passed、2 skipped |
| ✅ | 12 | SubAgent 与后台任务 | 前台/后台结果、Trace、通知、取消与回收；82 passed |
| ⬜ | 13 | Git Worktree | 隔离开发、变更检测和安全清理 |
| ⬜ | 14 | Agent Teams 与最终集成 | 双 Agent 演示、全量测试、项目讲解稿 |

## 每日执行闭环

1. 用一句话定义用户价值和非目标。
2. 让 Agent 先定位调用链和测试入口，不立即改代码。
3. 写出可观察的失败测试。
4. 实现最小改动并重构重复逻辑。
5. 运行定向测试，必要时运行全量测试。
6. 审阅 `git diff`，记录 Agent 的正确判断、错误判断和人工决策。
7. 形成一个可回退提交和一篇学习日志。
