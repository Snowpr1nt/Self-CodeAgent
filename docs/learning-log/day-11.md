# Day 11：Hook、进程树与跨平台测试

## 今天交付了什么

三个 Agent 工具执行入口都先运行 pre Hook，执行后运行 post Hook。Hook 命令及 Bash 在超时和取消时清理自己的进程树。三条历史红线（主 Agent 绕过 Hook、`/tmp`、Unix `sleep`）已解除，并修复了 Windows 记忆整理锁的 PID 存活查询。

## 为什么这样设计

工具入口各有不同的事件协议，但 Hook 的业务顺序必须一致。将 Hook 调用提取为 Agent 的两个共享方法，使拒绝发生在工具调用之前，也让直接入口不会漏掉 post。进程控制需要覆盖 shell 退出而子进程继续占用 stdout 的场景；仅 kill shell PID 不够。Windows Job Object 与 Unix 进程组把进程树作为收尾单位。

## 测试如何证明

先让三个入口的 Hook 行为及四种“shell 已退出但子进程存活”测试失败，再做最小实现。最终 Hook、Permission、Bash、Agent、记忆整理相关门禁是 **157 passed、2 skipped（16.42s）**。两个跳过分别是原有条件性测试和缺少 `MEWCODE_TEST_API_KEY` 的真实 LLM 用例；Fake/Mock 测试不代表在线 Provider 验收。新测试还证明 Windows 锁探测不再调用 `os.kill(pid, 0)`。

## 判断修正与风险

最初把记忆整理用例的 `KeyboardInterrupt` 归因于临时目录；改用独立可写目录后仍复现。全栈显示中断发生在检查假 PID 后，新增禁止调用 `os.kill` 的测试稳定先红后绿，才定位到 Windows 控制台事件。命令收尾在本机 Windows 实测；Unix 分支沿用标准进程组 API，但本轮未在 Unix 主机运行。

详见 [实现与测试说明](../hooks-portability.md)。
