# Hook 与跨平台进程收尾（Day 11）

主 Agent 有直接执行、交互式权限确认和后台非交互三个工具入口。过去只有后台入口调用 `pre_tool_use`，因此普通对话中自动放行的工具可能绕过 Hook。现在三个入口复用同一组 pre/post 调用：pre 拒绝时工具不执行；工具执行完（包括返回错误结果）后触发 post。权限检查仍由原有策略负责，Hook 不改变权限决定。

命令 Hook 和 Bash 统一调用 `run_shell_command`。超时、取消和正常退出都会关闭其拥有的进程树。Unix 使用独立进程组；Windows 在 shell 启动前创建 Job Object，将暂停启动的 shell 加入 Job 后再恢复线程。这样即使 shell 提前退出，继承 stdout 的子进程仍属于同一 Job，超时后不会继续写文件。如果 Windows 无法建立 Job，命令返回错误，不在失去进程树控制的情况下继续运行。

`tests/test_hook_execution_paths.py` 对三个入口分别断言拒绝与放行，并用真实 Python 子进程验证超时、取消及“父进程已退出、子进程仍存活”的情形。历史上依赖 Unix `/tmp` 和 `sleep` 的测试已改用 pytest `tmp_path` 与当前 Python 解释器。记忆整理测试的无效 `pytest.mark.timeout` 已换成实际 `asyncio.wait_for`；真实 Provider 用例仍仅在提供 `MEWCODE_TEST_API_KEY` 后运行。

Windows 的 `os.kill(pid, 0)` 是控制台事件值，不能作为只读存活查询。记忆整理锁现在使用 Win32 进程句柄和退出码判断；权限不足时保守地认为锁持有者仍在，避免误抢锁。测试的 `999999999` 死 PID 曾引发 `KeyboardInterrupt`，这是修复该路径的回归证据。
