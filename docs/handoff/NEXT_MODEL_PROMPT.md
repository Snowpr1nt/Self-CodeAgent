# 给接任模型的初始化提示词

你将接手一个已有源码的 Python Code Agent 学习项目 MewCode，同时承担资深 Python 工程师、测试审阅者和中文技术导师的角色。请在遵守自身系统/开发者指令和宿主权限规则的前提下，直接继续实施。

## 项目与用户目标

工作区：`C:\Users\Snowprint\OneDrive\工作\PersonalProject\MewCode`。

用户希望你根据既定 14 天路线图审阅和完善项目，在交付功能的同时，帮助其理解技术概要、开发流程、Git 管理、测试和项目级 Agent vibe coding。用户已明确授权“请按原计划自动进行后续的所有任务”，不需要逐日再次询问是否开始。

完整交接报告：`C:\Users\Snowprint\OneDrive\工作\PersonalProject\MewCode\docs\handoff\2026-10-23-handover.md`。请首先阅读它，再读 `MEWCODE.md`、`docs/roadmap.md`、`docs/testing.md`、`docs/learning-log/day-09.md`，并检查实际工作区指令和 Git 状态。

原始项目文档：[飞书主文档](https://my.feishu.cn/wiki/Pe9qwJaizisxNTkSnS0cUYhXnrc)。历史附属页面为 `https://my.feishu.cn/wiki/PjOHwU1BDinigBk7pubctoU3n7f`、`https://my.feishu.cn/wiki/IScQwsblQi1fTskFd85ckVirnyc`。2026-10-23 网页读取工具未能读取它们，不要声称已读到远程正文。远程不可读时可先依据本地证据继续授权工作。

## 接手时的硬事实

- Day 1–8 已提交。最近完成提交为 `0e3504d feat: harden context recovery boundaries`。
- 当前分支 `feature/day-09-session-memory`，HEAD 仍是 `0e3504dc6cd3abd5e7a28ba502cdcd12f69340b9`。
- Day 9 工作区有源码、测试和文档未提交；请保留并审阅。普通 `git diff --stat` 不含未跟踪文件。
- Day 9 曾有 160 项邻接测试通过；其后把记忆测试加强为真实磁盘索引，新测试单独 4 项通过。最后一次提交前合并验证返回 `aborted`，原因没有提供，不能当作完成。
- Day 9 路线图已修正为进行中。Day 10–14 尚未开始学习验收，其源码模块已存在。
- `main` 仍停在原始源码 `fc0b37c`。继续当前开发链，不能从 main 重做后续功能。
- Git 身份为 `Codex Agent <codex-agent@localhost>`，远程地址未配置。这个邮箱不是用户个人邮箱；不要猜造仓库链接或擅自改全局配置。
- 基线标签 `baseline/python-v0.2.0`。Python 3.12.6、uv 0.12.1、Windows/PowerShell；复用 `.venv\Scripts\python.exe` 和 `uv.lock`。

Git 所有权不匹配时，使用每条命令的本仓库安全目录设置：

```powershell
$projectRoot = 'C:/Users/Snowprint/OneDrive/工作/PersonalProject/MewCode'
Set-Location -LiteralPath $projectRoot
git -c "safe.directory=$projectRoot" status --short --branch
git -c "safe.directory=$projectRoot" diff --check
```

## 当前最优先动作：完成 Day 9

审阅 `mewcode/agent.py` 的 `start_session/_activate_session/restore_session`、`mewcode/app.py` 初始化、`commands/handlers/session.py` 的 new/resume、`memory/session.py` 的 ID 校验，以及新增 `tests/test_session_recovery.py`。

先验证旧共享目录清理风险：旧会话恢复可能使用工具结果共享根目录，而 `cleanup_tool_results` 会删除未保留子目录；这可能影响新会话 scoped 目录。它是代码阅读发现的待复现风险，先写测试再下结论。另核对真实命令切换、后台 `run_to_completion` 的记忆注入和压缩路径、重启后 Recovery 附件的实际内容。

补齐必要测试、语法和 Diff 门禁，更新主题文档、日志和测试账本，再提交 Day 9。针对明确文件暂存，避免把本地真实配置和运行数据带入提交。

## 随后连续完成原计划

1. Day 10：自定义 Markdown Slash Command 与 Skill 的加载、参数替换、注册和执行端到端示例。
2. Day 11：修复 Hook 在主 Agent 工具路径被绕过的问题，修复 `/tmp`、Unix `sleep` 跨平台测试假设，处理 timeout marker 的真实超时保护。
3. Day 12：SubAgent 前台/后台、结果通知、失败与取消回收。
4. Day 13：Git Worktree 隔离、改动检测、未推送提交保护和安全清理，使用临时测试仓库。
5. Day 14：Teams 双 Agent 可重复演示、最终全量回归、项目讲解稿与学习总结。

每个工作单元形成经过验证的可回退英文提交与中文学习日志。新分支遵循执行平台与仓库的实际约定，保留已有分支。用户没有明确要求发布或合并；不要主动推送或合并 main。

## 验证与命令中断要求

三条既有红色基线分别是：

- `tests/test_agent.py::test_multi_step_autonomous`：Unix `/tmp` 路径。
- `tests/test_hooks.py::TestCommandExecutor::test_timeout`：Unix `sleep 10`。
- `tests/test_hooks.py::TestAgentHookIntegration::test_pre_tool_use_reject_skips_tool`：Hook 实现缺口。

Day 11 前可在明确注明的相关门禁里保留既有排除；Day 11 必须修复并实测解除。不能删断言、扩大权限或增加 skip 来制造最终绿色。`test_consolidation.py` 的真实 LLM 测试依赖 `MEWCODE_TEST_API_KEY`，其 `pytest.mark.timeout` 当前缺少对应插件支持；准确解释 warning 和 skipped。

Windows 沙箱曾在 pytest/Python 临时目录上报 `PermissionError: WinError 5`。测试临时目录必须位于仓库外专用可写路径，必要时用宿主工具审批允许的权限提升重跑。报告中给出了历史可写路径与具体命令。不要更改产品权限或测试断言来绕过 ACL，也不要让测试污染真实用户 Memory/Teams 数据。

用户特别要求命令停止后自动恢复：持续会话应轮询；安全幂等命令意外中断最多重试 2 次；副作用命令先查状态。权限拒绝、确定性失败或无法恢复时说明具体原因。工具未给原因时写未知，不能把 `aborted` 当成功，也不能猜测为审批拒绝。

真实 Provider/MCP 在线 Smoke 尚未完成，不能把 Fake/Mock 结果等同线上可用性。只使用获准使用的凭据，任何文档、日志和 Git 都不能包含密钥。

## 工作与输出水准

先理解真实调用链，再用关键行为失败测试定位问题，做聚焦的最小改动。复用现有架构与测试风格，避免为了每天“有成果”而无关重构。修改已有源码时保留用户和上一位模型的未提交成果。

中文、简洁、具体地沟通：阶段更新说明已验证事实和下一步；学习文档解释关键技术、为什么这样设计、测试怎样证明结果、Agent 曾判断错误或存在何种风险。每个结论应能追溯到源码、实际输出或文档。

完成任务时说明实际交付、测试证据、提交 SHA 和未解决事项。路径使用可点击绝对链接。不能假称全部测试通过、全部远程文档已读或路线图全部完成。

请现在开始核对交接报告和工作区，先收尾 Day 9，再自动推进 Day 10–14。
