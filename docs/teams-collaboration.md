# Agent Teams：协作、退出与安全清理

Teams 把一个大任务拆成有负责人和状态的共享任务，队友通过邮箱交换证据，lead 汇总结果。Day14 验收使用 Fake Provider 与真实 Python Agent 循环、Task 工具、SendMessage、Mailbox、SharedTaskStore 和进程内后端。

## 任务与消息怎么流动

`TeamManager.create_team` 创建配置、tasks.json 和邮箱目录。`register_member` 保存队友信息与名称；`TaskCreate/TaskUpdate` 创建、分配和更新任务；`SendMessage` 按当前团队的成员名称或 ID 解析目标，`lead` 总是解析为当前团队的 lead_agent_id。

名称只能在当前团队解析，不能通过全局名称表把消息误投给另一个团队。邮箱文件使用真实 agent_id，普通消息由 Agent 在轮次边界注入 Conversation。进程内后端在空闲时也按同一个 ID 等待消息；为兼容旧调用，可同时读取按名称保存的旧邮箱。

队友完成一轮后向真正的 lead ID 发送 idle 消息；`drain_lead_mailbox` 消费一次后清空未读状态。idle 表示能接下一项任务，runner 仍然存活，不能当作已退出。后续消息复用同一 Conversation，再次进入真实 Agent 循环。

## 产品入口与资源归属

产品 `AgentTool(team_name=...)` 通过 TaskManager 启动后台任务；TaskManager 的 team 分支委派给 `spawn_inprocess_teammate`，将 handle 注册给 TeamManager，然后 await 这个长驻 runner。普通一次性 SubAgent 仍走原有路径。

此前产品使用另一个独立的 60 秒邮箱循环，进程内后端只存在于源码、没有被产品入口调用。现在产品与演示使用同一后端，进度、消息地址与退出契约一致。TaskManager 负责最终一次性的后台通知与 Trace；TeamManager 负责队友 handle 和成员是否仍在运行。

## 两种退出

- 正常结束：通过 SendMessage 发送 `message_type="shutdown_request"`。如果队友空闲，直接退出；如果正在执行一轮工具/模型闭环，记住请求，在该轮结束后退出。该标志独立于普通消息消费，避免 Agent 先消费消息后丢掉关闭请求。
- 立即停止：cancel runner；`TeamManager.shutdown()` 取消并 await 所有进程内 handle，`TaskManager.shutdown()` 取消并 await 其后台 runner。完成回调处理尚未进入协程就取消的情况，状态变为 stopped，移除 handle，标记成员已退出。

TUI 退出会先等待 TaskManager，再等待 TeamManager 的 runner 回收，然后尝试删除团队；有未发布或未提交成果时删除仍会拒绝并保留团队资料。退出后端的自动化测试覆盖了只由 TeamManager 持有的 runner。

产品后台任务的最终状态为 completed/failed/cancelled；队友 UI 进度使用 running/idle/completed/failed/stopped。两层分别描述不同状态。取消只是请求；宿主必须 await 回收后再删除团队资源。

## 子工具的目录隔离

子 Agent 需要自己的执行工具实例。只复制 ToolRegistry 容器会共享 ReadFile、WriteFile、EditFile、Glob、Grep 和 Bash；修改子工具目录可能影响父 Agent，未绑定目录时相对写入还可能落入父项目。

创建子注册表时克隆六个执行工具，读前编辑状态与文件缓存由子 Agent 独立持有。同一个子 Agent 内的 ReadFile/WriteFile/EditFile 保持共享自己的状态缓存。创建子 Agent 后绑定各自 work_dir、FileHistory；Bash 的允许和拒绝路径从父项目重定位到对应子工作树，父配置保持原样。已有 disabled/discovered 约束继续保留。

## TeamDelete 的数据保护

删除前先确认成员已退出。工作树必须属于当前 WorktreeManager 的 active 记录；未知所有权、未提交改动、新提交或未发布提交都会保留。Git 查询失败按未知处理，不作为可删除的证据。

安全检查通过后，使用正确仓库目录执行不带 force 的 `git worktree remove`，同时检查返回码与目录是否确实消失。Git 拒绝或超时会抛出 TeamError，保留目标、团队配置、名称与 WorktreeManager 记录供检查和重试；没有递归删除兜底。只有工作树安全移除后才清理团队元信息。

没有 remote-tracking ref 时，未发布提交检测采取保守保留。不能为了让 TeamDelete 成功而丢弃本地成果。多个队友工作树中，前面已安全删除而后面保留时，团队元信息仍会保留，重试可跳过已经不存在的目录。

## 可复现验收

```powershell
.\.venv\Scripts\python.exe -m examples.teams.demo
.\.venv\Scripts\python.exe -m pytest tests/test_team_lifecycle.py tests/test_subagent_execution_isolation.py tests/test_subagent_lifecycle.py tests/test_subagent.py tests/test_teams.py tests/test_coordinator_multi_team.py -q --basetemp <新的专用目录>
```

演示为 Alice 和 Bob 各创建一项真实任务，Alice 把 20 通过 SendMessage 发给 Bob；Bob 从实际 Conversation 验证收到该邮箱消息，并向 lead 提交 22。lead 从真实消息解析并计算 42；重新打开 tasks.json 确认两个任务 completed；结构化关闭两名队友，断言 0 个运行 handle 后删除团队。

生命周期测试还覆盖前台/后台产品入口、后续任务唤醒、运行中 Provider 失败、启动前取消、流关闭、关闭请求在工具循环中的保留，以及真实临时 Git 仓库中的 dirty/unpushed/locked/timeout/clean 清理边界。

验收边界：使用进程内后端，不涉及终端 pane 或真实联网 Provider/MCP。共享任务文件在单个事件循环中同步更新；这组验收不代表多个独立 OS 进程同时更新任务文件时具有事务隔离。Windows 上 OS 级 Bash 沙箱的可用性仍按已有平台能力决定。
