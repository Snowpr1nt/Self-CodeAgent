# 双 Agent 离线协作

从项目根目录运行：

```powershell
.\.venv\Scripts\python.exe -m examples.teams.demo
```

Provider 是 Fake；两个 Worker 都执行真实 Agent 循环、共享任务工具和 SendMessage。Alice 把部分结果 20 通过真实邮箱发给 Bob；Bob 确认实际收到消息，再向 lead 提交 22；lead 汇总为 42。两个任务状态从 pending 变为 in_progress、completed，重新打开 tasks.json 核实持久化。

完成后发送结构化 shutdown_request，等待两个 Worker 退出，再删除团队配置、邮箱和名称注册。输出 `running_handles: 0` 和 `team_removed: true`。运行仅使用自动清理的临时工作目录和进程内隔离 home，不访问用户团队数据或需要网络凭据。

本演示直接使用进程内后端，不启动终端 pane 或真实 Git worktree。产品 AgentTool 到同一后端的接线及工作树安全清理由 `tests/test_team_lifecycle.py`、`tests/test_subagent_execution_isolation.py` 验证。
