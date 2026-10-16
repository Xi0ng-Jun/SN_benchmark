# 给评测服务器 agent：保留旧结果，只提速续跑剩余题

请直接完成当前 ASQA chunk 实验的提速续跑。此前“新旧准备策略不能合并、需要重跑全量”的说明已撤回，以本交接为准。不要重跑已完成题，不处理那 13 道答案合成失败，不另外开性能对照实验。

交付仓库：`git@github.com:Xi0ng-Jun/SN_benchmark.git`，分支 `feat/deferred-notebook-metadata`。固定 evaluator SHA 以本次交付回执为准；继续使用原 captured SN `970b679304a216c280b720666f18c4802065028f`、原 frozen bundle/index、948 题题单、模型配置和 cache=false。实现说明见 `docs/deferred-notebook-metadata.md`。

1. **安装新版本。** Fetch 后在新目录建立固定 SHA 的独立 worktree，不切换正在运行的 checkout。使用原解释器，确保实际 runner 导入自新目录。按说明运行 4 个交付测试文件和 CLI help；这些检查不调用模型。

2. **停止旧实例派发，等待在途完成。** 检查准确的 orchestrator PID、子进程、日志去向与 supervisor。对于当前没有 drain 的脚本，确认 runner 独立运行、日志不依赖父进程消费且 supervisor 不会连带重启/终止子进程后，只对 orchestrator PID 发 SIGSTOP，冻结后核实实际在途 runner 集合；不要对进程组或 runner 发停止/终止信号。依据 runner 进程和落盘状态等待全部结束，不依据已冻结父进程的 DONE 日志判断，也不要等所谓“波次空隙”。若运行方式不满足这些条件，先使用已有托管机制的停派能力；无法做到只停派时报告具体阻碍，保留运行中的工作。全部在途结束后再清理旧调度进程，确认不会恢复派发或自动重启。

3. **汇总已尝试题。** 扫描 b1 和 chunk-full 等所有旧运行目录，读取真实 manifest、outputs 和终态。保存旧 run 清单和已尝试 case IDs，包含 success、no_answer、clarification、error；finished_with_errors 的空答案也是已尝试，不能重跑。题目覆盖按 case ID 计算，不按目录名/数量猜测。半成品目录保留；如果已有输出，也保留该次尝试，不按成败另选答案。新题单是原 948 题减去这些已尝试题。

4. **只续跑剩余题。** 对私有 orchestrator 做最小修改，使它读取剩余题单、跳过已有尝试且支持停止派发后等待在途完成。不需要重写调度框架。新任务使用新版本 runner 和新 run 目录，保持 parallel=8，在原参数中添加 `--preparation-policy deferred-notebook-metadata-v1`。不要改缓存、模型、候选资料或问答参数；不要复用/覆盖半成品目录。

5. **用正常续跑的第一批观察收益，然后继续完成。** 记录资料数、chunk/embedding 覆盖、notebook_metadata 调用数、最终刷新回执、准备/Ask 耗时与答案状态。这些题直接计入实验；偶发答案合成失败正常保留，不因此重跑。若 metadata 仍重复到原有约 448 次，核对实际 evaluator SHA 和传入参数。开发侧 100 篇短资料离线 probe 为 448→8，约 52→13 分钟只是估算。

6. **合并并统一评分。** 用新版本 `scripts/benchmark_protocol.py export-sn --bundle ... --runs <所有选定旧run和新run的实际目录> --output <新导出目录>` 合并；`--runs` 要展开实际运行目录，不传共同父目录。先用现有少量不同版本结果做一次离线导出即可确认兼容，不需要额外调用模型。完整提交保留全部 948 题及错误答案，再走原计划的官方评分。新导出器允许准备策略和评测器版本不同，保留每题原始来源；仍检查同题重复、SN/模型/请求/mode 等条件。报告中注明续跑时切换了 notebook metadata 准备策略。

回报新 evaluator/SN SHA、旧批次保留题数（含错误）、剩余题数、在途是否结束、续跑是否已启动，以及首批实际耗时。不要停在“是否允许我修改脚本/启动剩余题”的确认上；本指令已经授权这些步骤。若遇到真实阻碍，说明具体事实和已经保留的工作。
