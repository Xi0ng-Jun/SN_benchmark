# 已完成实验的服务器导出升级指令

日期：2026-10-08。适用于已有旧版 run 的离线导出；不是启动新 campaign 的指令。目标分支为 `feat/benchmark-protocol-correctness`，目标提交 SHA 由本次交接消息提供。

用户报告：QASPER 与 QMSum 已导出／评分完成；Hotpot 的 7,405 个 run 正在由旧进程导出，已剔除 37 个重复 case，读取量约 110 GB／1.49 TB，报告 PID 为 2657613。这些是用户转述，接手时核实进程与实际工件，不能认为 PID 永久有效。

## 可交给服务器 agent 的 prompt

```text
请同步 SN_benchmark 的 feat/benchmark-protocol-correctness 到交接消息给定的目标 SHA，并升级“已有完成实验”的离线导出。不要重新运行 SN、生成模型、judge 或整个实验；已经完成的 qasper-sn / qmsum-sn 成果及全部原始 run 必须保留。本次只授权同步、离线检查、导出和已有任务范围的官方评分。

1. 先阅读个人/仓库 AGENTS.md、CURRENT_STATE.md、docs/result-storage-and-export.md 和 docs/result-storage-export-verification-2026-10-08.md。核实当前 Git SHA、未提交修改、现有进程和导出输出路径。用户报告旧 Hotpot exporter 的 PID 是 2657613，7405 run、37 个重复 case 被剔除、读取约 110GB/1.49TB；用实际 command line、父子进程、日志和输出核实，不仅凭 PID 发信号。

2. 已启动的 Python 进程不会因 git pull 自动使用新版逻辑。若确认旧进程仅执行只读 export-sn、尚有长时间剩余，正常中断这个导出进程；先保存实际命令、已选 run 列表、日志/进度和输出路径。不要终止生成/评分/其他任务，不用 kill -9，不删部分输出。若 PID 不符或任务已经完成，保留成果并跳过中断。新导出没有断点续跑；旧进程已读数据不是可直接复用的新版检查点。

3. 不在正在运行其他任务的 checkout 内切换代码。git fetch origin feat/benchmark-protocol-correctness，核实交接给定 SHA 位于该远程分支；在新 worktree/checkout 检出这个精确 SHA。不得 reset/clean 覆盖服务器修改。记录新旧 SHA。继续使用原冻结依赖/官方 scorer，不升级模型或 SDK；若现有环境以 editable install 指向旧 checkout，为新 checkout 建明确环境或明确使用新版 src，验证 rag_eval.__file__ 和实际解释器路径，避免“checkout 新、import 旧”。

4. 在新 checkout 做无模型离线检查。至少执行 artifact_store、bundle_index、scoring_projection、benchmark_submission、result_package 相关 tests；环境允许时执行完整 pytest。实际 Perl ROUGE 已具备则沿用原配置。读取/评分原 run 时保留 manifest.identity.code 的原始生成源码身份；新 exporter SHA 另行记录，不把旧 run 的身份改写成新版本。

5. 对 Hotpot 现有 run 分类：带 artifact-refs.json 的 shared run，或每 run 有 input/ 的 legacy run。IMPORTANT：新版不会自动把旧 input/source 副本迁移为共享引用；旧物理副本仍逐份哈希与 canonical 重建。仅安装一个新共享 bundle，不会让已有 legacy run 自动享受一次 canonical 重建。因此不要承诺读取 1.49TB 的成本消失，不要手工给旧 run 加 refs/改 manifest，也不要跳过输入校验。

6. 保留原始选题/attempt 决策，核对 37 个重复 case 的来源、run 状态、协议/模型/源码身份、outputs 哈希和排除理由。所有重复的原始 run 保留，只形成显式选择清单；不得按分数/答案质量挑选“更好的”回答，也不要只取文件枚举的第一项作为解决方案。若此前选择规则无依据或存在歧义，先报告具体冲突，不静默沿用。全量选择的 case ID 集合必须与 frozen Hotpot bundle 的 7405 个计划题一致，每题唯一；缺失/失败仍按契约保留，不能删题补覆盖。

7. 用现有同一条件、没有重复 case 的少量完成 run 做新版离线导出试验，比如 10 个后再 50 个。显式传这些 run 的真实目录，--case-id-file 声明匹配的 case 子集，写到新的 probe 目录。使用 /usr/bin/time -v、进程 I/O 与日志记录实际耗时、RSS、读取量和是否仍重复完整重建。Hotpot 的完整 response/captures/snapshot 必须保留；不截断证据或答案。验证输出 method.configuration.scoring_projection 为 sn-official-scoring-projection-v1、状态与来源 outputs hash 对得上，并按现有官方 Hotpot scorer 做子集证据回放。probe 成绩不作为 full 正式成绩。

8. 依据 probe 决定如何继续：若正确且成本合理，用新版 export-sn 对已冻结的全部实际 run 清单导出到新的 full 输出目录，不覆盖旧目录。full 不传子集参数，核对全 frozen scope 与 7405 题覆盖、失败/缺失及实际分母。新版 export 没有 --artifact-root 参数；共享 flags 属于新 run 的运行入口，不用于把旧导出伪装成 shared。

   若 legacy input 的逐份验证仍明显主导，且全量仍预计数小时，先汇报测量与瓶颈，不再盲目启动第二轮漫长全量，也不降低校验标准。下一步需要单独设计旧 run 的只读迁移/复核表示；当前代码不提供这个迁移命令。已完成的生成结果不需要因此重跑。

9. qasper-sn / qmsum-sn 已完成的 submission、官方 score、prepared/scorer audit 和原 run 原样保留。用户报告 QASPER answer_f1=0.3147/evidence_f1=0.4745 (1451/1451)，QMSum Perl ROUGE1=0.2775/ROUGE2=0.0904/ROUGEL=0.2425 (281/281)：以实际工件核实，不凭转述发布。若现有结果有效，不要求为升级重导出/重评分；若做旧/新等价检查则写独立目录，比较 prepared 输入、逐题值及分母，method ID 因 projection 元数据变化不能直接当成评分变化。

10. 后续回传用 package_benchmark_results.py --mode results；确需完整复核再用 --mode review。先核实目录角色：submissions/、official-scores/、reports/、直接 runs/<run-id>/。旧嵌套 layout 不自动识别，不遗漏官方产物，也不为整理而复制整套 1.49TB campaign。结果包排除 runtime；legacy review 仍会附各自 input 副本，所以打大型 review 包前先盘点、报告体积。禁止整目录 tar、删除原 run/runtime、自动迁移或 GC。

最后汇报：目标/实际 SHA、import 路径、旧进程如何处置、当前 shared/legacy 格式、重复选择依据、probe 和 full 的时间/I/O/RSS/大小、逐题覆盖/官方分母，以及是否仍被 legacy 逐份输入校验卡住。不要用本地合成秒数推算服务器性能。当前不启动新 benchmark、外部方法或新模型实验。
```
