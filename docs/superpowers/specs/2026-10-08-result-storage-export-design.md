# 结果保存与导出改造设计（已确认并实现）

日期：2026-10-08。代码基线：`bcea0c51c63729d413709cf664584188f758d9cb`，分支 `feat/benchmark-protocol-correctness`。
状态：用户已于 2026-10-08 确认设计；代码已在当前工作树实现并做离线验证，服务器验收尚未执行。实际接口／布局见 ../../result-storage-and-export.md。

## 目标与边界

让五套 benchmark 的输入、源码、结果和回传工件按实际用途保存。共享不可变数据只存一份；运行可变状态仍按 partition × mode × attempt 隔离；导出成本主要由实际答卷和必要证据大小决定。

本机只做开发与无模型离线测试，包括合成工件规模测试。真实 SN、模型、judge、官方模型评分仍在服务器执行。保留原有数据、请求、分区、证据预测、评分公式、实际分母和失败语义。不会为节省存储合并题目的资料库，也不通过删上下文、截断答案或跳过校验提速。

## 现有证据和最可能推翻方案的事实

- `notebook_runner.execute` 每个 run 复制整个 frozen bundle，重新加载副本；`runtime_environment.snapshot_sources` 每次复制 evaluator 源码并执行 SN git archive。
- `run_report.load_run` 调用 `validate_saved_run`，后者每次重新加载、哈希与适配整套 bundle，并重建 partition。导出、Dashboard 都使用此路径。
- `export_sn_runs` 深拷贝每题完整 product_record，随后 `write_submission` 再加载 bundle，`build_submission` 再深拷贝。后者按列表检查 case 成员、重复构造 set；全量题单存在二次方查找成本。
- `notebook_rescoring`、`notebook_alce_results.attach_scores` 也复制 input；只修改主 runner 会把问题留在派生路径。
- QASPER/Hotpot 证据快照的 observation_id 覆盖 `status/answer/response/captures`。删 response 或 captures 会破坏当前回放协议；它们必须原样保留。
- 官方 MultiHop 检索依赖完整真实 ranking capture；ALCE 引用转换依赖 anchors、source_to_document、anchor_documents。只导出答案不是等价替代。
- EventJournal 每条 fsync；Agent 单项完成即持久化是已确定恢复保证。第一阶段不改变它。
- 尚无服务器各类文件的字节数、实际导出耗时剖析和文件系统数据。因此不承诺固定秒数或压缩比例。若 runtime/证据而非重复 bundle 占主要体积，要调整后续保留策略；不能用架构推测替代数据。

最小证伪：五套合成 full record 与 projection 的 prepared inputs 必须完全一致；QASPER/Hotpot 原快照回放必须通过；篡改输入、引用、排名或答案必须仍被拒绝。若不成立，扩大 projection 的必要字段，不降低评分标准。

## 路线选择

| 路线 | 收益与代价 | 决策 |
| --- | --- | --- |
| 只压缩每个 run | 传输更小，但仍有 P 份 bundle/source，仍反复解压与适配 | 只作为打包末端选项 |
| 共享不可变工件、精简答卷、复用同次验证上下文 | 改动集中在数据与工件边界；保留实验隔离与评分语义 | 推荐第一阶段 |
| 合并 partition、共享可变数据库、批量 fsync | 可能改变输入范围、生命周期、失败恢复和隔离 | 暂缓；不属于本次实现范围 |

## 1. 共享不可变工件与分区索引

建议目录：

```text
campaign/
  artifacts/
    objects/bundle/<object-id>/           # 原 frozen bundle 一份；保留 raw/source/gold
    objects/index/<object-id>/            # 派生索引、分区 capsule、各文件哈希
    objects/evaluator/<object-id>/         # 实际 evaluator 字节快照一份
    objects/sn/<object-id>/                # SN 源码 archive 一份
  runs/<run-id>/
    artifact-refs.json             # 相对路径、object id、hash、明确格式版本
    manifest.json
    product-bundle.json            # 当前 partition 的实际无 gold 请求
    planned.jsonl
    outputs.jsonl                  # 权威完整观测
    scores.jsonl
    agent/                         # 若运行 Agent：权威完整组件/轨迹/分数
    runtime/                       # 仍独立；不是共享对象
  submissions/
  official-scores/
  reports/
```

新增 `artifact_store.py` 负责对象安装、引用解析、并发发布与完整性验证；不负责评分或共享数据库。新增 `bundle_index.py` 负责 canonical bundle 到 partition capsule 的派生表示。

- 安装器完整校验原 bundle，复制一次，生成索引与每个 partition 的 capsule；索引含派生协议版本、原 bundle identity、partition identity、文件哈希。`index-id` 不冒充官方 `bundle-id`。
- capsule 只按已冻结 partition 拆分：完整公共资料、原题单、评测侧 case/必要 catalogue 分别存放；gold 不进入产品请求。多次 chunk/reasoning/retry 复用同一 capsule，不缩减材料。
- 新 run CLI 同时指定 campaign 的 `--artifact-root` 和受信任 canonical 安装时冻结的 `--artifact-index-id`；初始化读取已安装索引和当前 capsule，校验这次实际读取的字节与身份，不重新适配整套数据。索引安装是显式准备步骤，不在每个 run 隐式完整重建。
- 原始文件通过复制安装；不对可变源目录做软链接/硬链接。私有临时目录构建完成后原子发布；并发安装相同 id 只有一个有效对象，竞争者验证已有对象。
- 引用使用受限的相对路径和固定 object id；越界、链接逃逸、缺失、hash 不符、冲突均拒绝，绝不联网或按文件名猜测。
- 权威数据仍是 canonical frozen bundle 与 outputs/Agent journals；索引是版本化派生物，可重建。每次官方导出/评分完整核实所使用的 canonical bundle 与索引一致性。
- 源码身份按真实字节与已有 SN archive identity 保存；不能仅凭 branch 名或 evaluator commit 去重未提交源码。模型配置仍私有地复制到每个 runtime，公开工件只保存必要配置哈希。
- 同步改动 Notebook、现有 QMSum baseline、补评与 ALCE attach 的输入读取/引用发布；所有消费者通过同一个 resolver，避免到处添加 input 路径兜底。

空间：旧的输入/源码主要成本是 `P × (B + S)`，新结构是 `B + S + I + P × references`。B 为同源完整 bundle，S 为源码，I 为各 partition 索引/capsule；I 自身不是零成本，应在报告中单列。runtime 与实际证据仍按每次尝试增长。

## 2. 官方答卷与审计观测分开

新增 `scoring_projection.py`，按 suite 提取官方 scorer 必需字段。首版保留现有 `benchmark-submission-v1` 顶层交换结构，在方法 configuration 中明确投影协议版本；不新增第二套评分公式。

`outputs.jsonl` 与 Agent 工件仍保存完整事实。submission 的每行只保留 case/status/prediction、必要 record 与来源 run/hash 引用。Dashboard 和组件补评继续读取审计工件，不能把缺少全上下文的 compact submission 当作完整 run。

| Suite | compact record 必须保留 |
| --- | --- |
| QASPER | status、answer、完整 response、完整 captures、qasper_evidence（含 snapshot/projection/identity）、predicted_evidence |
| HotpotQA | status、answer、完整 response、完整 captures、hotpot_evidence（含 snapshot/projection/identity）、predicted_supporting_facts |
| MultiHop-RAG | status、answer、真实 retrieval capture（含原排名、实际文本、快照和其身份；reasoning 缺 ranking 时维持不适用语义） |
| ALCE | status、answer、response 中原 anchors、source_to_document、anchor_documents 及其有效/错误状态；官方候选文档仍由 bundle 提供 |
| QMSum | status、answer；官方 query/references 仍由 bundle 提供 |

保留必要的 reason 与错误状态；missing/error/no_answer/clarification 不改成成功或零分。Compact projection 之前先校验原 record；gold 标签被发现时仍拒绝，不能通过 allowlist 删除后掩盖违规。

第一阶段 QASPER/Hotpot 的 captures 仍可能较大，这是当前回放契约的成本。若后来要进一步压缩观测，必须另立证据协议、重新校准官方等价性，不能修改 observation_id 来让旧快照通过。

答卷构建使用 set/dict 索引，避免每条 prediction 搜索整份列表；只在发布边界创建独立 compact 值，不重复深拷贝 full record。整份提交的数据结构仍需要内存，不声称首版实现常数内存流式评分。

## 3. 验证与导出成本

新增 `run_reader.py` 归属独立读取/校验职责；`run_report` 只负责报告。重用一次操作内的 `RunReadContext`：canonical bundle、索引、case/document/partition 字典只构建一次；每个 run 仍校验独立 manifest、计划、输出、证据和状态。

- 新共享对象在一次 export/report 中按规范路径与期望 hash 验证一次，所有引用要求匹配。不能按相同 manifest 内容去跳过另一个物理目录的字节验证。
- 运行中目录的读取维持 current/missing/尾行披露，不把运行中的记录登记成已封存可信对象。
- 导出一次加载 bundle，逐 run 检查身份，生成 compact rows，再发布一次 submission；不绕道 Dashboard 统计/读取不相关模型日志。
- `write_submission` 允许同次调用传入已经验证的 bundle context；不再隐藏地 load_bundle 第二次。公共独立 CLI 仍从外部输入完整验证。
- 保留 duplicate case/attempt、scope、mode/model/request/source 混用检查；不能以选择最成功重试来解决冲突。
- 完成收据记录各工件 hash 和状态，作为来源索引；导出仍检查本次读入文件，绝不相信仅凭 `validated=true`、mtime 或旧收据。
- 老 run 的复制输入仍逐个验证。首次验证/迁移老物理副本无法凭空消除 I/O；可提供显式只读转换到共享 store 的新 run 表示，默认不修改旧目录、不删旧文件。

期望复杂度：新数据的一次导出主要为 `O(B + I_used + total_run_ledgers + required_evidence)`，不再是每个 run 完整重建 `O(P × adapt(B))`。仍核对 source 字节；真实耗时还取决于文件系统。

## 4. 回传包与保留策略

新增 `package_benchmark_results.py` 与 `result_package.py`：按明确产物角色生成新包，不把 campaign 整个目录 tar。

- 结果包：submission、官方 scores、必要 prepared/scorer 审计、scope/coverage、来源身份、耗时/大小清单、比较/报告（若已生成）。不夹带 runtime 数据库、storage、凭据配置或原始产品日志。
- 复核包：结果包 + 完整 outputs/Agent 组件轨迹 + 文档映射等现有 reader/补评依赖；共享 bundle/source 依赖按 hash 只附带一次，解包后能重建引用。
- 包默认私有权限。结构化凭据检查不冒充自由文本脱敏；本项目已有隐私边界继续适用。
- runtime 与产品原始日志先保留服务器本地。首版提供 inventory 与保留分类，不默认删除；尤其旧 QASPER recovery、失败/中断/仍运行 run 的数据库必须保留。
- 对新已终止 run，只有当 reader/官方证据/复核/组件补评证明不再依赖 runtime，才可在后续另行提供显式清理操作；首次实现不含自动清理、自动 TTL、跨 campaign GC。
- 归档只在末端做一次压缩；共享依赖已去重再压缩。会分别报告逻辑字节、实际占用（适用时）、包大小、打包耗时，不把 sparse/hardlink 等影响混为一谈。

这一阶段先消除重复 input/source 和不必要回传；若实际 runtime 仍是主要磁盘占用，再依据 inventory 制定可验证的清理范围。

## 验收与规模预算

1. 五套合成 record：原 full record 与 compact projection 的 prepared 输入、官方指标（可离线者）、分母、missing/error/pending、引用假阳性/映射错误行为一致。无需调用真实模型。
2. QASPER/Hotpot projection：原 observation/snapshot 哈希与回放一致；删必要字段或改答案、snapshot、引用后拒绝。
3. 索引 capsule：与现有 canonical `partition_bundle` 的公共请求、全部资料、题单、计划完全一致；改 gold 不影响公共生成输入。每个 run 无跨题污染，数据库仍独立。
4. 共享对象：并发发布、中途失败、重复安装、篡改、引用逃逸/缺失测试；原数据、原 run、产品目录无改写。
5. reader/补评/ALCE attach/Dashboard：新格式读写和旧五套显式读取通过；不恢复退役 benchmark reader，不增加随处猜格式的兼容链。
6. 1k/5k/10k 合成 case/run 工件，记录 setup/export/package 阶段耗时、读字节、bundle canonical rebuild 次数、峰值内存及产物字节。不运行 SN 或 judge。相同 B/S 的 N 次运行只能有一份共享对象；同次 export 每个 canonical bundle 只完整重建一次。
7. 结构预算：QMSum compact submission 不含无用 context；QASPER/Hotpot 必需 captures 不裁剪。新 export 不产生按 N 倍复制同一 B/S 的路径，不采用按 N×N case 查找。
8. 合成耗时作为开发回归信号，不给 CI 设机器无关秒数阈值。服务器最终比较原版/新版同范围：disk/input/source/runtime/outputs/agent 字节、导出和打包时间、最大 RSS、逐题覆盖与官方分。结论必须附机器/存储条件。

## 交付顺序与影响模块

1. artifact_store/bundle_index + prepare/run CLI + runtime/source snapshot + 所有输入 resolver 消费者。先通过 partition 等价、隔离与并发测试。
2. run_reader 与同次上下文 + compact projection + submission；五套证据/scorer/复核边界验证，修正 case 查找与冗余深拷贝。
3. 包生成与 inventory，回归 Dashboard/派生读写；更新 RUNBOOK、ARCHITECTURE_CURRENT、CURRENT_STATE、TRIAGE、服务器执行 prompt。
4. 离线全量回归与合成规模检查；代码可验收后再按用户授权提交/推送、准备服务器执行 prompt。服务器真实实验另行执行。

源码范围见 Birdview revision 2 的 planned/completed event；独立审查后将安装所得 index ID 固定到 run 配置，拒绝可变指针替换，并禁止发布目录与 store 重叠。共享存储、projection、reader 和打包已实现；实际验证及服务器待验收项见 ../../result-storage-export-verification-2026-10-08.md。
