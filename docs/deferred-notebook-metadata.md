# 资料导入后的单次笔记本元数据刷新

2026-10-16 开发侧实现。目标是减少串行导入中重复合成整本元数据的成本；真实服务器耗时与答案质量尚待验证。

## 代码交付与安装

交付仓库为 `Xi0ng-Jun/SN_benchmark`，独立分支为 `feat/deferred-notebook-metadata`。使用交付回执中的完整 evaluator SHA 固定版本，不以分支名称代替版本身份。服务器原基线为 `dfb7c51c2ea890f105189f8e30255a2e289a492e`；交付版本还包含其后的既有 workbench 设计文档、ALCE CPU 评分适配/浮点上界处理和显式 reasoning-no-clarification 协议。这些不是本次准备优化的一部分，相关选项按原默认保留。

先在服务器已有评测仓库 fetch，再创建独立 checkout；`EVAL_REPO` 指原评测仓库，`EVAL_CANDIDATE` 必须是尚不存在的新目录，`EVAL_RELEASE_SHA` 来自交付回执：

```bash
git -C "$EVAL_REPO" fetch origin refs/heads/feat/deferred-notebook-metadata
git -C "$EVAL_REPO" cat-file -e "${EVAL_RELEASE_SHA}^{commit}"
git -C "$EVAL_REPO" worktree add --detach "$EVAL_CANDIDATE" "$EVAL_RELEASE_SHA"
git -C "$EVAL_CANDIDATE" rev-parse HEAD
git -C "$EVAL_CANDIDATE" status --porcelain
```

如服务器不能访问远端，交付包中的 `evaluator.bundle` 包含从上述原基线到固定交付版本的 Git 对象。先在包目录运行 `sha256sum -c SHA256SUMS`，再执行 `git -C "$EVAL_REPO" bundle verify /absolute/path/evaluator.bundle`，通过后用 `git -C "$EVAL_REPO" fetch /absolute/path/evaluator.bundle refs/heads/feat/deferred-notebook-metadata` 代替上面的远端 fetch。已有仓库必须包含原基线对象；不对活动目录执行 checkout/reset/clean/pull。

在新 checkout 用现有评测解释器完成离线检查，显式把新源码置于导入路径，避免解释器的 editable install 指向旧仓库：

```bash
cd "$EVAL_CANDIDATE"
PYTHONPATH="$EVAL_CANDIDATE/src:$EVAL_CANDIDATE" "$EVAL_PYTHON" -c \
  'import rag_eval.notebook_runner as runner; print(runner.__file__)'
PYTHONPATH="$EVAL_CANDIDATE/src:$EVAL_CANDIDATE" "$EVAL_PYTHON" -m pytest -q \
  tests/test_notebook_preparation.py tests/test_notebook_batch.py \
  tests/test_notebook_v3_runtime.py tests/test_benchmark_submission.py
"$EVAL_PYTHON" scripts/run_notebook_benchmarks.py --help
"$EVAL_PYTHON" scripts/run_notebook_batch.py run --help
```

测试依赖使用服务器已有 dev/评测环境，不为此次交付更换模型服务或重建 frozen bundle。真实 SN 离线 probe 见下方专用入口。安装与离线检查不需要停止现有 orchestrator；新旧 Python 进程不会因为 fetch 自动换代码。后续真实诊断沿用用户已授权的运行范围；本文本身不扩大题单或自动切换全量批次。

## 策略与行为

单分区 `run_notebook_benchmarks.py`、批量 `run_notebook_batch.py run` 和 native Agent CLI `run_notebook_agent.py` 均接受：

```bash
--preparation-policy deferred-notebook-metadata-v1
```

省略参数时仍为 `per-source-metadata-v1`。Python `execute`、`plan_batch`、`prepare_notebook` 接受同名 `preparation_policy` 参数。工作台未增加此选项，继续使用默认策略。

新策略在同步 `upload_sources(..., scheduler=None)` 循环内使用 SN 的 `suppress_notebook_metadata_refresh()`。每篇摘要、论文元数据、原始文本、分块、embedding 和其他导入钩子沿用原路径。完整 chunk/embedding 覆盖校验及建索引通过后，退出 suppression，调用一次 `repo._augment_notebook_meta(notebook_id)`，之后才标记 prepared、进入 Ask。没有采用同时抑制其他钩子的 offline batch scope，没有开启缓存或导入并发。

SN 必须提供上述上下文与刷新入口；缺失时直接失败，不能静默回退。SN `970b679304a216c280b720666f18c4802065028f` 已用离线真实仓库 probe 验证。导入异常/取消会恢复上下文并保留已保存 mapping；不会合成半份语料的最终元数据，也不会进入 Ask。原来的新目录规则不变；这不是恢复旧 run 的机制。

SN 的最终刷新本身 best-effort：模型出错时可能发布 fallback，因此记录“调用返回”不能解释为“模型成功”。该优化减少中间调用，未证明随机模型的答案逐字相同；实际策略保留在运行记录与答卷披露中。

## 身份与回执

- `manifest.json: identity.preparation_policy`、`runtime-identity.json: preparation_policy`：明确策略；原始 protocol/pairing identity 保留，报告显示策略名。导出时不再要求所有题使用同一种准备策略。
- batch dry-run/job 行包含策略及子进程参数；`--dry-run` 可离线核对，不写 run、不调用模型。
- `product-artifacts/state.json: preparation_policy`：已有 notebook 的策略不匹配会被拒绝；旧无字段状态按默认策略处理。
- 新策略的 `final_metadata_refresh`：`invoked`、`call_returned`、`source_count`、`elapsed_seconds`；边界抛错或取消时保留 `call_returned=false`，不标记 prepared。
- `document-map.json` 的 `ingest_seconds` 只覆盖各篇上传；最终刷新时间另见上述回执。准备全过程的 usage 仍在 `preparation-usage.json`，包含最终刷新。模型用途/重试需结合 scheduled 及物理日志，不能把调度层事件差值当作物理重试证据。

**可以保留旧结果，用优化策略跑剩余题，再一起 `export-sn`。不需要重跑完整 948 题。** 这是 2026-10-16 根据用户“尽快完成实验”的反馈对初版过度限制的修正。

导出按相同 frozen bundle、SN 源码、模型/设置、请求和问答模式合并；准备策略、评测器文件哈希以及包含时间/checkout 路径的启动观测哈希不再阻止续跑合并。依赖声明和已安装包版本仍保留在共同条件中，原始启动观测仍由 run reader 校验。评测器哈希不同不自动证明行为等价；本轮沿用已核对的准备优化发布版本，其他评测器行为改动仍须按实验内容判断。

每题保留 `run_id`、`outputs_sha256`，新增 `run_provenance`（原始 identity 哈希、准备策略、评测器源码集合哈希）；方法配置汇总 `preparation_policies` 和 `evaluator_source_sha256s`。旧 run 缺策略字段时按原默认解释，不改写旧 manifest。重复题仍要求明确选择一次尝试，error/空答案照常保留，缺失题不会被删除。

## 证据和预期

服务器反馈（本机未读取远端原始日志）：3 个 ASQA run 各准备约 51–52 分钟；每题 notebook_metadata 448 次、source_summary 100 次、paper_metadata 100 次，实际缓存关闭。CPU/内存/I/O 较空闲。36 个 bindings 中 chat `max_concurrency=80` 是进程内配置，不能据此断言公司网关容量。

本地真实 SN repository + 离线假模型，分别执行两种策略，各 100 篇合成短资料，独立临时数据库/存储，不调用真实模型：

| 观测 | 默认策略 | 最后统一刷新 |
|---|---:|---:|
| notebook_metadata chat | 448 | 8 |
| source_summary chat | 100 | 100 |
| paper_metadata chat | 100 | 100 |
| source_element_embedding | 100 | 100 |
| chunk_embedding | 100 | 100 |
| 有 chunk 的资料 / 已嵌入 chunk | 100 / 100 | 100 / 100 |

同时验证 100 篇摘要及论文元数据标记已保存，所有资料标题进入元数据提示，笔记本最终 purpose 已写入。该 probe 在本地 SN `0680aa12` 及从 Git 提取的服务器同提交 `970b6793` 各通过一次；不依赖修改 SN 原树。测试入口：

```bash
SN_PREPARATION_BACKEND=/absolute/path/to/sn/backend \
  PYTHONPATH=src .venv/bin/python -m pytest -q tests/test_sn_preparation_integration.py
```

测试在独立子进程清除继承的配置/凭据并禁用 dotenv，使用 SN 的 RecordingModelProvider 和 FakeEmbedder；无兼容 checkout 时会 skip，不能把 skip 当通过。`448→8` 只适用于此处的 100 篇短摘要、16 sources/12,000 chars 分组；真实长摘要可能产生额外分组。

按服务器所报平均时延，减少 440 次 × 5.25 秒 ≈ 38.5 分钟；约 51–52 分钟可能降到约 13 分钟。这是推算，不是服务器测量，也不是端到端质量验收。

## 服务器续跑

按[服务器续跑交接文本](server-asqa-continuation-prompt.md)执行：安装新 checkout；停止旧 orchestrator 派发并等待在途 runner 完成；汇总所有已尝试题（包括错误），只运行剩余题。沿用原 captured SN ref、frozen bundle/index、候选资料、模型配置、cache=false 和题间并发 8，加 `--preparation-policy deferred-notebook-metadata-v1`。

从第一批剩余题观察 metadata 调用数、最终刷新、文档/chunk/embedding 覆盖和耗时；这些题直接计入实验，不另外重跑旧题作验收。最后使用新版本导出全部旧/新 run，统一做官方评分，并披露准备策略在续跑时切换。此次开发没有执行服务器模型调用、修改在跑任务或重跑 13 道合成失败题。私有 orchestrator 的停派/跳过已完成目录逻辑由服务器 agent 根据其实际脚本做最小修改；本仓库没有替它实现 drain。
