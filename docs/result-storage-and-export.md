# 共享结果存储、答卷导出与回传

核实日期：2026-10-08。本文描述当前实现和服务器操作方法，不表示服务器已经迁移或跑过新版实验。只维护 QASPER、MultiHop-RAG、ALCE、QMSum、HotpotQA；当前 campaign 仍为 SN-only：五套 chunk、MultiHop reasoning，先 smoke 后 full。外部方法与专门 Agent judge 任务不由存储改造自动启动。

## 目录与职责

推荐让同一 campaign 的 run 共用一个不可变 store，并把官方成绩放在打包器识别的 `official-scores/`：

```text
campaign/
  artifacts/
    objects/{bundle,index,evaluator,sn}/<object-id>/
    bundle-indexes/
    source-indexes/
  runs/<job--partition--attempt>/
    artifact-refs.json
    manifest.json
    product-bundle.json
    planned.jsonl
    outputs.jsonl
    scores.jsonl
    product-artifacts/
    agent/                      # 只有显式 Agent 运行才有
    runtime/                    # 每次运行独立
  submissions/<job>/submission.json
  official-scores/<job>/
  reports/
  comparisons/
```

对象类型、路径和内容哈希由 `artifact_store.py` 管理。bundle 对象保存冻结来源和评测侧 gold；index 对象保存派生分区 capsule。capsule 包含该分区的完整公开资料、原题单、评测侧 cases 和证据 catalogue，产品请求仍按 `notebook-request-v3` 排除 gold。index/object ID 不代替官方 `bundle_id`，不允许为节省存储删资料或改变 partition。

当前 installer 为每个 partition 保存 **三套 request revision（v1/v2/v3）** 的 capsule。每套 capsule 含公开资料与完整 product 请求，所以 index 按分区／版本重复这些字节，不是零成本索引；新正式运行只选 v3，不能把历史 v1/v2 的评测字段当作 v3 生成输入。bundle object identity 的 `partition_capsules` 逐文件哈希绑定派生结果；加载时把所选 capsule 的 index hash 与 bundle reference 对应绑定核对，不能替换一个自洽但未获 bundle 绑定的 index。

`artifact-refs.json` 使用相对 store 路径与明确对象身份；`manifest.artifact_identity` 绑定对象集合，不绑定可迁移的 store 根目录。evaluator 源码按实际字节快照；SN 源码按干净受跟踪 checkout 的 revision/tree 与 archive 校验。配置 TOML 仍复制到独立 runtime，公开身份只保存配置哈希。

reader 除了校验对象字节，还把 evaluator 文件哈希集合、SN archive hash 与 product revision 和 run 中记录的 code identity 精确对照；具有另一套有效哈希的源码对象也不能冒充本次执行源码。每个 run 的 `product-bundle.json` 仍保存完整分区请求，不因已存 capsule 改成小引用；它和 runtime 仍是随 run 增长的体积。

共享的是输入和源码。notebook、数据库、storage、模型配置、日志、索引及请求生命周期仍属于每个 partition × mode × attempt；不共享可变 runtime，不改变逐条 checkpoint/fsync，不提供隐式原目录续跑。

## 安装 frozen bundle

已有 bundle 先安装一次，不重新 prepare 或改写原目录。installer 仍调用 `load_bundle`，完整哈希核验原字节并重新适配一次，与 canonical 保存结果对照；这不是跳过来源重建的快路径。`--install-bundle` 是现行 flag；必须同时提供 `--artifact-root`，不能混用 `--suite/--raw/--source/--output/--corpus`：

```bash
export ARTIFACT_ROOT="$CAMPAIGN_ROOT/artifacts"
"$EVAL_PYTHON" scripts/prepare_notebook_benchmarks.py \
  --install-bundle "$BUNDLE" --artifact-root "$ARTIFACT_ROOT"
```

从本地原始数据新建 frozen bundle 时也可同次安装：

```bash
"$EVAL_PYTHON" scripts/prepare_notebook_benchmarks.py \
  --suite qasper --raw "$RAW" --source "$SOURCE" \
  --adaptation-revision notebook-data-v3 --output "$BUNDLE" \
  --artifact-root "$ARTIFACT_ROOT"
```

MultiHop-RAG 新建 bundle 另传实际 `--corpus`。安装完整验证 canonical bundle，随后生成全部分区索引；相同对象重复安装复用校验过的字节。原始 bundle 不删除，不以同名目录或旧 `validated=true` 跳过验证。旧 run 的 `input/` 副本仍能独立读，不会自动改成引用。

受信任的 installer 输出 `Shared bundle: <id>` 与 `Shared partition index: <id>`。保存安装输出，在 campaign 冻结配置中按每套／ALCE task 记录 bundle object ID 和 **预期 index ID**。运行前从这份已冻结配置设置 `ARTIFACT_INDEX_ID`；不要每次运行从可变的 `bundle-indexes/` pointer 重新取“预期”值。新的 shared run CLI/API 必须同时提供 artifact root 与预期 index ID，入口拒绝 pointer index ID 不符，也拒绝 bundle/index 同时被替换后自洽的新对象集合。重新安装得到不同 ID 时先审查来源／协议变化并新建明确的 campaign 版本，不能静默更新冻结值。

## 按分区运行与报告边界

在普通 Notebook CLI 同时提供 `--artifact-root` 和从冻结 campaign 配置取得的 `--artifact-index-id`，`--bundle` 仍指向已经安装过的 frozen bundle：

```bash
"$EVAL_PYTHON" scripts/run_notebook_benchmarks.py \
  --bundle "$BUNDLE" --artifact-root "$ARTIFACT_ROOT" \
  --artifact-index-id "$ARTIFACT_INDEX_ID" \
  --partition-id "$PARTITION_ID" --mode chunk \
  --request-revision notebook-request-v3 \
  --project-root "$PROJECT_ROOT" --model-config "$SN_MODEL_CONFIG" \
  --case-id-file "$PARTITION_CASE_FILE" \
  --run-dir "$CAMPAIGN_ROOT/runs/$JOB_ID--$PARTITION_ID--attempt-1"
```

上例是 smoke；full 省略题目筛选，MultiHop reasoning 使用另一新 run-dir。store 必须与 run-dir 分离；不得把 run 放在 store 内。Agent 和现有 QMSum baseline CLI 的共享调用也必须同时传 `--artifact-root`／`--artifact-index-id`；对应 Python API 同时传 `artifact_root`／`artifact_index_id`。这只说明代码能力，不扩大当前 SN-only 执行范围。

SN Notebook／Agent 的共享模式初始化验证实际消费的索引与 capsule，保留完整分区材料，不在每次 run 中重新适配整套数据。其自动生成的单 run 报告使用 `partition_only=True`；`summary.json.input_validation` 明示 `partition-capsule; full canonical audit required before official export`。这证明已用分区的输入契约，不能代替全 bundle 审计。

QMSum BM25 是另一条保留的历史／对照能力，不在当前 SN-only 执行范围。它虽复用共享引用，初始化提取原始 turns 时仍完整哈希输入并解析 raw data，不能将 SN 的 bounded startup 成本结论推广到所有方法。

默认 `run_report.load_run`、`write_report`、Dashboard 与正式 export 使用 canonical 验证。`RunReadContext` 只在一次调用内复用 bundle 和字典：共享对象每个物理对象核字节后才可复用同 manifest 的 canonical 适配；旧 run 的不同 `input/` 副本逐个验证。下一次调用重新验证，不信任 mtime 或旧收据。

## 正式 compact 答卷

显式列出目标 partition 的真实 run 目录；export 不递归查找父目录。attempt 冲突、混合 mode/model/request/source 继续拒绝，smoke scope 必须显式声明：

```bash
mapfile -t RUN_DIRS < "$CAMPAIGN_ROOT/$JOB_ID.run-dirs.txt"
"$EVAL_PYTHON" scripts/benchmark_protocol.py export-sn \
  --bundle "$BUNDLE" --runs "${RUN_DIRS[@]}" \
  --output "$CAMPAIGN_ROOT/submissions/$JOB_ID"
```

smoke 另加 `--case-id-file "$SMOKE_CASE_FILE"`；full 省略筛选即按整套 frozen case 对账，缺失题保持 missing。export 创建一个 canonical `RunReadContext`，复用到每个 reader 与 `write_submission`，不读无关模型事件日志。来源行保留 `run_id` 与本次 `outputs.jsonl` 的 `outputs_sha256`；读取期间 ledger 变化则拒绝本次导出，保持稳定后再导出。

顶层格式仍为 `benchmark-submission-v1`，`method.configuration.scoring_projection` 为 `sn-official-scoring-projection-v1`。完整审计事实仍在 `outputs.jsonl`，submission 仅保留必要记录：

| Suite | compact record |
| --- | --- |
| QASPER | status、answer、**完整 response/captures**、qasper_evidence 快照与身份、predicted_evidence |
| HotpotQA | status、answer、**完整 response/captures**、hotpot_evidence 快照与身份、predicted_supporting_facts |
| MultiHop-RAG | status、answer、**完整 retrieval**，包括真实顺序、文本、snapshot 和身份；reasoning 无 ranking 保持不适用／pending |
| ALCE | status、answer、response 原 anchors、source_to_document、anchor_documents、映射状态／错误 |
| QMSum | status、完整 answer；query/reference 由 frozen bundle 提供 |

每套保留必要 reason/error 字段；不截答案，不把 error/no_answer/clarification/missing 变成成功。QASPER/Hotpot observation hash 覆盖整个 response/captures，不能按已知键裁剪。投影前验证完整原 record 的 finite JSON、结构化凭据及禁止的 gold 标签，不能用 allowlist 掩盖违规。case 查找使用 set/dict，只复制保留值。

compact submission 不是完整 run，不能供 Dashboard 或组件补评代替 `outputs.jsonl`。旧 QASPER 没有原快照时，继续使用明确的 `export-sn --qasper-evidence` 只读恢复路径；恢复可能依赖服务器 runtime 数据库和 document-map，不能提前删除它们。

## 按角色打包

打包器识别 `runs/<run-id>/` 的直接子目录、`submissions/`、`official-scores/`、`prepared/`、`scorer-audit/`、`reports/` 和 `comparisons/`。新的 campaign 按上述布局保存。已有任意嵌套 run 或 `scores/` 不会自动转换成这些角色；先在新 staging campaign 按实际角色复制必要文件、保持共享引用可解析，原目录只读，不能假设整个 campaign tar 后就可复核。

```bash
"$EVAL_PYTHON" scripts/package_benchmark_results.py \
  --campaign "$CAMPAIGN_ROOT" --mode results \
  --output "$SERVER_ROOT/deliveries/$CAMPAIGN_ID-results.tar.gz"

"$EVAL_PYTHON" scripts/package_benchmark_results.py \
  --campaign "$CAMPAIGN_ROOT" --mode review \
  --output "$SERVER_ROOT/deliveries/$CAMPAIGN_ID-review.tar.gz"
```

| 模式 | 用途与内容 |
| --- | --- |
| results | compact submission、官方成绩／prepared 审计、scope/coverage/来源身份、已生成报告和清单；完整 raw observations 不回传 |
| review | results 加完整 outputs、计划／诊断分、必要 Agent 组件／轨迹／指标、映射与声明依赖；共享 bundle/index/source 去重，解包后能校验引用 |

两种模式均排除 runtime/storage/模型配置和原始模型／judge 日志，检查所选结构化 JSON 的凭据键；这不是自由文本隐私审查。共享依赖须位于 campaign 内，拒绝路径逃逸、符号链接与缺失依赖。review 为已声明的评分附件重写包内路径时，保留原 manifest 与重写审计，不改服务器源 run。

`--output *.tar.gz` 产生 archive 和相邻 `*.tar.gz.receipt.json`；其他新 output 名产生私有目录、相邻 `.tar.gz` 与收据。output 必须在 campaign 外且各目标不存在。目录权限 0700、文件／archive 0600。收据记录 archive hash/字节、campaign 与 packed inventory、总耗时、验证计数与共享对象份数；包内 `package-manifest.json` 逐文件哈希，`inventory.json` 区分 logical/allocated bytes（系统支持时）。

需要单独盘点或解包后验证时使用现行 Python API，没有额外虚构的 CLI 子命令：

```bash
PYTHONPATH=src "$EVAL_PYTHON" - "$CAMPAIGN_ROOT" <<'PY'
import json
from pathlib import Path
import sys
from rag_eval.result_package import inventory
print(json.dumps(inventory(Path(sys.argv[1])), ensure_ascii=False, indent=2))
PY

PYTHONPATH=src "$EVAL_PYTHON" - "$UNPACKED_REVIEW" <<'PY'
import json
from pathlib import Path
import sys
from rag_eval.result_package import validate_package
print(json.dumps(validate_package(Path(sys.argv[1])), ensure_ascii=False))
PY
```

包本身不证明已完成人工复核，也不承诺无需 runtime 即可执行旧 QASPER recovery、重生成或任何依赖数据库的操作。runtime 与原始日志继续留服务器；本实现没有自动删除、TTL、GC 或清理命令，失败／中断／运行中工件同样保留。

## 验证范围与服务器待验收

离线测试覆盖五套 full/compact prepared inputs 一致、证据与排名篡改拒绝、未回答与映射错误、共享对象／capsule／迁移／包完整性，以及 1k/5k/10k 合成答卷查找。用 `.venv/bin/python -m pytest -q -rs` 执行；真实 Perl ROUGE 需显式配置本地环境，不能把 skip 算作实测。

合成 QMSum 的上下文缩减只是结构样例；QASPER/Hotpot 保留 captures，真实体积可能仍大。服务器应按同一 B/S、题单、mode 和机器记录安装／运行／导出／打包阶段耗时、canonical rebuild 次数、峰值内存、input/source/runtime/outputs/agent 的 logical/allocated bytes、产物与包字节、逐题状态及官方分母。首次 canonical 安装和旧物理副本仍有完整 I/O，不承诺固定秒数、压缩比或服务器性能提升。

服务器执行交接见[存储与回传 prompt](server-result-storage-export-prompt.md)；全流程见 [RUNBOOK](../RUNBOOK.md)，派生评分见[Notebook 重评分](notebook-rescoring.md)。
