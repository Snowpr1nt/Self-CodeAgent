# SubAgent 离线演示

在仓库根目录执行：

```powershell
.\.venv\Scripts\python.exe -m examples.subagents.demo
```

使用 Fake Provider 与真实 Agent 循环、AgentLoader、AgentTool、ReadFile、TaskManager 和 TraceManager。无需 API Key 或网络。临时工作目录在退出时移除，不加载用户级 Agent、配置或记忆。

依次演示前台读取文件返回结果、后台完成、后台 Provider 失败、运行中取消和启动前立即取消。每个后台任务输出最终 status/trace、1 条通知、0 个运行任务。失败场景会故意打印一条后台失败日志；所有断言成功时进程退出码为 0。

演示使用 Event 控制调度和任务等待，不依靠任意秒数的 sleep。2 秒只作为死锁保护上限。详细契约及限制见 [技术说明](../../docs/subagents-background.md)。
