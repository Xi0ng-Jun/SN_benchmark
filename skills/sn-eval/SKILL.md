# Silicon Notebook 评测工具

这套工具只消费已经冻结的输入和运行工件。运行时生成不读取 gold；答案先写入 run，再单独导出和评分。当前实现是实验工具，正式 benchmark 题单和模型调用仍需人工核验。

## 命令清单

先设：

```bash
export EVAL_ROOT="$PWD"
export EVAL_PYTHON="$EVAL_ROOT/.venv/bin/python"
```

| 命令 | 何时用 | 必需参数 | 产物／费用 |
| --- | --- | --- | --- |
| `scripts/prepare_notebook_benchmarks.py --install-bundle` | 服务器已有 frozen bundle，要把它安装进不可变 shared store | `--install-bundle BUNDLE --artifact-root ARTIFACT_ROOT` | shared bundle/index；不调用模型 |
| `scripts/run_notebook_benchmarks.py` | 运行一个 captured partition × mode × attempt；每次都建全新的 run 目录 | `--bundle --artifact-root --artifact-index-id --partition-id --mode --request-revision --run-dir --project-root --model-config`；captured 还要 `--sn-source-ref --dependency-requirements --runtime-expectation`，单题 smoke 还要 `--case-id/--case-id-file --readiness-receipt` | `manifest.json`、`state.json`、`planned.jsonl`、`outputs.jsonl`、`scores.jsonl`、runtime/provenance；**会调用模型** |
| `scripts/benchmark_protocol.py export-sn` | 运行结束且答案已保存后，生成官方答卷 | `--bundle --runs RUN_DIR... --output OUTPUT`；smoke 另给 `--case-id-file` | `submission.json`；不调用模型 |
| `scripts/benchmark_protocol.py score` | 对冻结答卷做官方文本评分 | `--bundle --submission --sources --output` | `scores.json` 等官方结果；QASPER、MultiHop-RAG、HotpotQA、普通 QMSum/ALCE 文本路径不调用模型；ALCE `--alce-full` 或 `--alce-answer-only` **会调用模型** |
| `rag_eval.run_reader.load_run` | 离线读取 run 的状态、计划、输出、运行期分数和审计 | Python 中传 `run_dir`；可选 `include_model_events=True` | 只读 run 工件，不调用模型、不改文件 |
| `scripts/sn_eval_ledger.py append` | 一个新 run 完成后入账；可传独立官方 `scores.json` | `--run-dir RUN_DIR`；账本可用 `--ledger PATH`，官方分可用 `--scores PATH` | 追加 `var/sn-eval/runs.jsonl` 一行；重复 `run_dir` 拒绝 |
| `scripts/sn_eval_ledger.py query` | agent 查找已有运行 | 可选 `--suite --mode --state`；`--format json|table` | JSON 或表格；不调用模型 |

账本行固定包含 `run_dir`、`suite`、`bundle_id`、`partition_id`、`case_ids_count`、`mode`、`sn_source_ref`、`service_config_sha256`、`runtime_settings_sha256`、`state`、`exit_code`、`stderr_summary`、`official_score`、`stage_metrics`、`created_at`。退出码只取实际进程回执，可通过 `--exit-code` 与 `--stderr-summary` 传入；stderr 会脱敏并截取尾部 4096 字符，旧行缺字段时查询为 `null`。官方分和阶段指标没有实际文件时保留 `null`，不补零；`scores.jsonl` 是运行期指标，只有显式传入官方 `scores.json` 才写 `official_score`。

## 工作台 Agent 工具（`scripts/sn-eval`）

工作台里的 Agent 用它自己查上下文，不依赖用户附加材料。所有命令输出 JSON，不启动 SN、不调用模型；从任意目录都可以运行（脚本会切到评测仓库根目录）。

| 命令 | 角色 | 作用 |
| --- | --- | --- |
| `scripts/sn-eval --task T task` | 分析 | 任务目标、绑定 worktree、关联运行、假设卡 |
| `... runs [--all]` | 分析 | 本任务（或全部已登记）运行：状态、失败阶段、官方分、阶段指标、待读题、run_dir |
| `... cases RUN [--needs-read] [--status S]` | 分析 | 逐题行 |
| `... case RUN CASE` | 分析 | 单题完整证据：问题、SN 答案、逐题分、gold、引用、按排名的检索块 |
| `... flips LEFT RIGHT` | 分析 | 同题配对翻转表 |
| `... hypotheses` | 分析 | 本任务假设卡及核对结果 |
| `... hypothesis-draft --file card.json` | 分析 | 提交假设卡草稿；交给实现 Agent 的字段若与题面或 gold 重叠会被拒绝；草稿需人在更改页批准 |
| `... docs` | 分析 | 评测文档索引 |
| `... --role implementer --hypothesis H card` | 实现 | 已批准卡片中交给实现者的字段 |
| `... leak-check` | 两者 | 扫描 worktree diff 是否与本任务运行的题面、gold 或 case id 重叠（实现角色只看到重叠类型） |

角色分工：分析 Agent 工作目录是评测仓库，能看 gold，对 SN 只读，产出失败归类和假设卡。实现 Agent 工作目录是 Task 绑定的 SN worktree，只从已批准卡片的实现说明出发，看不到考题和 gold；服务端拒绝把案例、运行摘要、成绩发给实现 Agent。读文件这一层没有沙箱，隔离靠信息不下发、角色说明和泄漏扫描，不能当作强制隔离。

假设卡的逐题预测（`expect`: improve / unchanged / not_worse / worse，`guard` 表示守护题）在验证时按配对翻转表逐条核对；效果与原因结论由人分别填写。

## 付费边界

会启动 SN、访问模型服务并产生费用的只有 `run_notebook_benchmarks.py`，以及显式选择 ALCE 模型模式的 `score`。运行前必须先写一张预算卡并由人确认：suite、partition、case 数、mode、预计 embedding/chat/rerank 调用数、模型服务代际、超时和费用上限。没有确认就停在 prepare、离线检查、export、普通文本 score、load_run 或 ledger query。

## 禁止事项

- 不把 gold、答案标注或官方证据传给生成进程；生成侧只读冻结 source documents。
- 不复用已有 run 目录；重试必须新建 attempt 并记录原因。
- 不按成功状态筛题或丢弃 `no_answer`、`clarification`、`error`、`missing`、`pending`；状态按实际工件保留。
- 不把推测证据写成实际输出证据；证据只能来自 run 的保存输出。
- 不把运行期诊断分当官方分；先保存答案，再 export，再独立 score。
- 不打印凭据、`.env` 正文、服务地址或私有配置正文；结果只保留必要哈希和变量名状态。
- 不把实验配置、实验数据库、日志和 storage 接到生产；每个 run 使用隔离的 runtime。

## 今日单题链路（2026-10-13，可复现序列）

以下变量由操作者按本机材料填写；不要把私有配置内容写进命令或日志。`EVIDENCE_ROOT` 是今日证据目录，`ATTEMPT_ROOT` 是其中一个新建 attempt，`STORE_BUNDLE` 是 shared store 中的 bundle object，`SCORERS` 是固定 scorer 工件目录。

1. 零网络 preflight（只观测启动路径，在 ingestion 前停止）：

   ```bash
   cd "$EVAL_ROOT"
   env -i PATH HOME LANG USER "$EVAL_PYTHON" \
     "$EVIDENCE_ROOT/preflight/preflight_child.py" "$EVAL_ROOT" \
     "$EVIDENCE_ROOT/preflight/preflight-config.json"
   ```

2. 用已保存的 `preflight/readiness-input.json` 离线决定 readiness，并保存 receipt（此步不读主机凭据、不联网）：

   ```bash
   PYTHONPATH="$EVAL_ROOT/src" "$EVAL_PYTHON" - "$EVIDENCE_ROOT" <<'PY'
   import json, sys
   from pathlib import Path
   from rag_eval.s3_readiness import decide_readiness
   root = Path(sys.argv[1])
   result = decide_readiness(json.loads((root / "preflight/readiness-input.json").read_text()))
   (root / "preflight/readiness-receipt.json").write_text(
       json.dumps(result, ensure_ascii=False, indent=2) + "\n")
   print(result["status"])
   PY
   ```

3. 在预算卡获人确认后，通过 reviewed owner 启动唯一 captured 单题 attempt；`launch_attempt.py` 会调用 `run_notebook_benchmarks.py`，并消费 readiness receipt：

   ```bash
   "$EVAL_PYTHON" "$EVIDENCE_ROOT/launch_attempt.py" \
     "$EVAL_ROOT" "$EVIDENCE_ROOT" "attempt-1" 1800
   ```

4. 确认 `attempt-1/cli-run/state.json` 是终态，且 `outputs.jsonl` 已存在并保存该题；不要用分数筛掉失败状态。

5. 导出这一个真实 run 的答卷（只读 run，不做生成）：

   ```bash
   "$EVAL_PYTHON" scripts/benchmark_protocol.py export-sn \
     --bundle "$STORE_BUNDLE" \
     --runs "$EVIDENCE_ROOT/attempt-1/cli-run" \
     --case-ids "$CASE_ID" \
     --output "$EVIDENCE_ROOT/official/submission"
   ```

6. 做独立官方文本评分：

   ```bash
   "$EVAL_PYTHON" scripts/benchmark_protocol.py score \
     --bundle "$STORE_BUNDLE" \
     --submission "$EVIDENCE_ROOT/official/submission/submission.json" \
     --sources "$SCORERS" \
     --output "$EVIDENCE_ROOT/official/scores"
   ```

7. 离线回读完整 run（不调用模型）：

   ```bash
   PYTHONPATH="$EVAL_ROOT/src" "$EVAL_PYTHON" - "$EVIDENCE_ROOT" <<'PY'
   import sys
   from rag_eval.run_reader import load_run
   r = load_run(f"{sys.argv[1]}/attempt-1/cli-run", include_model_events=True)
   print(r["state"], r.get("scheduled_generation_audit"))
   PY
   ```

8. 把真实 run 和独立官方分写入账本；重复执行会被拒绝：

   ```bash
   "$EVAL_PYTHON" scripts/sn_eval_ledger.py append \
     --ledger "$EVAL_ROOT/var/sn-eval/runs.jsonl" \
     --run-dir "$EVIDENCE_ROOT/attempt-1/cli-run" \
     --scores "$EVIDENCE_ROOT/official/scores/scores.json"
   ```

9. 查询今日记录（JSON）：

   ```bash
   "$EVAL_PYTHON" scripts/sn_eval_ledger.py query \
     --ledger "$EVAL_ROOT/var/sn-eval/runs.jsonl" \
     --suite qasper --mode chunk --state finished --format json
   ```

10. 需要人工浏览时再请求表格视图；这一步仍然只读：

   ```bash
   "$EVAL_PYTHON" scripts/sn_eval_ledger.py query \
     --ledger "$EVAL_ROOT/var/sn-eval/runs.jsonl" --format table
   ```
