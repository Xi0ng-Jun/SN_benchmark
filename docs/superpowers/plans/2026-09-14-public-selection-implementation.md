# Public Selection and Partitioned Execution Implementation Plan

> 使用 superpowers:subagent-driven-development，任务间做静态接口审阅，末尾做独立整体审查。用户已授权实施；之前暂停测试/实验的约定保留，本轮不运行测试、SDK 或应用，不下载数据，不提交。

**Goal:** 实现十套公开题按 task/split 全量选择、确定性分库及完整题单的运行覆盖报告。

**Architecture:** 新 `public-selection-v1` 容器保存选择政策、原始来源身份、逐行决定及兼容原 canonical case 的 Native manifest。旧 bundle 加载不变；新加载器从原始行重建且记录本地范围与上游完整性声明的区别。独立 `public-corpus-partitions-v1` 计划保存审核状态、各分区完整产品 bundle 与来源身份；单次 runner 显式选分区，聚合报告以整个计划作分母。

**Tech Stack:** Python 标准库、已有 rag_eval、DeepEval 源码快照；所有 SDK/模型调用仍延迟到显式执行入口。

**Spec:** `docs/public-benchmark-selection-plan.md`

## Global Constraints

- 工作树：`benchmark-deepeval/.worktrees/public-benchmark-agent-expansion`，沿用分支，保留已有未提交测试/文档。
- 生产项目只读；不运行测试、Python 应用、SDK、下载器、模型、baseline 或 timer。不提交或推送。
- 编写回归用例但不宣称通过；已有 205 项结果不适用于本轮代码。
- 不设题数上限。单分区最多 40 篇文档；选题清单独立且不截断。
- 保留旧 `public-starter-v1` / `public-expansion-v2` case 和加载逻辑，数据选择的新身份使用独立容器，不伪装历史协议。

## Task 1：选择与本地准备

文件：新增 `public_selection.py`、`selection_bundle.py` 与 `tests/test_public_selection.py`；修改 `starter_protocol.py` 的新格式分派、`scripts/prepare_public_starter.py` 的显式选择参数。

接口：

```python
SELECTION_VERSION = "public-selection-v1"
select_public_rows(suite, raw_path, source) -> dict
# cases: canonical old-shape records; decisions: one per physical source row;
# summary: selected/out_of_scope/invalid counts, memberships, task coverage, duplicate groups
prepare_selection(suite, raw_path, source_path, output) -> None
load_selection_bundle(directory) -> dict
# keys: manifest, native_source, cases, decisions; native_source includes
# selection_bundle_sha256 = digest(manifest.json), so the runner pins the outer identity.
```

- [ ] 写回归：超过旧题额不截断；DROP 多 section/不足十题仍保留；四个 MMLU/BBH tasks 精确过滤；LogiQA 多任务；未知/异常行不丢失；完整 split 范围声明不伪造。
- [ ] 实现规则与原始记录身份：SQuAD 两主题、DROP 两类别全部 section、LogiQA 两任务，其余依照 spec。独立问题与任务记录分别计数。重复源身份冲突记录为异常；同文不同 ID 保留且标记。
- [ ] 保存 raw、cases、decisions、instruction-audit、cards、source/SDK 哈希；加载器校验路径包含、固定策略和从 raw 重建全部结果。格式异常也有决定记录；有效部分可用于探索但整体状态不能称上游完整。
- [ ] 新容器返回嵌套的原版 Native source，源文件字节与 canonical case 身份不改变。准备默认函数继续 legacy；CLI 新增 `--selection-protocol`，默认新格式，显式 legacy 保留旧入口。

## Task 2：确定性分库

文件：新增 `selection_partitions.py`、`scripts/plan_public_selection.py`、`tests/test_selection_partitions.py`。

接口：

```python
PARTITION_VERSION = "public-corpus-partitions-v1"
build_partition_plan(cases, source, reviews=None, max_documents=40) -> dict
# manifest binds source fingerprint/version and exact contents;
# partitions: [{partition_id, case_ids, product_bundle}];
# decisions: [{case_id, sample_id, status, reason, ...}]; reviews and max_documents retained for rebuild
load_partition_plan(path, cases, source) -> dict
# checks integrity then rebuilds plan using saved reviews/cap; exact equality required
```

- [ ] 写回归：>40 篇分库不丢题；共享资料/LogiQA 多归属同库；MMLU/BBH task 分组；确定性；审核待定、DROP 缺注释保留；修改题单/审核/分区被拒绝。
- [ ] 用已有白名单构造资料。先按单题解析产品适用性，再按任务（MMLU/BBH）、主题（SQuAD）、其他源顺序聚合同文资料。每批最多 cap 篇，题目不受 cap 限制。
- [ ] 原三套保留真实人审要求，填充同来源的剩余干扰容量；新七套无默认 Faithfulness 或假审批。分区 bundle 声明 corpus 协议与分区范围，保持原问答/评分协议。
- [ ] 本地计划 CLI 只读取已准备选择容器和可选审核文件。不得导入产品/SDK或调用模型。源选择集合和所有未执行/待审题仍保留。

## Task 3：运行入口与覆盖报告

主代理实施文件：`starter_runner.py`、`scripts/run_public_starter.py`、`starter_report.py`，新增 `selection_report.py`、`scripts/report_public_selection.py` 和对应回归。

- [ ] R 对新选择容器必须明确提供分库计划与 partition_id；N 使用完整有效题单。旧 bundle 无新参数时行为保持不变。
- [ ] runner 校验整个选择容器和计划，在构造 runtime 前选出计划中确切 case/product bundle；保存复制输入与计划，复核复制后的选择和计划。identity 增加选择/分区上下文，chunk/reasoning 严格按同一完整计划配对。
- [ ] 复用单分区原执行、评分、引用和澄清保存。原三套无可执行题时仍由完整计划展示 pending/N/A，不启动空 notebook。
- [ ] 新报告 CLI 接受选择容器、分库计划与零个或多个 run。无 run 时也显示完整计划和所有未运行分区。严格校验来源、计划、分区、case 集合与重复运行身份；不自动挑最新运行。
- [ ] 按 task/mode/scorer 汇总计划、已保存输出、已评分和缺失，原始选择/适用性/执行覆盖分母分开。选择源完整性未核实时不可宣称全数据完成；有 pending/invalid 或未完成运行也不可显示整体完成。
- [ ] 写对应回归规格；不执行。

## Task 4：静态核对与文档交接

- [ ] 独立审查选择身份、SDK 兼容、完整分母、分库与资料泄漏、旧路径兼容，修正具体发现。
- [ ] 更新 README/context/status/选题方案和实现交接：标明代码写入与未验证状态、命令示例及来源清单格式，保留本轮不下载/不运行边界。
- [ ] 只执行文本/补丁检查，不运行应用或测试，不提交。

## 接口审阅与裁决记录

| 项目 | 核对结论 |
|---|---|
| Task 1 / 2 | native_source 保留原 SDK 协议，额外绑定外层选择身份；分库消费完整有效 cases 与同一 source |
| Task 2 / 3 | plan 内保存完整 product_bundle 与 case_ids；加载时重建，runner 无任意覆盖参数 |
| Task 1 / 3 | 新容器通过既有 load_bundle 分派返回兼容 source/cases；旧 bundle 重建规则不改 |
| Task 1 自洽 | 无下载器，只处理显式本地来源；源完整性声明与机器验证分开 |
| Task 2 自洽 | 40 为文档容量，既不裁剪题目，也不把 gold ID 作为每题检索范围 |
| Task 3 自洽 | 单分区执行复用原 runtime；整体报告另外绑定完整计划，不删除缺失分区 |
| Task 4 自洽 | 静态审阅不冒充测试，先前 205 项不是本轮验收 |

Ruling: 保留此前测试暂停要求，只写回归用例；最新用户授权的是实现代码并明确不下载数据。

Ruling: 使用外层选择容器和独立语料计划身份，内层 canonical case 与 SDK 协议不变，以保持历史重建和 scorer 接口兼容。

Ruling: 本轮主代理负责运行/报告，子代理分别负责选择与分库；文件所有权不重叠，接口变化先沟通。整个计划文件作为进度账本，不运行技能辅助脚本或自动提交。
