# Session 与 Memory：重启恢复

Session 把消息追加到 `.mewcode/sessions/<id>.jsonl`，把标题、时间和 Token 统计写入同名 `.meta`。恢复时读取记录，校验未完成的工具调用链，并从最后一条 `compact_boundary` 重建摘要、近期原文和后续消息。

Agent 的工具结果预算还产生另一类状态：完整大结果文件和 `replacement_records.jsonl`。Day 9 起每个新会话使用独立的 `.mewcode/session/tool-results/<id>/`，避免一个会话压缩时清理另一个会话的落盘输出。恢复时从记录重建 `seen_ids` 与 `replacements`，仅应用属于当前消息链的工具调用 ID。旧版本共用目录的会话仍从原目录读取，且重复恢复不会改变这个选择。

`/session new` 会重置替换决策、恢复快照、压缩熔断器与轮次计数。`/session resume` 先恢复消息，再恢复 Agent 状态。会话 ID 只允许 ASCII 字母、数字、下划线和连字符，防止路径逃逸；缺少 `.jsonl` 或 `.meta` 时返回“未找到”。

长期记忆保存在用户级和项目级 `MEMORY.md` 索引。恢复的 Conversation 不携带旧进程中的注入标记；下一次 `Agent.run()` 从当前磁盘索引重新加载并注入一次，因此重启后能看到最新记忆，也不会在同一次运行中重复注入。

测试用真实 `SessionManager`、两个 Agent 实例与项目级 `MEMORY.md` 验证重启往返、会话隔离、旧版目录兼容、路径边界和记忆注入。

收尾验证：旧共享目录压缩只删除自己直接持有的文件，保留所有会话子目录。`run_to_completion()` 与前台入口都初始化环境和长期记忆，压缩后再次注入。前台压缩须在发送通知前完成注入，TUI 才能从正确的历史游标继续写 Session，避免保留尾部重复。`compact_boundary` 新增可选 `attachment`（旧记录仍可恢复），因此最近文件/技能的 Recovery 正文跨重启仍可读；这只是快照，不代表当前磁盘状态。new/resume 同时重建 FileHistory 所属目录并清除旧技能、recall 状态。最终 Day9 邻接门禁 170 passed。
