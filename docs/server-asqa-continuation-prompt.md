# 给评测服务器 agent：立即停止当前在途批次，原目录更新后续跑

这是本次提速的首次执行指令，请直接按以下步骤完成当前 ASQA chunk 实验，不依赖其他交接文本。

目标：保留已经完成的结果，剩余题改用导入提速策略，最后合并成完整 948 题答卷。用户已决定放弃当前尚未完成的在途批次，允许立即终止旧调度和这些 runner、更新评测器、修改本机 orchestrator 并启动剩余题。沿用现有评测代码目录和 Python 环境，不新建 worktree。

- 评测仓库：`git@github.com:Xi0ng-Jun/SN_benchmark.git`
- 交付分支：`feat/deferred-notebook-metadata`
- 固定 evaluator SHA：使用本次交接提供的完整 SHA。
- SN 保持：`970b679304a216c280b720666f18c4802065028f`，沿用原 captured source ref。
- frozen bundle/index、948 题题单、全部候选资料、模型配置、cache=false 和 parallel=8 均沿用现有设置。

1. **立即停止当前批次，不等题目跑完。** 先冻结旧 orchestrator 的派发，记录此刻尚未完成的在途 case IDs 和 run 目录，然后终止这些 runner 及其所属子进程、终止旧 orchestrator。只操作本次评测进程，不停止共享模型服务；确认旧评测进程已退出且不会自动重启后就更新代码。终止信号后仍不退出的所属进程可强制结束，不等几十分钟的导入完成。把记录下的在途题统一标记为本次人为中断，旧目录保留但不进入最终答卷；无论是否已有部分输出，都按同一规则重新运行。

2. **进程退出后，在原评测目录更新。** 保存当前 SHA、本地脚本及未提交修改；保留全部 runs、store、配置和虚拟环境。设 EVAL_REPO 为现有评测目录、EVAL_RELEASE_SHA 为本交接的固定版本，执行：

   ```bash
   git -C "$EVAL_REPO" fetch origin refs/heads/feat/deferred-notebook-metadata
   git -C "$EVAL_REPO" switch --detach "$EVAL_RELEASE_SHA"
   git -C "$EVAL_REPO" rev-parse HEAD
   ```

   不使用 reset --hard 或 clean。若本地修改与切换冲突，先备份再处理，保留私有 orchestrator。继续使用原解释器；显式令 PYTHONPATH 指向原目录的 src 和仓库根，核对 runner 导入路径及 `run_notebook_benchmarks.py --help` 中出现 `--preparation-policy`。开发侧相关测试已通过，不要求额外调用模型作验收。

3. **保留完成题，重建待跑题单。** 汇总 b1、chunk-full 等全部旧 run 的 manifest、outputs 和终态，按 case ID 记录已经完成的题及对应目录；排除步骤 1 统一放弃的中断 run。已完成的 success、no_answer、clarification、error 都保留，那 13 道合成失败及后续已完成的同类失败不重跑。待跑题单 = 原 948 题减去保留的已完成题，包含本次主动中断的题。对私有 orchestrator 做最小修改，让它读取待跑题单并跳过保留的完成题，不再因旧目录存在而从头规划失败。

4. **启动续跑。** 在原代码目录运行更新后的 runner；每个待跑题使用新的 run 结果目录，不覆盖旧目录。中断题的新 run 记录对应旧 run 和“用户决定停止旧批次后提速重跑”的原因。在原运行参数中添加：

   ```text
   --preparation-policy deferred-notebook-metadata-v1
   ```

   该优化只把逐篇 notebook metadata 刷新推迟到整份资料导入结束；每篇摘要、论文元数据、chunk、embedding 和 Ask 路径保留。用正常续跑的第一批记录调用次数和准备/Ask 耗时，然后继续完成剩余题；这些题直接计入实验，不另外重跑旧题作性能对照。开发侧 100 篇短资料离线验证 metadata 调用 448→8，约 52→13 分钟只是推算，实际收益以本次测量为准。

5. **合并并评分。** 新版本允许保留的旧完成结果与新结果合并，保留每题原始来源、策略和错误；步骤 1 的中断 run 不参与导出，用其新 run 替代。执行 `scripts/benchmark_protocol.py export-sn --bundle ... --runs <全部选定旧run和新run的实际目录> --output <新导出目录>`，不要把共同父目录传给 --runs。完整提交保留 948 题，再执行原计划的官方评分。报告注明续跑时切换了 metadata 准备策略。可以用首批新结果加旧结果先做一次离线导出确认兼容，不增加模型调用。

回报旧进程是否已退出、中断题数、保留完成题数（含错误）、更新后的 evaluator/SN SHA、待跑题数、续跑是否启动及首批耗时。按以上授权连续执行；遇到具体阻碍再说明事实，不停在例行确认上。
