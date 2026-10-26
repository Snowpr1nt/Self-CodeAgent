# Day 9–14 执行计划

用户已批准按既定路线连续实施。本计划细化交接报告，不改变范围；在当前开发链保留 WIP，每单元独立验证、英文提交，不推送、不合并 main。

- [x] Day 9：`agent.py`、`context/manager.py`、Session/TUI 调用链。先以真实压缩复现共享目录清理；验证后台记忆、命令切换和 Recovery 重启往返，再最小修复。门禁覆盖 Session/Memory/Context/Command/Agent Loop，更新主题、日志与账本。
- [x] Day 10：commands/skills 真实 Markdown 加载、注册、参数和 inline/fork 执行；交付 examples 和端到端测试，审查 Recovery 激活状态。
- [x] Day 11：三个工具执行入口统一遵守 pre/post Hook；解除三条红线，以跨平台 Python 子进程验证真实超时并收尾；联合 Permission/Hook/Bash/Agent 门禁。
- [ ] Day 12：agents/TaskManager/AgentTool，使用事件控制的 Fake Provider 验证前台、后台、失败、即时取消、通知去重和资源回收。
- [ ] Day 13：worktree 模块，仅在专用临时 Git 仓库验收改动、无 upstream/未推送提交保护、创建恢复与安全清理。
- [ ] Day 14：隔离 Teams 数据的双 Agent 离线演示；全量 pytest、语法、CLI/TUI Smoke、Diff 审阅，交付讲解稿和学习总结。

实现前写能捕获错误行为的测试并观察 RED；已有正确行为只需验收，不能为制造 RED 改坏代码。每单元先审阅实际 Diff、运行门禁再提交。测试根目录使用本轮仓库外可写 visualization 目录；真实凭据与运行配置不进入日志或 Git。未知中断不计成功，幂等命令按交接恢复约定处理。
